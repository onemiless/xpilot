"""template_clone.v1 sensor_input flag (record byte 43 = 1).

Failure cases, before implementation:
S1: a sensor frame is blocked while disengaged although its presence RX is fresh.
S2: a sensor frame passes without a fresh presence RX (never seen, or older than the TTL).
S3: a sensor frame passes with a field out of range or a constant bit flipped.
S4: sensor_input skips the window budget or the minimum interval.
S5: a sensor TX address that another node sends on the bus is still granted, or the latch survives a mode set.
S6: relay malfunction or a wrong bus still lets a sensor frame through.
S7: the loader accepts byte 43 > 1, sensor_input with predicate/copy masks, or unequal RX/TX length without the flag.
S8: a normal (non-sensor) rule now passes while disengaged.
S9: the host encoder accepts sensor_input other than true, Tesla DBC addresses, or presence masks.
"""
import random
import unittest

from openpilot.tesla.panda_rules import OWNED, car_addresses, encode_rule
from opendbc.safety.tests.xpilot_rules_helpers import AUTO, configure, frames, harness, packet, table, upload

PRESENCE = 0x60A


def speed_rule(**changes):
  r = {'type': 'template_clone.v1', 'sensor_input': True, 'tx_address': 0x300, 'rx_address': PRESENCE,
       'tx_bus': 1, 'rx_bus': 1, 'tx_length': 2, 'rx_length': 4, 'template_ttl_us': 200_000,
       'min_interval_us': 40_000, 'session': 2, 'max_count': 25, 'idle_reset_us': 0, 'window_us': 1_000_000,
       'counter': {'kind': 0, 'bit_offset': 0, 'width': 0}, 'tx_checksum': {'kind': 0, 'checksum_byte': 0},
       'rx_checksum': {'kind': 0, 'checksum_byte': 0}, 'predicate_mask': [0] * 8, 'predicate_value': [0] * 8,
       'copy_mask': [0] * 8, 'constant_mask': [32, 0, 0, 0, 0, 0, 0, 0], 'constant_value': [0] * 8,
       # ARS408 is big endian: byte 0 = direction(7..6) reserved(5) speed high(4..0), byte 1 = speed low.
       'fields': [{'bit_offset': 0, 'width': 5, 'min': 0, 'max': 16, 'exempt_values': []},
                  {'bit_offset': 6, 'width': 2, 'min': 0, 'max': 2, 'exempt_values': []},
                  {'bit_offset': 8, 'width': 8, 'min': 0, 'max': 255, 'exempt_values': []}],
       'legal_tuples': []}
  r.update(changes)
  return r


SPEED = encode_rule(speed_rule())
GOOD = bytes((0x40 | 1, 0x2C))  # forward, 300 * 0.02 = 6 m/s


class TestSensorInput(unittest.TestCase):
  def setUp(self):
    self.s = harness()
    configure(self.s, (SPEED, AUTO))
    self.now = 1_000
    self.s.set_timer(self.now)
    self.s.set_controls_allowed(False)

  def at(self, dt):
    self.now += dt
    self.s.set_timer(self.now)

  def present(self):
    self.s.safety_rx_hook(packet(PRESENCE, bytes(4)))

  def tx(self, data=GOOD, bus=1):
    return self.s.safety_tx_hook(packet(0x300, data, bus))

  def test_S1_S2_presence_and_disengaged(self):
    self.assertFalse(self.tx())
    self.s.safety_rx_hook(packet(PRESENCE, bytes(3)))  # wrong RX length is not presence
    self.assertFalse(self.tx())
    self.present()
    self.assertTrue(self.tx())
    self.at(200_001)
    self.assertFalse(self.tx())

  def test_S3_ranges_and_constants(self):
    self.present()
    for data in (bytes((0x40 | 17, 0)), bytes((0xC0, 0)), bytes((0x40 | 0x20, 0)), bytes(1), bytes(3)):
      with self.subTest(data=data.hex()):
        self.assertFalse(self.tx(data))
    self.assertTrue(self.tx(bytes((0x80 | 16, 255))))

  def test_S4_budget(self):
    self.present()
    self.assertTrue(self.tx())
    self.at(39_999)
    self.assertFalse(self.tx())
    configure(self.s, (encode_rule(speed_rule(max_count=3)),))
    self.s.set_controls_allowed(False)
    self.present()
    results = []
    for _ in range(4):
      self.at(40_000)
      self.present()
      results.append(self.tx())
    self.assertEqual([True, True, True, False], results)

  def test_S5_collision_latch(self):
    self.present()
    self.s.safety_rx_hook(packet(0x300, bytes(2)))
    self.present()
    self.assertFalse(self.tx())
    configure(self.s, (SPEED,))
    self.s.set_controls_allowed(False)
    self.present()
    self.assertTrue(self.tx())

  def test_S6_gates(self):
    self.present()
    for bus in (0, 2):
      self.assertFalse(self.tx(bus=bus))
    self.s.set_relay_malfunction(True)
    self.assertFalse(self.tx())
    self.s.set_relay_malfunction(False)
    self.assertTrue(self.tx())

  def test_S7_loader(self):
    self.s.set_safety_hooks(19, 0)
    bad = []
    for patch in ({43: 2}, {44: 1}, {44: 1, 52: 1}, {60: 1}):
      r = bytearray(SPEED)
      for offset, value in patch.items():
        r[offset] = value
      bad.append(r)
    plain = bytearray(SPEED)
    plain[43] = 0
    bad.append(plain)  # unequal RX/TX length without the flag
    for r in bad:
      with self.subTest(r=bytes(r[40:64]).hex()):
        self.assertNotEqual(0, upload(self.s, table((bytes(r),)))[3])
    self.assertEqual(0, upload(self.s, table((SPEED,)))[3])

  def test_S8_normal_rule_still_needs_controls(self):
    rx, tx = frames(0, random.Random(18))
    self.s.safety_rx_hook(packet(0x3C2, rx))
    self.assertFalse(self.s.safety_tx_hook(packet(0x3C2, tx)))
    self.s.set_controls_allowed(True)
    self.assertTrue(self.s.safety_tx_hook(packet(0x3C2, tx)))


class TestSensorInputEncoder(unittest.TestCase):
  def test_S9_host_encoder(self):
    self.assertEqual(1, SPEED[43])
    self.assertEqual(4, SPEED[15])
    car = min(car_addresses() - set(OWNED))
    for changes in ({'sensor_input': False}, {'sensor_input': 1}, {'tx_address': car}, {'predicate_mask': [1] + [0] * 7},
                    {'copy_mask': [0, 1] + [0] * 6}, {'rx_length': 9}):
      with self.subTest(changes=changes), self.assertRaises(ValueError):
        encode_rule(speed_rule(**changes))
    plain = speed_rule()
    del plain['sensor_input']
    with self.assertRaises(ValueError):
      encode_rule(plain)  # unequal lengths need the flag


if __name__ == '__main__':
  unittest.main()
