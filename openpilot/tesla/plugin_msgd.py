"""One cereal publisher for advisory plugin messages; Linux authenticates AF_UNIX senders by PID."""
from collections import deque
import fcntl
import json
import os
from pathlib import Path
import select
import socket
import stat
import struct
import sys
import time

MAX_PAYLOAD = 16 * 1024
MAX_DATAGRAM = 128 * 1024
SOURCE_HZ = 100
SOURCE_BYTES = 256 * 1024
TOTAL_BYTES = 1024 * 1024
HOST_CLAIMS = {
  "plannerd": ("long.target", "plan.filter", "role:long.planner"),
  "controlsd": ("lat.correction", "role:lateral.controller", "role:long.owner"),
  "selfdrived": ("alert", "event.filter", "role:engagement.state", "role:safety.fallback"),
  "modeld": ("role:lane_change.policy", "role:perception.model"),
  "models_manager": ("role:perception.model",),
  "modeld_tinygrad": ("role:perception.model",),
  "modeld_warmup": ("role:perception.model",),
}


def socket_path() -> str:
  return f"/tmp/xpilot-plugin-msg-{os.environ.get('OPENPILOT_PREFIX', '')}/socket"


class Broker:
  def __init__(self):
    self.path = Path(socket_path())
    self.path.parent.mkdir(mode=0o700, exist_ok=True)
    directory = self.path.parent.lstat()
    if not stat.S_ISDIR(directory.st_mode) or directory.st_uid != os.getuid() or directory.st_mode & 0o077:
      raise PermissionError('pluginMsg socket requires a private directory owned by this user')
    from openpilot.cereal import messaging
    from openpilot.common.params import Params
    from openpilot.tesla import plugins
    self.messaging, self.plugins, self.params = messaging, plugins, Params()
    self.lock = (self.path.parent / 'lock').open('w')
    fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    self.path.unlink(missing_ok=True)
    self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    self.sock.setblocking(False)
    if sys.platform == 'linux':
      self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
    elif sys.platform != 'darwin':
      raise RuntimeError('pluginMsg requires Linux credentials (Darwin is test-only)')
    self.sock.bind(str(self.path))
    self.pm = messaging.PubMaster(['pluginMsg'])
    self.sm = messaging.SubMaster(['managerState', 'deviceState'])
    self.publishers, self.pids = {}, {}
    self.usage, self.total, self.seq = {}, deque(), {}
    self.total_bytes = 0
    self.drive, self.faulted = None, set()
    self.fault_path = self.path.parent / 'faults.json'
    if self.fault_path.exists():
      saved = json.loads(self.fault_path.read_text())
      self.drive = saved['drive']
      self.faulted = set(saved['sources']) & (self.params.get(self.plugins.FAULTS_PARAM) or {}).keys()

  def save_faults(self):
    temporary = self.fault_path.with_suffix('.tmp')
    temporary.write_text(json.dumps({'drive': self.drive, 'sources': sorted(self.faulted)}))
    temporary.replace(self.fault_path)

  def fault(self, source, reason):
    if source not in self.faulted:
      self.faulted.add(source)
      self.save_faults()
      self.plugins.record_fault(source, 'pluginMsg', ValueError(reason))

  def update(self):
    self.sm.update(0)
    if self.sm.updated['managerState']:
      self.pids = {p.pid: p.name for p in self.sm['managerState'].processes if p.running}
      self.publishers = {p.NAME: p for p in self.plugins.active(self.params) if any(c.startswith('msg:') for c in p.CLAIMS)}
    if self.sm.updated['deviceState']:
      state = self.sm['deviceState']
      drive = state.startedMonoTime if state.started else 0
      if self.drive != drive:
        self.drive, self.faulted = drive, set()
        self.save_faults()

  def receive(self):
    data, credentials, flags, _ = self.sock.recvmsg(MAX_DATAGRAM, socket.CMSG_SPACE(struct.calcsize('3i')))
    pid = None
    if sys.platform == 'linux':
      for level, kind, value in credentials:
        if level == socket.SOL_SOCKET and kind == socket.SCM_CREDENTIALS:
          pid, uid, _ = struct.unpack('3i', value)
          if uid != os.getuid():
            return
    if flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC):
      return
    try:
      d = json.loads(data)
      if sys.platform == 'darwin':
        pid = d.get('pid')  # Test-only: Darwin datagrams have no SCM_CREDENTIALS; never used on Linux.
      proc = self.pids.get(pid)
      source, kind = d['source'], d['kind']
      plugin = self.publishers.get(source)
      if self.drive is None or plugin is None or proc is None or not self.sm.alive['managerState']:
        return
      claims = plugin.CLAIMS
      if not (proc in ('card', 'ui') or f'process:{proc}' in claims or set(HOST_CLAIMS.get(proc, ())) & set(claims)):
        return
      if f'msg:{kind}' not in claims:
        self.fault(source, 'undeclared message kind')
        return
      payload, ttl, stamp = d['payload'], d['validForMs'], d['logMonoTime']
      if not isinstance(payload, str) or len(payload.encode()) > MAX_PAYLOAD:
        self.fault(source, 'payload exceeds 16 KiB or is not JSON text')
        return
      json.loads(payload)
      now = time.monotonic_ns()
      if type(ttl) is not int or not 0 <= ttl <= 0xFFFFFFFF or type(stamp) is not int or not 0 <= stamp <= now:
        self.fault(source, 'invalid TTL or timestamp')
        return
      if ttl and now - stamp > ttl * 1_000_000:
        return
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError):
      return
    msg = self.messaging.new_message('pluginMsg', valid=True, logMonoTime=stamp)
    msg.pluginMsg = {'source': source, 'kind': kind, 'payload': payload, 'validForMs': ttl, 'seq': (self.seq.get(source, 0) + 1) & 0xFFFFFFFF}
    encoded = msg.to_bytes()
    size = len(encoded)
    history = self.usage.setdefault(source, deque())
    while history and now - history[0][0] >= 1_000_000_000:
      history.popleft()
    while self.total and now - self.total[0][0] >= 1_000_000_000:
      self.total_bytes -= self.total.popleft()[1]
    # Equal reservations prevent a flood from spending any other enabled source's share.
    budget = min(SOURCE_BYTES, TOTAL_BYTES // len(self.publishers))
    if len(history) >= SOURCE_HZ or sum(n for _, n in history) + size > budget or self.total_bytes + size > TOTAL_BYTES:
      self.fault(source, 'message rate exceeds 100 Hz or fair byte rate budget')
      return
    history.append((now, size))
    self.total.append((now, size))
    self.total_bytes += size
    self.seq[source] = msg.pluginMsg.seq
    self.pm.send('pluginMsg', encoded)


def main():
  broker = Broker()
  while True:
    broker.update()
    if select.select([broker.sock], [], [], .01)[0]:
      broker.receive()


if __name__ == '__main__':
  main()
