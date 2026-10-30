"""Failure: an in-flight VPN startup continues after the vehicle goes onroad."""
import os
from contextlib import nullcontext
from pathlib import Path
import tempfile
from unittest.mock import patch
from openpilot.tesla import network_runtime as runtime
from openpilot.tesla import network_artifacts, network_launch, network_provision

class FakeProcess:
  pid = 54321
  killed = False
  def poll(self): return None
  def kill(self): self.killed = True
  def wait(self, timeout=None): return 0

with tempfile.TemporaryDirectory() as tmp:
  with open(Path(tmp) / 'core', 'w') as core, open(Path(tmp) / 'config', 'w') as config:
    fds = [os.dup(core.fileno()), os.dup(config.fileno())]
    process = FakeProcess()
    # Two checks pass; the third simulates the transition after launch.
    with patch.object(runtime, 'owner', lambda: {'lease': 'lease'}), patch.object(runtime, 'require_owner'), \
         patch.object(runtime, 'valid_lease', return_value=True), patch.object(runtime, 'core_pids', return_value=[]), \
         patch.object(runtime, 'mutation_gate', nullcontext), \
         patch.object(runtime, 'parked', side_effect=[None, None, None, PermissionError('onroad')]), \
         patch.object(network_provision, 'provision'), patch.object(network_artifacts, 'prepare', return_value=fds), \
         patch.object(network_launch, 'launch', return_value=process):
      try:
        runtime.startcore('test')
      except PermissionError:
        pass
      else:
        raise AssertionError('startup did not stop after onroad transition')
    assert process.killed, 'core survived onroad transition during startup'
    for fd in fds:
      try: os.fstat(fd)
      except OSError: pass
      else: raise AssertionError('descriptor leaked')
print('in-flight core startup stops on onroad transition: PASS')
