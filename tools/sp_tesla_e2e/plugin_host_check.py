#!/usr/bin/env python3
"""Plugin host check: fault isolation, enable switch and plugin metadata (runs without a device).

Failure cases listed before implementation:
- P1 a plugin hook raises and card's host raises with it (card dies, openpilot drops out while driving)
- P2 a faulted plugin keeps sending CAN or getting hooks
- P3 a plugin whose __init__ raises takes the other plugins down with it
- P4 a fault is not recorded for the UI (plugin name + where)
- P5 a disabled plugin is still built in card, its process still runs, or its plan filter is still used
- P6 a plan filter raises and the longitudinal plan is not published (controlsd would see a stale plan)
- P7 a faulted plan filter keeps being called every cycle
- P8 a non-Tesla car loads plugins
- P9 a real plugin lacks TITLE/ENABLE_PARAM, or names a setting key not declared in params_keys.h
       (or a store plugin's plugin.json),
       or two settings share a key, or ENABLE_PARAM is not a BOOL key
- P10 a plugin ui.py does not parse, or the plugin page is missing a plugin
- P11 common/plugin_params.inc is out of date with the plugins' plugin.json params (device would miss a key)
- P12 a key is declared both in params_keys.h and a plugin.json, or in two plugins
- P13 a lower plugin speed target does not win over cruise (cannot tighten)
- P14 a higher plugin target does not raise the cruise target while engaged (cannot accelerate)
- P15 a raise applies while disengaged, during gas override, or while pressing the brake
- P16 a raise goes above V_CRUISE_MAX
- P17 a raise overrides SP's other limiters (speed limit assist, SCC) instead of only the driver set speed
- P18 two raising plugins: the higher one wins (should be the lower, more conservative one)
- P19 a long target that raises takes plannerd down or keeps running; None changes nothing
- P20 a disabled plugin still contributes a long target
- P21 an active source:radar plugin does not replace the port's radar, or one without the claim does
- P22 a radar plugin that raises takes card down or keeps being called; the port's radar must take over
- P23 an active source:map plugin leaves SP's OSM map processes running (two liveMapDataSP publishers),
       or a disabled one stops them
- P24 externald bridges a provider from a disabled plugin or one without the external.provider claim,
       or a raising provider hook takes the others down
- P26 SP's four model processes lose their own conditions, run beside op_modeld, fail to resume after switching
       back, or an arbitrary role owner cannot select all, some or none of the supplied process objects
- P27 send_msg publishes a kind the plugin has no msg:<kind> claim for, or does not record it as a fault
- P27 a declared kind is not delivered, its source is not the plugin id, or kind/payload/validForMs are lost
- P27 PluginMsgs.fresh returns other kinds or expired messages, or drops validForMs 0 (no expiry)
- P28 a plugin whose processes() raises takes manager registration down or its fault is not recorded
- P29 a plugin process replaces an official process (including alert_output) or an earlier plugin's process
- P30 a plugin with an unknown SERVICES name is loaded (SubMaster would crash)
- P31 a disabled or conflicting plugin's SERVICES are subscribed, or unavailable Params breaks host checks
- P32 pm.send raising escapes send_msg or is not recorded as a pluginMsg fault
- P33 SP's model processes run while nobody owns role:perception.model
- P34 role_processes is called without the role claim, or accepts a foreign object (even with a supplied name)
- P35 a raising role_processes hook takes registration down, loses healthy processes, or is not recorded
- P36 processes() can register a reserved SP model process name after it leaves the official process list
- P37 missing_roles omits an unowned required role, or counts a disabled or conflicting claimant as its owner
- P38 removing every sp_* provider does not produce one offroad alert per missing required role
- P39 switching off the only enabled required-role claimant writes params or fails to show the refusal in either UI
- P40 switching off a replacement is refused while another enabled claimant remains
- P41 switching on a conflict silently disables the only enabled claimant of another required role

Run from the repo root with the openpilot python env. Native-only modules (Params) are stubbed if missing.
- P42 enabling an incompatible plugin writes switches, disables peers, or clears faults
- P43 staged update uses running capabilities, omits incompatibilities/alerts, or blocks activation
- P44 drive start omits system/plugin hashes or rule CRC, includes inactive plugins, or logs repeatedly
"""
import ast
import json
from pathlib import Path
import re
import runpy
import sys
import tempfile
import types
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = Path(tempfile.mkdtemp(prefix="xp-host-"))
sys.path[:0] = [str(ROOT), str(ROOT / "opendbc_repo")]
try:
  import openpilot.common.params  # noqa: F401
except Exception:
  stub = types.ModuleType("openpilot.common.params")
  stub.Params = object
  stub.UnknownKeyName = KeyError
  sys.modules["openpilot.common.params"] = stub

from openpilot.tesla import plugins  # noqa: E402
from openpilot.tesla.plugin import Plugin  # noqa: E402

KEY_RE = r'\{"(\w+)", \{[^,]+, (\w+)'
CORE_KEYS = dict(re.findall(KEY_RE, (ROOT / "openpilot/common/params_keys.h").read_text()))
INC = ROOT / "openpilot/common/plugin_params.inc"
PLUGIN_KEYS = dict(re.findall(KEY_RE, INC.read_text())) if INC.exists() else {}
KEYS = {**CORE_KEYS, **PLUGIN_KEYS}


DEFAULT_CORE = {"sp_models", "sp_engagement", "sp_safety_fallback"}


class FakeParams:
  def __init__(self, values=None):
    self.values = dict(values or {})

  def get_bool(self, key):
    return bool(self.values.get(key, False))

  def get(self, key, return_default=False):
    return self.values.get(key)


