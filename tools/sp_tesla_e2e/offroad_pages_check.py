#!/usr/bin/env python3
"""Offroad pages check (big-screen UI v4 detail pages): the pyray-free logic behind the native offroad pages
(openpilot/tesla/ui/offroad.py) and the static shape of openpilot/tesla/ui/pages/ and the settings tiles. Drawing is not checked here (tools/sp_tesla_e2e/big_screen_e2e.py on the device).

Failure cases listed before implementation:
- O1 a settings tile (ui/slots/settings.py, ui/slots/models.py) has no PAGE, its PAGE is not imported from a pages/<name>.py whose class defines
     show_event, a tile still sets MICI (no settings tile opens a mici layout), or the Store sub-page has no such module
- O2 a page reads or writes a param key (string literal in get/get_bool/put/put_bool/remove) the compiled Params does not know
- O3 the Plugins rows miss a PLUGINS entry or one of its settings, or a plugin that failed to load is not listed first
- O4 switching a plugin on leaves a plugin with a shared exclusive claim on, switches off one without a shared claim,
     or leaves its fault mark
- O5 a store row shows the wrong action/value for an index entry, a row acts while busy or onroad, or install/remove runs onroad,
     or a downloaded plugin missing from the index cannot be removed
- O12 the plugin market misses a PLUGINS entry or a not-loaded index entry, lists a loaded plugin twice, puts one in the wrong
     category, or breaks CATEGORIES order
- O13 the Tesla car model list is empty, names a platform opendbc does not know, or a bundle lacks name/platform
     (card.py fingerprints as CarPlatformBundle["platform"])
- O6 offroad the mici texture gets a viewport outside mici widgets pushed above main (onboarding and Device sub-pages are
     native now), or loses it for such a widget
- O7 offroad without a viewport a screen touch maps inside the mici logical screen (a hidden mici widget would react)
- O8 offroad alerts are not most severe first, miss the %1 extra, or show inactive alerts
- O9 the v2 pieces (PAGES factories, _hide_back) remain in screen.py, or the application.py no-viewport hook is missing
- O10 the detail page content rect overlaps the back button or the title band or leaves the screen, or the mici fallback
     view leaves the content rect
- O11 a tesla UI module uses a pyray type in a | union annotation without from __future__ import annotations
     (pyray types are functions on the device: TypeError at import, UI exits)
- O14 non-printable/non-ASCII or overlong text/secret input is accepted; valid empty/space/boundary input is lost
- O15 secret label leaks content/length, empty secret lacks Not set, or new paths contain logging/fault output
- O16 either settings page omits the text editor or its offroad entry/commit gates (GPU case 28 exercises both)
- O17 existing BOOL/range/choices row kinds or labels change
"""
import ast
import json
import re
from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "opendbc_repo")]
PKG = ROOT / "openpilot"


class FakeParams:
  def __init__(self, values=None):
    self.values = dict(values or {})

  def get(self, key, block=False, return_default=False):
    return self.values.get(key)

  def get_bool(self, key, block=False):
    return bool(self.values.get(key, False))

  def put(self, key, value, block=False):
    self.values[key] = value

  def put_bool(self, key, value, block=False):
    self.values[key] = bool(value)

  def remove(self, key):
    self.values.pop(key, None)


try:
  import openpilot.common.params  # noqa: F401
except Exception:
  sys.modules["openpilot.common.params"] = types.SimpleNamespace(Params=FakeParams, UnknownKeyName=KeyError)

from openpilot.tesla import manifest as mf, store
from openpilot.tesla.plugins import FAULTS_PARAM, LOAD_ERRORS, PLUGINS
from openpilot.tesla.ui import offroad

PAGE_MODULES = {"Home": "home", "Toggles": "toggles", "Plugins": "plugins", "Tesla": "tesla", "Network": "network",
                "Device": "device", "Software": "software", "Developer": "developer", "Training": "training", "Terms": "terms",
                "Regulatory": "regulatory", "Driver camera": "driver_camera", "Onboarding": "onboarding", "Models": "models"}
