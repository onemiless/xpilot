#!/usr/bin/env python3
"""Failure cases: wrong/missing interface metadata, unsafe source, oversized
packet, flood, stale acknowledgement, owner loss, changed interface and port leak.
Real UDP uses loopback and an ephemeral port, never the active navigation service.
"""
from collections import deque
import importlib.util
from pathlib import Path
import socket
import struct
from unittest.mock import patch

root = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('nav_api', root / 'openpilot/tesla/navigation.py')
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)


class Transport:
  def __init__(self, entries):
    self.entries, self.closed, self.sent = deque(entries), False, []

  def recvmsg(self, *_args):
    return self.entries.popleft()

  def sendto(self, *args):
    self.sent.append(args)

  def close(self):
    self.closed = True


def packet(body=b'{}', peer='192.168.1.2', idx=7, flags=0):
  cmsg = [(socket.IPPROTO_IP, 8, struct.pack('=I4s4s', idx, bytes(4), bytes(4)))]
  return body, cmsg, flags, (peer, 1234)


def main():
  assert api.private_peer('192.168.1.2') and api.private_peer('10.0.0.2')
  assert not any(api.private_peer(x) for x in ('8.8.8.8', '127.0.0.1', '::1', '100.64.1.2'))
  clock = [100.0]
  entries = [packet(idx=8), (b'{}', [], 0, ('192.168.1.2', 1234)),
             packet(flags=socket.MSG_CTRUNC), packet(peer='8.8.8.8'), packet(body=b'x'*(api.MAX_BYTES+1))] + [packet() for _ in range(22)]
  fake, enabled = Transport(entries), [True]
  with patch.object(api.time, 'monotonic', side_effect=lambda: clock[0]):
    ingress = api.NavigationIngress('fixture', authorize=lambda: enabled[0], transport=fake, interface_index=7)
    for _ in range(5):
      assert ingress.receive() is None
    for _ in range(20):
      assert ingress.receive() == (b'{}', '192.168.1.2')
    assert ingress.receive() is None
    try:
      ingress.ack({'messageType':'navassist_udp_ack','schemaVersion':3,'sessionId':'x','sequence':1})
      raise AssertionError('acknowledgement without admitted peer')
    except PermissionError:
      pass
    clock[0] += 1.01
    assert ingress.receive() == (b'{}', '192.168.1.2')
    ack = {'messageType':'navassist_udp_ack','schemaVersion':3,'sessionId':'x','sequence':1}
    ingress.ack(ack)
    assert len(fake.sent) == 1 and fake.sent[0][1] == ('192.168.1.2', 1234)
    enabled[0] = False
    clock[0] += 1.01
    try:
      ingress.receive()
      raise AssertionError('disabled owner')
    except PermissionError:
      pass
    assert fake.closed

  indices, fake, clock = [7], Transport([]), [100.0]
  with patch.object(api.time, 'monotonic', side_effect=lambda:clock[0]), patch.object(api.socket, 'if_nametoindex', side_effect=lambda _:indices[0]):
    ingress = api.NavigationIngress('fixture', authorize=lambda: True, transport=fake)
    indices[0] = 8
    clock[0] += 1.01
    try:
      ingress._check()
      raise AssertionError('changed interface accepted')
    except OSError:
      pass
    assert fake.closed

  with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
    server.setsockopt(socket.IPPROTO_IP, 8, 1)  # Linux IP_PKTINFO
    server.bind(('127.0.0.1', 0))
    server.settimeout(.1)
    ingress = api.NavigationIngress('fixture', authorize=lambda:True, transport=server, interface_index=socket.if_nametoindex('lo'))
    with patch.object(api, 'private_peer', return_value=True):  # test-only loopback peer
      client.sendto(b'{}', server.getsockname())
      assert ingress.receive() == (b'{}', '127.0.0.1')
    ingress.close()
    assert server.fileno() == -1
  print('navigation ingress: interface, bounds, owner, UDP and cleanup passed')


if __name__ == '__main__':
  main()