class CP:
  brand = "tesla"


calls = []


made = []


def make(name, fail_in=None, enable=None):
  made.append(name)
  addr = 0x100 + len(made)  # each fake owns its own exclusive CAN claim
  class P(Plugin):
    NAME, TITLE, ENABLE_PARAM = name, name, enable
    CLAIMS = (f"can.tx:0x{addr:X}:0", "plan.filter", f"process:{name}d")

    def __init__(self, CP, params):
      super().__init__(CP, params)
      if fail_in == "__init__":
        raise RuntimeError("init")

    def observe_can(self, can_list, now_ns):
      calls.append((name, "observe_can"))
      if fail_in == "observe_can":
        raise RuntimeError("observe")
      return [(addr, f"{name}-now".encode(), 0)]

    def control_sends(self, CS, CC, now_ns):
      calls.append((name, "control_sends"))
      if fail_in == "control_sends":
        raise RuntimeError("control")
      return [(addr, f"{name}-ctl".encode(), 0)]

    @classmethod
    def processes(cls):
      if fail_in == "processes":
        raise RuntimeError("processes")
      return [types.SimpleNamespace(name=f"{name}d", should_run=lambda started, params, CP: started)]

    @classmethod
    def plan_filter(cls, CP, params):
      return Filter(name, fail_in == "plan")
  P.__name__ = name
  return P


class Filter:
  def __init__(self, name, fail):
    self.name, self.fail, self.calls = name, fail, 0

  def publisher(self, pm, sm):
    outer = self

    class Sink:
      def send(self, service, msg):
        outer.calls += 1
        if outer.fail:
          raise RuntimeError("filter")
        pm.send(service, msg + [outer.name])
    return Sink()


class PM:
  def __init__(self):
    self.sent = []

  def send(self, service, msg):
    self.sent.append((service, msg))


def main() -> int:
  errors = []
  faults = []
  plugins.record_fault = lambda name, where, exc: faults.append((name, where))

  plugins.PLUGINS = (make("good"), make("bad_obs", "observe_can"), make("bad_init", "__init__"), make("off", enable="OffKey"))
  host = plugins.CarPlugins(CP, FakeParams())
  names = [p.NAME for p in host.plugins]
  if "bad_init" in names:
    errors.append("P3 plugin with failing __init__ kept")
  if "good" not in names:
    errors.append("P3 good plugin dropped")
  if "off" in names:
    errors.append("P5 disabled plugin built in card")
  try:
    out1 = host.observe_can([])
    out2 = host.observe_can([])
  except Exception as e:
    errors.append(f"P1 host raised {e!r}")
    out1 = out2 = []
  if b"good-now" not in [s[1] for s in out1] or b"good-now" not in [s[1] for s in out2]:
    errors.append("P1 good plugin output lost after a fault")
  if any(c == ("bad_obs", "observe_can") for c in calls[2:]):
    errors.append("P2 faulted plugin still called")
  if b"bad_obs-ctl" in [s[1] for s in host.control_sends(None, None, 0)]:
    errors.append("P2 faulted plugin still sends")
  if ("bad_obs", "card.observe_can") not in faults or ("bad_init", "card.__init__") not in faults:
    errors.append(f"P4 faults not recorded: {faults}")

  procs = plugins.processes()
  off = [p for p in procs if p.name == "offd"]
  if not off or off[0].should_run(True, FakeParams(), None):
    errors.append("P5 disabled plugin process still runs")
  if not [p for p in procs if p.name == "goodd"][0].should_run(True, FakeParams(), None):
    errors.append("P5 enabled plugin process blocked")
  if not off[0].should_run(True, FakeParams({"OffKey": True}), None):
    errors.append("P5 plugin process ignores its enable param")

  plugins.PLUGINS = (make("bad_plan", "plan"), make("good"), make("off", enable="OffKey"))
  filters = plugins.plan_filters(CP, FakeParams())
  if any(f.name == "off" for f in filters):
    errors.append("P5 disabled plugin plan filter used")
  pm = PM()
  bad = next(f for f in filters if f.name == "bad_plan")
  for _ in range(3):
    try:
      plugins.plan_sink(filters, pm, None).send("longitudinalPlan", [])
    except Exception as e:
      errors.append(f"P6 plan_sink raised {e!r}")
      break
  if len(pm.sent) != 3:
    errors.append(f"P6 plan published {len(pm.sent)}/3 times")
  if bad.calls > 1:
    errors.append(f"P7 faulted filter called {bad.calls} times")

  class Other:
    brand = "toyota"
  if plugins.CarPlugins(Other, FakeParams()).plugins or plugins.plan_filters(Other, FakeParams()):
    errors.append("P8 non-Tesla car loads plugins")

  import importlib
  plugins_mod = importlib.reload(plugins)
  real = plugins_mod.PLUGINS
  keys = {**KEYS, **{k: v["type"] for k, v in plugins_mod.INSTALLED_SPECS.items()}}  # store plugins keep params in .params/
  seen = set()
  for cls in real:
    default = cls.NAME in DEFAULT_CORE  # always-on default of a replaceable core role (XPILOT.md rule 6)
    if not cls.TITLE or (not cls.ENABLE_PARAM and not default):
      errors.append(f"P9 {cls.NAME} lacks TITLE or ENABLE_PARAM")
    if default and cls.ENABLE_PARAM is not None:
      errors.append(f"P9 default {cls.NAME} has a switch {cls.ENABLE_PARAM}")
    if not default and keys.get(cls.ENABLE_PARAM) != "BOOL":
      errors.append(f"P9 {cls.NAME} ENABLE_PARAM {cls.ENABLE_PARAM} not a declared BOOL")
    for s in cls.SETTINGS:
      if s.key not in keys:
        errors.append(f"P9 {cls.NAME} setting {s.key} not in params_keys.h")
      if s.key in seen:
        errors.append(f"P9 duplicate setting {s.key}")
      seen.add(s.key)
    ui = ROOT / "openpilot/tesla/plugins" / cls.NAME / "ui.py"
    if ui.exists():
      try:
        ast.parse(ui.read_text())
      except SyntaxError as e:
        errors.append(f"P10 {ui} {e}")
  if KEYS.get("XpilotPluginFaults") != "JSON":
    errors.append("P4 XpilotPluginFaults not declared as JSON")
  page = (ROOT / "openpilot/tesla/ui/plugins_page.py")
  if not page.exists() or "PLUGINS" not in page.read_text():
    errors.append("P10 plugin page missing or not driven by PLUGINS")

  import subprocess
  gen = subprocess.run([sys.executable, str(ROOT / "openpilot/tesla/plugins/gen_params.py"), "--check"], capture_output=True, text=True)
  if gen.returncode != 0:
    errors.append(f"P11 {gen.stdout.strip() or gen.stderr.strip()[-200:]}")
  if not INC.exists() or '#include "common/plugin_params.inc"' not in (ROOT / "openpilot/common/params_keys.h").read_text():
    errors.append("P11 params_keys.h does not include common/plugin_params.inc")
  errors += [f"P12 {k} in params_keys.h and a plugin.json" for k in CORE_KEYS.keys() & PLUGIN_KEYS.keys()]
  owners = {}
  for f in sorted((ROOT / "openpilot/tesla/plugins").glob("*/plugin.json")):
    for k in json.loads(f.read_text()).get("params", {}):
      if k in owners:
        errors.append(f"P12 {k} in {owners[k]} and {f.parent.name}")
      owners[k] = f.parent.name
  errors += long_target_checks(plugins_mod)
  errors += source_checks(plugins_mod, real)
  errors += msg_checks(plugins_mod)
  errors += registration_checks(plugins_mod)
  errors += service_checks(plugins_mod)
  errors += required_role_checks(plugins_mod, real)
  errors += capability_checks(plugins_mod, real)

  print(json.dumps({"plugins": [c.NAME for c in real], "artifacts": str(ARTIFACTS), "errors": errors}, indent=1))
  return 1 if errors else 0


