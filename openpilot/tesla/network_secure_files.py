"""Experimental fixed-name file reads through a trusted directory descriptor.

Not wired into the VPN helper; binary provenance, configuration confinement,
resource cleanup and privilege separation must be addressed independently.
"""
import os
import re
import stat


def read_fixed(root, name, *, owner=0, max_bytes=1048576):
  if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+', name) or name in ('.', '..'):
    raise ValueError('not a fixed filename')
  if type(max_bytes) is not int or max_bytes < 0:
    raise ValueError('invalid length limit')
  flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
  directory = os.open(root, flags)
  try:
    info = os.fstat(directory)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != owner or info.st_mode & 0o022:
      raise PermissionError('untrusted directory')
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=directory)
    try:
      info = os.fstat(fd)
      if not stat.S_ISREG(info.st_mode) or info.st_uid != owner or info.st_nlink != 1 or info.st_mode & 0o022:
        raise PermissionError('untrusted file')
      chunks = []
      remaining = max_bytes + 1
      while remaining:
        chunk = os.read(fd, min(remaining, 65536))
        if not chunk:
          break
        chunks.append(chunk)
        remaining -= len(chunk)
      data = b''.join(chunks)
      if len(data) > max_bytes:
        raise ValueError('file exceeds limit')
      return data
    finally:
      os.close(fd)
  finally:
    os.close(directory)


def open_approved_executable(root, name, expected_sha256s, *, owner=0, check=lambda: None):
  """Return a verified open descriptor and its digest; caller must close it."""
  import hashlib
  if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+', name) or name in ('.', '..'):
    raise ValueError('not a fixed filename')
  if not isinstance(expected_sha256s, (tuple, list)) or not expected_sha256s or not all(
      isinstance(value, str) and re.fullmatch(r'[a-f0-9]{64}', value) for value in expected_sha256s):
    raise ValueError('invalid binary digest')
  directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
  try:
    info = os.fstat(directory)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != owner or info.st_mode & 0o022:
      raise PermissionError('untrusted directory')
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=directory)
    try:
      info = os.fstat(fd)
      if not stat.S_ISREG(info.st_mode) or info.st_uid != owner or info.st_nlink != 1 or info.st_mode & 0o022 or not info.st_mode & 0o111:
        raise PermissionError('untrusted executable')
      if not 0 < info.st_size <= 128 * 1024 * 1024:
        raise ValueError('invalid executable size')
      h = hashlib.sha256()
      while block := os.read(fd, 1024 * 1024):
        check()
        h.update(block)
      digest = h.hexdigest()
      if digest not in expected_sha256s:
        raise ValueError('executable digest mismatch')
      os.lseek(fd, 0, os.SEEK_SET)
      return fd, digest
    except BaseException:
      os.close(fd)
      raise
  finally:
    os.close(directory)


def open_verified_executable(root, name, expected_sha256, *, owner=0, check=lambda: None):
  """Return an open verified descriptor; caller must close it and execute by fd."""
  fd, _ = open_approved_executable(root, name, (expected_sha256,), owner=owner, check=check)
  return fd