PARAM_CALLS = {"get", "get_bool", "put", "put_bool", "remove"}


def check_tiles(errors: list) -> None:
  pages = {f"openpilot.tesla.ui.pages.{m}" for m in PAGE_MODULES.values()}
  tiles = []
  for f in ("tesla/ui/slots/settings.py", "tesla/ui/slots/models.py"):
    tree = ast.parse((PKG / f).read_text())
    imported = {a.asname or a.name: n.module for n in tree.body if isinstance(n, ast.ImportFrom) for a in n.names}
    tiles += [(n, imported) for n in tree.body if isinstance(n, ast.ClassDef) and any(isinstance(b, ast.Assign) and b.targets[0].id == "SPEC" for b in n.body
                                                                                    if isinstance(b, ast.Assign) and isinstance(b.targets[0], ast.Name))]
  if len(tiles) < 9:
    errors.append(f"O1 only {len(tiles)} settings tiles")
  for cls, imported in tiles:
    attrs = {b.targets[0].id: b.value for b in cls.body if isinstance(b, ast.Assign) and isinstance(b.targets[0], ast.Name)}
    page = attrs.get("PAGE")
    if not isinstance(page, ast.Name):
      errors.append(f"O1 {cls.name} has no PAGE")
    elif "MICI" in attrs or imported.get(page.id) not in pages:
      errors.append(f"O1 {cls.name}: PAGE {page.id} from {imported.get(page.id)}{', MICI set' if 'MICI' in attrs else ''}")


def check_pages(errors: list) -> None:
  known = mf.compiled_param_keys()
  for label, module in PAGE_MODULES.items():
    path = PKG / "tesla/ui/pages" / f"{module}.py"
    if not path.exists():
      errors.append(f"O1 {label}: {path.relative_to(ROOT)} missing")
      continue
    tree = ast.parse(path.read_text())
    if not any(isinstance(n, ast.ClassDef) and any(isinstance(f, ast.FunctionDef) and f.name == "show_event" for f in n.body)
               for n in tree.body):
      errors.append(f"O1 {label}: no class with show_event in {path.name}")
    for node in ast.walk(tree):
      if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in PARAM_CALLS and node.args
          and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
        key = node.args[0].value
        if key[:1].isupper() and key not in known:
          errors.append(f"O2 {path.name}:{node.lineno} param {key} unknown to the compiled Params")


def check_plugins(errors: list) -> None:
  LOAD_ERRORS["broken_check_plugin"] = "SyntaxError"
  try:
    rows = offroad.plugin_rows(FakeParams())
  finally:
    LOAD_ERRORS.pop("broken_check_plugin")
  if not rows or rows[0][:2] != ("failed", "broken_check_plugin"):
    errors.append(f"O3 failed plugins not listed first: {rows[:1]}")
  kinds = [kind for kind, _, _ in rows]
  if "failed" in kinds[kinds.index("plugin") if "plugin" in kinds else 0:]:
    errors.append("O3 a failed plugin row after plugin rows")
  for p in PLUGINS:
    if ("plugin", p.NAME) not in {r[:2] for r in rows}:
      errors.append(f"O3 plugin {p.NAME} has no row")
    for s in p.SETTINGS:
      if ("setting", p.NAME, s.key) not in rows:
        errors.append(f"O3 setting {p.NAME}.{s.key} has no row")
  for p in PLUGINS:
    for s in p.SETTINGS:
      kind = offroad.setting_kind(s)
      if kind not in ("text", "secret", "toggle", "buttons", "cycle") or (kind == "toggle") != (s.type is None and s.range is None and s.choices is None):
        errors.append(f"O3 setting {s.key} kind {kind}")
  if offroad.next_choice([("a", 1), ("b", 2)], 2) != 1 or offroad.next_choice([("a", 1), ("b", 2)], 9) != 1:
    errors.append("O3 next_choice does not wrap or recover from an unknown value")

  def fake(name, claims, param):
    return type(name, (), {"NAME": name, "PRIORITY": 0, "CLAIMS": claims, "ENABLE_PARAM": param, "MANIFEST": PLUGINS[0].MANIFEST})
  a, b, c, d = fake("a", ["x.claim"], "ExtAOn"), fake("b", ["x.claim"], "ExtBOn"), fake("c", ["y.claim"], "ExtCOn"), fake("d", ["x.claim"], None)
  saved = offroad.PLUGINS
  offroad.PLUGINS = (a, b, c, d)
  try:
    params = FakeParams({"ExtBOn": True, "ExtCOn": True, FAULTS_PARAM: {"a": "boom", "c": "old"}})
    offroad.switch_plugin(a, True, params)
  finally:
    offroad.PLUGINS = saved
  v = params.values
  if not v.get("ExtAOn") or v.get("ExtBOn") or not v.get("ExtCOn"):
    errors.append(f"O4 switching a on: {v}")
  if "a" in v[FAULTS_PARAM] or "c" not in v[FAULTS_PARAM]:
    errors.append(f"O4 fault marks after switching a on: {v[FAULTS_PARAM]}")


