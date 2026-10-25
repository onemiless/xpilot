#!/usr/bin/env python3
"""Host -> real Panda loader E2E, no hardware.

Failure cases, before implementation:
- H1 CAPS fails or ABI/limits differ: EP2 must never be written; base mode still arms.
- H2 bootstub (no app CAPS) receives any EP2 record.
- H3 STATUS CRC/count/length mismatch does not CLEAR, fault and arm the base mode.
- H4 overall deadline expires but upload continues or base mode never arms.
- H5 more than one EP2 record per loop, or heartbeats stop during upload.
- H6 onroad critical rule-owner fault does not CLEAR and latch the feature fault.
- H7 changed rules reload onroad, or fail to reload on the next offroad->onroad arming sequence.
- H8 malformed param, short transfer, BEGIN/COMMIT failure or mode reset reuses a stale table.
- H9 valid manifest -> card table differs from the firmware encoder or firmware rejects it.
- H10 a pandad respawn clears a latched fault before deviceState, then regrants stale rules.
- H11 failed CLEAR and NO_OUTPUT are treated as revoked without readback, or never retried.
- H12 an onroad SILENT/reset causes automatic re-arm or upload.
- H13 configured idle loops read health/params at 100 Hz instead of the 10 Hz slow path.
- H14 SPI lock wait consumes the 5 ms transfer deadline and faults a healthy upload.
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


def spi_check(output):
  source = (ROOT / 'openpilot/selfdrive/pandad/spi.cc').read_text()
  header = (ROOT / 'openpilot/selfdrive/pandad/panda_comms.h').read_text()
  declarations = [line for line in header.splitlines() if line.strip().startswith('int spi_transfer')]
  (output / 'spi_declarations.inc').write_text('\n'.join(declarations) + '\n')
  pieces = [source[source.index('// protocol,'):source.index('#define SPILOG')],
            source[source.index('// random, so'):source.index('int PandaSpiHandle::lltransfer')],
            source[source.index('// One request and its response.'):]]
  (output / 'spi_under_test.inc').write_text('\n'.join(pieces).replace('PandaSpiHandle::', 'TestSpi::'))
  commands = [['c++', '-std=c++17', '-Wall', '-Wextra', '-Werror', '-pthread', '-I'+str(ROOT / 'openpilot'), '-I'+str(output),
               str(ROOT / 'openpilot/selfdrive/pandad/tests/test_panda_rules_spi.cc'), '-o', str(output / 'spi_check')],
              [str(output / 'spi_check')]]
  records = []
  for i, command in enumerate(commands):
    result = subprocess.run(command, capture_output=True, text=True)
    (output / f'spi-{i}.log').write_text(result.stdout + result.stderr)
    records.append({'command': command, 'rc': result.returncode})
    if result.returncode:
      break
  evidence = {'source_sha256': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in
              ['openpilot/selfdrive/pandad/spi.cc', 'openpilot/selfdrive/pandad/panda_comms.h']}, 'checks': records}
  (output / 'spi-result.json').write_text(json.dumps(evidence, indent=2) + '\n')
  return records[-1]['rc']


def main():
  parser = argparse.ArgumentParser()
  parser.add_argument('--spi-only', action='store_true')
  parser.add_argument('--output', type=Path, required=True)
  parser.add_argument('--native', type=Path, default=ROOT / 'openpilot/selfdrive/pandad/tests/test_panda_rules')
  args = parser.parse_args()
  args.output.mkdir(parents=True, exist_ok=True)
  if args.spi_only:
    assert spi_check(args.output) == 0, 'H14: see spi-result.json'
    return
  loop = (ROOT / 'openpilot/selfdrive/pandad/pandad.cc').read_text()
  slow = loop[loop.index('if (rk.frame() % 10 == 0)'):loop.index('// Send out peripheralState')]
  assert slow.index('configureSafetyMode') < slow.index('\n    }\n'), 'H13: arming/fault poll outside 10 Hz block'
  assert slow.index('updateRules') > slow.index('\n    }\n'), 'H13: upload is not at 100 Hz'
  safety = (ROOT / 'openpilot/selfdrive/pandad/panda_safety.cc').read_text()
  fast = safety.split('void PandaSafety::updateRules() {', 1)[1]
  assert fast.lstrip().startswith('if (car_params_.empty()) return;'), 'H13: idle upload path must return before I/O'
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
  spi_rc = spi_check(args.output) if result.returncode == 0 else None
  evidence = {'spi_rc': spi_rc, 'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
              'native_rc': result.returncode, 'source_sha256': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in [ROOT / 'openpilot/tesla/panda_rules.py', ROOT / 'openpilot/tesla/manifest.py',
                          ROOT / 'openpilot/tesla/plugins/__init__.py', ROOT / 'openpilot/selfdrive/pandad/panda_safety.cc',
                          ROOT / 'openpilot/selfdrive/pandad/pandad.cc', ROOT / 'openpilot/selfdrive/pandad/pandad.h',
                          ROOT / 'openpilot/selfdrive/pandad/spi.cc', ROOT / 'openpilot/selfdrive/pandad/panda_comms.h']},
              'sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in args.output.glob('*.bin')}}
  (args.output / 'result.json').write_text(json.dumps(evidence, indent=2) + '\n')
  print(json.dumps(evidence, indent=2))
  assert result.returncode == 0, result.stdout + result.stderr
  assert spi_rc == 0, 'H14: see spi-result.json'


if __name__ == '__main__':
  main()
