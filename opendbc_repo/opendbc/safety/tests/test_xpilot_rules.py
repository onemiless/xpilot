"""Xpilot runtime rules failure matrix (G1).

R1: malformed ABI/type/length/count/reserved fields or >4096 bytes accepted.
R2: bit coverage overlap/gap, invalid field/range/tuple/checksum/counter accepted.
R3: duplicate TX address or mode-owned address (any bus/length) accepted.
R4: EP2 before BEGIN, wrong upload/offset/flags/size, truncated upload or bad CRC committed.
R5: car-mode BEGIN/COMMIT accepted; boot/non-car switch retains table; mode set retains runtime state.
R6: non-Tesla/no-vehicle/relay/noOutput/silent/controls gates bypassed; rejected TX spends budget.
R7: mode-owned TX dispatch falls through to rules; unknown or wrong bus/length TX accepted.
R8: auto_speed/ambient/navassist verdict or budget differs on replay/random streams.
R9: TTL/interval/idle/span/window +/-1 us or uint32 timer wrap differs.
R10: navassist cancel consumes or is blocked by session budget, or skips other gates.
R11: failed replacement leaves an active/partially loaded table; explicit empty table fails.
R12: old profile bits still grant production TX; production includes legacy headers.
R13: cross-mode inventory scan assumes every test module exports a car inventory.
R14: a stale RX template revives after a full uint32 timer cycle despite periodic safety ticks.
R15: interval/IDLE/WINDOW state survives expiry and stalls after timer wrap; IDLE restart loses its timestamp gate.
R16: an RX/TX frame with the wrong standard/extended ID type matches a rule's address.
R17: a successful car-to-car mode set retains a refused car-mode BEGIN error.
"""

import hashlib
import json
import os
from pathlib import Path
import random
import struct
import unittest
import zlib

from opendbc.safety.tests.xpilot_rules_helpers import (
  ADDRS, AUTO, OWNED, ROOT, configure, control, field, frames, harness, packet, record, rule, status, table, upload,
)