def capability_checks(plugins, real) -> list[str]:
  from dataclasses import replace
  from openpilot.tesla import manifest as mf
  from openpilot.tesla.plugin_params import ParamsView
  from openpilot.tesla.ui import offroad
  errors = []
  class Params(FakeParams):
    def put(self, key, value, block=False):
      self.values[key] = value
    put_bool = put

  m = replace(real[0].MANIFEST, requires={"hooks": ["teleport"]}) if hasattr(real[0].MANIFEST, "requires") else real[0].MANIFEST
  incoming = type("Incoming", (Plugin,), {"NAME": "incoming", "ENABLE_PARAM": "IncomingOn", "MANIFEST": m,
                                          "CLAIMS": ("source:demo",)})
  peer = type("Peer", (Plugin,), {"NAME": "peer", "ENABLE_PARAM": "PeerOn", "CLAIMS": incoming.CLAIMS})
  p = Params({"PeerOn": True, plugins.FAULTS_PARAM: {"incoming": "old fault"}})
  before = dict(p.values)
  with patch.object(offroad, "PLUGINS", (incoming, peer)):
    error = offroad.switch_plugin(incoming, True, p)
  if not error or "teleport" not in error or p.values != before:
    errors.append(f"P42 incompatible enable: {error!r}, params={p.values}")
  if not hasattr(mf, "check_staged") or not hasattr(plugins, "log_snapshot"):
    return errors + ["P43 missing staged precheck", "P44 missing drive snapshot"]

  with tempfile.TemporaryDirectory() as tmp, patch.object(mf, "INSTALL_DIR", Path(tmp) / "installed"):
    stage = Path(tmp) / "staged"
    path = stage / "openpilot/tesla/manifest.py"
    path.parent.mkdir(parents=True)
    # The staged host drops setup; the running host still provides it.
    path.write_text((ROOT / "openpilot/tesla/manifest.py").read_text() + '\nHOST_CAPS["hooks"] = []\n')
    installed = mf.INSTALL_DIR / "needs_setup"
    installed.mkdir(parents=True)
    data = {"id": installed.name, "api": 1, "category": "tools", "use": "both", "entry": "plugin:P",
            "version": "1.2.3", "requires": {"hooks": ["setup"]}}
    (installed / "plugin.json").write_text(json.dumps(data))
    mf.check_staged(stage, p)
    warnings = ParamsView(p, mf.UPDATE_SPECS).get(mf.UPDATE_COMPAT_PARAM)
    (ARTIFACTS / "staged-incompatibilities.json").write_text(json.dumps(warnings, indent=2))
    if "setup" not in warnings.get("needs_setup", ""):
      errors.append(f"P43 staged capabilities ignored: {warnings}")
    with patch.object(plugins, "PLUGINS", real):
      if not any("needs_setup" in text and "setup" in text for _, text in offroad.offroad_alerts(p)):
        errors.append("P43 staged warning not shown offroad")
    # Execute the real finalizer with only filesystem/update plumbing replaced.
    tree = ast.parse((ROOT / "openpilot/system/updated/updated.py").read_text())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "finalize_update")
    events = []
    env = {"os": types.SimpleNamespace(path=types.SimpleNamespace(exists=lambda p: False)),
           "shutil": types.SimpleNamespace(copytree=lambda *a, **k: None), "OVERLAY_MERGED": "merged", "FINALIZED": str(stage),
           "cloudlog": types.SimpleNamespace(info=lambda *a: None), "run": lambda *a: None,
           "set_consistent_flag": lambda value: events.append(value)}
    with patch.object(mf, "check_staged", side_effect=lambda *a: events.append("precheck")):
      exec(compile(ast.Module(body=[fn], type_ignores=[]), "updated.py", "exec"), env)
      env["finalize_update"]()
    if events != [False, "precheck", True]:
      errors.append(f"P43 update activation ordering/blocked: {events}")
    path.write_text((ROOT / "openpilot/tesla/manifest.py").read_text())
    mf.check_staged(stage, p)
    if ParamsView(p, mf.UPDATE_SPECS).get(mf.UPDATE_COMPAT_PARAM):
      errors.append("P43 resolved warning not cleared")
    path.unlink()
    mf.check_staged(stage, p)
    if not ParamsView(p, mf.UPDATE_SPECS).get(mf.UPDATE_COMPAT_PARAM):
      errors.append("P43 failed precheck silently ignored")

    events = []
    tree = ast.parse((ROOT / "openpilot/selfdrive/car/card.py").read_text())
    init = next(n for c in tree.body if isinstance(c, ast.ClassDef) and c.name == "Car"
                for n in c.body if isinstance(n, ast.FunctionDef) and n.name == "__init__")
    snapshot_calls = [n for n in ast.walk(init) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "log_snapshot"]
    if len(snapshot_calls) != 1:
      errors.append("P44 card does not log once per drive initialization")
    active_plugin = real[0]
    off = type("Off", (Plugin,), {"NAME": "off", "ENABLE_PARAM": "OffKey"})
    loser = type("Loser", (Plugin,), {"NAME": "loser", "PRIORITY": 20000, "CLAIMS": active_plugin.CLAIMS})
    p.put("XpilotPandaRules", {"crc32": 12345})
    with patch.object(plugins, "PLUGINS", (active_plugin, off, loser)), patch.object(plugins.cloudlog, "event", side_effect=lambda name, **kw: events.append((name, kw))), \
         patch.object(mf, "compiled_param_keys", return_value={"XpilotPandaRules"}):
      if snapshot_calls:
        exec(compile(ast.Expression(snapshot_calls[0]), "card.py", "eval"), {"log_snapshot": plugins.log_snapshot,
             "self": types.SimpleNamespace(params=p)})
    if len(events) != 1 or events[0][0] != "xpilot.drive_snapshot":
      errors.append(f"P44 missing drive event: {events}")
    else:
      snapshot = events[0][1]
      (ARTIFACTS / "drive-snapshot.json").write_text(json.dumps(snapshot, indent=2))
      expected = [{"id": active_plugin.NAME, "version": active_plugin.MANIFEST.version, "sha256": active_plugin.SHA256}]
      if snapshot.get("plugins") != expected or len(snapshot.get("system_commit", "")) != 40 or snapshot.get("rule_table_crc32") != 12345:
        errors.append(f"P44 incomplete snapshot: {snapshot}")
    (installed / "__init__.py").write_text("")
    code = installed / "plugin.py"
    code.write_text("from openpilot.tesla.plugin import Plugin\nclass P(Plugin):\n  pass\n")
    def digest():
      return next(c.SHA256 for c in plugins._discover()[0] if c.NAME == installed.name)
    first = digest()
    cache = installed / "__pycache__"
    cache.mkdir(exist_ok=True)
    (cache / "ignored.pyc").write_bytes(b"cache")
    if digest() != first:
      errors.append("P44 content hash depends on import cache")
    code.write_text(code.read_text() + "\nCHANGED = 1\n")
    if digest() == first or len(first) != 64:
      errors.append("P44 content hash misses source changes")
  return errors


