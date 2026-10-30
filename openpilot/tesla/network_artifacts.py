"""Experimental fixed-path VPN artifact preparation; not yet connected to startup.

No arbitrary paths or executables are exposed by the public entrypoint. The
caller owns returned descriptors and must close both on every exit path.
"""
from pathlib import Path
import os
import yaml
from openpilot.tesla.network_config_guard import build_config
from openpilot.tesla.network_secure_files import open_approved_executable, read_fixed
from openpilot.tesla.network_secure_runtime import stage_config

SOURCE = Path('/data/mihomo')
CONTROL = Path('/data/xpilot_network')
BIN = CONTROL / 'bin'
CORE_SHA256 = 'e44258004baf8b61a4e804d86049c1e3312aae81e3f4ea6e265f0f7660714376'
CORE_SHA256S = (CORE_SHA256, 'c80df2ca5159567804da883401e0118908fc1155e05df2baf3dbd4d3ecfc3754')


def _prepare(source, control, binary, digest, source_owner, control_owner, runtime_owner, *, check=lambda: None):
  digests = digest if isinstance(digest, (tuple, list)) else (digest,)
  executable, _ = open_approved_executable(binary, 'mihomo', digests, owner=control_owner, check=check)
  try:
    raw = read_fixed(source, 'config.yaml', owner=source_owner, max_bytes=2*1024*1024)
    try:
      secret = read_fixed(source, 'secret', owner=source_owner, max_bytes=256).decode('utf8').strip()
    except UnicodeDecodeError:
      raise ValueError('invalid VPN secret') from None
    try:
      input_config = yaml.safe_load(raw)
    except yaml.YAMLError:
      raise ValueError('invalid VPN configuration') from None
    config = build_config(input_config, secret)
    data = yaml.safe_dump(config, allow_unicode=True).encode('utf8')
    runtime = stage_config(control, data, control_owner=control_owner, runtime_owner=runtime_owner)
    return executable, runtime
  except BaseException:
    os.close(executable)
    raise


def prepare():
  from openpilot.tesla.network import parked
  return _prepare(SOURCE, CONTROL, BIN, CORE_SHA256S, 1000, 0, 61400, check=parked)