class TestXpilotRules(unittest.TestCase):
  def setUp(self):
    self.s = harness()
    self.s.set_safety_hooks(19, 0)
    self.s.set_timer(0)

  def test_R1_R2_R3_R11_validator(self):
    self.assertEqual((2, 0), upload(self.s, table())[2:4])
    for kind, error in (('abi', 6), ('ownership', 9), ('coverage', 8), ('length', 2)):
      r = bytearray(AUTO)
      b = bytearray(table((r,)))
      if kind == 'abi':
        b[4] = 2
      elif kind == 'ownership':
        struct.pack_into('<I', b, 20, 0x488)
      elif kind == 'coverage':
        b[84] |= 1
      else:
        b.extend(bytes(1))
        struct.pack_into('<H', b, 8, len(b))
      self.assertEqual(error, upload(self.s, b)[3])
    bad = []
    for offset, value in ((0, 0), (4, 2), (6, 0), (8, 0), (10, 9), (11, 1), (12, 1),
                          (18, 2), (19, 9), (28, 0), (29, 0), (30, 0), (31, 0),
                          (52, 2), (54, 33), (55, 2), (56, 9), (57, 9), (59, 1),
                          (100, 64), (101, 33), (102, 9), (103, 1)):
      b = bytearray(table((AUTO,)))
      b[offset] = value
      bad.append(b)
    # Every protected bit: a coverage gap and overlap must both be rejected.
    for bit in range(64):
      for overlap in (False, True):
        r = bytearray(AUTO)
        b, m = divmod(bit, 8)
        mask = 1 << m
        if overlap:
          r[68 + b] |= mask
          r[60 + b] |= mask
        elif r[60 + b] & mask:
          r[60 + b] &= ~mask
        else:
          r[85] -= 1
        bad.append(table((r,)))
    for addr in OWNED:
      for bus in (0, 1, 2):
        for length in (1, 4, 8):
          r = bytearray(AUTO)
          struct.pack_into('<I', r, 4, addr)
          r[12] = bus
          r[14] = length
          bad.append(table((r,)))
    bad.extend((table((AUTO, AUTO)), table((AUTO,) * 9), table() + bytes(4097)))
    for b in bad:
      with self.subTest(blob=bytes(b).hex()):
        self.assertNotEqual(0, upload(self.s, b)[3])
        self.assertEqual((0, 0, 0), status(self.s)[4:7])
    self.assertEqual((2, 0), upload(self.s, table(()))[2:4])
    self.assertEqual(zlib.crc32(table(())), status(self.s)[4])
    # Exercise both fixed buffer/rule-count ceilings with a valid 4096-byte table.
    largest = []
    for i, (fields, tuples) in enumerate(zip((4, 4, 4, 4, 4, 4, 3, 3), (8, 8, 7, 7, 7, 7, 7, 7), strict=True)):
      largest.append(rule(0x500 + i, 8, (255 ^ ((1 << fields) - 1),) + (255,) * 7,
                          fields=tuple(field(j, 1, 0, 1) for j in range(fields)), tuples=((1, [0]),) * tuples))
    self.assertEqual(4096, len(table(largest)))
    self.assertEqual((2, 0), upload(self.s, table(largest))[2:4])
    full = rule(0x500, 8, (0,) + (255,) * 7,
                fields=tuple(field(j, 1, 0, 1, (0,) * 8) for j in range(8)), tuples=((255, [0] * 8),) * 8)
    self.assertEqual((2, 0), upload(self.s, table((full,)))[2:4])

  def test_R4_R5_loader(self):
    b = table()
    self.assertEqual(5, upload(self.s, b, crc=0)[3])
    for record_change in ('id', 'offset', 'flags', 'length', 'padding', 'short', 'meta_twice'):
      control(self.s, 0xA2, len(b), 3)
      ident = status(self.s)[8]
      meta = record(ident, 0, struct.pack('<I', zlib.crc32(b)), 1)
      self.s.test_ep2(meta, 64)
      r = bytearray(record(ident, 0, b[:56]))
      if record_change == 'id':
        r[0] ^= 1
      elif record_change == 'offset':
        r[2] = 1
      elif record_change == 'flags':
        r[6] = 2
      elif record_change == 'length':
        r[4] = 57
      elif record_change == 'padding':
        r = bytearray(record(ident, 0, b[:4]))
        r[63] = 1
      elif record_change == 'meta_twice':
        r = meta
      self.s.test_ep2(bytes(r), 63 if record_change == 'short' else 64)
      self.assertNotEqual(0, status(self.s)[3], record_change)
      self.assertEqual(0, status(self.s)[6])
    upload(self.s, b, commit=False)
    self.s.set_safety_hooks(10, 0)
    self.assertEqual(1, struct.unpack('<IHBBIHBBHHHBB', control(self.s, 0xA3))[3])
    self.assertEqual(1, struct.unpack('<IHBBIHBBHHHBB', control(self.s, 0xA2, len(b), 3))[3])
    self.s.set_safety_hooks(19, 0)
    control(self.s, 0xA2, len(b), 3)
    self.assertNotEqual(0, struct.unpack('<IHBBIHBBHHHBB', control(self.s, 0xA3))[3])
    upload(self.s, b)
    self.s.set_safety_hooks(10, 0)
    self.assertEqual(3, status(self.s)[6])
    self.s.set_safety_hooks(10, 0)
    self.assertEqual(3, status(self.s)[6])
    for mode in (0, 3, 17, 19):
      self.s.set_safety_hooks(19, 0)
      upload(self.s, b)
      self.s.set_safety_hooks(mode, 0)
      self.assertEqual(0, status(self.s)[6])
    self.assertEqual(16, len(control(self.s, 0xA0)))

  def test_R6_R7_R12_dispatch(self):
    rng = random.Random(32)
    for vehicle in (False, True):
      configure(self.s, vehicle=vehicle)
      rx, tx = frames(0, rng)
      self.s.set_timer(100)
      self.s.safety_rx_hook(packet(ADDRS[0], rx))
      self.s.set_controls_allowed(True)
      self.s.set_relay_malfunction(True)
      self.assertFalse(self.s.safety_tx_hook(packet(ADDRS[0], tx)))
      self.assertEqual(0, self.s.test_state(0, 0))
      self.s.set_relay_malfunction(False)
      for bus in (0, 2):
        self.assertFalse(self.s.safety_tx_hook(packet(ADDRS[0], tx, bus)))
      self.assertFalse(self.s.safety_tx_hook(packet(ADDRS[0], tx[:7])))
      self.assertFalse(self.s.safety_tx_hook(packet(0x123, tx)))
      self.assertEqual(vehicle, self.s.safety_tx_hook(packet(ADDRS[0], tx)))
    self.s.set_safety_hooks(19, 0)
    self.s.set_current_safety_param_sp(0x7001)
    self.s.set_safety_hooks(10, 0)
    self.s.set_controls_allowed(True)
    self.s.safety_rx_hook(packet(ADDRS[0], rx))
    self.assertFalse(self.s.safety_tx_hook(packet(ADDRS[0], tx)))
    self.assertNotIn('plugins/registry.h', (ROOT / 'opendbc_repo/opendbc/safety/safety.h').read_text())

  def compare(self, i, tx):
    expected = self.s.test_old_tx(i, packet(ADDRS[i], tx))
    actual = self.s.safety_tx_hook(packet(ADDRS[i], tx))
    self.assertEqual(expected, actual, (i, bytes(tx).hex()))
    for member in (0, 1, 2) if i else (0, 1):
      self.assertEqual(self.s.test_old_state(i, member), self.s.test_state(i, member), (i, member))
    return actual

  def test_R8_R9_differential(self):
    rng = random.Random(2026102532)
    digest = hashlib.sha256()
    n = 0
    trace = []
    for start in (100, 0xFFF00000):
      configure(self.s)
      now = start
      for iteration in range(12000):
        i = rng.randrange(3)
        rx, tx = frames(i, rng)
        now = (now + rng.choice((0, 79999, 80000, 80001, 99999, 100000, 100001, 249999, 250000,
                                 250001, 999999, 1000000, 1000001, 1499999, 1500000, 1500001,
                                 2999999, 3000000, 3000001, 15000000, 60000000))) & 0xFFFFFFFF
        self.s.set_timer(now)
        observed = rng.randrange(4) != 0
        if observed:
          if rng.randrange(8) == 0:
            rx[rng.randrange(len(rx))] ^= rng.randrange(1, 256)
          self.s.test_old_rx(i, packet(ADDRS[i], rx))
          self.s.safety_rx_hook(packet(ADDRS[i], rx))
        controls = rng.randrange(4) != 0
        self.s.set_controls_allowed(controls)
        if rng.randrange(3) == 0:
          tx[rng.randrange(len(tx))] ^= rng.randrange(1, 256)
        verdict = self.compare(i, tx)
        digest.update(struct.pack('<IIB', now, i, verdict) + rx + tx)
        trace.append([iteration == 0, now, i, observed, controls, rx.hex(), tx.hex(), verdict])
        n += 1
    replay_bytes = ''.join(json.dumps(event, separators=(',', ':')) + '\n' for event in trace).encode()
    for line in replay_bytes.splitlines():
      reset, now, i, observed, controls, rx, tx, verdict = json.loads(line)
      if reset:
        configure(self.s)
      self.s.set_timer(now)
      if observed:
        self.s.test_old_rx(i, packet(ADDRS[i], bytes.fromhex(rx)))
        self.s.safety_rx_hook(packet(ADDRS[i], bytes.fromhex(rx)))
      self.s.set_controls_allowed(controls)
      self.assertEqual(verdict, self.compare(i, bytes.fromhex(tx)))
    artifact = {'seed': 2026102532, 'frames': n, 'verdict_sha256': digest.hexdigest(),
                'navassist_source': 'Sniper-Eon/xpilot@ab3535b5a4acbf318c55005868d37ed19e2cf77d',
                'deliberate_difference': 'cancel skips session budget; tick latches expired state; CAN ID type must match',
                'replayed_frames': len(trace), 'trace_sha256': hashlib.sha256(replay_bytes).hexdigest()}
    output = os.environ.get('XPILOT_RULES_ARTIFACT')
    if output:
      Path(output).with_suffix('.jsonl').write_bytes(replay_bytes)
      Path(output).write_text(json.dumps(artifact, indent=2) + '\n')

  def test_R9_exact_boundaries_and_reset(self):
    rng = random.Random(9)
    for i, ttl, interval, idle, span in ((0, 1500000, 250000, 3000000, 0),
                                        (1, 1000000, 80000, 1000000, 15000000),
                                        (2, 1500000, 100000, 0, 60000000)):
      for start in (100, 0xFFF00000):
        for boundary in (ttl, interval, idle, span):
          if not boundary:
            continue
          for delta in (-1, 0, 1):
            configure(self.s)
            rx, tx = frames(i, rng)
            self.s.set_timer(start)
            self.s.test_old_rx(i, packet(ADDRS[i], rx))
            self.s.safety_rx_hook(packet(ADDRS[i], rx))
            self.s.set_controls_allowed(True)
            self.compare(i, tx)
            self.s.set_timer((start + boundary + delta) & 0xFFFFFFFF)
            if boundary != ttl:
              self.s.test_old_rx(i, packet(ADDRS[i], rx))
              self.s.safety_rx_hook(packet(ADDRS[i], rx))
            self.compare(i, tx)
        for delta in (-1, 0, 1):
          configure(self.s)
          rx, tx = frames(i, rng)
          cap = (50, 150, 200)[i]
          for j in range(cap):
            self.s.set_timer((start + j * interval) & 0xFFFFFFFF)
            self.s.test_old_rx(i, packet(ADDRS[i], rx))
            self.s.safety_rx_hook(packet(ADDRS[i], rx))
            self.s.set_controls_allowed(True)
            self.assertTrue(self.compare(i, tx))
          boundary = (start + span) if i == 2 else (start + (cap - 1) * interval + idle)
          self.s.set_timer((boundary + delta) & 0xFFFFFFFF)
          self.s.test_old_rx(i, packet(ADDRS[i], rx))
          self.s.safety_rx_hook(packet(ADDRS[i], rx))
          self.assertEqual(delta >= 0, self.compare(i, tx))
        if i == 1:
          for delta in (-1, 0, 1):
            configure(self.s)
            rx, tx = frames(i, rng)
            for j in range(100):
              self.s.set_timer((start + j * 150000) & 0xFFFFFFFF)
              self.s.test_old_rx(i, packet(ADDRS[i], rx))
              self.s.safety_rx_hook(packet(ADDRS[i], rx))
              self.s.set_controls_allowed(True)
              self.assertTrue(self.compare(i, tx))
            self.s.set_timer((start + span + delta) & 0xFFFFFFFF)
            self.s.test_old_rx(i, packet(ADDRS[i], rx))
            self.s.safety_rx_hook(packet(ADDRS[i], rx))
            self.assertEqual(delta < 0, self.compare(i, tx))
        self.s.set_safety_hooks(10, 0)
        self.s.test_old_reset()
        self.s.set_controls_allowed(True)
        self.assertFalse(self.compare(i, tx))

  def test_R10_cancel(self):
    rng = random.Random(10)
    configure(self.s)
    rx, tx = frames(2, rng)
    for j in range(200):
      self.s.set_timer(100 + j * 100000)
      self.s.safety_rx_hook(packet(ADDRS[2], rx))
      self.s.test_old_rx(2, packet(ADDRS[2], rx))
      self.s.set_controls_allowed(True)
      self.assertTrue(self.compare(2, tx))
    self.s.set_timer(20000100)
    self.s.safety_rx_hook(packet(ADDRS[2], rx))
    self.s.test_old_rx(2, packet(ADDRS[2], rx))
    self.assertFalse(self.compare(2, tx))
    cancel = bytearray(tx)
    cancel[1] = (cancel[1] & 252) | 3
    cancel[2] = (cancel[2] & 225) | 8
    cancel[7] = (sum(cancel[:7]) + 0xEC) & 255
    self.s.set_controls_allowed(False)
    self.assertFalse(self.s.test_old_tx(2, packet(ADDRS[2], cancel)))
    self.assertTrue(self.s.safety_tx_hook(packet(ADDRS[2], cancel)))
    self.assertEqual(200, self.s.test_state(2, 0))
    self.s.set_timer(20100099)
    self.assertFalse(self.s.safety_tx_hook(packet(ADDRS[2], cancel)))
    self.s.set_timer(20100100)
    self.s.set_relay_malfunction(True)
    self.assertFalse(self.s.safety_tx_hook(packet(ADDRS[2], cancel)))
    self.s.set_relay_malfunction(False)
    self.assertTrue(self.s.safety_tx_hook(packet(ADDRS[2], cancel)))
    self.s.set_timer(22000000)
    self.assertFalse(self.s.safety_tx_hook(packet(ADDRS[2], cancel)))

  def test_R14_template_expiry_latches_across_full_wrap(self):
    for ttl in (1500000, 0x7FFFFFFF):
      with self.subTest(ttl=ttl):
        r = bytearray(AUTO)
        struct.pack_into('<I', r, 16, ttl)
        configure(self.s, (r,))
        rx, tx = frames(0, random.Random(14))
        start = 100
        self.s.set_timer(start)
        self.s.safety_rx_hook(packet(ADDRS[0], rx))
        self.s.test_old_rx(0, packet(ADDRS[0], rx))
        for elapsed in range(1000000, (1 << 32) + 1, 1000000):
          self.s.set_timer((start + elapsed) & 0xFFFFFFFF)
          self.s.safety_tick()
        self.s.set_timer(start + 100)
        self.s.safety_tick()
        self.s.set_controls_allowed(True)
        self.assertTrue(self.s.test_old_tx(0, packet(ADDRS[0], tx)))
        self.assertFalse(self.s.safety_tx_hook(packet(ADDRS[0], tx)))
        self.assertFalse(self.s.test_state(0, 3))
        self.s.safety_rx_hook(packet(ADDRS[0], rx))
        self.assertTrue(self.s.safety_tx_hook(packet(ADDRS[0], tx)))

  def test_R15_interval_and_session_expiry(self):
    for i, interval, period in ((0, 250000, 3000000), (2, 100000, 60000000)):
      for elapsed in (interval - 1, interval, period - 1, period, (1 << 32) + 100):
        with self.subTest(i=i, elapsed=elapsed):
          configure(self.s)
          rx, tx = frames(i, random.Random(15))
          start = 100
          self.s.set_timer(start)
          self.s.safety_rx_hook(packet(ADDRS[i], rx))
          self.s.test_old_rx(i, packet(ADDRS[i], rx))
          self.s.set_controls_allowed(True)
          self.assertTrue(self.compare(i, tx))
          for tick in range(1000000, elapsed, 1000000):
            self.s.set_timer((start + tick) & 0xFFFFFFFF)
            self.s.safety_tick()
          self.s.set_timer((start + elapsed) & 0xFFFFFFFF)
          self.s.safety_tick()
          self.assertEqual(elapsed < interval, bool(self.s.test_state(i, 4)))
          self.assertEqual(int(elapsed < period), self.s.test_state(i, 0))
          if elapsed > (1 << 32):
            self.s.safety_rx_hook(packet(ADDRS[i], rx))
            self.s.test_old_rx(i, packet(ADDRS[i], rx))
            self.s.set_controls_allowed(True)
            self.assertFalse(self.s.test_old_tx(i, packet(ADDRS[i], tx)))
            self.assertTrue(self.s.safety_tx_hook(packet(ADDRS[i], tx)))
    # The idle boundary can arrive after tx_valid expired, before the next tick.
    for gap in (2999999, 3000000, 3000001):
      with self.subTest(gap=gap):
        configure(self.s)
        rx, tx = frames(0, random.Random(15))
        self.s.set_timer(100)
        self.s.safety_rx_hook(packet(ADDRS[0], rx))
        self.s.set_controls_allowed(True)
        self.assertTrue(self.s.safety_tx_hook(packet(ADDRS[0], tx)))
        self.s.set_timer(3000099)
        self.s.safety_tick()
        self.s.set_timer(100 + gap)
        self.s.safety_rx_hook(packet(ADDRS[0], rx))
        self.s.set_controls_allowed(True)
        self.assertTrue(self.s.safety_tx_hook(packet(ADDRS[0], tx)))
        self.assertEqual(2 if gap < 3000000 else 1, self.s.test_state(0, 0))

  def test_R16_can_id_type(self):
    for addr in (ADDRS[0], 0x18FF50E5):
      for direction in ('rx', 'tx'):
        with self.subTest(addr=addr, direction=direction):
          r = bytearray(AUTO)
          struct.pack_into('<II', r, 4, addr, addr)
          configure(self.s, (r,))
          rx, tx = frames(0, random.Random(16))
          source, send = packet(addr, rx), packet(addr, tx)
          wrong = source if direction == 'rx' else send
          wrong.extended = not wrong.extended
          self.s.safety_rx_hook(source)
          self.s.set_controls_allowed(True)
          self.assertFalse(self.s.safety_tx_hook(send))
          self.assertEqual(0, self.s.test_state(0, 0))
          if direction == 'rx':
            self.assertFalse(self.s.test_state(0, 3))
            source.extended = not source.extended
            self.s.safety_rx_hook(source)
          else:
            send.extended = not send.extended
          self.assertTrue(self.s.safety_tx_hook(send))

  def test_R17_successful_car_mode_clears_error(self):
    configure(self.s)
    committed = status(self.s)[4:7]
    control(self.s, 0xA2, len(table()), 3)
    self.assertEqual(1, status(self.s)[3])
    self.assertEqual(0, self.s.set_safety_hooks(10, 0))
    self.assertEqual(0, status(self.s)[3])
    self.assertEqual(committed, status(self.s)[4:7])

if __name__ == '__main__':
  unittest.main()
