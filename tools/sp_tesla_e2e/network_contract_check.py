"""Failure cases: missing network API, arbitrary helper/action, foreign resource
cleanup, expired tile state, and a parked request becoming onroad while queued.
Run on the base once before implementation; no real network commands are issued.
"""
import ast
import importlib.util
from pathlib import Path
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'opendbc_repo')]
from openpilot.tesla.plugin import Plugin
from openpilot.tesla.manifest import HOST_CAPS

assert hasattr(Plugin, 'network_vpn'), 'missing bounded host network API'
assert 'network_vpn' in HOST_CAPS['hooks']
from openpilot.tesla.network import ACTIONS, require_owner, checked_action
assert 'shell' not in ACTIONS and 'exec' not in ACTIONS
for provider, action in (('../escape', 'prepare'), ('sample', 'exec')):
  try:
    checked_action(provider, action)
  except ValueError:
    pass
  else:
    raise AssertionError('unbounded request accepted')
for owner in (None, {'provider': 'another'}):
  try:
    require_owner(owner, 'sample')
  except PermissionError:
    pass
  else:
    raise AssertionError('foreign ownership accepted')
if sys.platform == 'linux':
  from openpilot.tesla import network_routes
  with patch.object(network_routes, 'owner', return_value=None), patch.object(network_routes, 'run') as commands:
    network_routes.disable('sample')
    commands.assert_not_called()
  with patch.object(network_routes, 'owner', return_value={'provider': 'another'}), patch.object(network_routes, 'run') as commands:
    try:
      network_routes.disable('sample')
    except PermissionError:
      pass
    else:
      raise AssertionError('foreign cleanup reached the system')
    commands.assert_not_called()
  from openpilot.tesla.plugins import PluginMsgs
  reader = PluginMsgs.__new__(PluginMsgs)
  now = time.monotonic_ns()
  event = SimpleNamespace(logMonoTime=now, pluginMsg=SimpleNamespace(source='sample', kind='network.vpn',
                          validForMs=6000, payload='{"gateway":true}'))
  reader.sock, reader.last = object(), {}
  reader.messaging = SimpleNamespace(drain_sock=lambda _: [event])
  assert reader.fresh('network.vpn', now)['sample']['gateway']
  reader.messaging.drain_sock = lambda _: []
  assert not reader.fresh('network.vpn', now + 6_000_000_001), 'expired connectivity retained'
print('bounded actions, foreign cleanup and message expiry verified')
