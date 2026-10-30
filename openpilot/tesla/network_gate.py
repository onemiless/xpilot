"""Serialize network mutations independently from long-lived gateway requests."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import signal
import time
from openpilot.tesla import network

GATEWAY = Path(__file__).resolve().with_name('network_gateway.py')


def preempt_stuck_gateway():
  """Kill only a verified gateway in its own session, never an unrelated holder."""
  try:
    record = json.loads((network.CONTROL / 'mutation.owner').read_text())
    pid = int(record['pid'])
    group = int(record['group'])
    proc = Path(f'/proc/{pid}')
    cmd = (proc / 'cmdline').read_bytes().split(b'\0')
    session = os.getsid(pid)
    if (pid <= 1 or group <= 1 or group == os.getpgrp() or proc.stat().st_uid != 0
        or network.ticks(pid) != record['start'] or os.getpgid(pid) != group
        or session == os.getsid(0) or len(cmd) < 2 or cmd[1] != os.fsencode(GATEWAY)):
      return False
    if session != group:
      leader = Path(f'/proc/{group}')
      leader_cmd = (leader / 'cmdline').read_bytes().split(b'\0')
      if (leader.stat().st_uid != 0 or os.getsid(group) != session or not leader_cmd
          or leader_cmd[0] != b'/usr/bin/timeout' or os.fsencode(GATEWAY) not in leader_cmd):
        return False
    os.killpg(group, signal.SIGKILL)
    return True
  except (OSError, ValueError, KeyError, TypeError):
    return False


@contextmanager
def mutation_gate(timeout=.6, *, preempt=False):
  with (network.CONTROL / 'mutation.lock').open('a') as lock:
    deadline = time.monotonic() + timeout
    tried = False
    while True:
      try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        break
      except BlockingIOError:
        if preempt and not tried:
          preempt_stuck_gateway()
          tried = True
        if time.monotonic() >= deadline:
          raise TimeoutError('VPN mutation busy')
        time.sleep(.02)
    try:
      record = {'pid': os.getpid(), 'group': os.getpgrp(), 'start': network.ticks(os.getpid())}
      pending = network.CONTROL / 'mutation.owner.pending'
      pending.write_text(json.dumps(record))
      os.replace(pending, network.CONTROL / 'mutation.owner')
      yield
    finally:
      (network.CONTROL / 'mutation.owner').unlink(missing_ok=True)
      fcntl.flock(lock, fcntl.LOCK_UN)
