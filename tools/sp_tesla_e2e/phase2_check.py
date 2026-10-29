#!/usr/bin/env python3
"""Plugin alerts, lane change policy and lateral correction check (runs without a device).

Failure cases listed before implementation:
- A1 a plugin alert can disengage (softDisable/immediateDisable) or outranks SP's own alerts (priority above MID)
- A2 an alert source without the "alert" claim is used
- A3 an alert source that raises takes selfdrived down or keeps running
- A4 several alerts of one severity: not the first in plugin order, or text not length-limited
- A5 an unknown severity is shown
- A6 an event name has no alert definition (selfdrived would raise on create_alerts)
- L1 a policy that always allows starts a lane change into a blind spot or road edge; a driver nudge must override
  a road edge but never a blind spot (SP #2049)
- L2 a policy starts a lane change without the blinker, or below the lane change speed
- L3 a lane change policy without the role:lane_change.policy claim is used
- L4 a policy that raises breaks DesireHelper instead of falling back to SP's own decision
- C1 a lateral correction changes curvature by more than the cap (0.5 m/s^2 equivalent)
- C2 a correction applies while lateral control is inactive
- C3 a correction that raises keeps running or changes curvature
- C4 NaN/inf from a correction reaches the controller
- C5 a correction without the lat.correction claim is used
- C6 corrections run after clip_curvature (ISO jerk/accel limits would not cover them)
"""
import json
import math
from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "opendbc_repo")]
try:
  import openpilot.common.params  # noqa: F401
except Exception:
  class _HostParams:  # defaults only: AutoLaneChangeTimer 0 = nudge, every BOOL off
    def get(self, key, return_default=False, block=False):
      return 0

    def get_bool(self, key, block=False):
      return False
  sys.modules["openpilot.common.params"] = types.SimpleNamespace(Params=_HostParams, UnknownKeyName=KeyError)
from openpilot.common.swaglog import cloudlog  # noqa: E402
for _level in ("error", "exception", "warning", "info"):
  setattr(cloudlog, _level, lambda *a, **k: None)

from openpilot.tesla import plugins  # noqa: E402
from openpilot.tesla.plugin import Plugin, PluginAlert  # noqa: E402


class CP:
  brand = "tesla"


class FakeParams:
  def get_bool(self, key):
    return False


def make(name, claims, **hooks):
  attrs = {"NAME": name, "CLAIMS": tuple(claims), "ENABLE_PARAM": None, "PRIORITY": 100}
  for hook, obj in hooks.items():
    attrs[hook] = classmethod(lambda cls, *a, _o=obj: _o)
  return type(name, (Plugin,), attrs)


class Obj:
  def __init__(self, fn):
    self.fn, self.calls = fn, 0

  def __getattr__(self, name):
    if name in ("update", "allow"):
      def call(*a):
        self.calls += 1
        return self.fn(*a)
      return call
    raise AttributeError(name)


