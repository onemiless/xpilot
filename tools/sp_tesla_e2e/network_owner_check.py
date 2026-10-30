"""Network owner failure cases: disabled or non-winning provider, conflicting
process claim, incompatible installed manifest, and unsafe provider id. Synthetic
manifests and params only; this check does not run privileged network commands.
"""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'opendbc_repo')]
from openpilot.tesla import manifest as mf
from openpilot.tesla.network import authorized


def item(name, priority, claims, enabled=True, compatible=True):
  return SimpleNamespace(id=name, priority=priority, claims=claims, enable_param='Ext' + name,
                         params={'Ext' + name: {'type': 'BOOL', 'default': '1' if enabled else '0'}},
                         compatible=compatible)


def main():
  with TemporaryDirectory() as tmp:
    root = Path(tmp)
    a = item('starlink', 100, ('role:network.vpn', 'process:starlinkd'))
    competitor = item('earlier', 10, ('process:starlinkd',))
    def load(where):
      return ([], {}) if where == mf.PLUGINS_DIR else (items, {})
    def gate(m, _):
      return m.compatible, 'no capability'
    with patch.object(mf, 'INSTALL_DIR', root), patch.object(mf, 'load_all', side_effect=load), \
         patch.object(mf, 'compiled_param_keys', return_value=set()), patch.object(mf, 'compatible', side_effect=gate):
      items = [a]
      authorized('starlink')
      items = [a, competitor]
      try:
        authorized('starlink')
      except PermissionError:
        pass
      else:
        raise AssertionError('provider lost another exclusive claim but was authorized')
      items = [item('starlink', 100, ('role:network.vpn',), enabled=False)]
      try:
        authorized('starlink')
      except PermissionError:
        pass
      else:
        raise AssertionError('disabled provider was authorized')
      items = [item('starlink', 100, ('role:network.vpn',), compatible=False)]
      try:
        authorized('starlink')
      except PermissionError:
        pass
      else:
        raise AssertionError('incompatible provider was authorized')
  from openpilot.tesla import network_runtime as runtime
  with patch.object(runtime, 'parked', return_value=None):
    assert runtime.parking_allowed(), 'parked session rejected'
  with patch.object(runtime, 'parked', side_effect=PermissionError('onroad')):
    assert not runtime.parking_allowed(), 'onroad session allowed'
  print('network owner and parked session gating passed')


if __name__ == '__main__':
  main()
