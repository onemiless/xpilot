import json
from types import SimpleNamespace

from openpilot.tesla import host_api
from openpilot.tesla import manifest as mf
from openpilot.tesla import plugins


class FakeParams:
  def __init__(self, *, offroad=False, driver_view=False):
    self.values = {"IsOffroad": offroad, "IsDriverViewEnabled": driver_view}
    self.writes = []

  def get_bool(self, key):
    return self.values.get(key, False)

  def put_bool(self, key, value):
    self.values[key] = value
    self.writes.append((key, value))


def plugin(claims=()):
  return SimpleNamespace(NAME="test_plugin", CLAIMS=tuple(claims))


def activate(monkeypatch, item, params):
  monkeypatch.setattr(host_api, "_params", lambda: params)
  monkeypatch.setattr(plugins, "active", lambda _: [item])


def test_power_requires_active_claimed_parked_plugin(monkeypatch):
  item = plugin(("power",))
  params = FakeParams(offroad=False)
  activate(monkeypatch, item, params)
  assert host_api.reboot(item) == "park first"
  assert params.writes == []

  params.values["IsOffroad"] = True
  assert host_api.reboot(item) is None
  assert params.writes == [("DoReboot", True)]


def test_contract_violations_record_fault(monkeypatch):
  faults = []
  item = plugin(("power",))
  monkeypatch.setattr(plugins, "record_fault", lambda name, where, exc: faults.append((name, where, exc)))
  monkeypatch.setattr(plugins, "active", lambda _: [])
  assert host_api._refuse(item, "power", FakeParams()) == "plugin not active"

  missing = plugin()
  monkeypatch.setattr(plugins, "active", lambda _: [missing])
  assert host_api._refuse(missing, "power", FakeParams()) == "missing claim power"
  assert [(name, where, type(exc)) for name, where, exc in faults] == [
    ("test_plugin", "host_api", PermissionError),
    ("test_plugin", "host_api", PermissionError),
  ]


def test_play_sound_gates_hardware_and_path(monkeypatch, tmp_path):
  item = plugin(("sound",))
  params = FakeParams(offroad=True)
  activate(monkeypatch, item, params)
  monkeypatch.setattr(host_api, "has_audio_output", lambda: False)
  assert host_api.play_sound(item, tmp_path / "tone.wav") == "unsupported"

  monkeypatch.setattr(host_api, "has_audio_output", lambda: True)
  monkeypatch.setattr(mf, "INSTALL_DIR", tmp_path / "plugins")
  outside = tmp_path / "outside.wav"
  outside.write_bytes(b"not used")
  assert host_api.play_sound(item, outside) == "path outside plugin directories"


def test_host_api_capabilities_and_claims(tmp_path):
  known = SimpleNamespace(api=mf.API_VERSION, platform=None, requires={"host_api": list(mf.HOST_CAPS["host_api"])})
  assert mf.compatible(known, mf.HOST_CAPS) == (True, "")
  unknown = SimpleNamespace(api=mf.API_VERSION, platform=None, requires={"host_api": ["launch_missiles"]})
  assert mf.compatible(unknown, mf.HOST_CAPS) == (False, "requires host_api 'launch_missiles'")

  root = tmp_path / "claimed"
  root.mkdir()
  claims = ["power", "net.wifi", "sound", "display", "camera:road", "camera:wide", "camera:driver"]
  (root / "plugin.json").write_text(json.dumps({
    "id": "claimed", "name": "Claimed", "version": "1", "api": 1, "use": "both",
    "category": "tools", "entry": "plugin:Claimed", "claims": claims,
  }))
  assert mf.parse(root / "plugin.json").claims == tuple(claims)

  active, conflicts = mf.resolve([
    ("a", 1, ("power", "net.wifi")),
    ("b", 2, ("power", "net.wifi")),
  ], {"a", "b"})
  assert active == {"a"}
  assert conflicts == {"b": "a"}
  assert mf.shared("power")
  assert not mf.shared("net.wifi")