def required_role_checks(plugins, real) -> list[str]:
  from openpilot.tesla.ui import offroad
  errors = []
  roles = ("role:perception.model", "role:engagement.state", "role:safety.fallback")
  if getattr(plugins, "REQUIRED_ROLES", None) != roles:
    errors.append("P37 REQUIRED_ROLES differs from the three required roles")

  class Params(FakeParams):
    def __init__(self, values=None):
      super().__init__(values)
      self.writes = []

    def put(self, key, value, block=False):
      self.writes.append((key, value))
      self.values[key] = value

    put_bool = put

  remaining = tuple(p for p in real if not p.NAME.startswith("sp_"))
  with patch.object(plugins, "PLUGINS", remaining):
    try:
      if plugins.missing_roles(Params()) != list(roles):
        errors.append("P37 missing roles not listed in required order")
    except AttributeError as e:
      errors.append(f"P37 {e}")
    alerts = offroad.offroad_alerts(Params())
    if len(alerts) != 3 or {text for _, text in alerts} != {f"No {r} plugin: driving assistance unavailable" for r in roles}:
      errors.append(f"P38 missing-role alerts: {alerts}")
  with patch.object(plugins, "PLUGINS", real):
    if offroad.offroad_alerts(Params()):
      errors.append("P38 alerts remain with all built-in providers present")

  for role in roles:
    replacement = type("Required", (Plugin,), {"NAME": "required", "ENABLE_PARAM": "RequiredOn", "CLAIMS": (role,)})
    fallback = next(p for p in real if role in p.CLAIMS)
    p = Params({"RequiredOn": True, plugins.FAULTS_PARAM: {"required": "fault"}})
    with patch.object(plugins, "PLUGINS", (replacement,)), patch.object(offroad, "PLUGINS", (replacement,)):
      error = offroad.switch_plugin(replacement, False, p)
      if error != f"required role {role}" or p.writes:
        errors.append(f"P39 only enabled claimant {role}: error={error!r}, writes={p.writes}")
      errors += switch_ui_checks(replacement, p, offroad)
    for other in (fallback, type("Other", (replacement,), {"NAME": "other", "ENABLE_PARAM": "OtherOn"})):
      p = Params({"RequiredOn": True, "OtherOn": True})
      with patch.object(plugins, "PLUGINS", (replacement, other)), patch.object(offroad, "PLUGINS", (replacement, other)):
        if offroad.switch_plugin(replacement, False, p) or p.get_bool("RequiredOn"):
          errors.append(f"P40 replacement {role} cannot be switched off with an enabled fallback")
    disabled = type("Disabled", (replacement,), {"NAME": "disabled", "ENABLE_PARAM": "DisabledOn"})
    blocker = type("Blocker", (Plugin,), {"NAME": "blocker", "PRIORITY": -1, "CLAIMS": ("source:demo",)})
    loser = type("Loser", (replacement,), {"CLAIMS": (role, "source:demo")})
    with patch.object(plugins, "PLUGINS", (disabled, loser, blocker)):
      try:
        if role not in plugins.missing_roles(Params({"RequiredOn": True})):
          errors.append(f"P37 disabled/conflicting claimant supplies {role}")
      except AttributeError:
        pass  # P37 already reports the missing API above.
    p = Params({"RequiredOn": True})
    with patch.object(plugins, "PLUGINS", (replacement, disabled)), patch.object(offroad, "PLUGINS", (replacement, disabled)):
      if offroad.switch_plugin(replacement, False, p) != f"required role {role}" or p.writes:
        errors.append(f"P39 disabled fallback permits switching off {role}")

  combined = type("Combined", (Plugin,), {"NAME": "combined", "ENABLE_PARAM": "CombinedOn", "CLAIMS": roles[:2]})
  incoming = type("Incoming", (Plugin,), {"NAME": "incoming", "ENABLE_PARAM": "IncomingOn", "CLAIMS": roles[:1],
                                          "MANIFEST": real[0].MANIFEST})
  p = Params({"CombinedOn": True})
  with patch.object(offroad, "PLUGINS", (combined, incoming)):
    if offroad.switch_plugin(incoming, True, p) != f"required role {roles[1]}" or p.writes:
      errors.append(f"P41 conflict switch drops another required role: {p.writes}")
  return errors


