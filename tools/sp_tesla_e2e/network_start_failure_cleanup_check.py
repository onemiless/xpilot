"""Failure: a rejected core launch strands the previously prepared TUN interface."""
from contextlib import nullcontext
from unittest.mock import patch
from openpilot.tesla import network_gateway as gateway

calls = []
def start(provider):
  calls.append(('start', provider))
  raise RuntimeError('synthetic start failure')
def teardown(provider):
  calls.append(('teardown', provider))
with patch.object(gateway.runtime, 'startcore', start), patch.object(gateway, 'teardown', teardown), \
     patch.object(gateway, 'mutation_gate', nullcontext):
  try:
    gateway.start_guarded('test')
  except RuntimeError:
    pass
  else:
    raise AssertionError('startup failure hidden')
assert calls == [('start', 'test'), ('teardown', 'test')], calls
print('failed core launch clears prepared resources: PASS')
