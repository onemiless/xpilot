"""Failure: provisioning holds the VPN lock after a simulated onroad transition."""
import hashlib
import os
from pathlib import Path
import tempfile
from openpilot.tesla.network_provision import _provision

with tempfile.TemporaryDirectory() as tmp:
  source = Path(tmp) / 'source'
  dest = Path(tmp) / 'dest'
  source.mkdir(mode=0o700)
  dest.mkdir(mode=0o700)
  core = source / 'mihomo'
  core.write_bytes(b'x' * (3 * 1024 * 1024))
  core.chmod(0o700)
  digest = hashlib.sha256(core.read_bytes()).hexdigest()
  count = 0
  def check():
    global count
    count += 1
    if count == 2:
      raise PermissionError('simulated onroad')
  try:
    _provision(source, dest, digest, os.getuid(), os.getuid(), check=check)
  except PermissionError:
    pass
  else:
    raise AssertionError('provision did not stop')
  assert not list(dest.iterdir()), 'cancelled provision left an executable or partial file'
print('in-flight core provisioning stops on onroad transition: PASS')