def check_store(errors: list) -> None:
  calls = []
  entry = lambda pid, version="1.0.0", installable=True: store.IndexEntry(pid, pid, version, mf.API_VERSION, "tools", "t", "d", "u",  # noqa: E731
                                                                          "0" * 64, None, installable)
  saved = store.installed_versions, store.install, store.uninstall
  store.installed_versions = lambda: {"same": "1.0.0", "old": "0.9.0", "gone": "1.0.0"}
  store.install = lambda e, **kw: calls.append(("install", e.id, kw.get("activate", False)))
  store.uninstall = lambda pid: calls.append(("uninstall", pid))
  try:
    offroad_now = [True]
    jobs = offroad.StoreJobs(lambda: offroad_now[0])
    jobs.entries = [entry("new"), entry("same"), entry("old"), entry("api", installable=False), entry("gone", installable=False)]
    want = {"new": ("INSTALL & ENABLE", "v1.0.0", True), "same": ("REMOVE", "v1.0.0 Installed", True), "old": ("UPDATE", "v0.9.0 -> v1.0.0", True),
            "api": ("N/A", f"needs api {mf.API_VERSION}", False), "gone": ("REMOVE", "v1.0.0 installed", True)}
    for e in jobs.entries:
      got = (jobs.action(e), jobs.row_value(e), jobs.can_act(e.id))
      if got != want[e.id]:
        errors.append(f"O5 store row {e.id}: {got} != {want[e.id]}")
    jobs.busy = True
    if jobs.can_act("new"):
      errors.append("O5 a row can act while a job runs")
    jobs.busy = False
    offroad_now[0] = False
    if jobs.can_act("new"):
      errors.append("O5 a row can act onroad")
    jobs.run(lambda: jobs.act("new"), wait=True)
    if calls:
      errors.append(f"O5 store work onroad: {calls}")
    offroad_now[0] = True
    jobs.run(lambda: jobs.act("new"), wait=True)
    jobs.run(lambda: jobs.act("same"), wait=True)
    jobs.run(lambda: jobs.act("old"), wait=True)
    jobs.run(lambda: jobs.act("api"), wait=True)
    if calls != [("install", "new", True), ("uninstall", "same"), ("install", "old", False)]:
      errors.append(f"O5 store actions: {calls}")
    jobs.entries = []
    if jobs.action(None, "old") != "REMOVE" or jobs.action(None, "nope") != "N/A" or not jobs.can_act("old"):
      errors.append("O5 a downloaded plugin missing from the index has no REMOVE")
    jobs.run(lambda: jobs.act("old"), wait=True)
    jobs.run(lambda: jobs.act("nope"), wait=True)
    if calls[3:] != [("uninstall", "old")]:
      errors.append(f"O5 remove without an index entry: {calls[3:]}")
  finally:
    store.installed_versions, store.install, store.uninstall = saved