def switch_ui_checks(plugin, p, offroad) -> list[str]:
  """Run both UI callbacks and refresh methods without raylib or a device."""
  errors = []
  for path, name, methods, callback in (("pages/plugins.py", "Switches", ("switch", "refresh"), "switch"),
                                       ("plugins_page.py", "CategoryPage", ("_switch", "show_event"), "_switch")):
    tree = ast.parse((ROOT / "openpilot/tesla/ui" / path).read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == name)
    cls.bases = [ast.Name(id="Base", ctx=ast.Load())]
    cls.body = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in methods]
    tree.body = [cls]
    state, statuses = [], []
    toggle = types.SimpleNamespace(set_state=state.append, set_checked=state.append, set_value=statuses.append)
    status = {"": ""}
    env = {"Base": type("Base", (), {"show_event": lambda self: None}),
           "ui_state": types.SimpleNamespace(is_offroad=lambda: True, params=p), "params": lambda: p,
           **{n: getattr(plugins, n) for n in ("view", "is_enabled", "resolve", "FAULTS_PARAM")},
           "switch_plugin": offroad.switch_plugin, "plugin_status": offroad.plugin_status}
    exec(compile(ast.fix_missing_locations(tree), path, "exec"), env)
    ui = env[name]()
    ui.items, ui.rows, ui.errors = [(plugin, types.SimpleNamespace(action_item=toggle), status)], [(plugin, toggle)], {}
    getattr(ui, callback)(plugin, False)
    shown = status[""] if name == "Switches" else statuses[-1]
    if state[-1] is not True or shown != f"required role {plugin.CLAIMS[0]}":
      errors.append(f"P39 {path}: state={state}, status={shown!r}")
  return errors


