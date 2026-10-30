"""Failure: cancelled parked operation keeps the gateway lock during teardown."""
from unittest.mock import patch
from openpilot.tesla import network_gateway as gateway

def cancelled(_provider):
  raise PermissionError('onroad')
with patch.object(gateway.runtime, 'startcore', cancelled), patch.object(gateway, 'teardown') as teardown:
  try:
    gateway.start_guarded('test')
  except PermissionError:
    pass
  else:
    raise AssertionError('cancelled operation did not return')
  assert not teardown.called, 'onroad transition cannot acquire lock promptly'
print('onroad-cancelled startup releases lock for transition cleanup: PASS')