def main() -> int:
  errors = []
  faults = []
  plugins.record_fault = lambda name, where, exc: faults.append((name, where))

  # ---- alerts ----
  from openpilot.tesla import alerts
  from openpilot.sunnypilot.selfdrive.selfdrived.events import EVENTS_SP, EventsSP
  from openpilot.sunnypilot.selfdrive.selfdrived.events_base import ET, Priority
  for name in alerts.EVENT_NAMES.values():
    entry = EVENTS_SP.get(name)
    if entry is None:
      errors.append(f"A6 no alert for {name}")
      continue
    if set(entry) & {ET.SOFT_DISABLE, ET.IMMEDIATE_DISABLE, ET.NO_ENTRY, ET.USER_DISABLE}:
      errors.append(f"A1 {name} can disengage: {set(entry)}")
    for et, alert in entry.items():
      a = alert(None, None, None, True, 0, None) if callable(alert) else alert
      if a.priority > Priority.MID:
        errors.append(f"A1 {name} priority {a.priority}")

  boom = Obj(lambda sm: 1 / 0)
  first = Obj(lambda sm: [PluginAlert("warning", "First" * 40, "x" * 200), PluginAlert("nope", "Bad")])
  second = Obj(lambda sm: [PluginAlert("warning", "Second"), PluginAlert("info", "Info")])
  plugins.PLUGINS = (make("a_boom", ["alert"], alert_source=boom), make("b_first", ["alert"], alert_source=first),
                     make("c_second", ["alert"], alert_source=second), make("d_noclaim", [], alert_source=Obj(lambda sm: [PluginAlert("critical", "X")])))
  sources = plugins.alert_sources(CP, FakeParams())
  if [s.plugin_name for s in sources] != ["a_boom", "b_first", "c_second"]:
    errors.append(f"A2 sources {[s.plugin_name for s in sources]}")
  events = EventsSP()
  for _ in range(2):
    try:
      plugins.update_alerts(sources, {}, events)
    except Exception as e:
      errors.append(f"A3 update_alerts raised {e!r}")
      break
  if boom.calls != 1 or ("a_boom", "selfdrived.alert_source.update") not in faults:
    errors.append(f"A3 failing source called {boom.calls}x, faults {faults}")
  shown = {sev: alerts.current.get(sev) for sev in alerts.EVENT_NAMES}
  if not shown["warning"] or not shown["warning"][0].startswith("First") or len(shown["warning"][0]) > alerts.MAX_TITLE \
     or len(shown["warning"][1]) > alerts.MAX_BODY:
    errors.append(f"A4 warning {shown['warning']}")
  if shown["critical"] or "nope" in alerts.current:
    errors.append(f"A5 shown {shown}")
  if set(events.names) != {alerts.EVENT_NAMES["warning"], alerts.EVENT_NAMES["info"]}:
    errors.append(f"A4 events {events.names}")

  # ---- lane change policy ----
  from openpilot.selfdrive.controls.lib.desire_helper import DesireHelper, LANE_CHANGE_SPEED_MIN
  from openpilot.cereal import log
  LCS = log.LaneChangeState

  def cs(left=False, right=False, v=30.0, bsl=False, nudge=False):
    return types.SimpleNamespace(vEgo=v, leftBlinker=left, rightBlinker=right, leftBlindspot=bsl, rightBlindspot=False,
                                 steeringPressed=nudge, steeringTorque=1.0 if nudge else 0.0, brakePressed=False)

  def run(policy, frames, edge=False):
    dh = DesireHelper()
    dh.lane_change_policy = [policy] if policy else []
    for c in frames:
      dh.update(c, True, 0.0, left_edge_detected=edge)
    return dh

  always = Obj(lambda carstate, direction, default: True)
  if run(always, [cs(), cs(left=True), cs(left=True)]).lane_change_state != LCS.laneChangeStarting:
    errors.append("L1 policy that allows did not start a lane change (hook not wired)")
  if run(always, [cs(), cs(left=True, bsl=True), cs(left=True, bsl=True)]).lane_change_state == LCS.laneChangeStarting:
    errors.append("L1 lane change started into a blind spot")
  if run(always, [cs(), cs(left=True), cs(left=True)], edge=True).lane_change_state == LCS.laneChangeStarting:
    errors.append("L1 lane change started into a road edge")
  if run(None, [cs(), cs(left=True, nudge=True), cs(left=True, nudge=True)], edge=True).lane_change_state != LCS.laneChangeStarting:
    errors.append("L1 driver nudge did not override a road edge (SP #2049)")
  if run(None, [cs(), cs(left=True, bsl=True, nudge=True), cs(left=True, bsl=True, nudge=True)]).lane_change_state == LCS.laneChangeStarting:
    errors.append("L1 driver nudge started a lane change into a blind spot")
  if run(always, [cs(), cs(), cs()]).lane_change_state != LCS.off:
    errors.append("L2 lane change without blinker")
  slow = LANE_CHANGE_SPEED_MIN - 1
  if run(always, [cs(v=slow), cs(left=True, v=slow), cs(left=True, v=slow)]).lane_change_state != LCS.off:
    errors.append("L2 lane change below min speed")
  bad = Obj(lambda *a: 1 / 0)
  try:
    dh = run(bad, [cs(), cs(left=True), cs(left=True)])
    if dh.lane_change_policy or bad.calls != 1:
      errors.append(f"L4 failing policy kept ({bad.calls} calls)")
  except Exception as e:
    errors.append(f"L4 DesireHelper raised {e!r}")
  plugins.PLUGINS = (make("p_no", [], lane_change_policy=always), make("p_yes", ["role:lane_change.policy"], lane_change_policy=always))
  got = plugins.lane_change_policies(FakeParams())
  if [p.plugin_name for p in got] != ["p_yes"]:
    errors.append(f"L3 policies {[p.plugin_name for p in got]}")

  # ---- lateral correction ----
  v = 20.0
  cap = plugins.LAT_CORRECTION_ACCEL / v ** 2
  huge = Obj(lambda sm, v_ego, c: c + 1.0)
  plugins.PLUGINS = (make("l_huge", ["lat.correction"], lat_correction=huge), make("l_noclaim", [], lat_correction=Obj(lambda *a: 5.0)))
  lats = plugins.lat_corrections(CP, FakeParams())
  if [x.plugin_name for x in lats] != ["l_huge"]:
    errors.append(f"C5 corrections {[x.plugin_name for x in lats]}")
  out = plugins.apply_lat_corrections(lats, {}, v, 0.01, True)
  if abs(out - 0.01) > cap + 1e-9:
    errors.append(f"C1 changed by {out - 0.01}, cap {cap}")
  if plugins.apply_lat_corrections(lats, {}, v, 0.01, False) != 0.01:
    errors.append("C2 applied while inactive")
  for value in (float("nan"), float("inf")):
    weird = [Obj(lambda sm, v_ego, c, _v=value: _v)]
    for x in weird:
      x.plugin_name = "weird"
    res = plugins.apply_lat_corrections(weird, {}, v, 0.01, True)
    if not math.isfinite(res) or res != 0.01:
      errors.append(f"C4 {value} gave {res}")
  boom = Obj(lambda *a: 1 / 0)
  boom.plugin_name = "l_boom"
  lst = [boom]
  try:
    r1 = plugins.apply_lat_corrections(lst, {}, v, 0.01, True)
    plugins.apply_lat_corrections(lst, {}, v, 0.01, True)
    if r1 != 0.01 or boom.calls != 1 or lst:
      errors.append(f"C3 failing correction: result {r1}, calls {boom.calls}, kept {bool(lst)}")
  except Exception as e:
    errors.append(f"C3 raised {e!r}")
  src = (ROOT / "openpilot/selfdrive/controls/controlsd.py").read_text()
  i, j = src.find("apply_lat_corrections"), src.find("clip_curvature(CS.vEgo")
  if i < 0 or j < 0 or i > j:
    errors.append("C6 corrections not applied before clip_curvature")

  print(json.dumps({"errors": errors}, indent=1))
  return 1 if errors else 0


if __name__ == "__main__":
  sys.exit(main())
