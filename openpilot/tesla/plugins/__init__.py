"""xpilot plugins, found from each plugin.json, and the small hosts SP processes call.

- Built-in plugins: openpilot/tesla/plugins/<id>/. Downloaded plugins (store.py): manifest.INSTALL_DIR/<id>/, imported
  as package xpilot_ext.<id> (use relative imports inside it); one whose id or param keys clash is skipped.
- Params handed to plugins, enable checks and settings go through plugin_params.ParamsView, so downloaded plugins'
  keys (not compiled into Params) work with the usual API.

- Active plugins: enabled (ENABLE_PARAM) and winning every exclusive claim (manifest.resolve: lower priority, then id).
- Claims are enforced: CAN sends outside the plugin's can.tx claims, or long_target / plan_filter / processes without
  their claim, drop the plugin (recorded like a fault).
- Fault isolation: a plugin that raises in any hook is logged, recorded in Params XpilotPluginFaults (shown on the
  Plugins page) and dropped for the rest of the process; the other plugins and SP carry on.
"""
import hashlib
import importlib
import importlib.util
import json
import math
import os
import socket
import sys
import time
import types

from openpilot.cereal.services import SERVICE_LIST
from openpilot.common.swaglog import cloudlog
from openpilot.tesla import manifest as mf, panda_rules
from openpilot.tesla.plugin import Plugin
from openpilot.tesla.plugin_params import ParamsView

FAULTS_PARAM = "XpilotPluginFaults"
REQUIRED_ROLES = ("role:perception.model", "role:engagement.state", "role:safety.fallback")
ROLE_PROCESSES = {"role:perception.model": ("models_manager", "modeld", "modeld_tinygrad", "modeld_warmup")}


def _import_installed(m, module: str):
  """Import INSTALL_DIR/<id> as package xpilot_ext.<id> and return its entry module."""
  pkg = f"xpilot_ext.{m.id}"
  if "xpilot_ext" not in sys.modules:
    ns = types.ModuleType("xpilot_ext")
    ns.__path__ = []
    sys.modules["xpilot_ext"] = ns
  for stale in [k for k in sys.modules if k == pkg or k.startswith(pkg + ".")]:
    del sys.modules[stale]
  path = mf.INSTALL_DIR / m.id
  spec = importlib.util.spec_from_file_location(pkg, path / "__init__.py", submodule_search_locations=[str(path)])
  mod = importlib.util.module_from_spec(spec)
  sys.modules[pkg] = mod
  spec.loader.exec_module(mod)
  return importlib.import_module(f"{pkg}.{module}")