def long_target_checks(plugins) -> list[str]:
  from openpilot.cereal import custom
  from openpilot.common.constants import CV
  from openpilot.selfdrive.car.cruise import V_CRUISE_MAX
  Source = custom.LongitudinalPlanSP.LongitudinalPlanSource
  errors = []

  def sm(enabled=True, override=False, gas=False, brake=False):
    cc = types.SimpleNamespace(enabled=enabled, cruiseControl=types.SimpleNamespace(override=override))
    return {"carControl": cc, "carState": types.SimpleNamespace(gasPressed=gas, brakePressed=brake)}

  class T:
    def __init__(self, v, fail=False):
      self.v, self.fail, self.calls = v, fail, 0

    def update(self, sm, v_ego, v_cruise):
      self.calls += 1
      if self.fail:
        raise RuntimeError("target")
      return self.v

  def run(targets, s, cruise=20.0, sla=40.0):
    base = {Source.cruise: (cruise, 0.0), Source.speedLimitAssist: (sla, 0.0)}
    out = plugins.apply_long_targets(targets, s, dict(base), 0.0, 0.0, cruise)
    return min(out.values(), key=lambda t: t[0])[0], out

  if run([T(15.0)], sm())[0] != 15.0:
    errors.append("P13 lower plugin target ignored")
  if run([T(25.0)], sm())[0] != 25.0:
    errors.append("P14 raise ignored while engaged")
  for label, s in (("disengaged", sm(enabled=False)), ("override", sm(override=True)), ("gas", sm(gas=True)), ("brake", sm(brake=True))):
    if run([T(25.0)], s)[0] != 20.0:
      errors.append(f"P15 raise applied while {label}")
  if run([T(500.0)], sm(), sla=1000.0)[0] > V_CRUISE_MAX * CV.KPH_TO_MS + 1e-6:
    errors.append("P16 raise above V_CRUISE_MAX")
  if run([T(50.0)], sm(), sla=30.0)[0] != 30.0:
    errors.append("P17 raise beat speed limit assist")
  if run([T(25.0), T(28.0)], sm())[0] != 25.0:
    errors.append("P18 higher of two raises won")
  bad = T(25.0, fail=True)
  faults = []
  plugins.record_fault = lambda name, where, exc: faults.append(where)
  targets = [bad, T(None)]
  try:
    for _ in range(2):
      v, _ = run(targets, sm())
      if v != 20.0:
        errors.append(f"P19 failing/None target changed speed to {v}")
  except Exception as e:
    errors.append(f"P19 apply_long_targets raised {e!r}")
  if bad.calls != 1 or not faults:
    errors.append(f"P19 failing target called {bad.calls}x, faults {faults}")

  class Off(Plugin):
    NAME, ENABLE_PARAM, CLAIMS = "off", "OffKey", ("long.target",)

    @classmethod
    def long_target(cls, CP, params):
      return T(1.0)
  plugins.PLUGINS = (Off,)
  if plugins.long_targets(CP, FakeParams()):
    errors.append("P20 disabled plugin contributes a long target")
  return errors


def source_checks(plugins, real) -> list[str]:
  errors, faults = [], []
  plugins.record_fault = lambda name, where, exc: faults.append(where)

  class Radar:
    def __init__(self, out, fail=False):
      self.out, self.fail, self.calls = out, fail, 0

    def update(self, can_list):
      self.calls += 1
      if self.fail:
        raise RuntimeError("radar")
      return self.out

  def plugin(name, claims, hook, obj, enable=None):
    return type(name, (Plugin,), {"NAME": name, "ENABLE_PARAM": enable, "CLAIMS": claims, hook: classmethod(lambda cls, *a: obj)})

  port = Radar("port")
  plugins.PLUGINS = (plugin("radar", ("source:radar",), "radar_interface", Radar("plugin")),)
  if plugins.radar_interface(CP, None, FakeParams(), port).update([]) != "plugin":
    errors.append("P21 active radar plugin did not replace the port's radar")
  plugins.PLUGINS = (plugin("radar", (), "radar_interface", Radar("plugin")),)
  if plugins.radar_interface(CP, None, FakeParams(), port) is not port:
    errors.append("P21 radar plugin without source:radar replaced the port's radar")
  bad = Radar("plugin", fail=True)
  plugins.PLUGINS = (plugin("radar", ("source:radar",), "radar_interface", bad),)
  ri = plugins.radar_interface(CP, None, FakeParams(), port)
  try:
    outs = [ri.update([]) for _ in range(3)]
    if outs != ["port"] * 3 or bad.calls != 1 or "card.radar_interface.update" not in faults:
      errors.append(f"P22 failing radar plugin: outputs {outs}, called {bad.calls}x, faults {faults}")
  except Exception as e:
    errors.append(f"P22 failing radar plugin raised {e!r}")

  plugins.PLUGINS = (plugin("map", ("source:map",), "processes", [], enable="MapKey"),)
  if plugins.holds(FakeParams(), "source:map") or not plugins.holds(FakeParams({"MapKey": True}), "source:map"):
    errors.append("P23 holds(source:map) does not follow the map plugin's switch")
  gate = (ROOT / "openpilot/sunnypilot/system/fork_processes.py").read_text()
  if 'for name in ("mapd", "mapd_manager", "map_osm")' not in gate or 'plugins.holds(params, "source:map")' not in gate:
    errors.append("P23 fork_processes.py does not gate SP's OSM processes on source:map")

  faults.clear()
  good, unclaimed, off = types.SimpleNamespace(), types.SimpleNamespace(), types.SimpleNamespace()
  plugins.PLUGINS = (plugin("good", ("external.provider",), "external_provider", good),
                     plugin("unclaimed", (), "external_provider", unclaimed),
                     plugin("off", ("external.provider",), "external_provider", off, enable="OffKey"),
                     type("raising", (Plugin,), {"NAME": "raising", "CLAIMS": ("external.provider",),
                                                 "external_provider": classmethod(lambda cls, p: 1 / 0)}))
  try:
    got = plugins.external_providers(FakeParams())
    if got != [good] or "externald.external_provider" not in faults:
      errors.append(f"P24 external providers {got}, faults {faults}")
  except Exception as e:
    errors.append(f"P24 external_providers raised {e!r}")
  return errors


