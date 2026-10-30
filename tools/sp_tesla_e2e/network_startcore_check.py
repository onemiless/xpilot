"""Failures: startup skips verified staging, leaks descriptors, or leaves an unwatched core."""
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
  with open(Path(tmp) / 'executable', 'w') as core, open(Path(tmp) / 'runtime', 'w') as config:
    fds = [os.dup(core.fileno()), os.dup(config.fileno())]
    process = FakeProcess()
    with patch.object(runtime, 'CONTROL', Path(tmp)), patch.object(runtime, 'owner', lambda: {'lease': 'lease'}), \
         patch.object(runtime, 'mutation_gate', nullcontext), \
         patch.object(runtime, 'require_owner'), patch.object(runtime, 'valid_lease', return_value=True), \
         patch.object(runtime, 'core_pids', side_effect=[[], [process.pid]]), patch.object(runtime, 'ticks', return_value='123'), \
         patch.object(runtime, 'parked'), \
         patch.object(network_provision, 'provision') as provision, \
         patch.object(network_artifacts, 'prepare', return_value=fds) as prepare, \
         patch.object(network_launch, 'launch', return_value=process) as launch, \
         patch.object(runtime.subprocess, 'Popen', side_effect=OSError('watcher failed')):
      try:
        runtime.startcore('test')
      except OSError:
        pass
      else:
        raise AssertionError('watcher failure ignored')
    assert provision.called and prepare.called and launch.called
    assert process.killed, 'unwatched core left running'
    for fd in fds:
      try: os.fstat(fd)
      except OSError: pass
      else: raise AssertionError('descriptor leaked')
print('verified startup and watcher-failure cleanup: PASS')
