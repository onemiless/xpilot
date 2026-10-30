"""Failure: VPN uses a divergent parking gate or permits engagement."""
from unittest.mock import patch
from openpilot.tesla import network, host_api
class FakeParams:
 def __init__(self,engaged=False): self.engaged=engaged
 def get_bool(self,key): return self.engaged if key=='IsEngaged' else True
with patch('openpilot.common.params.Params',return_value=FakeParams()), patch.object(host_api,'parked',return_value=True) as check:
 network.parked(); assert check.call_count==1
with patch('openpilot.common.params.Params',return_value=FakeParams(True)), patch.object(host_api,'parked',return_value=True):
 try: network.parked()
 except PermissionError: pass
 else: raise AssertionError('engaged request permitted')
with patch('openpilot.common.params.Params',return_value=FakeParams()), patch.object(host_api,'parked',return_value=False):
 try: network.parked()
 except PermissionError: pass
 else: raise AssertionError('onroad request permitted')
print('host parking contract and engaged veto: PASS')
