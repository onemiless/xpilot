"""Failure: a new mutation overwrites an unfinished recovery record."""
from contextlib import nullcontext
from unittest.mock import patch
from openpilot.tesla import network_routes as routes

state = {'pending': {'key': 'interface', 'value': True}}
with patch.object(routes, 'mutation_gate', nullcontext), patch.object(routes, 'parked'), \
     patch.object(routes, 'owner', return_value=state), patch.object(routes, 'resource_absent', return_value=True), \
     patch.object(routes, 'run') as run, patch.object(routes, 'remember') as remember:
  try:
    routes.journaled('rules', ['mark'], 'ip', 'rule', 'add', 'priority', '17880')
  except RuntimeError:
    pass
  else:
    raise AssertionError('new mutation was accepted before recovery')
  assert not run.called and not remember.called
print('unfinished VPN mutation prevents further changes: PASS')
