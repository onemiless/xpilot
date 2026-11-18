#!/usr/bin/env python3
"""Actual shell -> staged OTA function -> manifest -> partition-IO boundary.

Failures: wrong19.9 system, stock C3 boot chain, lost C3XL profile, stale inherited
version, divergent launcher/OTA paths, unsafe fallback, boot tampering reaching IO.
The updater function is executed from its source AST; native Params/overlay setup
are outside this test. No device/partition access or native build is performed.
"""
import argparse
import ast
import copy
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'opendbc_repo')]
from openpilot.common.hardware.comma import agnos
from openpilot.sunnypilot.hardware.agnos import UnsafeBootChainManifest

DIRECTORY = 'openpilot/common/hardware/comma/'
MR_ONE = {
  'xbl': 'e8acf2a9cc7f0ce84cb803bfea9477f765c0d7b4daf26048e59651b9e6a7bfbb',
  'xbl_config': '758552ecf92b5569677197783bf0ccb73d7f961685308e45d3276ac9dd974f85',
  'abl': '32a2174b5f764e95dfc54cf358ba01752943b1b3b90e626149c3da7d5f1830b6',
  'boot': '0191529aa97d90d1fa04b472d80230b777606459e1e1e9e2323c9519839827b4',
}
SYSTEM = '80d9373348ddff2a5d1f534fab3420383939c96f3eba60baab6b15339fc602c7'