def check_market(errors: list) -> None:
  entry = lambda pid, cat: store.IndexEntry(pid, pid, "1.0.0", mf.API_VERSION, cat, "t", "d", "u", "0" * 64, None, True)  # noqa: E731
  loaded = PLUGINS[0].NAME
  sections = offroad.market_sections([entry(loaded, "tools"), entry("ext_new", "alerts"), entry("ext_odd", "nonsense")])
  titles = [t for t, _, _ in sections]
  if titles != [t for t in mf.CATEGORIES.values() if t in titles]:
    errors.append(f"O12 market categories out of order: {titles}")
  plugins = [(t, p.NAME) for t, ps, _ in sections for p in ps]
  if sorted(n for _, n in plugins) != sorted(p.NAME for p in PLUGINS):
    errors.append("O12 market plugins differ from PLUGINS")
  if any(t != mf.CATEGORIES[p.CATEGORY] for (t, _), p in zip(plugins, [p for _, ps, _ in sections for p in ps], strict=True)):
    errors.append("O12 a plugin in the wrong market category")
  found = {e.id: t for t, _, es in sections for e in es}
  if found != {"ext_new": mf.CATEGORIES["alerts"], "ext_odd": mf.CATEGORIES["tools"]}:
    errors.append(f"O12 market store entries: {found}")


def check_models(errors: list) -> None:
  from opendbc.car.tesla.values import CAR
  models = offroad.tesla_models()
  if not models:
    errors.append("O13 no Tesla car models")
  for name, bundle in models:
    if bundle.get("name") != name or bundle.get("platform") not in {c.value for c in CAR}:
      errors.append(f"O13 car model bundle {bundle}")


def check_view(errors: list) -> None:
  for onboarding in (False, True):
    for main in (False, True):
      for modal in (False, True):
        mode, view = offroad.view_mode(onboarding, main, modal)
        drawn = not onboarding and not (main or modal)
        if (view is not None) != drawn:
          errors.append(f"O6 view_mode(onboarding={onboarding}, main={main}, modal={modal}) = {mode}, {view}")
  for view in (offroad.FALLBACK_VIEW,):
    x, y = offroad.to_logical(view, *offroad.to_screen(view, 100, 50))
    if abs(x - 100) > 1e-6 or abs(y - 50) > 1e-6:
      errors.append(f"O6 to_logical/to_screen do not round trip for {view}")
  for sx in (0, 359, 360, 1080, 2159):
    for sy in (0, 149, 150, 540, 1079):
      x, y = offroad.to_logical(None, sx, sy)
      if 0 <= x <= offroad.MICI[0] and 0 <= y <= offroad.MICI[1]:
        errors.append(f"O7 screen ({sx}, {sy}) maps inside mici: ({x:.0f}, {y:.0f})")


def check_alerts(errors: list) -> None:
  keys = offroad.OFFROAD_ALERT_KEYS
  if len(keys) < 2:
    errors.append(f"O8 too few offroad alert keys: {keys}")
    return
  params = FakeParams({keys[0]: {"text": "low %1", "severity": 0, "extra": "X"}, keys[1]: {"text": "bad", "severity": 1}})
  if offroad.offroad_alerts(params) != [(1, "bad"), (0, "low X")]:
    errors.append(f"O8 offroad alerts: {offroad.offroad_alerts(params)}")
  if offroad.offroad_alerts(FakeParams()):
    errors.append("O8 alerts shown with no alert param set")


