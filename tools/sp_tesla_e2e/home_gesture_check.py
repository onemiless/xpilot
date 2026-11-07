"""Check actual gesture handlers: edge activation, short drags, taps and edit mode."""
import ast
import os
from pathlib import Path
from types import SimpleNamespace
import unittest


def load_handlers(path):
  tree = ast.parse(Path(path).read_text(encoding='utf-8'))
  cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'BigScreenHost')
  names = {'_edge_swipe_start', '_bar_shortcut', '_on_gesture'}
  methods = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in names]
  if {m.name for m in methods} != names:
    raise AssertionError('screen gesture handlers are missing')
  code = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0),
                          ast.ClassDef(name='Handlers', bases=[], keywords=[], body=methods, decorator_list=[])], type_ignores=[])
  env = {'SCREEN': (2160, 1080), 'EDGE_SWIPE': 56, 'SWIPE_MIN': 140,
         'ui_state': SimpleNamespace(started=True)}
  exec(compile(ast.fix_missing_locations(code), str(path), 'exec'), env)
  return env['Handlers'], env['ui_state']


class ReturnToTilesTest(unittest.TestCase):
  def test_swipe_and_bar_navigation(self):
    default = Path(__file__).resolve().parents[2] / 'openpilot/tesla/ui/screen.py'
    handlers, state = load_handlers(os.environ.get('XPILOT_SCREEN_FILE', str(default)))
    host = handlers()
    host._edge_swipe, host._mode, host._stack, host._edit, host._screen_override = None, 'onroad', [], False, None
    bar = {'park': (100, 100, 100, 100), 'drive': (200, 100, 100, 100)}
    host._bar_buttons = lambda placed: bar
    self.assertEqual(host._edge_swipe_start(0, 500, 30, 500), 'left')
    self.assertEqual(host._edge_swipe_start(2159, 500, 2100, 500), 'right')
    self.assertEqual(host._edge_swipe_start(800, 1079, 800, 1040), 'bottom')
    self.assertIsNone(host._edge_swipe_start(800, 500, 900, 500))
    host._on_gesture((0, 500), 'drag', 30, 500, {})
    host._on_gesture((0, 500), 'drop', 150, 500, {})
    self.assertEqual(host._screen_override, 'offroad')
    host._screen_override = None
    host._on_gesture((0, 500), 'drag', 30, 500, {})
    host._on_gesture((0, 500), 'drop', 100, 500, {})
    self.assertIsNone(host._screen_override)
    self.assertIsNone(host._edge_swipe)
    self.assertTrue(host._bar_shortcut(bar, 120, 120, 'tap'))
    self.assertEqual(host._screen_override, 'offroad')
    host._mode = 'native'
    self.assertTrue(host._bar_shortcut(bar, 220, 120, 'tap'))
    self.assertEqual(host._screen_override, 'onroad')
    state.started = False
    self.assertFalse(host._bar_shortcut(bar, 120, 120, 'tap'))
    state.started = True
    host._edit = True
    self.assertFalse(host._bar_shortcut(bar, 120, 120, 'tap'))


if __name__ == '__main__':
  unittest.main()
