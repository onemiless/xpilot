#!/usr/bin/env python3
"""Failure modes of gen_safety.py (declarations in opendbc/safety/plugins/safety.json):
- A duplicate bit, undeclared header, missing header, header not named <id>.h, tx differing from the header .msg, or a built-in plugin
  can.tx claim missing from safety.json is accepted.
- A stale registry passes --check, regeneration is not deterministic, or invalid input overwrites the registry.
- G1 a built-in plugin with another id using a safety profile is rejected or changes registry.h
- G2 an unknown profile, profile with extra keys, mismatched/missing can.tx, or non-dict safety is accepted
- G3 an exact legacy safety dict is rejected, or a differing/ambiguous legacy dict is accepted
- G4 uint16 safetyParam bits overflow Int16, change on wire round-trip, or mutate the source dataclass
- G5 safetyParam outside 0..0xFFFF is accepted or rejected without a clear ValueError
- G6 safety replay reads signed CarParamsSP.safetyParam without recovering the uint16 bits
The store side (installed plugins must match safety.json) is store_check S18; the plugins' own use of their
param_bit is tested in the plugin repository. Real files in a disposable miniature repository.
"""

import argparse
import ast
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'opendbc_repo')]

from openpilot.cereal import custom
from openpilot.selfdrive.car.helpers import convert_to_capnp
from opendbc.car import structs

SCRIPT = Path('openpilot/tesla/plugins/gen_safety.py')
HEADERS = Path('opendbc_repo/opendbc/safety/plugins')


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
  with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    shutil.copytree(ROOT / HEADERS, root / HEADERS)
    (root / SCRIPT).parent.mkdir(parents=True)
    shutil.copyfile(ROOT / SCRIPT, root / SCRIPT)
    shutil.copyfile(ROOT / 'openpilot/__init__.py', root / 'openpilot/__init__.py')
    shutil.copyfile(ROOT / 'openpilot/tesla/manifest.py', root / 'openpilot/tesla/manifest.py')
    declared_path = root / HEADERS / 'safety.json'
    declared = json.loads(declared_path.read_text())
    registry = root / HEADERS / 'registry.h'

    def run(label, expected=0, check=False, reason=None):
      proc = subprocess.run([sys.executable, str(root / SCRIPT), *(['--check'] if check else [])], capture_output=True, text=True)
      records.append({'label': label, 'exit_code': proc.returncode, 'expected': expected, 'reason': reason,
                      'output': proc.stdout + proc.stderr})

    run('generate')
    original = registry.read_bytes()
    run('current-check', check=True)
    registry.write_bytes(original + b'\n')
    run('stale-check', 1, check=True)
    run('regenerate')
    assert registry.read_bytes() == original
    builtin = root / 'openpilot/tesla/plugins/x/plugin.json'
    builtin.parent.mkdir()
    manifest = {'id': 'x', 'api': 1, 'category': 'tools', 'use': 'onroad', 'entry': 'plugin:X'}
    for label in ('duplicate-bit', 'missing-header', 'tx-differs-from-header', 'builtin-claim-undeclared'):
      data = json.loads(json.dumps(declared))
      if label == 'duplicate-bit':
        data['ambient_light']['param_bit'] = data['auto_speed']['param_bit']
      elif label == 'missing-header':
        data['ambient_light']['header'] = 'missing.h'
      elif label == 'tx-differs-from-header':
        data['ambient_light']['tx'] = ['0x67A:1']
      else:
        builtin.write_text(json.dumps({**manifest, 'claims': ['can.tx:0x123:0']}))
      declared_path.write_text(json.dumps(data))
      run(label, 1)
      assert registry.read_bytes() == original, label
    builtin.unlink()
    declared_path.write_text(json.dumps(declared))
    safe, profile = declared['auto_speed'], {'profile': 'auto_speed'}
    claims = [f'can.tx:{t}' for t in safe['tx']]
    cases = [
      ('G1-profile-other-id', {'claims': claims, 'safety': profile}, 0),
      ('G2-unknown-profile', {'claims': claims, 'safety': {'profile': 'missing'}}, 1),
      ('G2-extra-bit', {'claims': claims, 'safety': {**profile, 'param_bit': safe['param_bit']}}, 1),
      ('G2-extra-header', {'claims': claims, 'safety': {**profile, 'header': safe['header']}}, 1),
      ('G2-extra-tx', {'claims': claims, 'safety': {**profile, 'tx': safe['tx']}}, 1),
      ('G2-wrong-tx', {'claims': ['can.tx:0x679:1'], 'safety': profile}, 1),
      ('G2-no-claims', {'safety': profile}, 1),
      ('G2-no-safety', {'claims': claims}, 1),
      ('G3-legacy-other-id', {'claims': claims, 'safety': safe}, 0),
      ('G3-legacy-differs', {'claims': claims, 'safety': {**safe, 'param_bit': '0x2000'}}, 1),
      ('G3-empty-legacy', {'safety': {}}, 1),
    ]
    for name, value in (('null', None), ('list', []), ('string', 'auto_speed'), ('number', 1), ('bool', False)):
      cases.append((f'G2-safety-{name}', {'safety': value}, 1))
    for label, over, expected in cases:
      builtin.write_text(json.dumps({**manifest, **over}))
      run(label, expected, check=True)
      assert registry.read_bytes() == original, label
    declared_path.write_text(json.dumps({**declared, 'alias': safe}))
    builtin.write_text(json.dumps({**manifest, 'claims': claims, 'safety': safe}))
    run('G3-ambiguous-legacy', 1, check=True, reason='legacy safety must match exactly one profile')
    assert registry.read_bytes() == original
    builtin.unlink()
    declared_path.write_text(json.dumps(declared))
    (root / HEADERS / 'orphan.h').write_text('#pragma once\n')
    run('undeclared-header', 1)
    assert registry.read_bytes() == original
    (root / HEADERS / 'orphan.h').unlink()
    (root / HEADERS / 'ambient_light.h').rename(root / HEADERS / 'ambient.h')
    declared['ambient_light']['header'] = 'ambient.h'
    declared_path.write_text(json.dumps(declared))
    run('header-not-id', 1)
    assert registry.read_bytes() == original

  print(json.dumps(records, indent=2))
  failed = [r for r in records if r['exit_code'] != r['expected'] or (r['reason'] and r['reason'] not in r['output'])]
  assert not failed, failed


if __name__ == '__main__':
  main()
