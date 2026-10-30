"""Core and independent crash cleanup for host-owned VPN resources."""
import json
import os
from pathlib import Path
import signal
import subprocess
import time

from openpilot.tesla.network import CONTROL, GATEWAY_PYTHON, ROOT, UID, lease, owner, parked, require_owner, ticks, valid_lease
from openpilot.tesla.network_gate import mutation_gate
from openpilot.tesla.network_routes import disable


def core_pids():
  result = []
  try:
    binary = (CONTROL / 'bin' / 'mihomo').stat()
  except OSError:
    return result
  for entry in Path('/proc').iterdir():
    if not entry.name.isdigit():
      continue
    try:
      executable = (entry / 'exe').stat()
      if entry.stat().st_uid == UID and (executable.st_dev, executable.st_ino) == (binary.st_dev, binary.st_ino):
        result.append(int(entry.name))
    except (OSError, ValueError):
      pass
  return result


def startcore(provider):
  from openpilot.tesla import network_artifacts, network_launch, network_provision
  state = owner()
  require_owner(state, provider)
  if not valid_lease(provider, state['lease']) or core_pids():
    raise RuntimeError('VPN daemon is unavailable or core already exists')
  parked()
  network_provision.provision()
  parked()
  executable, directory = network_artifacts.prepare()
  process = None
  try:
    with mutation_gate():
      parked()
      current = owner()
      require_owner(current, provider)
      if current.get('lease') != state['lease'] or not valid_lease(provider, current['lease']):
        raise PermissionError('VPN service lease changed')
      process = network_launch.launch(executable, directory)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
      parked()
      if process.poll() is not None:
        raise RuntimeError('VPN core exited')
      if process.pid in core_pids():
        helper = Path(__file__).resolve().with_name('network_gateway.py')
        watcher = subprocess.Popen([str(GATEWAY_PYTHON), str(helper), provider, 'watch',
                                    str(process.pid), ticks(process.pid)], stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, start_new_session=True)
        (CONTROL / 'network-watch.pid').write_text(str(watcher.pid))
        return
      time.sleep(.05)
    raise RuntimeError('VPN core launch timed out')
  except BaseException:
    if process is not None and process.poll() is None:
      process.kill()
      process.wait(timeout=2)
    raise
  finally:
    os.close(executable)
    os.close(directory)


def stop_watch():
  path = CONTROL / 'network-watch.pid'
  try:
    pid = int(path.read_text())
    command = Path(f'/proc/{pid}/cmdline').read_bytes()
    if Path(f'/proc/{pid}').stat().st_uid == 0 and b'network_gateway.py' in command and b'watch' in command:
      started = ticks(pid)
      def alive():
        try:
          fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
          return fields[0] != 'Z' and fields[19] == started
        except (OSError, IndexError):
          return False
      os.kill(pid, signal.SIGTERM)
      deadline = time.monotonic() + .3
      while alive() and time.monotonic() < deadline:
        time.sleep(.05)
      if alive():
        os.kill(pid, signal.SIGKILL)
        deadline = time.monotonic() + .3
        while alive() and time.monotonic() < deadline:
          time.sleep(.05)
        if alive():
          raise RuntimeError('VPN watcher did not stop')
  except (OSError, ValueError):
    pass
  path.unlink(missing_ok=True)


def stopcore(provider):
  if owner() is None:
    return
  require_owner(owner(), provider)
  stop_watch()
  targets = core_pids()
  for pid in targets:
    try:
      os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
      pass
  deadline = time.monotonic() + .3
  while time.monotonic() < deadline and set(targets) & set(core_pids()):
    time.sleep(.05)
  for pid in set(targets) & set(core_pids()):
    try:
      os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
      pass
  deadline = time.monotonic() + .3
  while time.monotonic() < deadline and set(targets) & set(core_pids()):
    time.sleep(.05)
  if set(targets) & set(core_pids()):
    raise RuntimeError('VPN core did not stop')


def parking_allowed():
  try:
    parked()
    return True
  except (OSError, PermissionError):
    return False


def watch(provider, core_pid, started):
  import fcntl
  from openpilot.tesla.network import CONTROL
  from openpilot.tesla.network_gate import mutation_gate
  expected = owner()
  require_owner(expected, provider)
  stopped = False
  def explicit_stop(*_):
    nonlocal stopped
    stopped = True
  signal.signal(signal.SIGTERM, explicit_stop)
  signal.signal(signal.SIGINT, explicit_stop)
  def same_core():
    try:
      return core_pid in core_pids() and ticks(core_pid) == started
    except OSError:
      return False
  while not stopped and same_core() and valid_lease(provider, expected['lease']) and parking_allowed():
    time.sleep(.5)
  if stopped:
    return
  with (CONTROL / 'lock').open('a') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    with mutation_gate():
      current = owner()
      if current is None or current.get('lease') != expected['lease']:
        return  # Never clean a replacement generation.
      if same_core():
        os.kill(core_pid, signal.SIGTERM)
        deadline = time.monotonic() + 1
        while same_core() and time.monotonic() < deadline:
          time.sleep(.05)
        if same_core():
          os.kill(core_pid, signal.SIGKILL)
          deadline = time.monotonic() + 1
          while same_core() and time.monotonic() < deadline:
            time.sleep(.05)
          if same_core():
            disable(provider, retain=True)
            raise RuntimeError('VPN core did not stop; ownership retained for retry')
      disable(provider)
