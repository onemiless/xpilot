"""Panda rules ABI v1 encoder. Contract: docs/design/xpilot-panda-rules.md."""
import struct
import zlib

MAGIC = 0x31525058
OWNED = (0x118, 0x145, 0x155, 0x257, 0x27D, 0x286, 0x2B9, 0x311, 0x370, 0x3DF, 0x488)


def field(offset, width, lo, hi, exempt=()):
  return struct.pack('<4B10I', offset, width, len(exempt), 0, lo, hi, *exempt, *([0] * (8 - len(exempt))))


def rule(addr, length, copy, const=(0,) * 8, value=(0,) * 8, fields=(), tuples=(),
         predicate=(bytes(8), bytes(8)), ttl=1_500_000, interval=250_000,
         session=1, count=50, idle=3_000_000, span=0, counter=(0, 0, 0), checksum=(0, 0)):
  r = bytearray(84)
  struct.pack_into('<HBBII4BII2BHII', r, 0, 84 + 44 * len(fields) + 36 * len(tuples), 1,
                   len(fields), addr, addr, 1, 1, length, length, ttl, interval, session, len(tuples), count, idle, span)
  r[36:43] = bytes((*counter, *checksum, *checksum))
  r[44:60] = b''.join(predicate)
  r[60:84] = bytes((*copy, *const, *value))
  for f in fields:
    r.extend(f)
  for mask, vals in tuples:
    r.extend(struct.pack('<B3x8I', mask, *vals, *([0] * (8 - len(vals)))))
  return bytes(r)


def table(rules=()):
  body = b''.join(rules)
  return struct.pack('<IHHHBBI', MAGIC, 1, 16, 16 + len(body), len(rules), 0, 0) + body


def record(upload, offset, data, flags=0):
  return struct.pack('<4H', upload, offset, len(data), flags) + data + bytes(56 - len(data))


def param(blob):
  return blob + struct.pack('<I', zlib.crc32(blob))


def _keys(value, names):
  if not isinstance(value, dict) or set(value) != set(names.split()):
    raise ValueError(f'rule fields must be {names}')


def _int(value, lo, hi, name):
  if type(value) is not int or not lo <= value <= hi:
    raise ValueError(f'{name} must be {lo}..{hi}')
  return value


