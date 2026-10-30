"""Failure: hashing a large VPN core cannot yield to an onroad transition."""
import hashlib
import os
from pathlib import Path
import tempfile
from openpilot.tesla.network_secure_files import open_verified_executable

with tempfile.TemporaryDirectory() as tmp:
  core = Path(tmp) / 'mihomo'
  core.write_bytes(b'a' * (3 * 1024 * 1024))
  core.chmod(0o700)
  digest = hashlib.sha256(core.read_bytes()).hexdigest()
  count = 0
  def check():
    global count
    count += 1
    if count == 2:
      raise PermissionError('onroad')
  try:
    fd = open_verified_executable(tmp, 'mihomo', digest, owner=os.getuid(), check=check)
  except PermissionError:
    pass
  else:
    os.close(fd)
    raise AssertionError('hashing ignored onroad transition')
print('core hash verification cancels onroad: PASS')
