#!/usr/bin/env python3
"""Plugin UI translation regression.

Failure cases: plugin tile title bypasses the language pack; untranslated plugin title or
settings label; user-entered setting value or callback changes; English fallback changes.
This exercises actual display expressions without a GPU; device layout is checked separately.
"""
import ast
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
UI = ROOT / 'openpilot/tesla/ui'

def tree(path):
  return ast.parse((UI / path).read_text(encoding='utf-8'))

def cls_method(module, cls, method):
  c = next(n for n in module.body if isinstance(n, ast.ClassDef) and n.name == cls)
  return next(n for n in c.body if isinstance(n, ast.FunctionDef) and n.name == method)

def named_assignment(method, name):
  return next(n.value for n in ast.walk(method) if isinstance(n, ast.Assign)
              and any(isinstance(t, ast.Name) and t.id == name for t in n.targets))

def evaluate(expr, **kwargs):
  return eval(compile(ast.Expression(expr), '<ui-expression>', 'eval'), kwargs)

def main():
  tile = cls_method(tree('tile.py'), 'Tile', 'face')
  expression = named_assignment(tile, 'title')
  fake = SimpleNamespace(SPEC=SimpleNamespace(title='Battery'), TRANSLATE_TITLE=False)
  zh = lambda s: {'Battery': '电池'}.get(s, s)
  assert evaluate(expression, self=fake, tr=zh) == '电池', 'downloaded tile title bypasses translation'
  assert evaluate(expression, self=fake, tr=lambda s: s) == 'Battery', 'English fallback changed'
  fake.SPEC.title = 'Unknown title'
  assert evaluate(expression, self=fake, tr=zh) == 'Unknown title', 'unknown title fallback changed'
  settings = cls_method(tree('settings_page.py'), 'SettingsList', 'add_settings')
  title = named_assignment(settings, 'label')
  assert evaluate(title, setting=SimpleNamespace(label='Battery'), translate=False, tr=zh) == '电池', 'plugin setting label untranslated'
  # Text/secret values and parameter callbacks are not passed through translation.
  keyboard = cls_method(tree('settings_page.py'), 'SettingsList', '_choose')
  calls = [n for n in ast.walk(keyboard) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == 'edit_text_setting']
  assert len(calls) == 1 and isinstance(calls[0].args[0], ast.Name) and calls[0].args[0].id == 'setting', 'text edit changed'
  print('UI translation: plugin tile and settings labels pass; English/unknown fallback and text editor preserved')

if __name__ == '__main__':
  main()
