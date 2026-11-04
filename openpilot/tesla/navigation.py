"""Fixed unprivileged LAN navigation ingress, filtered by ingress interface. No routing, DNS or shell access.

The source:navigation owner may receive RFC1918 IPv4 UDP on 4213 and acknowledge
only its last admitted packet. Claims arbitrate resources, not OS isolation.
"""
import ipaddress
import json
import socket
import struct
import time


NETWORKS = tuple(ipaddress.ip_network(x) for x in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))
PORT, MAX_BYTES = 4213, 8192


def private_peer(host):
  try:
    address = ipaddress.ip_address(host)
    return address.version == 4 and any(address in network for network in NETWORKS)
  except ValueError:
    return False


def authorized(provider):
  from openpilot.common.params import Params
  from openpilot.tesla.plugins import active
  return any(p.NAME == provider and 'source:navigation' in p.CLAIMS for p in active(Params()))


class NavigationIngress:
  def __init__(self, provider, *, authorize=None, transport=None, interface="wlan0", interface_index=None):
    self.authorize = authorize or (lambda: authorized(provider))
    self._authorized_until = 0.
    self._interface = interface if interface_index is None else None
    self.sock, self.peer = transport, None
    self.interface_index = socket.if_nametoindex(interface) if interface_index is None else interface_index
    self.window, self.total, self.counts = int(time.monotonic()), 0, {}
    self._check()
    if self.sock is None:
      self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
      try:
        self.sock.settimeout(.05)
        self.sock.setsockopt(socket.IPPROTO_IP, 8, 1)  # Linux IP_PKTINFO
        self.sock.bind(('0.0.0.0', PORT))
      except BaseException:
        self.close()
        raise

  def _check(self):
    now = time.monotonic()
    if now >= self._authorized_until or now < self._authorized_until - 1.:
      if not self.authorize():
        self.close()
        raise PermissionError('Navigation provider is not the enabled source owner')
      if self._interface is not None:
        try:
          current = socket.if_nametoindex(self._interface)
        except OSError:
          current = 0
        if current != self.interface_index:
          self.close()
          raise OSError('Navigation interface changed')
      self._authorized_until = now + 1.

  def receive(self):
    self._check()
    self.peer = None
    try:
      body, ancillary, flags, address = self.sock.recvmsg(MAX_BYTES + 1, socket.CMSG_SPACE(12))
    except socket.timeout:
      return None
    if flags & socket.MSG_CTRUNC:
      return None
    info = [data for level, kind, data in ancillary if level == socket.IPPROTO_IP and kind == 8]
    if len(info) != 1 or len(info[0]) < 12 or struct.unpack("=I4s4s", info[0][:12])[0] != self.interface_index:
      return None
    now = time.monotonic()
    if int(now) != self.window:
      self.window, self.total, self.counts = int(now), 0, {}
    self.total += 1
    if self.total > 100 or not private_peer(address[0]) or not 0 < len(body) <= MAX_BYTES:
      return None
    count = self.counts.get(address[0], 0) + 1
    self.counts[address[0]] = count
    if count > 20:
      return None
    self.peer = (address, now)
    return body, address[0]

  def ack(self, value):
    self._check()
    if self.peer is None or time.monotonic() - self.peer[1] > .5:
      raise PermissionError('No current navigation peer')
    if (set(value) != {'messageType', 'schemaVersion', 'sessionId', 'sequence'}
        or value['messageType'] != 'navassist_udp_ack' or value['schemaVersion'] != 3
        or not isinstance(value['sessionId'], str) or not 1 <= len(value['sessionId']) <= 64
        or type(value['sequence']) is not int or not 1 <= value['sequence'] < 2**63):
      raise ValueError('Invalid navigation acknowledgement')
    body = json.dumps(value, separators=(',', ':')).encode()
    self.sock.sendto(body, self.peer[0])
    self.peer = None

  def close(self):
    if self.sock is not None:
      self.sock.close()
    self.peer = None

  def __enter__(self):
    return self

  def __exit__(self, *_args):
    self.close()
