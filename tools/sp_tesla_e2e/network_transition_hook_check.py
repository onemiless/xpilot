"""Failure: transition cleanup blocks driving startup or runs when VPN is absent."""
import subprocess
import time
from unittest.mock import patch
from openpilot.tesla import network_transition as transition

with patch.object(transition, 'vpn_present', return_value=False), \
     patch.object(transition.subprocess, 'run') as run:
  assert transition.onroad_transition() is True
  assert not run.called

with patch.object(transition, 'vpn_present', return_value=True), \
     patch.object(transition.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as run:
  assert transition.onroad_transition() is True
  assert run.call_args.kwargs['timeout'] <= 2

with patch.object(transition, 'vpn_present', return_value=True), \
     patch.object(transition.subprocess, 'run', side_effect=subprocess.TimeoutExpired('test', 2)):
  start = time.monotonic()
  assert transition.onroad_transition() is False
  assert time.monotonic() - start < 1
print('bounded onroad transition cleanup: PASS')