def msg_checks(plugins) -> list[str]:
  from openpilot.cereal import messaging
  errors, faults, sent = [], [], []
  plugins.record_fault = lambda name, where, exc: faults.append((name, where))
  pm = types.SimpleNamespace(send=lambda svc, msg: sent.append((svc, messaging.log_from_bytes(msg.to_bytes()))))

  class Pub(Plugin):
    NAME, CLAIMS = "pub", ("msg:demo.a", "msg:demo.b")
  if plugins.send_msg(pm, Pub, "demo.c", {"x": 1}) or sent or faults != [("pub", "pluginMsg")]:
    errors.append(f"G1 undeclared kind: sent {len(sent)}, faults {faults}")
  ok = plugins.send_msg(pm, Pub(CP, None), "demo.a", {"x": 1}, 100) and plugins.send_msg(pm, Pub, "demo.b", [2])
  m = sent[0][1].pluginMsg if sent else None
  if not ok or [s for s, _ in sent] != ["pluginMsg"] * 2 or (m.source, m.kind, json.loads(m.payload), m.validForMs) != ("pub", "demo.a", {"x": 1}, 100):
    errors.append(f"G2 declared kind: ok {ok}, sent {[(s, e.pluginMsg) for s, e in sent]}")

  reader = plugins.PluginMsgs(sock=object())
  reader.messaging = types.SimpleNamespace(drain_sock=lambda sock: [e for _, e in sent])
  t = sent[0][1].logMonoTime if sent else 0
  if reader.fresh("demo.a", t + 50_000_000) != {"pub": {"x": 1}} or reader.fresh("demo.a", t + 200_000_000):
    errors.append("G3 demo.a fresh within 100 ms only")
  if reader.fresh("demo.b", t + 10**12) != {"pub": [2]}:
    errors.append("G3 validForMs 0 expired, or kinds mixed")
  faults.clear()

  def fail_send(service, msg):
    raise getattr(messaging, "MultiplePublishersError", RuntimeError)(b"second publisher")

  try:
    if plugins.send_msg(types.SimpleNamespace(send=fail_send), Pub, "demo.a", {}):
      errors.append("P32 failed send returned True")
  except Exception as e:
    errors.append(f"P32 send_msg raised {e!r}")
  if faults != [("pub", "pluginMsg")]:
    errors.append(f"P32 send fault not recorded: {faults}")
  return errors


def registration_checks(plugins) -> list[str]:
  errors, faults, logs = [], [], []
  plugins.record_fault = lambda name, where, exc: faults.append((name, where))
  modules = {
    "openpilot.common.hardware": types.SimpleNamespace(PC=True),
    "openpilot.sunnypilot.hardware.driver_monitoring": types.SimpleNamespace(driver_monitoring_enabled=lambda p: True),
    "openpilot.sunnypilot.hardware.profile": types.SimpleNamespace(
      HardwareProfile=types.SimpleNamespace(C3XL="c3xl"), get_hardware_profile=lambda: "pc",
      has_audio_output=lambda: True, has_microphone=lambda: True),
    "openpilot.system.manager.process": types.SimpleNamespace(
      PythonProcess=lambda name, module, should_run: types.SimpleNamespace(name=name, should_run=should_run)),
  }
  with patch.dict(sys.modules, modules):
    apply = runpy.run_path(str(ROOT / "openpilot/sunnypilot/system/fork_processes.py"))["apply_fork_processes"]

  def official():
    return [types.SimpleNamespace(name=n, should_run=lambda s, p, c: s) for n in (
      "controlsd", "dmonitoringmodeld", "dmonitoringd", "micd", "soundd", "mapd", "mapd_manager", "map_osm",
      "models_manager", "modeld", "modeld_tinygrad", "modeld_warmup")]

  plugins.PLUGINS = (make("bad_proc", "processes"), make("good_proc"))
  try:
    if "good_procd" not in [p.name for p in apply(official(), None)]:
      errors.append("P28 healthy plugin process lost")
  except Exception as e:
    errors.append(f"P28 manager registration raised {e!r}")
  if faults != [("bad_proc", "manager.processes")]:
    errors.append(f"P28 registration fault not recorded: {faults}")

  first = types.SimpleNamespace(name="shared", should_run=lambda s, p, c: s)
  second = types.SimpleNamespace(name="shared", should_run=lambda s, p, c: s)
  rogue = [types.SimpleNamespace(name=n, should_run=lambda s, p, c: s) for n in ("controlsd", "alert_output")]
  plugins.PLUGINS = tuple(type(name, (Plugin,), {"NAME": name, "ENABLE_PARAM": "OffKey",
    "CLAIMS": tuple(f"process:{p.name}" for p in procs), "processes": classmethod(lambda cls, ps=procs: ps)})
    for name, procs in (("rogue", rogue), ("first", [first]), ("second", [second])))
  core = official()
  with patch.object(plugins.cloudlog, "error", side_effect=lambda msg: logs.append(msg)):
    procs = apply(core, None)
  managed = {p.name: p for p in procs}
  if managed["controlsd"] is not core[0] or managed["alert_output"] is rogue[1] or managed.get("shared") is not first or \
     len(procs) != len(managed) or len(logs) != 3:
    errors.append(f"P29 duplicate processes accepted: {[p.name for p in procs]}, logs {logs}")

  role = "role:perception.model"
  model_names = ("models_manager", "modeld", "modeld_tinygrad", "modeld_warmup")
  from openpilot.tesla.plugins.sp_models.plugin import SpModels

  class Replacement(Plugin):
    NAME, ENABLE_PARAM, PRIORITY = "op_models", "OpKey", 100
    CLAIMS = (role, "process:op_modeld")

    @classmethod
    def processes(cls):
      return [types.SimpleNamespace(name="op_modeld", should_run=lambda s, p, c: s)]

  plugins.PLUGINS = (SpModels, Replacement)
  procs = apply(official(), None)
  managed = {p.name: p for p in procs}
  if len(procs) != len(managed) or any(n not in managed for n in model_names):
    errors.append(f"P26 missing or repeated model processes: {[p.name for p in procs]}")
  else:
    for enabled in (False, True, False):
      params = FakeParams({"OpKey": enabled})
      for started in (False, True):
        runs = [managed[n].should_run(started, params, None) for n in model_names]
        if runs != [started and not enabled] * 4 or managed["op_modeld"].should_run(started, params, None) != (started and enabled):
          errors.append(f"P26 model switch on={enabled}, started={started}: {runs}")

  class Selector(Plugin):
    NAME, CLAIMS = "custom_models", (role,)
    selected = model_names

    @classmethod
    def role_processes(cls, claimed_role, procs):
      assert claimed_role == role and tuple(procs) == model_names
      return [procs[n] for n in cls.selected]

  for selected in (model_names, model_names[:2], ()):
    Selector.selected = selected
    plugins.PLUGINS = (SpModels, Selector)
    procs = apply(official(), None)
    got = {p.name for p in procs if p.name in model_names and p.should_run(True, FakeParams(), None)}
    if got != set(selected):
      errors.append(f"P26 arbitrary role owner selected {selected}, running {sorted(got)}")

  plugins.PLUGINS = ()
  procs = apply(official(), None)
  if any(p.should_run(s, FakeParams(), None) for p in procs if p.name in model_names for s in (False, True)):
    errors.append("P33 model processes run without an owner")

  class Unclaimed(Selector):
    NAME, CLAIMS = "unclaimed_models", ()

    @classmethod
    def role_processes(cls, claimed_role, procs):
      calls.append((cls.NAME, "role_processes"))
      return list(procs.values())

  class Foreign(Selector):
    NAME = "foreign_models"

    @classmethod
    def role_processes(cls, claimed_role, procs):
      return [procs["models_manager"], types.SimpleNamespace(**vars(procs["modeld"]))]

  plugins.PLUGINS = (Unclaimed,)
  procs = apply(official(), None)
  if (Unclaimed.NAME, "role_processes") in calls or any(p.should_run(True, FakeParams(), None) for p in procs if p.name in model_names):
    errors.append("P34 role_processes called or processes granted without a role claim")
  faults.clear()
  plugins.PLUGINS = (Foreign,)
  procs = apply(official(), None)
  if any(p.should_run(True, FakeParams(), None) for p in procs if p.name in model_names) or faults != [(Foreign.NAME, "manager.role_processes")]:
    errors.append(f"P34 foreign process accepted or not recorded: {faults}")

  class Raising(Selector):
    NAME = "raising_models"

    @classmethod
    def role_processes(cls, claimed_role, procs):
      raise RuntimeError("role_processes")

  faults.clear()
  plugins.PLUGINS = (Raising, SpModels, make("healthy"))
  try:
    procs = apply(official(), None)
    if "healthyd" not in [p.name for p in procs] or any(p.should_run(True, FakeParams(), None) for p in procs if p.name in model_names):
      errors.append("P35 raising role hook lost healthy processes or kept its role processes")
  except Exception as e:
    errors.append(f"P35 role registration raised {e!r}")
  if faults != [(Raising.NAME, "manager.role_processes")]:
    errors.append(f"P35 role fault not recorded: {faults}")

  rogue = [types.SimpleNamespace(name=n, should_run=lambda s, p, c: True) for n in model_names]
  plugins.PLUGINS = (type("Rogue", (Plugin,), {"NAME": "rogue_models", "CLAIMS": tuple(f"process:{n}" for n in model_names),
    "processes": classmethod(lambda cls: rogue)}),)
  procs = apply(official(), None)
  if any(p is r for p in procs for r in rogue) or plugins.processes():
    errors.append("P36 processes() registered a reserved model process name")
  return errors