def run(output):
  records = []
  def check(name, action):
    try:
      detail = action()
      records.append({'case': name, 'passed': True, 'detail': detail})
    except Exception as error:
      records.append({'case': name, 'passed': False, 'error': f'{type(error).__name__}: {error}'})

  source = (ROOT / 'openpilot/system/updated/updated.py').read_text()
  function = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == 'handle_agnos_update')
  updater = compile(ast.Module(body=[function], type_ignores=[]), '<actual handle_agnos_update>', 'exec')
  with tempfile.TemporaryDirectory() as temp:
    temp = Path(temp)
    model = temp / 'model'
    profile_file = temp / 'hardware_profile'
    profile_file.write_text('c3xl\n')
    def env(physical, profile):
      model.write_bytes(('comma ' + physical).encode() + b'\0')
      return os.environ | {'SUNNYPILOT_HARDWARE_PROFILE': profile, 'SUNNYPILOT_HARDWARE_MODEL_FILE': str(model),
                           'SUNNYPILOT_HARDWARE_PROFILE_FILE': str(temp / 'absent'), 'AGNOS_VERSION': ''}
    def shell(environment):
      return subprocess.check_output(['bash', '-c', 'source launch_env.sh; printf "%s\n%s\n" "$AGNOS_VERSION" "$AGNOS_MANIFEST_FILE"'],
                                     cwd=ROOT, env=environment, text=True, stderr=subprocess.PIPE).splitlines()

    for physical, profile, manifest in [('tici', 'standard', 'agnos-c3.json'), ('tici', 'c3xl', 'agnos-c3xl.json'),
                                        ('tizi', 'standard', 'agnos.json'), ('mici', 'standard', 'agnos.json')]:
      for inherited in ('', '19.7', '19.9'):
        def selected(p=physical, h=profile, m=manifest, old=inherited):
          actual = shell(env(p, h) | {'AGNOS_VERSION': old})
          assert actual == ['19.9', DIRECTORY + m], actual
          return actual
        check(f'shell_{physical}_{profile}_{inherited or "unset"}', selected)

    def persistent():
      actual = shell(env('tici', '') | {'SUNNYPILOT_HARDWARE_PROFILE_FILE': str(profile_file)})
      assert actual == ['19.9', DIRECTORY + 'agnos-c3xl.json'], actual
    check('persistent_c3xl_precedes_generic_tici', persistent)
    for value in ('unknown-profile', ''):
      def rejected(v=value):
        profile_file.write_text(v)
        result = subprocess.run(['bash', '-c', 'source launch_env.sh; printf "%s" "$AGNOS_MANIFEST_FILE"'], cwd=ROOT,
                                env=env('tici', '') | {'SUNNYPILOT_HARDWARE_PROFILE_FILE': str(profile_file)}, capture_output=True)
        assert result.returncode != 0 and not result.stdout
      check('reject_profile_' + (value or 'empty'), rejected)

    for name in ('agnos.json', 'agnos-c3.json', 'agnos-c3xl.json'):
      def manifest_identity(n=name):
        parts = json.loads((ROOT / DIRECTORY / n).read_text())
        system = next(p for p in parts if p['name'] == 'system')
        assert system['hash_raw'] == SYSTEM and SYSTEM in system['url'], system
        stock = json.loads((ROOT / DIRECTORY / 'agnos.json').read_text())
        assert system == next(p for p in stock if p['name'] == 'system')
        by_name = {p['name']: p for p in parts}
        if n == 'agnos.json':
          assert by_name['boot']['hash_raw'] == 'b9c9b9261fca0cef9e6d9b198fa4b563767e6342afc7a61ab137b2b519260615'
        else:
          assert all(by_name[k]['hash_raw'] == digest for k, digest in MR_ONE.items())
        return {'system_raw_sha256': system['hash_raw']}
      check('identity_' + name, manifest_identity)

    for profile, name, required in [('standard', 'agnos-c3.json', tuple(MR_ONE)),
                                     ('c3xl', 'agnos-c3xl.json', (*MR_ONE, 'aop', 'devcfg'))]:
      for alteration in ('stock', 'duplicate'):
        def unsafe(h=profile, n=name, edit=alteration):
          parts = json.loads((ROOT / DIRECTORY / ('agnos.json' if edit == 'stock' else n)).read_text())
          if edit == 'duplicate': parts.append(copy.deepcopy(parts[0]))
          path = temp / 'unsafe.json'; path.write_text(json.dumps(parts))
          with patch.dict(os.environ, env('tici', h)):
            try: agnos.load_agnos_manifest(str(path))
            except UnsafeBootChainManifest: return
          raise AssertionError('stock/duplicate boot chain accepted')
        check(f'reject_{profile}_{alteration}', unsafe)
      for partition in required:
        for change in ('hash', 'missing', 'full_check', 'has_ab'):
          def damaged(h=profile, n=name, part=partition, edit=change):
            parts = json.loads((ROOT / DIRECTORY / n).read_text())
            entry = next(p for p in parts if p['name'] == part)
            if edit == 'missing': parts.remove(entry)
            else: entry[edit] = '0' * 64 if edit == 'hash' else False
            path = temp / 'damaged.json'; path.write_text(json.dumps(parts))
            io = []
            with patch.dict(os.environ, env('tici', h)), patch.object(agnos.subprocess, 'run', side_effect=lambda *a, **k: io.append('abctl')), \
                 patch.object(agnos, 'flash_partition', side_effect=lambda *a, **k: io.append('partition')):
              try: agnos.flash_agnos_update(str(path), 1, logging)
              except UnsafeBootChainManifest: pass
              else: raise AssertionError('unsafe boot chain accepted')
            assert not io, io
          check(f'pre_IO_reject_{profile}_{partition}_{change}', damaged)

    for physical, profile, manifest in [('tici', 'standard', 'agnos-c3.json'), ('tici', 'c3xl', 'agnos-c3xl.json'),
                                        ('mici', 'standard', 'agnos.json')]:
      for installed in ('19.7', '19.9'):
        def update(p=physical, h=profile, m=manifest, old=installed, missing=False):
          stage = temp / ('stage_' + h + '_' + p + '_' + old + str(missing))
          stage.mkdir()
          shutil.copyfile(ROOT / 'launch_env.sh', stage / 'launch_env.sh')
          for current in (ROOT / DIRECTORY).glob('agnos*.json'):
            destination = stage / DIRECTORY / current.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not (missing and current.name == m): shutil.copyfile(current, destination)
          writes, consistent = [], []
          def validate_only(path, slot, log):
            agnos.load_agnos_manifest(path)
            writes.append({'manifest': str(Path(path).relative_to(stage)), 'slot': slot})
          namespace = {'HARDWARE': SimpleNamespace(get_os_version=lambda: old), 'OVERLAY_MERGED': str(stage),
                       'run': lambda cmd, cwd: subprocess.check_output(cmd, cwd=cwd, text=True),
                       'cloudlog': SimpleNamespace(info=lambda *a: None), 'set_consistent_flag': consistent.append, 'os': os}
          exec(updater, namespace)
          with patch.dict(os.environ, env(p, h)), patch.object(agnos, 'get_target_slot_number', return_value=1), \
               patch.object(agnos, 'flash_agnos_update', side_effect=validate_only):
            if missing:
              try: namespace['handle_agnos_update']()
              except FileNotFoundError: pass
              else: raise AssertionError('missing hardware manifest fell back to stock')
            else: namespace['handle_agnos_update']()
          assert writes == ([] if old == '19.9' or missing else [{'manifest': DIRECTORY + m, 'slot': 1}]), writes
          if old == '19.9': assert not consistent
          return {'installed': old, 'writes_at_stubbed_boundary': writes}
        check(f'actual_OTA_{physical}_{profile}_{installed}', update)
      if profile == 'c3xl' or physical == 'tici':
        check(f'missing_{manifest}_never_falls_back', lambda f=update: f(old='19.7', missing=True))

  result = {'passed': all(r['passed'] for r in records), 'cases': records,
            'source_head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
            'scope': 'real shell and source OTA function/manifest/pre-IO guard; no native Params, partition access or device build',
            'source_sha256': {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in
                              ('launch_env.sh', 'openpilot/system/updated/updated.py', 'openpilot/sunnypilot/hardware/agnos.py')}}
  if output is not None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + '\n')
  print(json.dumps({'passed': result['passed'], 'cases': len(records), 'failed': [r['case'] for r in records if not r['passed']]}))
  return result['passed']


if __name__ == '__main__':
  parser = argparse.ArgumentParser()
  parser.add_argument('--output', type=Path)
  raise SystemExit(0 if run(parser.parse_args().output) else 1)
