"""Failure: accepting an older approved core requires repeated full-file hashing."""
import hashlib
import os
from pathlib import Path
import tempfile
from unittest.mock import patch
from openpilot.tesla import network_secure_files as files

with tempfile.TemporaryDirectory() as tmp:
  binary = Path(tmp) / 'mihomo'
  binary.write_bytes(b'approved fixture' * 1000)
  binary.chmod(0o700)
  digest = hashlib.sha256(binary.read_bytes()).hexdigest()
  read_count = [0]
  original = os.read
  def read(fd, length):
    read_count[0] += 1
    return original(fd, length)
  with patch.object(files.os, 'read', read):
    fd, found = files.open_approved_executable(tmp, 'mihomo', ('0' * 64, digest), owner=os.getuid())
  try:
    assert found == digest
    assert read_count[0] == 2, f'core read more than once: {read_count[0]}'
    binary.unlink()
    binary.write_bytes(b'different')
    assert os.read(fd, 8) == b'approved'
  finally:
    os.close(fd)
print('approved core identified with one verified read and pinned descriptor: PASS')
