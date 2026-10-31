"""Pyray-free logic of the offroad detail pages (UI v4): view modes, touch mapping, plugin rows and store jobs.
Kept apart from drawing so tools/sp_tesla_e2e/offroad_pages_check.py can test it on a host without raylib."""
import json
import os
import threading
import time

from openpilot.common.basedir import BASEDIR
from openpilot.tesla import store
from openpilot.tesla import manifest as mf
from openpilot.tesla.manifest import CATEGORIES, conflicting
from openpilot.tesla.plugin_params import ParamsView
from openpilot.tesla.plugins import FAULTS_PARAM, LOAD_ERRORS, PLUGINS, REQUIRED_ROLES, PluginMsgs, is_enabled, missing_roles, resolve
from openpilot.system.ui.lib.multilang import tr
from openpilot.system.ui.lib.multilang import tr_noop

MICI = (536, 240)
TITLE_H = 230  # detail page title band: back button, parent name, page title
BACK = (0, 0, 140, TITLE_H)  # back button touch area
CONTENT = (140, TITLE_H, 1920, 1080 - TITLE_H)  # x, y, w, h of a detail page's rows
FALLBACK_VIEW = (CONTENT[0] + (CONTENT[2] - 1608) // 2, CONTENT[1] + (CONTENT[3] - 720) // 2, 1608, 720)  # mici pages: 3x in CONTENT
FAR = 100000.0
NATIVE_VIEW = (FAR, FAR, MICI[0], MICI[1])  # nothing drawn; touches map far outside every mici widget


def view_mode(onboarding: bool, top_is_main: bool, top_is_modal: bool) -> tuple[str, tuple | None]:
  """Offroad (mode, viewport): the mici texture is drawn only for a mici page pushed above main (none of the built-in pages
  do any more); onboarding is the native pages/onboarding.py."""
  if onboarding:
    return "onboarding", None
  if top_is_main or top_is_modal:
    return ("modal" if top_is_modal else "native"), None
  return "mici", FALLBACK_VIEW


def to_logical(view, x: float, y: float) -> tuple[float, float]:
  vx, vy, vw, vh = view or NATIVE_VIEW
  return (x - vx) * MICI[0] / vw, (y - vy) * MICI[1] / vh


def to_screen(view, x: float, y: float) -> tuple[float, float]:
  vx, vy, vw, vh = view or NATIVE_VIEW
  return vx + x * vw / MICI[0], vy + y * vh / MICI[1]


with open(os.path.join(BASEDIR, "openpilot/selfdrive/selfdrived/alerts_offroad.json")) as f:
  OFFROAD_ALERT_KEYS = tuple(json.load(f))

CAR_LIST = os.path.join(BASEDIR, "openpilot/sunnypilot/selfdrive/car/car_list.json")
MODEL_PARAM = "CarPlatformBundle"  # card.py fingerprints as bundle["platform"] when set; unset = auto fingerprint
_plugin_msgs = None

CATEGORY_LABELS = {
  "modes": tr_noop("Driving Modes"),
  "models": tr_noop("Models"),
  "lateral": tr_noop("Lateral Control"),
  "longitudinal": tr_noop("Longitudinal Control"),
  "car_actions": tr_noop("Car Actions (CAN)"),
  "data": tr_noop("Data & System"),
  "alerts": tr_noop("Alerts"),
  "ui": tr_noop("Tiles & Overlays"),
  "language": tr_noop("Languages"),
}


def tesla_models() -> list[tuple[str, dict]]:
  """(name, CarPlatformBundle value) of each Tesla in sunnypilot's car list, in name order (bundle as params_migration writes it)."""
  with open(CAR_LIST) as f:
    cars = json.load(f)
  return sorted((name, {**data, "name": name}) for name, data in cars.items() if data.get("brand") == "tesla")


def plugin_notifications() -> list[tuple[int, str]]:
  global _plugin_msgs
  try:
    if _plugin_msgs is None:
      _plugin_msgs = PluginMsgs()
    messages = _plugin_msgs.fresh("host.notify")
  except Exception:
    return []
  notifications = []
  for source, payload in messages.items():
    if not isinstance(payload, dict):
      continue
    text, severity = payload.get("text"), payload.get("severity", 0)
    if not isinstance(text, str) or type(severity) is not int or not (text := text.strip()[:80]):
      continue
    notifications.append((max(0, min(1, severity)), f"{source}: {text}"))
  return notifications


def offroad_alerts(params) -> list[tuple[int, str]]:
  """(severity, text) of the active offroad alerts (alertmanager.set_offroad_alert), most severe first."""
  alerts = [(1, tr("No {} plugin: driving assistance unavailable").format(role)) for role in missing_roles(params)]
  warnings = ParamsView(params, mf.UPDATE_SPECS).get(mf.UPDATE_COMPAT_PARAM) or {}
  alerts += [(1, tr("System update plugin incompatibility: {}: {}").format(name, reason)) for name, reason in warnings.items()]
  for key in OFFROAD_ALERT_KEYS:
    alert = params.get(key)
    if alert:
      alerts.append((alert.get("severity", 0), alert.get("text", key).replace("%1", alert.get("extra", ""))))
  alerts += plugin_notifications()
  return sorted(alerts, key=lambda a: -a[0])


def default_role_standby(plugin, conflicts) -> bool:
  claims = getattr(plugin, "CLAIMS", ())
  return (plugin.NAME in conflicts and getattr(plugin, "ENABLE_PARAM", "") is None
          and bool(claims) and all(c.startswith("role:") for c in claims))


def plugin_conflict_count(conflicts) -> int:
  standby = {p.NAME for p in PLUGINS if default_role_standby(p, conflicts)}
  return len(conflicts.keys() - standby)


def plugin_status(plugin, faults, conflicts) -> str:
  """"fault" = dropped this boot (FAULTS_PARAM), "conflict" = switched on but lost an exclusive claim."""
  if plugin.NAME in faults:
    return tr("fault")
  if plugin.NAME in conflicts:
    if default_role_standby(plugin, conflicts):
      return tr("inactive")
    return tr("conflict")
  if (mf.INSTALL_DIR / plugin.NAME).is_dir():
    return store.plugin_state(plugin.NAME, plugin.MANIFEST.version)
  return ""


def plugin_sections():
  """(category title, plugins) for each manifest category that has plugins, in CATEGORIES order."""
  return [(CATEGORY_LABELS[c], [p for p in PLUGINS if p.CATEGORY == c]) for c in CATEGORIES if any(p.CATEGORY == c for p in PLUGINS)]


def market_sections(entries) -> list[tuple[str, list, list]]:
  """Plugin market: (category title, plugins, store entries) per manifest category with any, in CATEGORIES order.
  Store entries are index entries not loaded this boot (new, or installed and waiting for a restart); store.fetch_index
  already gave them their claims-derived category (unknown goes to data)."""
  loaded = {p.NAME for p in PLUGINS}
  extra = [e for e in entries if e.id not in loaded]
  sections = []
  for c in CATEGORIES:
    plugins = [p for p in PLUGINS if p.CATEGORY == c]
    found = [e for e in extra if (e.category if e.category in CATEGORIES else "data") == c]
    if plugins or found:
      sections.append((CATEGORY_LABELS[c], plugins, found))
  return sections


def plugin_rows(params) -> list[tuple[str, str, str]]:
  """Rows of the Plugins page: ("failed", name, error), ("plugin", name, status), ("setting", plugin, key)."""
  faults = params.get(FAULTS_PARAM) or {}
  _, conflicts = resolve(params)
  rows = [("failed", name, error) for name, error in LOAD_ERRORS.items()]
  for _, plugins in plugin_sections():
    for p in plugins:
      rows.append(("plugin", p.NAME, plugin_status(p, faults, conflicts)))
      rows += [("setting", p.NAME, s.key) for s in p.SETTINGS]
  return rows


def switch_changes(name, value, items, enabled) -> tuple[dict[str, bool], str | None]:
  """Shared toggle plan for loaded UI plugins and newly installed disk manifests. items: id/priority/claims/key."""
  claims = [(n, priority, resources) for n, priority, resources, _ in items]
  off = set(conflicting(name, claims)) if value else {name}
  remaining = {n for n, _, _, key in items if n in enabled and (n not in off or key is None)}
  if value:
    remaining.add(name)
  for role in REQUIRED_ROLES:
    if any(role in c for n, _, c, _ in items if n in enabled) and not any(role in c for n, _, c, _ in items if n in remaining):
      return {}, tr("required role {}").format(role)
  changes = {key: False for n, _, _, key in items if n in off and key is not None}
  changes.update({key: value for n, _, _, key in items if n == name and key is not None})
  return changes, None


def switch_plugin(plugin, value: bool, params, jobs=None) -> str | None:
  """Switch a plugin; switching on switches off every plugin sharing an exclusive claim. Clears its fault mark.
  Switching on refuses while a plugin named in requires.plugins is off. None on success, else the refusal. params = plugins.view(...); callers check offroad."""
  if jobs is not None and jobs.busy:
    return tr("store busy")
  if value:
    ok, reason = mf.compatible(plugin.MANIFEST, mf.HOST_CAPS)
    if not ok:
      return reason
    needs = plugin.MANIFEST.requires.get("plugins", []) if plugin.MANIFEST else []
    missing = [n for n in needs if not any(p.NAME == n and is_enabled(p, params) for p in PLUGINS)]
    if missing:
      return tr("turn on first: {}").format(", ".join(next((p.TITLE for p in PLUGINS if p.NAME == n), "") or n for n in missing))
  if plugin.ENABLE_PARAM is None:
    return
  changes, error = switch_changes(plugin.NAME, value, [(p.NAME, p.PRIORITY, p.CLAIMS, p.ENABLE_PARAM) for p in PLUGINS],
                                  {p.NAME for p in PLUGINS if is_enabled(p, params)})
  if error:
    return error
  for key, state in changes.items():
    params.put_bool(key, state, block=True)
  faults = params.get(FAULTS_PARAM) or {}
  if faults.pop(plugin.NAME, None) is not None:
    params.put(FAULTS_PARAM, faults, block=True)


def editable(state=None) -> bool:
  """Plugin switches and plugin/vehicle settings: offroad, or started but in P and not engaged (owner 2026-10-28).
  Changes still apply on the next drive. Store, model, software and device actions stay offroad-only. state: ui_state."""
  if state is None:
    from openpilot.selfdrive.ui.ui_state import ui_state as state
  return state.is_offroad() or (str(state.sm['carState'].gearShifter) == 'park' and not state.engaged)


def setting_kind(setting) -> str:
  """text/secret, toggle, stepper (numeric range: -/+), buttons (≤5 choices), or cycle (stepping to the next choice)."""
  if setting.type in ("text", "secret"):
    return setting.type
  if setting.range is None and setting.choices is None:
    return "toggle"
  if setting.range is not None:
    return "stepper"
  from openpilot.tesla.ui.settings_meta import setting_choices
  return "buttons" if len(setting_choices(setting)) <= 5 else "cycle"


def step_value(setting, value, delta: int) -> int:
  """One -/+ step on a range setting, on the step grid from minimum and inside [minimum, maximum].
  A value outside the range or off the grid first snaps back (toward delta); a missing value gives minimum."""
  minimum, maximum, step = setting.range[:3]
  top = minimum + (maximum - minimum) // step * step
  if not isinstance(value, int):
    return minimum
  v = min(max(value, minimum), top)
  q, r = divmod(v - minimum, step)
  if v != value or r:
    return minimum + (q + (1 if delta > 0 and r else 0)) * step
  return min(max(v + (step if delta > 0 else -step), minimum), top)


def next_choice(choices: list[tuple[str, int]], value) -> int:
  values = [v for _, v in choices]
  return values[(values.index(value) + 1) % len(values)] if value in values else values[0]


def install_defaults_later(offroad, params) -> None:
  """First boot: install index defaults once offroad and online, then reboot so the plugins load."""
  def loop():
    while not store.defaults_done():
      if offroad() and store.install_defaults() and offroad():
        params.put_bool("DoReboot", True)
      time.sleep(30)
  if not store.defaults_done():
    threading.Thread(target=loop, daemon=True).start()


class StoreJobs:
  """Plugin store state for the Store page: network and disk work in one background thread at a time.
  offroad: callable; install/remove only while it returns True."""

  def __init__(self, offroad):
    self.offroad = offroad
    self.busy = False
    self.status = tr("tap Refresh")
    self.failed = False  # last fetch returned an error (status holds it)
    self.entries: list[store.IndexEntry] = []
    self.installed: dict[str, str] = store.installed_versions()
    self.dirty = False  # set after a fetch; the UI thread rebuilds its rows and clears it
    self.states = {}

  def run(self, job, wait: bool = False) -> None:
    if self.busy:
      return
    self.busy = True
    t = threading.Thread(target=self._job, args=(job,), daemon=True)
    t.start()
    if wait:
      t.join()

  def _job(self, job) -> None:
    try:
      job()
    except Exception as e:
      self.status = tr("error: {}").format(e)[:60]
    finally:
      self.installed = store.installed_versions()
      self.states.clear()
      self.busy = False

  def fetch(self) -> None:
    self.status = tr("loading...")
    entries, error = store.fetch_index()
    self.entries, self.status, self.dirty = entries, error or tr("{} plugins").format(len(entries)), True
    self.failed = bool(error)

  def current(self, pid: str) -> store.IndexEntry | None:
    """The entry from the latest fetch (rows outlive refreshes; a dropped entry gives None)."""
    return next((e for e in self.entries if e.id == pid), None)

  def act(self, pid: str) -> None:
    """Install, update or remove pid; a downloaded plugin missing from the index (or before a fetch) is removed."""
    entry = self.current(pid)
    have = self.installed.get(pid)
    if entry is None and have is None:
      return
    if not self.offroad():
      self.status = tr("park first")
      return
    if have is not None and (entry is None or have == entry.version or not entry.installable):
      error = store.uninstall(pid)
      self.status = f"{pid}: {error}"[:60] if error else tr("removed {}: restart to apply").format(pid)
    elif entry.installable:
      self.status = tr("installing {}...").format(entry.id)
      error = store.install(entry, activate=have is None, offroad=self.offroad)
      self.status = f"{entry.id}: {error}"[:60] if error else (
        tr("enabled {}: restarting").format(entry.id) if have is None else tr("installed {}: restart to apply").format(entry.id)
      )

  def can_act(self, pid: str) -> bool:
    return not self.busy and self.offroad() and (pid in self.installed or getattr(self.current(pid), "installable", False))

  def action(self, entry: store.IndexEntry | None, pid: str = "") -> str:
    """Button text of an entry row; entry None: a downloaded plugin pid missing from the index."""
    if entry is None:
      return tr("REMOVE") if pid in self.installed else tr("N/A")
    have = self.installed.get(entry.id)
    if have is not None and (have == entry.version or not entry.installable):
      return tr("REMOVE")
    if not entry.installable:
      return tr("N/A")
    return tr("UPDATE") if have else tr("INSTALL & ENABLE")

  def row_value(self, entry: store.IndexEntry) -> str:
    have = self.installed.get(entry.id)
    if not entry.installable:
      return tr("needs api {}").format(entry.api) if have is None else tr("v{} installed").format(have)
    if have == entry.version:
      key = (entry.id, have)
      cached = self.states.get(key)
      now = time.monotonic()
      if cached is None or now - cached[0] >= 1:
        cached = self.states[key] = (now, store.plugin_state(entry.id, have))
      return tr("v{} {}").format(have, cached[1])
    return tr("v{} -> v{}").format(have, entry.version) if have else tr("v{}").format(entry.version)
