"""Failure cases: device tests cannot be enabled; defaults/minimal mode change.

Parse the real SConstruct options without importing SCons or touching build outputs.
"""
import ast
from contextlib import redirect_stderr
import io
from optparse import OptionParser
from pathlib import Path
import sys

root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[2]
path = root / 'SConstruct'
nodes = [node for node in ast.parse(path.read_text()).body
         if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
         and isinstance(node.value.func, ast.Name) and node.value.func.id == 'AddOption'
         and node.value.args and isinstance(node.value.args[0], ast.Constant)
         and node.value.args[0].value in ('--minimal', '--extras')]
for hardware in (False, True):
  for release in (False, True):
    for arguments, expected in (([], not hardware and not release), (['--minimal'], False), (['--extras'], True),
                                (['--minimal', '--extras'], True), (['--extras', '--minimal'], False)):
      parser = OptionParser()
      env = {'AddOption': parser.add_option, 'COMMA_HARDWARE': hardware, 'release': release}
      exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), env)
      errors = io.StringIO()
      try:
        with redirect_stderr(errors):
          options, _ = parser.parse_args(arguments)
      except SystemExit as error:
        raise AssertionError(f'build arguments unsupported: {arguments}: {errors.getvalue()}') from error
      assert options.extras == expected, (hardware, release, arguments, options.extras, expected)
print('PASS: 20 build option cases; explicit extras, unchanged defaults/minimal mode, last option wins')
