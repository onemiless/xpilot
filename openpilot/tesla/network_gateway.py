"""Fixed root entrypoint; never imports or executes downloaded plugin code."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time

source = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(source), *(str(source / item) for item in
                ('opendbc_repo', 'msgq_repo', 'tinygrad_repo', 'rednose_repo'))]
sys.path.append('/usr/lib/python3/dist-packages')
from openpilot.tesla.network import CONTROL, authorized, checked_action, owner, parked, require_owner, valid_lease
from openpilot.tesla.network_gate import mutation_gate
from openpilot.tesla import network_routes as routes, network_runtime as runtime


def teardown(provider):
  try:
    runtime.stopcore(provider)
  except BaseException:
    routes.disable(provider, retain=True)
    raise
  routes.disable(provider)


def start_guarded(provider):
  try:
    runtime.startcore(provider)
  except PermissionError:
    # Leave the ownership journal for the onroad transition to clean after the lock is released.
    raise
  except BaseException:
    with mutation_gate():
      teardown(provider)
    raise


def routes_guarded(provider, handler):
  try:
    handler(provider)
  except (PermissionError, subprocess.TimeoutExpired):
    # A waiting transition or caller can retry cleanup using the journal.
    raise
  except BaseException:
    with mutation_gate():
      teardown(provider)
    raise


def main():
  os.umask(0o077)
  if os.geteuid() != 0 or not Path('/AGNOS').is_file():
    raise PermissionError('AGNOS root entrypoint required')
  provider, action = sys.argv[1:3]
  if action == 'watch':
    checked_action(provider, 'prepare')
    runtime.watch(provider, int(sys.argv[3]), sys.argv[4])
    return
  checked_action(provider, action)
  CONTROL.mkdir(mode=0o700, parents=True, exist_ok=True)
  if CONTROL.is_symlink() or CONTROL.stat().st_uid != 0:
    raise PermissionError('Invalid host control directory')
  if action == 'transition':
    with mutation_gate(preempt=True):
      state = owner()
      if state is not None:
        target = state.get('provider')
        checked_action(target, 'disable')
        teardown(target)
    print(json.dumps({'ok': True}))
    return
  with (CONTROL / 'lock').open('a') as lock:
    deadline = time.monotonic() + 10
    while True:
      try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        break
      except BlockingIOError:
        if time.monotonic() >= deadline:
          raise TimeoutError('Network operation busy')
        time.sleep(.05)
    result = {'ok': True}
    if action == 'status':
      state = owner()
      if state is not None:
        require_owner(state, provider)
        pids = runtime.core_pids()
        result['pid'] = pids[0] if len(pids) == 1 else None
      else:
        result['pid'] = None
    elif action in ('prepare', 'startcore', 'enable'):
      authorized(provider)
      parked()
      if not valid_lease(provider):
        raise PermissionError('VPN service lease is unavailable')
      if action == 'startcore':
        start_guarded(provider)
      else:
        routes_guarded(provider, routes.prepare if action == 'prepare' else routes.enable)
    else:
      with mutation_gate():
        if action == 'disable':
          teardown(provider)
        else:
          runtime.stopcore(provider)
  print(json.dumps(result))


if __name__ == '__main__':
  try:
    main()
  except Exception:
    print(json.dumps({'error': 'Host VPN operation failed'}))
    sys.exit(1)
