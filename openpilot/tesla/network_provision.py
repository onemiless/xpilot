"""Provision a fixed digest-verified core into an existing trusted directory.

The directory bootstrap and production startup integration are separate tasks.
"""
import hashlib
import os
from pathlib import Path
import secrets
import stat

from openpilot.tesla.network_secure_files import open_approved_executable, open_verified_executable


def _find_approved(source, digests, owner, *, check=lambda: None):
  fd, digest = open_approved_executable(source, 'mihomo', digests, owner=owner, check=check)
  os.close(fd)
  return digest


def _provision(source, destination, digest, source_owner, destination_owner, *, check=lambda: None, source_fd=None):
  check()
  src = (os.dup(source_fd) if source_fd is not None else
         open_verified_executable(source, 'mihomo', digest, owner=source_owner, check=check))
  directory = None
  temporary = None
  try:
    directory = os.open(destination, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    info = os.fstat(directory)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != destination_owner or info.st_mode & 0o022:
      raise PermissionError('untrusted binary directory')
    temporary = '.core-' + secrets.token_hex(16)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                 0o755, dir_fd=directory)
    try:
      checksum = hashlib.sha256()
      with os.fdopen(fd, 'wb') as output:
        os.fchmod(output.fileno(), 0o755)
        while chunk := os.read(src, 1024 * 1024):
          check()
          checksum.update(chunk)
          output.write(chunk)
        output.flush()
        os.fsync(output.fileno())
      if checksum.hexdigest() != digest:
        raise ValueError('core changed during copy')
      check()
      try:
        os.link(temporary, 'mihomo', src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
      except FileExistsError:
        existing = open_verified_executable(destination, 'mihomo', digest, owner=destination_owner, check=check)
        os.close(existing)
      os.unlink(temporary, dir_fd=directory)
      temporary = None
      os.fsync(directory)
    finally:
      if temporary is not None:
        try:
          os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError:
          pass
  finally:
    if directory is not None:
      os.close(directory)
    os.close(src)


def provision():
  from openpilot.tesla.network_artifacts import BIN, CONTROL, CORE_SHA256S, SOURCE
  from openpilot.tesla.network import parked
  parent = os.open(CONTROL, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
  try:
    info = os.fstat(parent)
    if info.st_uid != 0 or info.st_mode & 0o022:
      raise PermissionError('untrusted control directory')
    try:
      os.mkdir('bin', 0o700, dir_fd=parent)
    except FileExistsError:
      pass
  finally:
    os.close(parent)
  try:
    _find_approved(BIN, CORE_SHA256S, 0, check=parked)
    return
  except FileNotFoundError:
    pass
  source_fd, digest = open_approved_executable(SOURCE, 'mihomo', CORE_SHA256S, owner=1000, check=parked)
  try:
    _provision(SOURCE, BIN, digest, 1000, 0, check=parked, source_fd=source_fd)
  finally:
    os.close(source_fd)
