#!/usr/bin/env python3
"""Check the real process subscription call sites without starting control processes.

Failure cases, recorded before the fix:
- S1 plugin/base overlap creates multiple subscriptions for the same service.
- S2 plugin declarations exempt a required base service from health checks.
- S3 plugin-only inputs become mandatory, or caller ignores/poll/frequency are lost.
- S4 disabled plugins add subscriptions, or input lists are modified in place.

The constructors are captured; this checks their contracts, not native IPC timing.
"""
import ast
from collections import Counter
import json
from pathlib import Path
import runpy
import sys
import types
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
DECLARED = ['carControl', 'carState', 'deviceState', 'modelDataV2SP', 'navInstruction']
CALLERS = {
  'openpilot/selfdrive/selfdrived/selfdrived.py': ('deviceState', 'carControl', 'modelV2'),
  'openpilot/selfdrive/controls/controlsd.py': ('carState', 'modelV2', 'selfdriveState'),
  'openpilot/selfdrive/controls/plannerd.py': ('carState', 'carControl', 'modelV2'),
}
IGNORE_KEYS = ('ignore_alive', 'ignore_avg_freq', 'ignore_valid')


def check() -> dict:
  errors, cases = [], []
  def capture(services, **kwargs):
    return list(services), kwargs

  messaging = types.SimpleNamespace(SubMaster=capture)
  plugins = types.SimpleNamespace(services=lambda: DECLARED.copy())
  cereal = types.ModuleType('openpilot.cereal')
  cereal.messaging = messaging
  tesla = types.ModuleType('openpilot.tesla')
  tesla.plugins = plugins
  with patch.dict(sys.modules, {'openpilot.cereal': cereal, 'openpilot.tesla': tesla}):
    helper = ROOT / 'openpilot/tesla/subscriptions.py'
    if helper.exists():
      plugins.submaster = runpy.run_path(str(helper))['submaster']
      exports = ast.parse((ROOT / 'openpilot/tesla/plugins/__init__.py').read_text())
      if not any(isinstance(n, ast.ImportFrom) and n.module == 'openpilot.tesla.subscriptions'
                 and any(a.name == 'submaster' for a in n.names) for n in exports.body):
        errors.append('S3 plugins.submaster is not exported')

    for path, required in CALLERS.items():
      tree = ast.parse((ROOT / path).read_text())
      call = next(n for n in ast.walk(tree) if isinstance(n, ast.Call)
                  and isinstance(n.func, ast.Attribute) and n.func.attr in ('SubMaster', 'submaster'))
      for enabled in (False, True):
        plugins.services = lambda on=enabled: DECLARED.copy() if on else []
        env = {'plugins': plugins, 'messaging': messaging, 'DT_CTRL': .01, 'gps_location_service': 'gpsLocationExternal',
               'self': types.SimpleNamespace(sensor_packets=['accelerometer', 'gyroscope'],
                                              gps_packets=['gpsLocationExternal'],
                                              camera_packets=['narrowRoadCameraState', 'cabinCameraState', 'wideRoadCameraState'],
                                              sm_services_ext=['radarState', 'selfdriveStateSP'])}
        for n in ast.walk(tree):
          assignment = isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and
                       t.id in ('ignore', 'ignore_services', 'plugin_services') for t in n.targets)
          addition = isinstance(n, ast.AugAssign) and isinstance(n.target, ast.Name) and n.target.id == 'ignore' \
                     and isinstance(n.value, ast.Call) and isinstance(n.value.func, ast.Attribute) and n.value.func.attr == 'services'
          if assignment or addition:
            exec(compile(ast.Module(body=[n], type_ignores=[]), path, 'exec'), env)
        services, kwargs = eval(compile(ast.Expression(call), path, 'eval'), env)
        duplicates = [s for s, count in Counter(services).items() if count > 1]
        if duplicates:
          errors.append(f'S1 {path}: duplicate subscriptions {duplicates}')
        for key in IGNORE_KEYS:
          ignored = kwargs.get(key) or []
          bypassed = set(required) & set(ignored)
          if bypassed:
            errors.append(f'S2 {path}: {key} bypasses {sorted(bypassed)}')
          if enabled and 'navInstruction' not in ignored:
            errors.append(f'S3 {path}: plugin-only navInstruction is mandatory for {key}')
        if ('navInstruction' in services) != enabled:
          errors.append(f'S4 {path}: optional input does not follow plugin enable state')
        expected = {'frequency': 100} if 'selfdrived/' in path else {'poll': 'modelV2' if 'plannerd.py' in path else 'selfdriveState'}
        if any(kwargs.get(k) != v for k, v in expected.items()):
          errors.append(f'S3 {path}: caller scheduling changed')
        cases.append({'caller': path, 'plugins_enabled': enabled, 'duplicates': duplicates})

    if helper.exists():
      base, ignored = ['modelV2', 'deviceState'], ['modelV2']
      plugins.services = lambda: ['deviceState', 'navInstruction']
      services, kwargs = plugins.submaster(base, ignore_alive=ignored, poll='modelV2')
      if base != ['modelV2', 'deviceState'] or ignored != ['modelV2']:
        errors.append('S4 helper mutated caller lists')
      if kwargs['ignore_alive'] != ['modelV2', 'navInstruction'] or kwargs['ignore_valid'] != ['navInstruction']:
        errors.append('S3 caller-specific ignore lists were changed')
  return {'cases': cases, 'errors': errors}


if __name__ == '__main__':
  result = check()
  print(json.dumps(result, indent=2))
  sys.exit(bool(result['errors']))