def service_checks(plugins) -> list[str]:
  errors = []
  with tempfile.TemporaryDirectory() as tmp:
    for name, services in (("svc_good", ("carState",)), ("svc_bad", ("carState", "unknownPluginService"))):
      path = Path(tmp) / name
      path.mkdir()
      (path / "__init__.py").write_text("")
      (path / "plugin.json").write_text(json.dumps({"id": name, "api": 1, "category": "tools", "use": "both", "entry": "plugin:P"}))
      (path / "plugin.py").write_text(f"from openpilot.tesla.plugin import Plugin\nclass P(Plugin):\n  SERVICES = {services!r}\n")
    with patch.object(plugins.mf, "INSTALL_DIR", Path(tmp)):
      loaded, faults, _ = plugins._discover()
    if "svc_bad" in [p.NAME for p in loaded] or "unknownPluginService" not in faults.get("svc_bad", "") or \
       "svc_good" not in [p.NAME for p in loaded] or "svc_good" in faults:
      errors.append(f"P30 invalid SERVICES discovery: {[p.NAME for p in loaded]}, errors {faults}")

  plugins.PLUGINS = tuple(type(name, (Plugin,), {"NAME": name, "ENABLE_PARAM": enable,
    "CLAIMS": claims, "SERVICES": (svc,)}) for name, enable, claims, svc in (
      ("on", None, ("source:demo",), "carState"), ("off", "OffKey", (), "radarState"),
      ("loser", None, ("source:demo",), "modelV2")))
  plugins.PLUGINS[0].PRIORITY = 1
  for params, expected in ((FakeParams(), ["carState"]),
                           (FakeParams({"OffKey": True}), ["carState", "radarState"]),
                           (object(), ["carState", "modelV2", "radarState"])):
    with patch("openpilot.common.params.Params", return_value=params):
      if (got := plugins.services()) != expected:
        errors.append(f"P31 subscribed {got}, expected {expected}")
  return errors


if __name__ == "__main__":
  sys.exit(main())
