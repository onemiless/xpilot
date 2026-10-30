"""Failure: journal write fails after successful rule creation and leaves a route rule."""
from contextlib import nullcontext
from unittest.mock import patch
from openpilot.tesla import network_routes as routes

calls = []
def run(*args, **_kwargs):
  calls.append(args)
  class Result: returncode = 0
  return Result()
def remember(key, _value):
  if key == 'rules': raise OSError('journal failed')
with patch.object(routes, 'mutation_gate', nullcontext), patch.object(routes, 'parked'), \
     patch.object(routes, 'owner', return_value={}), patch.object(routes, 'resource_absent', return_value=True), patch.object(routes, 'run', run), \
     patch.object(routes, 'remember', remember):
  try:
    routes.journaled('rules', ['mark'], 'ip', 'rule', 'add', 'priority', '17880',
                     'fwmark', routes.MARK + '/0xffff', 'lookup', routes.TABLE)
  except OSError:
    pass
  else:
    raise AssertionError('journal failure was ignored')
assert len(calls) == 2 and calls[1][:3] == ('ip', 'rule', 'del'), calls
print('failed journal write rolls back created rule: PASS')
