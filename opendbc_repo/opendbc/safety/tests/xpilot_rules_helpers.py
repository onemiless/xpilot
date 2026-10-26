"""ABI v1 rule-data fixtures and app-loader harness."""
import functools
from pathlib import Path
import struct
import zlib

from opendbc.safety.tests.libsafety import libsafety_py as lib

from openpilot.tesla.panda_rules import MAGIC as MAGIC, OWNED as OWNED, field, rule, table as _table, record

ROOT = Path(__file__).resolve().parents[4]


def table(rules=None):
  return _table(RULES if rules is None else rules)


AUTO = rule(0x3C2, 8, (255, 255, 255, 192, 255, 255, 255, 255),
            fields=(field(24, 6, 1, 63),), tuples=((1, [1]), (1, [63])),
            predicate=(bytes((3, 0, 0, 63, 0, 0, 0, 0)), bytes((1, 0, 0, 0, 0, 0, 0, 0))))
AMBIENT = rule(0x679, 7, (1, 0, 0, 0, 128, 6, 254, 0),
               const=(254, 255, 255, 255, 0, 1, 0, 0), value=(2, 255, 0, 0, 0, 0, 0, 0),
               fields=(field(32, 7, 0, 100), field(43, 5, 0, 31), field(48, 1, 0, 1)),
               tuples=((6, [0, 21, 0]), (6, [0, 10, 1]), (6, [0, 31, 1])),
               ttl=1_000_000, interval=80_000, count=150, idle=1_000_000, span=15_000_000)
NAV = rule(0x3E9, 8, (255, 252, 225, 255, 255, 255, 15, 0),
           fields=(field(8, 2, 1, 3, (3,)), field(17, 4, 4, 8, (4,))),
           tuples=((3, [1, 8]), (3, [2, 8]), (3, [3, 4])),
           predicate=(bytes((0, 3, 0, 0, 0, 0, 0, 0)), bytes(8)),
           interval=100_000, session=2, count=200, idle=0, span=60_000_000,
           counter=(1, 52, 4), checksum=(1, 7))
RULES = (AUTO, AMBIENT, NAV)
ADDRS = (0x3C2, 0x679, 0x3E9)


def packet(addr, data, bus=1):
  return lib.make_CANPacket(addr, bus, data)


@functools.cache
def harness():
  result = lib.libsafety
  result.test_init()
  return result


def control(safety, request, p1=0, p2=0):
  response = lib.ffi.new('uint8_t[64]')
  length = safety.test_control(request, p1, p2, response)
  return bytes(lib.ffi.buffer(response, length))


def status(safety):
  return struct.unpack('<IHBBIHBBHHHBB', control(safety, 0xA1))


def upload(safety, blob, count=None, crc=None, commit=True):
  count = blob[10] if count is None else count
  crc = zlib.crc32(blob) if crc is None else crc
  control(safety, 0xA2, len(blob), count)
  ident = status(safety)[8]
  safety.test_ep2(record(ident, 0, struct.pack('<I', crc), 1), 64)
  for offset in range(0, len(blob), 56):
    safety.test_ep2(record(ident, offset, blob[offset:offset + 56]), 64)
  if commit:
    control(safety, 0xA3, crc & 65535, crc >> 16)
  return status(safety)


def configure(safety, rules=RULES, vehicle=True):
  safety.set_safety_hooks(19, 0)
  assert upload(safety, table(rules))[3] == 0
  safety.set_current_safety_param_sp(0x7001 if vehicle else 0x7000)
  assert safety.set_safety_hooks(10, 0) == 0
  safety.init_tests()
  safety.set_current_safety_param_sp(0x7001 if vehicle else 0x7000)


def frames(i, random):
  data = bytearray(random.randbytes(8 if i != 1 else 7))
  if i == 0:
    data[0] = (data[0] & 252) | 1
    data[3] &= 192
    tx = bytearray(data)
    tx[3] |= random.choice((1, 63))
  elif i == 1:
    tx = bytearray(data)
    tx[0] = (data[0] & 1) | 2
    tx[1:4] = bytes((255, 0, 0))
    tx[4] = (data[4] & 128) | random.randrange(101)
    zone, side = random.choice(((168, 0), (80, 1), (248, 1)))
    tx[5] = (data[5] & 6) | zone
    tx[6] = (data[6] & 254) | side
  else:
    data[1] &= 252
    data[7] = (sum(data[:7]) + 0xEC) & 255
    tx = bytearray(data)
    tx[1] |= random.choice((1, 2))
    tx[2] = (data[2] & 225) | 16
    tx[6] = (data[6] & 15) | (((data[6] >> 4) + 1) % 16 << 4)
    tx[7] = (sum(tx[:7]) + 0xEC) & 255
  return data, tx
