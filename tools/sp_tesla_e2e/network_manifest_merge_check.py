"""Failure: VPN authorization and plugin discovery select different valid manifests."""
from types import SimpleNamespace
from unittest.mock import patch
from openpilot.tesla import manifest

def item(name, *keys):
  return SimpleNamespace(id=name, params={key: {} for key in keys})

builtin = [item('built', 'CoreKey')]
installed = [item('built', 'UnusedKey'), item('first', 'PluginKey'),
             item('duplicate', 'PluginKey'), item('reserved', 'CompiledKey'), item('good', 'OtherKey')]
with patch.object(manifest, 'compiled_param_keys', return_value={'CompiledKey'}):
  accepted, errors, specs = manifest.merge_manifests(builtin, installed)
assert [m.id for m in accepted] == ['built', 'first', 'good']
assert set(errors) == {'built', 'duplicate', 'reserved'}
assert set(specs) == {'PluginKey', 'OtherKey'}
print('host and VPN share manifest conflict selection: PASS')
