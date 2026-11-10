"""Failure cases: side warnings miss video edges; empty side columns hide warnings."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest


class TestScreenOverlayZones(unittest.TestCase):
  def test_video_edges(self):
    tree = ast.parse((Path(__file__).parents[3] / 'tesla/ui/screen.py').read_text())
    zones = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_zones')
    namespace = {'rl': SimpleNamespace(Rectangle=lambda *r: r), 'SCREEN': (2160, 1080)}
    exec(compile(ast.Module(body=[zones], type_ignores=[]), '<screen>', 'exec'), namespace)
    for view in ((300, 59, 1560, 699), (0, 0, 2160, 1080)):
      x, y, w, h = view
      result = namespace['_zones'](view)
      self.assertEqual(result['view'], view)
      self.assertGreater(result['left'][2], x)
      self.assertLess(result['right'][0], x + w)
      self.assertEqual(result['right'][0] + result['right'][2], 2160)


if __name__ == '__main__':
  unittest.main()
