#!/usr/bin/env python3
"""Failures: pixel font selected; subset lacks Chinese UI glyphs; LFS pointer
not materialized; font has no outlines. Optional PO files check every CJK glyph.
This is asset/selection validation, not device rendering or layout validation.
"""
import argparse
import ast
from pathlib import Path
import struct


def cmap_glyphs(data):
  u16 = lambda p: struct.unpack_from('>H', data, p)[0]
  u32 = lambda p: struct.unpack_from('>I', data, p)[0]
  tables = {}
  for p in range(12, 12 + 16 * u16(4), 16):
    tag, _, offset, size = struct.unpack_from('>4sIII', data, p)
    assert offset + size <= len(data), 'invalid SFNT table'
    tables[tag] = offset
  assert b'CFF ' in tables or b'glyf' in tables, 'outline font required'
  cmap = tables[b'cmap']
  chars = set()
  for p in range(cmap + 4, cmap + 4 + 8 * u16(cmap + 2), 8):
    platform, encoding, offset = struct.unpack_from('>HHI', data, p)
    if platform != 0 and (platform, encoding) not in ((3, 1), (3, 10)):
      continue
    sub = cmap + offset
    if u16(sub) == 12:
      for group in range(sub + 16, sub + 16 + 12 * u32(sub + 12), 12):
        start, end, glyph = struct.unpack_from('>III', data, group)
        chars.update(range(start + (glyph == 0), end + 1))
    elif u16(sub) == 4:
      count = u16(sub + 6) // 2
      ends = sub + 14
      starts = ends + 2 * count + 2
      deltas = starts + 2 * count
      ranges = deltas + 2 * count
      for i in range(count):
        start, end = u16(starts + 2*i), u16(ends + 2*i)
        delta, offset = u16(deltas + 2*i), u16(ranges + 2*i)
        for cp in range(start, min(end, 65534) + 1):
          glyph = u16(ranges + 2*i + offset + 2*(cp-start)) if offset else cp
          if offset and glyph == 0:
            continue
          if (glyph + delta) & 65535:
            chars.add(cp)
  return chars


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[2])
  parser.add_argument('--font', type=Path)
  parser.add_argument('--catalog', type=Path, action='append', default=[])
  args = parser.parse_args()
  source = args.root / 'openpilot/system/ui/lib/application.py'
  mapping = next(ast.literal_eval(n.value) for n in ast.parse(source.read_text(encoding='utf-8')).body
                 if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'NOTO_FONTS' for t in n.targets))
  assert mapping['zh-CHS'] == 'NotoSansCJKsc-Regular.otf', 'Simplified Chinese still uses the pixel font'
  font = args.font or args.root / 'openpilot/selfdrive/assets/fonts' / mapping['zh-CHS']
  data = font.read_bytes()
  assert data[:4] in (b'OTTO', b'\x00\x01\x00\x00'), 'font missing or unresolved Git LFS pointer'
  chars = cmap_glyphs(data)
  # Check the CJK repertoire shipped by this Noto version, plus the actual catalog.
  needed = set(range(0x4E00, 0x9FF0)) | set(range(32, 127))
  for catalog in args.catalog:
    needed.update(ord(c) for c in catalog.read_text(encoding='utf-8') if 0x3400 <= ord(c) <= 0x9FFF)
  missing = needed - chars
  assert not missing, f'{len(missing)} missing glyphs; first: {sorted(missing)[:12]}'
  print(f'PASS: outline Noto selected; {len(needed)} required glyphs covered; {len(data)} bytes')


if __name__ == '__main__':
  main()