def check_sources(errors: list) -> None:
  screen = (PKG / "tesla/ui/screen.py").read_text()
  for name in ("PAGES", "_hide_back"):
    if name in screen:
      errors.append(f"O9 screen.py still has {name}")
  if "if texture and dst_rect is not None:" not in (PKG / "system/ui/lib/application.py").read_text():
    errors.append("O9 application.py no-viewport hook missing")
  x, y, w, h = offroad.CONTENT
  bx, by, bw, bh = offroad.BACK
  if x < bx + bw or y < offroad.TITLE_H or y < by + bh or x + w > 2160 or y + h > 1080:
    errors.append(f"O10 content rect {(x, y, w, h)}")
  fx, fy, fw, fh = offroad.FALLBACK_VIEW
  if fx < x or fy < y or fx + fw > x + w or fy + fh > y + h:
    errors.append(f"O10 fallback view {offroad.FALLBACK_VIEW} outside the content rect")
  for path in sorted((PKG / "tesla").rglob("*.py")):
    text = path.read_text()
    if re.search(r"rl\.[A-Z]\w* *\||\| *rl\.[A-Z]", text) and "from __future__ import annotations" not in text:
      errors.append(f"O11 {path.relative_to(ROOT)}: rl type in a | union without from __future__ import annotations")


def check_text_settings(errors: list) -> None:
  from openpilot.tesla.ui.settings_meta import setting_label
  from types import SimpleNamespace
  for kind in ("text", "secret"):
    setting = SimpleNamespace(key="Value", label="Value", type=kind, max_len=64, range=None, choices=None)
    if offroad.setting_kind(setting) != kind:
      errors.append("O16 text/secret row falls through to toggle")
    if kind == "secret":
      if [setting_label(setting, v) for v in (None, "", "a", "longer-value")] != ["Not set", "Not set", "******", "******"]:
        errors.append("O15 secret label reveals value/length or empty label wrong")
    elif setting_label(setting, None) != "Not set" or setting_label(setting, "hello") != "hello":
      errors.append("O14 text label wrong")
    try:
      real = mf.Setting("Value", "Value", type=kind, max_len=64)
    except TypeError:
      errors.append("O14 missing input validation")
      continue
    for value in ("\x00", "\n", "\t", "\x1f", "\x7f", "é", "a" * 65):
      if not real.input_error(value):
        errors.append("O14 invalid input accepted")
    for value in ("", " ", " !~", "a" * 64):
      if real.input_error(value):
        errors.append("O14 valid input refused")
  paths = ["tesla/ui/setting_keyboard.py", "tesla/ui/settings_page.py", "tesla/ui/pages/base.py", "tesla/ui/settings_meta.py"]
  tree = ast.parse((PKG / "tesla/manifest.py").read_text())
  setting_source = "\n".join(ast.get_source_segment((PKG / "tesla/manifest.py").read_text(), n) for n in tree.body
                             if isinstance(n, (ast.ClassDef, ast.FunctionDef)) and n.name in ("Setting", "_setting"))
  if re.search(r"\b(cloudlog|print|logging|record_fault)\b", setting_source):
    errors.append("O15 logging/fault output in manifest Setting")
  for name in paths:
    path = PKG / name
    if not path.exists():
      errors.append("O16 shared keyboard missing")
      continue
    if re.search(r"\b(cloudlog|print|logging|record_fault)\b", path.read_text()):
      errors.append("O15 logging/fault output in setting paths: " + name)
  for name in ("tesla/ui/settings_page.py", "tesla/ui/pages/base.py"):
    if "edit_text_setting(" not in (PKG / name).read_text():
      errors.append("O16 settings page missing shared editor: " + name)
  for setting, kind, label, value in ((mf.Setting("b", "Bool"), "toggle", "True", True),
      (mf.Setting("r", "Range", (0, 10, 1, "%", 1)), "cycle", "5%", 5),
      (mf.Setting("c", "Choice", choices=(("A", 1), ("B", 2))), "buttons", "B", 2)):
    if offroad.setting_kind(setting) != kind or setting_label(setting, value) != label:
      errors.append("O17 legacy row changed")


def main() -> int:
  errors: list[str] = []
  for check in (check_tiles, check_pages, check_plugins, check_store, check_market, check_models, check_view, check_alerts, check_sources, check_text_settings):
    try:
      check(errors)
    except Exception as e:
      errors.append(f"{check.__name__} raised {e!r}")
  print(json.dumps({"errors": errors}, indent=1))
  return 1 if errors else 0


if __name__ == "__main__":
  sys.exit(main())
