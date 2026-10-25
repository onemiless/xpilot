#!/usr/bin/env python3
"""Host -> real Panda loader E2E, no hardware.

Failure cases, before implementation:
- H1 CAPS fails or ABI/limits differ: EP2 must never be written; base mode still arms.
- H2 bootstub (no app CAPS) receives any EP2 record.
- H3 STATUS CRC/count/length mismatch does not CLEAR, fault and arm the base mode.
- H4 overall deadline expires but upload continues or base mode never arms.
- H5 more than one EP2 record per loop, or heartbeats stop during upload.
- H6 onroad critical rule-owner fault does not CLEAR and latch the feature fault.
- H7 changed rules reload in car mode, or fail to reload after returning to non-car mode.
- H8 malformed param, short transfer, BEGIN/COMMIT failure or mode reset reuses a stale table.
- H9 valid manifest -> card table differs from the firmware encoder or firmware rejects it.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import zlib
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'opendbc_repo')]


def sample_rule(address=0x600):
  return {'type': 'template_clone.v1', 'tx_address': address, 'rx_address': address, 'tx_bus': 1, 'rx_bus': 1,
              'tx_length': 8, 'rx_length': 8, 'template_ttl_us': 1_500_000, 'min_interval_us': 250_000,
              'session': 1, 'max_count': 50, 'idle_reset_us': 3_000_000, 'max_span_us': 0,
              'counter': {'kind': 0, 'bit_offset': 0, 'width': 0}, 'tx_checksum': {'kind': 0, 'checksum_byte': 0},
              'rx_checksum': {'kind': 0, 'checksum_byte': 0}, 'predicate_mask': [0]*8, 'predicate_value': [0]*8,
              'copy_mask': [255]*8, 'constant_mask': [0]*8, 'constant_value': [0]*8, 'fields': [], 'legal_tuples': []}


def main():
  parser = argparse.ArgumentParser()
  parser.add_argument('--output', type=Path, required=True)
  parser.add_argument('--native', type=Path, default=ROOT / 'openpilot/selfdrive/pandad/tests/test_panda_rules')
  args = parser.parse_args()
  args.output.mkdir(parents=True, exist_ok=True)
  from openpilot.tesla import panda_rules as rules
  from openpilot.common.prefix import OpenpilotPrefix
  prefix = OpenpilotPrefix()
  # Only IPC needs a fresh directory; Params below always uses the output directory.
  os.environ['OPENPILOT_PREFIX'] = prefix.prefix
  Path(prefix.msgq_path).mkdir(exist_ok=True)
  from opendbc.safety.tests.xpilot_rules_helpers import table, RULES
  from opendbc.safety.tests.libsafety import libsafety_py as lib
  from openpilot.tesla import manifest, plugins
  from openpilot.tesla.plugin import Plugin
  source = args.output / 'fixture' / 'plugin.json'
  source.parent.mkdir(exist_ok=True)
  source.write_text(json.dumps({'id': 'fixture', 'api': 1, 'category': 'actuation', 'use': 'onroad',
                                'entry': 'plugin:P', 'claims': ['can.tx:0x600:1'], 'rules': [sample_rule()]}))
  m = manifest.parse(source)
  candidate = type('P', (Plugin,), {'NAME': m.id, 'MANIFEST': m, 'CLAIMS': m.claims})
  from openpilot.common.params import Params
  params = Params(str(args.output / 'host-params'))
  with patch.object(plugins, 'PLUGINS', (candidate,)):
    host = plugins.CarPlugins(SimpleNamespace(brand='tesla'), params)
    host.setup(SimpleNamespace())
  blob = params.get('XpilotPandaRules')[:-4]
  assert params.get('XpilotPandaRules') == rules.param(rules.encode_rules(m.rules)), 'H9'
  assert zlib.crc32(blob) == int.from_bytes(rules.param(blob)[-4:], 'little')
  for name, data in [('rules', rules.param(blob)), ('empty', rules.param(table(()))), ('reference', rules.param(table(RULES)))]:
    (args.output / f'{name}.bin').write_bytes(data)
  libpath = lib._build_libsafety()
  env = {**os.environ, 'PARAMS_ROOT': str(args.output / 'params'), 'OPENPILOT_PREFIX': 'rules-e2e'}
  result = subprocess.run([str(args.native.resolve()), libpath, str(args.output.resolve())], env=env, capture_output=True, text=True)
  (args.output / 'native.log').write_text(result.stdout + result.stderr)
  evidence = {'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
              'native_rc': result.returncode, 'source_sha256': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in [ROOT / 'openpilot/tesla/panda_rules.py', ROOT / 'openpilot/tesla/manifest.py',
                          ROOT / 'openpilot/tesla/plugins/__init__.py', ROOT / 'openpilot/selfdrive/pandad/panda_safety.cc']},
              'sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in args.output.glob('*.bin')}}
  (args.output / 'result.json').write_text(json.dumps(evidence, indent=2) + '\n')
  print(json.dumps(evidence, indent=2))
  assert result.returncode == 0, result.stdout + result.stderr


if __name__ == '__main__':
  main()
