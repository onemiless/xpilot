"""Bounded host cleanup attempt when a VPN-owning device goes onroad."""
from pathlib import Path
import subprocess
from openpilot.tesla.network import GATEWAY_PYTHON

INTERFACE = Path('/sys/class/net/c3mh0')
PROC = Path('/proc')
VPN_UID = 61400


def vpn_present():
  if INTERFACE.exists():
    return True
  try:
    for entry in PROC.iterdir():
      try:
        if entry.name.isdigit() and entry.stat().st_uid == VPN_UID:
          return True
      except OSError:
        continue
  except OSError:
    return True  # Fail closed: ask the host rather than silently skip cleanup.
  return False


def onroad_transition():
  if not vpn_present():
    return True
  helper = Path(__file__).resolve().with_name('network_gateway.py')
  try:
    result = subprocess.run(['sudo', '-n', '/usr/bin/timeout', '-k', '0.2s', '1.5s',
                             str(GATEWAY_PYTHON), str(helper), 'host', 'transition'],
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            timeout=2, check=False)
    return result.returncode == 0
  except (OSError, subprocess.SubprocessError):
    return False
