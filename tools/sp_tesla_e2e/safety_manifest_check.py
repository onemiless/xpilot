#!/usr/bin/env python3
"""Runtime rule manifests; G1-G3 profile/generator cases retired by X-33.
Failure cases listed before implementation:
- G4 uint16 safetyParam encoding changes on wire or mutates the dataclass (G14).
- G5 out-of-range safetyParam is accepted.
- G6 replay does not recover unsigned safetyParam.
- G7 safety.profile or any legacy safety field is accepted.
- G8 a mode-owned TX address is accepted.
- G9 a table above 8 rules or 4096 bytes is accepted.
- G10 can.tx claims differ from rule TX addresses.
- G11 malformed type/ABI, bus, fields or rule container is accepted.
- G12 incomplete/overlapping TX bit coverage is accepted.
"""

import argparse
import ast
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'opendbc_repo')]

from openpilot.cereal import custom
from openpilot.selfdrive.car.helpers import convert_to_capnp
from opendbc.car import structs

def main():
  records = []
  replay = ast.parse((ROOT / 'opendbc_repo/opendbc/safety/tests/safety_replay/replay_drive.py').read_text())
  reader = next(n for n in ast.walk(replay) if isinstance(n, ast.If) and ast.unparse(n.test) == 'args.param_sp is None')
  reader_code = compile(ast.Module(body=reader.body, type_ignores=[]), 'replay_drive.py', 'exec')
  for value in (0x0000, 0x2000 | 0x4000, 0x8000, 0x8001, 0xE001, 0xFFFF):
    source = structs.CarParamsSP(safetyParam=value)
    wire = convert_to_capnp(source).to_bytes()
    with custom.CarParamsSP.from_bytes(wire) as decoded:
      assert decoded.safetyParam == (value if value < 0x8000 else value - 0x10000), f'G4 wire {value:#06x}'
      assert decoded.safetyParam & 0xFFFF == value, f'G4 round-trip {value:#06x}'
      assert source.safetyParam == value, f'G4 mutated source {value:#06x}'
      args = argparse.Namespace(param_sp=None)
      exec(reader_code, {'CP_SP': decoded, 'args': args})
      assert args.param_sp == value, f'G6 replay {value:#06x}: {args.param_sp}'
      records.append({'label': f'G4/G6-{value:#06x}', 'exit_code': 0, 'expected': 0, 'reason': None,
                      'output': f'wire={decoded.safetyParam}, replay={args.param_sp}'})
  for value in (-1, 0x10000):
    try:
      convert_to_capnp(structs.CarParamsSP(safetyParam=value))
    except ValueError as e:
      assert 'safetyParam' in str(e) and '0..0xFFFF' in str(e), f'G5 unclear error: {e}'
    else:
      raise AssertionError(f'G5 accepted {value}')
    records.append({'label': f'G5-{value}', 'exit_code': 0, 'expected': 0, 'reason': None, 'output': 'ValueError'})
  from copy import deepcopy
  from openpilot.tesla import manifest as mf, panda_rules
  from tools.sp_tesla_e2e.panda_rules_check import sample_rule
  with tempfile.TemporaryDirectory() as tmp:
    path = Path(tmp) / 'fixture' / 'plugin.json'
    path.parent.mkdir()
    base = dict(id='fixture', api=1, category='tools', use='both', entry='plugin:P')
    valid = sample_rule()
    cases = [('valid', dict(rules=[valid], claims=['can.tx:0x600:1']), None),
             ('G7', dict(safety={'profile': 'sender_a'}), 'safety'),
             ('G7-legacy', dict(safety={}), 'safety'),
             ('G8', dict(rules=[{**valid, 'tx_address': 0x488}], claims=['can.tx:0x488:1']), 'mode-owned'),
             ('G9-count', dict(rules=[{**valid, 'tx_address': 0x600+i} for i in range(9)], claims=[f'can.tx:{0x600+i:#x}:1' for i in range(9)]), '8'),
             ('G10', dict(rules=[valid], claims=['can.tx:0x601:1']), 'can.tx'),
             ('G11-type', dict(rules=[{**valid, 'type': 'unknown'}]), 'type'),
             ('G11-bus', dict(rules=[{**valid, 'tx_bus': 0}]), 'bus'),
             ('G11-list', dict(rules={}), 'rules'),
             ('G11-abi', dict(rules=[{**valid, 'abi': 2}]), 'field'),
             ('G12-coverage', dict(rules=[{**valid, 'copy_mask': [0]*8}]), 'coverage')]
    large = deepcopy(valid)
    large['fields'] = [dict(bit_offset=i*8, width=8, min=0, max=255, exempt_values=[]) for i in range(8)]
    large['copy_mask'] = [0]*8
    large['legal_tuples'] = [dict(field_mask=255, values=[i]*8) for i in range(8)]
    cases += [('G9-bytes', dict(rules=[{**large, 'tx_address': 0x600+i} for i in range(6)], claims=[f'can.tx:{0x600+i:#x}:1' for i in range(6)]), '4096')]
    for label, over, reason in cases:
      path.write_text(json.dumps({**base, **over}))
      try:
        m = mf.parse(path)
        assert reason is None, f'{label} accepted'
        blob = panda_rules.encode_rules(m.rules)
        assert blob[10] == 1
      except ValueError as e:
        assert reason is not None and reason in str(e), f'{label}: {e}'
      records.append(dict(label=label, exit_code=0, expected=0, reason=None, output='validated'))

  print(json.dumps(records, indent=2))
  failed = [r for r in records if r['exit_code'] != r['expected'] or (r['reason'] and r['reason'] not in r['output'])]
  assert not failed, failed


if __name__ == '__main__':
  main()
