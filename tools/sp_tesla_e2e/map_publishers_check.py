#!/usr/bin/env python3
"""Check map publisher ownership through the actual manager registration hook.

Failure cases, recorded before the fix:
- M1 mapd_manager and standalone map_osm both publish liveMapDataSP onroad.
- M2 fixing M1 stops the mapd backend or offroad map management.
- M3 a selected source:map plugin leaves an OSM publisher running.
- M4 disabling the provider leaves no OSM publisher, or non-OSM runs OSM jobs.

Process objects are inert fixtures; no map processes or publishers are started.
"""
import json
from pathlib import Path
import runpy
import sys
import types
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


def check() -> dict:
  errors, cases = [], []
  plugins = types.SimpleNamespace(ROLE_PROCESSES={}, holds=lambda p, claim: p['plugin'], processes=lambda *a: [])
  modules = {
    'openpilot.tesla': types.SimpleNamespace(plugins=plugins),
    'openpilot.common.hardware': types.SimpleNamespace(PC=True),
    'openpilot.sunnypilot.hardware.driver_monitoring': types.SimpleNamespace(driver_monitoring_enabled=lambda p: True),
    'openpilot.sunnypilot.hardware.profile': types.SimpleNamespace(
      HardwareProfile=types.SimpleNamespace(C3XL='c3xl'), get_hardware_profile=lambda: 'pc',
      has_audio_output=lambda: True, has_microphone=lambda: True),
    'openpilot.system.manager.process': types.SimpleNamespace(
      PythonProcess=lambda name, module, should_run: types.SimpleNamespace(name=name, should_run=should_run)),
  }
  with patch.dict(sys.modules, modules):
    apply = runpy.run_path(str(ROOT / 'openpilot/sunnypilot/system/fork_processes.py'))['apply_fork_processes']
  procs = [types.SimpleNamespace(name=n, should_run=lambda s, p, c: s)
           for n in ('dmonitoringmodeld', 'dmonitoringd', 'micd', 'soundd')]
  procs += [types.SimpleNamespace(name=n, should_run=lambda s, p, c: p['osm']) for n in ('mapd', 'mapd_manager')]
  procs.append(types.SimpleNamespace(name='map_osm', should_run=lambda s, p, c: s and p['osm']))
  managed = {p.name: p for p in apply(procs, None)}
  for started in (False, True):
    for osm in (False, True):
      for plugin in (False, True):
        params = {'osm': osm, 'plugin': plugin}
        running = [n for n in ('mapd', 'mapd_manager', 'map_osm') if managed[n].should_run(started, params, None)]
        publishers = [n for n in running if n != 'mapd']
        expected = ['mapd_manager'] if osm and not plugin else []
        if publishers != expected:
          errors.append(f'M1/M3/M4 started={started}, osm={osm}, plugin={plugin}: publishers={publishers}, expected={expected}')
        if ('mapd' in running) != (osm and not plugin):
          errors.append(f'M2 mapd backend condition changed: {params}, started={started}')
        cases.append({'started': started, 'osm': osm, 'plugin': plugin, 'running': running})
  return {'cases': cases, 'errors': errors}


if __name__ == '__main__':
  result = check()
  print(json.dumps(result, indent=2))
  sys.exit(bool(result['errors']))