def _discover() -> tuple[tuple, dict[str, str], dict[str, dict]]:
  builtin, errors = mf.load_all()
  installed, installed_errors = mf.load_all(mf.INSTALL_DIR)
  errors.update(installed_errors)
  taken_ids, taken_keys = {m.id for m in builtin}, mf.compiled_param_keys()
  manifests, specs = list(builtin), {}
  for m in installed:
    clash = m.id in taken_ids or (set(m.params) & (taken_keys | set(specs)))
    if clash:
      errors[m.id] = f"installed plugin clashes with an existing id or param: {clash}"
      continue
    manifests.append(m)
    specs.update(m.params)
  order = list(mf.CATEGORIES)
  manifests.sort(key=lambda m: (order.index(m.category), m.priority, m.id))
  installed_ids = {m.id for m in installed}
  classes = []
  for m in manifests:
    ok, reason = mf.compatible(m, mf.HOST_CAPS)
    if not ok:
      errors[m.id] = reason
      continue
    module, name = m.entry.split(":")
    try:
      root = (mf.INSTALL_DIR if m.id in installed_ids and m not in builtin else mf.PLUGINS_DIR) / m.id
      digest = hashlib.sha256()
      for path in sorted(root.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
          data = path.read_bytes()
          digest.update(path.relative_to(root).as_posix().encode() + b"\0" + len(data).to_bytes(8, "big") + data)
      mod = _import_installed(m, module) if m.id in installed_ids and m not in builtin else \
            importlib.import_module(f"{__name__}.{m.id}.{module}")
      cls = getattr(mod, name)
      if not (isinstance(cls, type) and issubclass(cls, Plugin)):
        raise TypeError(f"{m.entry} is not a Plugin subclass")
      if unknown := set(cls.SERVICES) - SERVICE_LIST.keys():
        raise ValueError(f"unknown SERVICES: {sorted(unknown)}")
    except Exception as e:
      cloudlog.exception(f"xpilot plugin {m.id} failed to import")
      errors[m.id] = f"import: {e!r}"[:200]
      continue
    cls.NAME, cls.TITLE, cls.DESCRIPTION, cls.CATEGORY = m.id, m.name, m.description, m.category
    cls.PRIORITY, cls.ENABLE_PARAM, cls.CLAIMS, cls.SETTINGS, cls.MANIFEST = m.priority, m.enable_param, m.claims, m.settings, m
    cls.SHA256 = digest.hexdigest()
    classes.append(cls)
  return tuple(classes), errors, specs


PLUGINS, LOAD_ERRORS, INSTALLED_SPECS = _discover()  # PLUGINS in (category, priority, id) order


def view(params):
  """params with downloaded plugins' keys routed to their store (idempotent)."""
  return params if isinstance(params, ParamsView) or params is None else ParamsView(params, INSTALLED_SPECS)


def ui_module(cls) -> str:
  """Module path of a plugin's ui.py: store plugins live under xpilot_ext, built-in ones here."""
  pkg = "xpilot_ext" if cls.__module__.startswith("xpilot_ext.") else __name__
  return f"{pkg}.{cls.NAME}.ui"


def is_enabled(cls, params) -> bool:
  return cls.ENABLE_PARAM is None or view(params).get_bool(cls.ENABLE_PARAM)


def active(params) -> list:
  """Enabled plugins that win their exclusive claims, in PLUGINS order."""
  names, _ = resolve(params)
  return [cls for cls in PLUGINS if cls.NAME in names]


def resolve(params) -> tuple[set[str], dict[str, str]]:
  """(active names, {losing name: winning name}) for the current enable switches."""
  return mf.resolve([(c.NAME, c.PRIORITY, c.CLAIMS) for c in PLUGINS], {c.NAME for c in PLUGINS if is_enabled(c, params)})


def log_snapshot(params) -> None:
  """card start: freeze the enabled winners and the compiled rule table identity for this drive."""
  from openpilot.common.version import get_build_metadata
  snapshot = {"system_commit": get_build_metadata().openpilot.git_commit,
              "plugins": [{"id": p.NAME, "version": p.MANIFEST.version, "sha256": p.SHA256} for p in active(params)]}
  if "XpilotPandaRules" in mf.compiled_param_keys() and (rules := params.get("XpilotPandaRules")) is not None:
    snapshot["rule_table_crc32"] = int.from_bytes(rules[-4:], "little")
  cloudlog.event("xpilot.drive_snapshot", **snapshot)


def record_fault(name: str, where: str, exc: Exception) -> None:
  cloudlog.exception(f"xpilot plugin {name} failed in {where}, disabled")
  try:
    from openpilot.common.params import Params
    params = Params()
    if where not in ("card.__init__", "card.setup", "card.rules") and any(p.NAME == name and p.MANIFEST.rules for p in PLUGINS):
      params.put_bool("XpilotPandaRulesFault", True, block=True)
    faults = params.get(FAULTS_PARAM) or {}
    faults[name] = f"{where}: {exc!r}"[:200]
    params.put(FAULTS_PARAM, faults)
  except Exception:
    cloudlog.exception("could not record plugin fault")


class CarPlugins:
  """card host: forwards each hook to every live plugin, in PLUGINS order."""

  def __init__(self, CP, params):
    self.plugins = []
    self.params = params
    self._gate = None
    if CP.brand != "tesla":
      return
    from openpilot.tesla.core_host import CardGate
    self._gate = CardGate(params)
    params = view(params)
    for cls in active(params):
      try:
        self.plugins.append(cls(CP, params))
      except Exception as e:
        record_fault(cls.NAME, "card.__init__", e)

  def _each(self, hook: str, *args) -> list:
    out = []
    for p in list(self.plugins):
      try:
        out.append(getattr(p, hook)(*args))
      except Exception as e:
        self.plugins.remove(p)
        record_fault(p.NAME, f"card.{hook}", e)
    return out

  def setup(self, CP_SP) -> None:
    self._each("setup", CP_SP)
    rules = []
    for p in list(self.plugins):
      try:
        own = p.MANIFEST.rules
        panda_rules.encode_rules(rules + own)
      except ValueError as e:
        self.plugins.remove(p)
        record_fault(p.NAME, "card.rules", e)
        continue
      rules += own
    self.params.put("XpilotPandaRules", panda_rules.param(panda_rules.encode_rules(rules)), block=True)

  def _sends(self, hook: str, *args) -> list:
    """Like _each, but every CanData must match one of the plugin's can.tx claims, else the plugin is dropped."""
    out = []
    for p in list(self.plugins):
      try:
        sends = list(getattr(p, hook)(*args))
        allowed = mf.can_tx(p.CLAIMS)
        if bad := [s for s in sends if (s[0], s[2]) not in allowed]:
          raise PermissionError(f"unclaimed CAN send 0x{bad[0][0]:X} bus {bad[0][2]}")
      except Exception as e:
        self.plugins.remove(p)
        record_fault(p.NAME, f"card.{hook}", e)
        continue
      out += sends
    return out

  def observe_can(self, can_list) -> list:
    return self._sends("observe_can", can_list, time.monotonic_ns())

  def publish_state(self, CS_SP) -> None:
    self._each("publish_state", CS_SP)

  def control_sends(self, CS, CC, now_ns: int) -> list:
    return self._sends("control_sends", CS, CC, now_ns)

  def service_params(self, params) -> None:
    self._each("service_params", view(params))

  def core_alive(self) -> bool:
    """False while a replaced engagement or fallback has no live selfdriveState: card then sends nothing."""
    return self._gate is None or self._gate.alive()


def install_core(selfdrive) -> None:
  """selfdrived: replaceable engagement and safety fallback (openpilot/tesla/core_host.py); no-op with SP's defaults."""
  from openpilot.tesla.core_host import install
  install(selfdrive)


def event_filters(params) -> list:
  """selfdrived: shared event policies, frozen at startup in (priority, id) order."""
  return sorted((c for c in active(params) if "event.filter" in c.CLAIMS), key=lambda c: (c.PRIORITY, c.NAME))


def processes(core: set[str] | None = None, role_procs: dict | None = None) -> list:
  """manager: claimed extra processes and SP role processes selected by their current owner."""
  procs = []
  names = set(core or ()) | {n for ns in ROLE_PROCESSES.values() for n in ns}
  for role, role_names in ROLE_PROCESSES.items():
    given = {n: role_procs[n] for n in role_names if n in (role_procs or {})}
    allowed = {n: set() for n in given}
    for cls in PLUGINS:
      if role not in cls.CLAIMS:
        continue
      try:
        selected = list(cls.role_processes(role, dict(given)))
        if any(not any(p is proc for proc in given.values()) for p in selected):
          raise PermissionError(f"foreign process for {role}")
      except Exception as e:
        record_fault(cls.NAME, "manager.role_processes", e)
        continue
      for n, proc in given.items():
        if any(p is proc for p in selected):
          allowed[n].add(cls.NAME)
    for n, proc in given.items():
      should_run = proc.should_run
      proc.should_run = lambda started, params, CP, run=should_run, r=role, owners=allowed[n]: owner(params, r) in owners and run(started, params, CP)
      procs.append(proc)
  for cls in PLUGINS:
    try:
      extra = cls.processes()
    except Exception as e:
      record_fault(cls.NAME, "manager.processes", e)
      continue
    for proc in extra:
      if proc.name in names:
        cloudlog.error(f"xpilot plugin {cls.NAME}: process {proc.name} already registered, not started")
        continue
      if f"process:{proc.name}" not in cls.CLAIMS:
        cloudlog.error(f"xpilot plugin {cls.NAME}: process {proc.name} not claimed, not started")
        continue
      should_run = proc.should_run
      proc.should_run = lambda started, params, CP, run=should_run, name=cls.NAME: name in resolve(params)[0] and run(started, params, CP)
      procs.append(proc)
      names.add(proc.name)
  return procs


def owner(params, claim: str) -> str | None:
  """Name of the active plugin holding this claim, or None."""
  return next((cls.NAME for cls in active(params) if claim in cls.CLAIMS), None)


def missing_roles(params) -> list[str]:
  """Required roles with no active owner, in required order."""
  claims = {claim for cls in active(params) for claim in cls.CLAIMS}
  return [role for role in REQUIRED_ROLES if role not in claims]


def holds(params, claim: str) -> bool:
  """True if an active plugin holds this claim, e.g. source:map stops SP's OSM map processes (fork_processes.py)."""
  return any(claim in cls.CLAIMS for cls in active(params))


def services() -> list[str]:
  """Extra services plannerd, selfdrived and controlsd subscribe for plugin hooks (ignored for alive/valid checks)."""
  try:
    from openpilot.common.params import Params
    enabled = active(Params())
  except Exception:
    enabled = PLUGINS
  return sorted({svc for p in enabled for svc in p.SERVICES})


_msg_socket = None


def send_msg(*args, valid_for_ms: int = 0) -> bool:
  """Send advisory data to plugin_msgd without blocking. The legacy first pm argument is ignored."""
  from openpilot.tesla import plugin_msgd
  global _msg_socket
  if len(args) >= 4 and not isinstance(args[1], str):
    args = args[1:]
  plugin, kind, payload, *ttl = args
  if ttl:
    valid_for_ms, = ttl
  try:
    if f"msg:{kind}" not in plugin.CLAIMS:
      raise PermissionError(f"pluginMsg {kind!r} without a msg:{kind} claim")
    data = json.dumps({"source": plugin.NAME, "kind": kind, "payload": json.dumps(payload),
                       "validForMs": valid_for_ms, "logMonoTime": time.monotonic_ns(), "pid": os.getpid()}).encode()
  except (TypeError, ValueError, PermissionError) as e:
    record_fault(plugin.NAME, "pluginMsg", e)
    return False
  if _msg_socket is None:
    _msg_socket = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    _msg_socket.setblocking(False)
  try:
    _msg_socket.sendto(data, plugin_msgd.socket_path())
  except OSError:
    return False
  return True


class PluginMsgs:
  """pluginMsg reader: fresh(kind) -> {source plugin id: payload} of the latest unexpired message per source."""

  def __init__(self, sock=None):
    from openpilot.cereal import messaging
    self.messaging, self.last = messaging, {}
    self.sock = sock or messaging.sub_sock("pluginMsg", conflate=False)

  def fresh(self, kind: str, now_ns: int | None = None) -> dict:
    for e in self.messaging.drain_sock(self.sock):
      m = e.pluginMsg
      key = (m.source, m.kind)
      if key not in self.last or e.logMonoTime >= self.last[key][0]:
        self.last[key] = (e.logMonoTime, m.validForMs, m.payload)
    now_ns = time.monotonic_ns() if now_ns is None else now_ns
    return {src: json.loads(p) for (src, k), (t, valid_ms, p) in self.last.items()
            if k == kind and (valid_ms == 0 or 0 <= now_ns - t <= valid_ms * 1_000_000)}


  def latest(self, kind: str, source: str):
    """Latest unexpired payload for this source, or None."""
    return self.fresh(kind).get(source)


def _collect(params, hook: str, claim: str, where: str, *args, skip=()) -> list:
  """Hook objects of the active plugins that return one; tagged with plugin_name. A plugin returning one without
  the claim, or raising, is recorded and skipped."""
  out, wrapped = [], view(params)
  args = tuple(wrapped if a is params else a for a in args)
  for cls in active(params):
    if cls.NAME in skip:
      continue
    try:
      if (obj := getattr(cls, hook)(*args)) is not None:
        if claim not in cls.CLAIMS:
          raise PermissionError(f"{hook} without a {claim} claim")
        obj.plugin_name = cls.NAME
        out.append(obj)
    except Exception as e:
      record_fault(cls.NAME, f"{where}.{hook}", e)
  return out


def _call(objs: list, obj, method: str, where: str, *args):
  """(True, result) or (False, None) after dropping obj from objs and recording the fault."""
  try:
    return True, getattr(obj, method)(*args)
  except Exception as e:
    if obj in objs:
      objs.remove(obj)
    record_fault(getattr(obj, "plugin_name", "?"), f"{where}.{method}", e)
    return False, None


def long_owner_policy(params, CP, CP_SP):
  """controlsd: a drive-frozen desired(ctx, CS) callable, including manual input and shadow observation."""
  if CP.brand != "tesla":
    return None
  from openpilot.tesla.long_owner import LongOwnerPolicy
  faults = view(params).get(FAULTS_PARAM) or {}
  # A controlsd restart must not revive a faulted hook during the same drive.
  policies = _collect(params, "long_owner", "role:long.owner", "controlsd", CP, CP_SP, params, skip=faults)
  for obj in list(policies):
    try:
      if not callable(obj.desired):
        raise TypeError("long_owner needs desired(ctx, CS)")
    except Exception as e:
      policies.remove(obj)
      record_fault(obj.plugin_name, "controlsd.long_owner", e)
  return LongOwnerPolicy(policies, view(params).get_bool("XpLongOwnerManual"))


def long_targets(CP, params) -> list:
  """plannerd: one target object per active plugin that has one."""
  return _collect(params, "long_target", "long.target", "plannerd", CP, params) if CP.brand == "tesla" else []


def apply_long_targets(targets: list, sm, candidates: dict, v_ego: float, a_ego: float, v_cruise: float) -> dict:
  """SP update_targets: add plugin speed targets to SP's {source: (v, a)} candidates, from which SP takes the min.
  Lower targets join as xpilotPlugin. Higher ones may only replace the cruise (driver set speed) candidate while
  engaged with no gas, brake or override; the lowest raise wins and is capped at V_CRUISE_MAX."""
  from openpilot.cereal import custom
  from openpilot.common.constants import CV
  from openpilot.selfdrive.car.cruise import V_CRUISE_MAX
  source = custom.LongitudinalPlanSP.LongitudinalPlanSource
  lower, raises = [], []
  for t in list(targets):
    ok, v = _call(targets, t, "update", "plannerd.long_target", sm, v_ego, v_cruise)
    if ok and v is not None and v == v:  # not None, not NaN
      (lower if v < v_cruise else raises).append(max(float(v), 0.0))
  if lower:
    candidates[source.xpilotPlugin] = (min(lower), a_ego)
  cc, cs = sm['carControl'], sm['carState']
  if raises and cc.enabled and not cc.cruiseControl.override and not cs.gasPressed and not cs.brakePressed:
    candidates[source.cruise] = (min(min(raises), V_CRUISE_MAX * CV.KPH_TO_MS), a_ego)
  return candidates


def plan_filters(CP, params) -> list:
  return _collect(params, "plan_filter", "plan.filter", "plannerd", CP, params) if CP.brand == "tesla" else []


# ---- selfdrived: alerts ----
def alert_sources(CP, params) -> list:
  return _collect(params, "alert_source", "alert", "selfdrived", CP, params) if CP.brand == "tesla" else []


def update_alerts(sources: list, sm, events_sp) -> None:
  """First alert per severity in plugin order -> tesla.alerts.current, and its SP event into events_sp."""
  from openpilot.tesla import alerts
  shown: dict[str, tuple[str, str]] = {}
  for src in list(sources):
    ok, out = _call(sources, src, "update", "selfdrived.alert_source", sm)
    for a in (out or []) if ok else []:
      if a.severity in alerts.EVENT_NAMES and a.severity not in shown:
        shown[a.severity] = (str(a.title)[:alerts.MAX_TITLE], str(a.body)[:alerts.MAX_BODY])
  alerts.current = shown
  for severity in shown:
    events_sp.add(alerts.EVENT_NAMES[severity])


# ---- modeld: lane change policy ----
def lane_change_policies(params) -> list:
  """The active lane change policy (role:lane_change.policy is exclusive, so at most one)."""
  return _collect(params, "lane_change_policy", "role:lane_change.policy", "modeld", params)


def lane_change_allowed(policies: list, carstate, direction, default: bool) -> bool:
  """DesireHelper: the policy's decision, or SP's default when there is none or it raises."""
  if not policies:
    return default
  ok, allowed = _call(policies, policies[0], "allow", "modeld.lane_change_policy", carstate, direction, default)
  return bool(allowed) if ok else default


# ---- controlsd: lateral correction ----
LAT_CORRECTION_ACCEL = 0.5  # m/s^2: cap on the total curvature change all corrections may make
LAT_CORRECTION_MIN_SPEED = 5.0  # m/s: below this the cap is computed at this speed


def lat_corrections(CP, params) -> list:
  return _collect(params, "lat_correction", "lat.correction", "controlsd", CP, params) if CP.brand == "tesla" else []


def apply_lat_corrections(corrections: list, sm, v_ego: float, curvature: float, active: bool) -> float:
  """controlsd, before clip_curvature: chain corrections, ignore non-finite results, cap the total change."""
  if not active or not corrections:
    return curvature
  out = curvature
  for c in list(corrections):
    ok, res = _call(corrections, c, "update", "controlsd.lat_correction", sm, v_ego, out)
    if ok and res is not None and math.isfinite(res):
      out = float(res)
  cap = LAT_CORRECTION_ACCEL / max(v_ego, LAT_CORRECTION_MIN_SPEED) ** 2
  return min(max(out, curvature - cap), curvature + cap)


class _GuardedSink:
  """Runs one plan filter; if it raises, drops it and still publishes the message (filters only tighten).
  ponytail: the message may carry the filter's partial (tighter) edits for that one cycle, and is sent twice
  if the filter raised after its own send; track sends in the inner sink if that ever matters."""

  def __init__(self, inner, sink, f, filters):
    self._inner, self._sink, self._f, self._filters = inner, sink, f, filters

  def __getattr__(self, name):
    return getattr(self._inner, name)

  def send(self, service, msg) -> None:
    try:
      self._sink.send(service, msg)
    except Exception as e:
      if self._f in self._filters:
        self._filters.remove(self._f)
        record_fault(getattr(self._f, "plugin_name", "?"), f"plannerd.{service}", e)
      self._inner.send(service, msg)


def plan_sink(filters, pm, sm):
  sink = pm
  for f in list(filters):
    try:
      sink = _GuardedSink(sink, f.publisher(sink, sm), f, filters)
    except Exception as e:
      filters.remove(f)
      record_fault(getattr(f, "plugin_name", "?"), "plannerd.publisher", e)
  return sink


# ---- controlsd: replaceable lateral controller ----
class _GuardedLatControl:
  """Steers with the plugin controller while SP's runs alongside; on a raise or non-finite output SP takes over for good."""

  def __init__(self, plugin, sp):
    self.plugin, self.sp, self._plugins = plugin, sp, [plugin]

  def __getattr__(self, name):
    return getattr(self.sp, name)

  def update(self, *args):
    sp_out = self.sp.update(*args)
    if self._plugins:
      ok, out = _call(self._plugins, self.plugin, "update", "controlsd.lat_controller", *args)
      if ok:
        try:
          if all(math.isfinite(float(x)) for x in out[:2]):
            return out
          raise ValueError(f"non-finite lateral output {out[:2]}")
        except Exception as e:
          self._plugins.clear()
          record_fault(getattr(self.plugin, "plugin_name", "?"), "controlsd.lat_controller.update", e)
    return sp_out

  def reset(self):
    self.sp.reset()
    if self._plugins:
      _call(self._plugins, self.plugin, "reset", "controlsd.lat_controller")


def lateral_controller(CP, CP_SP, CI, params, sp_controller):
  """controlsd: SP's controller itself, or a guard around the active role:lateral.controller plugin."""
  if CP.brand != "tesla":
    return sp_controller
  found = _collect(params, "lat_controller", "role:lateral.controller", "controlsd", CP, CP_SP, CI, params)
  return _GuardedLatControl(found[0], sp_controller) if found else sp_controller


# ---- plannerd: replaceable longitudinal planner ----
def long_planners(CP, CP_SP, params) -> list:
  return _collect(params, "long_planner", "role:long.planner", "plannerd", CP, CP_SP, params) if CP.brand == "tesla" else []


def apply_long_planner(planners: list, planner, sm) -> None:
  """SP LongitudinalPlanner.update, after its own output: let the active planner replace the plan (not FCW)."""
  if not planners:
    return
  from types import SimpleNamespace
  from opendbc.car.interfaces import ACCEL_MAX, ACCEL_MIN
  sp = SimpleNamespace(speeds=list(planner.v_desired_trajectory), accels=list(planner.a_desired_trajectory),
                       jerks=list(planner.j_desired_trajectory), a_target=float(planner.output_a_target),
                       should_stop=bool(planner.output_should_stop))
  ok, plan = _call(planners, planners[0], "update", "plannerd.long_planner", sm, sp)
  if not ok or plan is None:
    return
  try:
    speeds, accels, jerks = ([float(x) for x in getattr(plan, f)] for f in ("speeds", "accels", "jerks"))
    a_target = float(plan.a_target)
    n = len(sp.speeds)
    if len(speeds) != n or len(accels) != n or len(jerks) != n:
      raise ValueError("plan length differs from SP's")
    if not all(math.isfinite(x) for x in (*speeds, *accels, *jerks, a_target)):
      raise ValueError("non-finite plan")
  except Exception as e:
    record_fault(getattr(planners[0], "plugin_name", "?"), "plannerd.long_planner.result", e)
    planners.clear()
    return
  import numpy as np  # SP publishes these with .tolist()
  planner.v_desired_trajectory, planner.a_desired_trajectory, planner.j_desired_trajectory = (np.array(x) for x in (speeds, accels, jerks))
  planner.output_a_target = min(max(a_target, ACCEL_MIN), ACCEL_MAX)
  planner.output_should_stop = bool(plan.should_stop)


# ---- card: replaceable radar ----
class _GuardedRadar:
  """Reads the plugin radar while the port's RadarInterface runs alongside; if the plugin raises the port's takes over for good."""

  def __init__(self, plugin, sp):
    self.plugin, self.sp, self._plugins = plugin, sp, [plugin]

  def __getattr__(self, name):
    return getattr(self.sp, name)

  def update(self, can_list):
    sp_out = self.sp.update(can_list)
    if self._plugins:
      ok, out = _call(self._plugins, self.plugin, "update", "card.radar_interface", can_list)
      if ok:
        return out
    return sp_out


def radar_interface(CP, CP_SP, params, sp_radar):
  """card: the port's RadarInterface itself, or a guard around the active source:radar plugin's."""
  if CP.brand != "tesla":
    return sp_radar
  found = _collect(params, "radar_interface", "source:radar", "card", CP, CP_SP, params)
  return _GuardedRadar(found[0], sp_radar) if found else sp_radar


# ---- externald: providers ----
def external_providers(params) -> list:
  return _collect(params, "external_provider", "external.provider", "externald", params)