def encode_rule(r):
  if not isinstance(r, dict) or r.get('type') != 'template_clone.v1':
    raise ValueError('rule type must be template_clone.v1 (ABI 1)')
  span_key = 'window_us' if r.get('session') == 2 else 'max_span_us'
  _keys(r, 'type tx_address rx_address tx_bus rx_bus tx_length rx_length template_ttl_us min_interval_us ' +
           'session max_count idle_reset_us counter tx_checksum rx_checksum predicate_mask predicate_value ' +
           'copy_mask constant_mask constant_value fields legal_tuples ' + span_key)
  addr = _int(r['tx_address'], 0, 0x1fffffff, 'tx_address')
  rx = _int(r['rx_address'], 0, 0x1fffffff, 'rx_address')
  if addr in OWNED:
    raise ValueError(f'mode-owned TX address {addr:#x}')
  for name in ('tx_bus', 'rx_bus'):
    _int(r[name], 1, 1, name)
  length = _int(r['tx_length'], 1, 8, 'tx_length')
  _int(r['rx_length'], length, length, 'rx_length')
  ttl = _int(r['template_ttl_us'], 1, 0x7fffffff, 'template_ttl_us')
  interval = _int(r['min_interval_us'], 1, 0x7fffffff, 'min_interval_us')
  session = _int(r['session'], 1, 2, 'session')
  count = _int(r['max_count'], 1, 65535, 'max_count')
  idle = _int(r['idle_reset_us'], 1 if session == 1 else 0, 0x7fffffff if session == 1 else 0, 'idle_reset_us')
  span = _int(r[span_key], 0 if session == 1 else 1, 0x7fffffff, span_key)
  masks = []
  for name in ('predicate_mask', 'predicate_value', 'copy_mask', 'constant_mask', 'constant_value'):
    m = r[name]
    if not isinstance(m, list) or len(m) != 8:
      raise ValueError(f'{name} must have 8 bytes')
    for i, b in enumerate(m):
      _int(b, 0, 255 if i < length else 0, name)
    masks.append(bytes(m))
  pm, pv, copy, const, value = masks
  if any((v & m) != v for m, v in zip(pm + const, pv + value, strict=True)):
    raise ValueError('masked value has bits outside mask')
  coverage = int.from_bytes(copy, 'little')
  def cover(bits):
    nonlocal coverage
    if bits & coverage:
      raise ValueError('TX coverage overlaps')
    coverage |= bits
  cover(int.from_bytes(const, 'little'))
  fields = r['fields']
  tuples = r['legal_tuples']
  if not isinstance(fields, list) or len(fields) > 8 or not isinstance(tuples, list) or len(tuples) > 8:
    raise ValueError('fields/legal_tuples must be lists of at most 8')
  encoded_fields, ranges = [], []
  for f in fields:
    _keys(f, 'bit_offset width min max exempt_values')
    width = _int(f['width'], 1, 32, 'field width')
    offset = _int(f['bit_offset'], 0, length*8-width, 'field bit_offset')
    low = _int(f['min'], 0, (1 << width)-1, 'field min')
    high = _int(f['max'], low, (1 << width)-1, 'field max')
    exemptions = f['exempt_values']
    if not isinstance(exemptions, list) or len(exemptions) > 8:
      raise ValueError('exempt_values must be a list of at most 8')
    for v in exemptions:
      _int(v, low, high, 'exempt value')
    cover(((1 << width)-1) << offset)
    encoded_fields.append(field(offset, width, low, high, exemptions))
    ranges.append((low, high))
  counter = r['counter']
  _keys(counter, 'kind bit_offset width')
  kind = _int(counter['kind'], 0, 1, 'counter kind')
  width = _int(counter['width'], kind, 32 if kind else 0, 'counter width')
  offset = _int(counter['bit_offset'], 0, length*8-width if kind else 0, 'counter bit_offset')
  if kind:
    cover(((1 << width)-1) << offset)
  checksums = []
  for name in ('tx_checksum', 'rx_checksum'):
    checksum = r[name]
    _keys(checksum, 'kind checksum_byte')
    k = _int(checksum['kind'], 0, 1, name)
    b = _int(checksum['checksum_byte'], 0, length-1 if k else 0, name)
    if name == 'tx_checksum' and k:
      cover(255 << (b*8))
    checksums.append((k, b))
  if coverage != (1 << (length*8))-1:
    raise ValueError('TX coverage incomplete')
  encoded_tuples = []
  for t in tuples:
    _keys(t, 'field_mask values')
    mask = _int(t['field_mask'], 1, (1 << len(fields))-1, 'tuple field_mask')
    vals = t['values']
    if not isinstance(vals, list) or len(vals) != len(fields):
      raise ValueError('tuple values must have one entry per field')
    for i, v in enumerate(vals):
      _int(v, ranges[i][0] if mask & (1 << i) else 0, ranges[i][1] if mask & (1 << i) else 0, 'tuple value')
    encoded_tuples.append((mask, vals))
  out = bytearray(rule(addr, length, copy, const, value, encoded_fields, encoded_tuples, (pm, pv),
                       ttl, interval, session, count, idle, span, (kind, offset, width), checksums[0]))
  struct.pack_into('<I', out, 8, rx)
  out[41:43] = bytes(checksums[1])
  return bytes(out)


def encode_rules(rules):
  if not isinstance(rules, list) or len(rules) > 8:
    raise ValueError('rules must be a list of at most 8')
  encoded, addresses = [], set()
  for r in rules:
    encoded.append(encode_rule(r))
    if r['tx_address'] in addresses:
      raise ValueError('duplicate TX address')
    addresses.add(r['tx_address'])
  if 16 + sum(map(len, encoded)) > 4096:
    raise ValueError('rule table exceeds 4096 bytes')
  return table(encoded)
