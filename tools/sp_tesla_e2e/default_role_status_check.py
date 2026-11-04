"""Rows and tile summary must agree: replaced defaults are standby, not conflicts.

Failure cases: summary counts a standby default; genuine/unknown conflicts disappear;
faults lose priority; a translated status changes the numerical summary.
"""
import ast
import sys
from pathlib import Path
from types import SimpleNamespace

path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[2] / 'openpilot/tesla/ui/offroad.py'
names = {'plugin_status', 'default_role_standby', 'plugin_conflict_count'}
functions = [n for n in ast.parse(path.read_text(encoding='utf-8')).body if isinstance(n, ast.FunctionDef) and n.name in names]
env = {'mf': SimpleNamespace(INSTALL_DIR=Path('/nonexistent-plugin-store')),
       'store': SimpleNamespace(plugin_state=lambda *args: 'installed'), 'tr': lambda text: text}
exec(compile(ast.Module(body=functions, type_ignores=[]), str(path), 'exec'), env)
status = env['plugin_status']
default = SimpleNamespace(NAME='default', ENABLE_PARAM=None, CLAIMS=['role:perception.model'])
manual = SimpleNamespace(NAME='manual', ENABLE_PARAM='ExtManual', CLAIMS=['role:perception.model'])
mixed = SimpleNamespace(NAME='mixed', ENABLE_PARAM=None, CLAIMS=['role:perception.model', 'can.tx:0x1:0'])
assert status(default, {}, {'default':'replacement'}) == 'inactive'
assert status(default, {'default':'failure'}, {'default':'replacement'}) == 'fault'
assert status(manual, {}, {'manual':'other'}) == 'conflict'
assert status(mixed, {}, {'mixed':'other'}) == 'conflict'
assert status(default, {}, {}) == ''

settings_path = path.parent / 'slots/settings.py'
tile = next(n for n in ast.parse(settings_path.read_text(encoding='utf-8')).body if isinstance(n, ast.ClassDef) and n.name == 'PluginsTile')
state = next(n for n in tile.body if isinstance(n, ast.FunctionDef) and n.name == 'state')
env.update(PLUGINS=[default, manual, mixed], LOAD_ERRORS={}, FAULTS_PARAM='faults')
params = SimpleNamespace(get=lambda _key: {})
env['ui_state'] = SimpleNamespace(params=params)
exec(compile(ast.Module(body=[state], type_ignores=[]), str(settings_path), 'exec'), env)
for conflicts, expected in (({}, 0), ({'default': 'replacement'}, 0), ({'manual': 'other'}, 1),
                            ({'default': 'replacement', 'manual': 'other'}, 1),
                            ({'mixed': 'other'}, 1), ({'unknown': 'other'}, 1)):
  env['resolve'] = lambda _params, conflicts=conflicts: ({'replacement'}, conflicts)
  value = env['state'](None, None)
  assert value == (1, expected, 0), f'Plugins tile miscounts standby/conflicts: {conflicts}: {value}'
env['tr'] = lambda _text: 'same translated label'
env['resolve'] = lambda _params: ({'replacement'}, {'default': 'replacement', 'manual': 'other'})
env['LOAD_ERRORS'] = {'broken': 'fixture'}
params.get = lambda _key: {'default': 'failure'}
assert env['state'](None, None) == (1, 1, 2), 'translation/fault state changed summary semantics'
print('PASS row status and tile summary: standby, actual/unknown conflicts, faults and translated labels')
