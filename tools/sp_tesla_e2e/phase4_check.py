#!/usr/bin/env python3
"""Replaceable engagement (role:engagement.state) and safety fallback (role:safety.fallback) check.

User floors (2026-10-23): a plugin may replace SP's whole engagement state machines (ordinary + MADS) or its whole
fallback policy (which events fire), but never panda safety, real brake/gas/cancel disengage, FCW, stock AEB, the
MADS disengage inputs (LKAS button, cruise main off), core-data liveness, selfdrivedLagging, DM, and an engage
always needs a driver ENABLE trigger. Runs where the openpilot python env (cereal, msgq) exists, i.e. the device.

Failure cases listed before implementation:
- C1 install_core changes a non-Tesla selfdrived
- C2 an engagement plugin that raises keeps control, or the drive re-engages later (latch), or no fault alert shows
- C3 a state machine returning enabled/active that disagree with its state (enabled=False, active=True) keeps control
- C4 a state machine ignoring a floor USER_DISABLE (pedalPressed, buttonCancel, pcmDisable) or IMMEDIATE_DISABLE
     (canError, controlsMismatch) event stays engaged
- C5 a state machine stuck in softDisabling, or engaged under a floor SOFT_DISABLE event (commIssue,
     selfdrivedLagging, processNotRunning), longer than SOFT_DISABLE_TIME + 1 s
- C6 a state machine engages without a driver ENABLE event, or with one while a floor NO_ENTRY event is present
- C7 a state machine dropping ET.PERMANENT hides FCW / stock AEB alerts
- C8 a fallback that clears the events loses a floor event (pedalPressed, fcw, stockAeb, commIssue, DM tooDistracted);
     a non-floor event it removed (doorOpen) comes back
- C9 a fallback with invalid output, or raising, keeps control
- C10 a MADS replacement ignores the LKAS button (also the Tesla screen gesture) or a floor IMMEDIATE_DISABLE event,
      engages laterally without a trigger, reports active without enabled, or overrides the Mads toggle
      (Tesla has no ACC main button, so SP's cruise-main-off disengage does not apply)
- C11 a plugin fault recorded earlier in this drive (selfdrived restarted) does not start the new process latched
- C12 card keeps sending while a replacement owns a role and selfdriveState is not alive
- C13 default owners leave SP unwrapped, or the host depends on core plugin ids
- C14 wrapped SP differs from bare SP frame by frame: pcmEnable, buttonEnable, softDisable/recovery/expiry,
      immediateDisable, userDisable, noEntry, preEnable, overrides, MADS LKAS on/off, exempt pcmDisable,
      paused/resume, lateral mismatch, Mads off, event counters and enabled_prev
- C15 missing owner or a hook raising/returning None permits engagement instead of latching xpilotPluginFault
- C16 removing both SP role plugins still constructs SP MADS for a replacement with mads=None, blocks ordinary
      engagement, or exposes lateral MADS
- C17 fallback receives selfdrive instead of the narrow ctx, accepts non-lists/unknown names/raises without
      latching, mutates the event objects/counters, or loses the original events and floors after invalid output
- C18 CardGate is inactive with default owners, sends before first selfdriveState, or fails to recover on publish
- C19 selfdrived sets state_machine.state to enabled through the wrapper for replay, then an update without ENABLE
      differs from bare SP instead of staying enabled; resetting state leaves stale enabled_prev
- C20 observe_can sends continue when core_alive is false.
- C21 a filter drops any non-allowlisted event (including ENABLE, door/seatbelt/steer/gear disables).
- C22 a filter drops a FLOOR/FLOOR_SP event, or a valid first list is applied before a bad second list is rejected.
- C23 a raising filter runs again, loses events, hides xpilotPluginFault, or latches both axes off.
- C24 garbage output (non-list, unknown name, bool, wrong shape) is accepted or partially applied.
- C25 two enabled event.filter claimants conflict, run outside (priority, id) order, or run after fallback.
- C26 a filter off at startup is called, or cannot withdraw its policy while its own switch is off.
- C27 a valid filter fails to permit SP engagement with invalidLkasSetting; copies/counters/CarState change.
- C28 without a filter, real Tesla CAN -> CarState -> SP events hides stock Autosteer or permits engagement.
- C29 a filter fault from before selfdrived restart runs again, hides its alert or latches the axes, including
      a filter that also owns an engagement role.
"""
import ast
import hashlib
import json
from pathlib import Path
import sys
import types
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "opendbc_repo")]
try:
  import openpilot.common.params  # noqa: F401
except Exception:
  sys.modules["openpilot.common.params"] = types.SimpleNamespace(Params=object, UnknownKeyName=KeyError)
from openpilot.common.swaglog import cloudlog  # noqa: E402
for _level in ("error", "exception", "warning", "info"):
  setattr(cloudlog, _level, lambda *a, **k: None)

from openpilot.cereal import custom, log  # noqa: E402
from openpilot.selfdrive.selfdrived.events import ET, Events  # noqa: E402
from openpilot.selfdrive.selfdrived.state import State, StateMachine  # noqa: E402
from openpilot.sunnypilot.mads import mads as sp_mads  # noqa: E402
from openpilot.sunnypilot.mads.state import State as MadsState  # noqa: E402
from openpilot.sunnypilot.selfdrive.selfdrived.events import EventsSP  # noqa: E402
from openpilot.tesla import core_host, plugins  # noqa: E402
from openpilot.tesla.plugin import Plugin  # noqa: E402
from opendbc.car.structs import car  # noqa: E402

DEFAULT_PLUGINS = tuple(c for c in plugins.PLUGINS if c.NAME in ("sp_engagement", "sp_safety_fallback"))
EN = log.OnroadEvent.EventName
ENSP = custom.OnroadEventSP.EventName
FAULTS = {}
plugins.record_fault = lambda name, where, exc: FAULTS.__setitem__(name, where)
core_host.record_fault = plugins.record_fault


class Params:
  def __init__(self, values=None):
    self.values = dict(values or {})

  def get_bool(self, key, block=False):
    return bool(self.values.get(key, False))

  def get(self, key, block=False, return_default=False):
    return self.values.get(key)


class Clock:
  t = 0.0

  def __call__(self):
    return self.t


def cs(**kw):
  d = dict(buttonEvents=[], cruiseState=types.SimpleNamespace(available=True), standstill=False, gasPressed=False, brakePressed=False,
           regenBraking=False, vEgo=10.0, gearShifter=car.CarState.GearShifter.drive)
  d.update(kw)
  return types.SimpleNamespace(**d)


def selfdrive(params=None):
  s = types.SimpleNamespace(events=Events(), events_sp=EventsSP(), state_machine=StateMachine(), enabled=False,
                            active=False, params=params or Params({"Mads": True}), CP=types.SimpleNamespace(brand="tesla", passive=False),
                            CP_SP=types.SimpleNamespace(flags=0), initialized=True, enabled_prev=False,
                            sm={"pandaStates": []}, CS_prev=cs())
  s.mads = types.SimpleNamespace(enabled=False, active=False, enabled_toggle=True, update=lambda CS: None,
                                 read_params=lambda: None, state_machine=types.SimpleNamespace(state=MadsState.disabled))
  s.update_events = lambda CS: None
  return s


def role_plugin(name, claim, hook, value):
  return type(name, (Plugin,), {"NAME": name, "ENABLE_PARAM": None, "CLAIMS": (claim,), "PRIORITY": 100,
                                hook: classmethod(lambda cls, *a: value)})


def default_plugins():
  return DEFAULT_PLUGINS


class Scripted:
  """A replacement ordinary state machine that returns whatever the test sets."""

  def __init__(self):
    self.state, self.current_alert_types, self.soft_disable_timer = State.disabled, [ET.PERMANENT], 0
    self.out = (State.disabled, False, False, [ET.PERMANENT])
    self.raises = False

  def update(self, events):
    if self.raises:
      raise RuntimeError("engagement")
    self.state, enabled, active, types_ = self.out
    self.current_alert_types = list(types_)
    return enabled, active


class Mads:
  """A replacement MADS that reports whatever the test sets."""

  def __init__(self):
    self.enabled = self.active = False
    self.want = (False, False)
    self.enabled_toggle = True
    self.state_machine = types.SimpleNamespace(state=MadsState.disabled)

  def update(self, CS):
    self.enabled, self.active = self.want
    self.state_machine.state = MadsState.enabled if self.enabled else MadsState.disabled

  def read_params(self):
    pass


def engagement(sm, mads=None):
  return types.SimpleNamespace(state_machine=sm, mads=mads or Mads())


def install(engagement_sm=None, fallback=None, params=None, mads=None):
  plugins.PLUGINS = default_plugins()
  if engagement_sm is not None:
    plugins.PLUGINS += (role_plugin("rep_eng", core_host.ENGAGEMENT, "engagement", engagement(engagement_sm, mads)),)
  if fallback is not None:
    plugins.PLUGINS += (role_plugin("rep_fb", core_host.FALLBACK, "safety_fallback", fallback),)
  s = selfdrive(params)
  plugins.install_core(s)
  return s


def step(s, CS=None, events=(), events_sp=()):
  """One selfdrived step in SP's order: update_events, ordinary state machine, MADS."""
  CS = CS or cs()
  s.events.clear()
  s.events_sp.clear()
  for e in events:
    s.events.add(e)
  for e in events_sp:
    s.events_sp.add(e)
  s.update_events(CS)
  s.enabled, s.active = s.state_machine.update(s.events)
  s.mads.update(CS)
  s.CS_prev = CS
  return s.enabled, s.active



def event_filter_checks():
  from openpilot.tesla import manifest as mf
  from openpilot.selfdrive.selfdrived.events import EVENTS
  from opendbc.can import CANPacker
  from opendbc.car.tesla.carstate import CarState
  from opendbc.car.tesla.values import CAR, DBC, CANBUS
  from opendbc.car import Bus, structs
  from opendbc.car.tesla.interface import CarInterface

  # Fingerprint discovery is outside this known-Tesla event replay.
  with patch.dict(sys.modules, {"opendbc.car.car_helpers": types.SimpleNamespace(interfaces={CAR.TESLA_MODEL_3: CarInterface})}):
    from openpilot.selfdrive.car.car_events import CarEvents

  errors, records = [], []

  def check(code, condition, detail):
    records.append(dict(case=code, passed=bool(condition), detail=detail))
    if not condition:
      errors.append(f"{code} {detail}")

  def filter_plugin(name, fn, priority=100, toggle=None):
    return type(name, (Plugin,), dict(NAME=name, ENABLE_PARAM=toggle, CLAIMS=("event.filter",), PRIORITY=priority,
                                     event_filter=classmethod(lambda cls, *a: fn(*a))))

  def driver(filters, params=None, fallback=None):
    plugins.PLUGINS = default_plugins() + tuple(filters)
    if fallback is not None:
      plugins.PLUGINS += (role_plugin("rep_fb", core_host.FALLBACK, "safety_fallback", fallback),)
    sd = selfdrive(params)
    plugins.install_core(sd)
    return sd

  # C21/C22/C24: both lists must be valid before any deletion; bad output is never reused.
  forbidden = {e for e, types_ in EVENTS.items() if any(et in types_ for et in
               (ET.NO_ENTRY, ET.USER_DISABLE, ET.SOFT_DISABLE, ET.IMMEDIATE_DISABLE, ET.ENABLE))}
  forbidden -= {EN.invalidLkasSetting}
  outputs = [("C21", ([EN.invalidLkasSetting, e], [])) for e in sorted(forbidden - core_host.FLOOR)]
  outputs += [("C22", ([EN.invalidLkasSetting, e], [])) for e in sorted(core_host.FLOOR)]
  outputs += [("C22", ([EN.invalidLkasSetting], [e])) for e in sorted(core_host.FLOOR_SP)]
  outputs += [("C24", bad) for bad in (None, [], "bad", ((), []), ([], ()), ([999999], []),
                                     ([True], []), ([float(EN.invalidLkasSetting)], []), ([EN.invalidLkasSetting], [999999]))]
  for code, output in outputs:
    FAULTS.clear()
    calls = []
    def invalid(ctx, CS, names, names_sp):
      calls.append(1)
      names.clear()
      names_sp.clear()
      return output
    sd = driver([filter_plugin("bad_filter", invalid)])
    events = sorted({EN.invalidLkasSetting, EN.buttonEnable, EN.doorOpen, *core_host.FLOOR})
    step(sd, events=events, events_sp=core_host.FLOOR_SP)
    first = (sd.events.names.copy(), sd.events_sp.names.copy())
    step(sd, events=events, events_sp=core_host.FLOOR_SP)
    check(code, sd.events.names == events and set(sd.events_sp.names) == core_host.FLOOR_SP and
          len(calls) == 1 and "bad_filter" in FAULTS and not sd.state_machine._h.latched and
          first == (sd.events.names, sd.events_sp.names), repr(output))

  FAULTS.clear()
  calls = []
  def raises(ctx, CS, names, names_sp):
    calls.append(1)
    names.clear()
    raise RuntimeError("filter")
  sd = driver([filter_plugin("raising_filter", raises)])
  engaged = step(sd, events=[EN.buttonEnable])
  step(sd, events=[EN.invalidLkasSetting])
  check("C23", engaged == (True, True) and len(calls) == 1 and "raising_filter" in FAULTS and
        sd.events.has(EN.invalidLkasSetting) and sd.events_sp.has(ENSP.xpilotPluginFault) and
        not sd.state_machine._h.latched, "raise stops only the filter, preserves events and shows fault")

  calls = []
  def ordered(name):
    def apply(ctx, CS, names, names_sp):
      calls.append((name, names.copy()))
      return ([EN.invalidLkasSetting] if name == "a" else []), []
    return apply
  filters = [filter_plugin("b", ordered("b"), 20), filter_plugin("z", ordered("z"), 10),
             filter_plugin("a", ordered("a"), 10)]
  fallback = types.SimpleNamespace(update=lambda ctx, CS, names, names_sp: (calls.append(("fallback", names.copy())) or names, names_sp))
  sd = driver(filters, fallback=fallback)
  step(sd, events=[EN.invalidLkasSetting, EN.buttonEnable, EN.fcw])
  resolved, conflicts = plugins.resolve(sd.params)
  check("C25", [name for name, _ in calls] == ["a", "z", "b", "fallback"] and
        EN.invalidLkasSetting in calls[0][1] and all(EN.invalidLkasSetting not in names for _, names in calls[1:]) and
        {f.NAME for f in filters} <= resolved and not ({f.NAME for f in filters} & conflicts.keys()) and sd.events.has(EN.fcw) and
        mf.shared("event.filter") and mf._CLAIM_RE.fullmatch("event.filter") is not None and
        "event_filter" in mf.HOST_CAPS["hooks"], "shared claim, priority/id order, SP -> filters -> fallback -> floors")

  calls = []
  def switched(ctx, CS, names, names_sp):
    calls.append(1)
    return ([EN.invalidLkasSetting] if ctx.params.get_bool("Mixed") else []), []
  toggle = filter_plugin("switched", switched, toggle="Mixed")
  sd = driver([toggle], Params({"Mixed": False}))
  step(sd, events=[EN.invalidLkasSetting])
  off = sd.events.has(EN.invalidLkasSetting) and not calls
  sd = driver([toggle], Params({"Mixed": True}))
  out = step(sd, events=[EN.invalidLkasSetting, EN.buttonEnable])
  on = not sd.events.has(EN.invalidLkasSetting)
  refs = sd.events, sd.events_sp
  counters = sd.events.event_counters.copy()
  sd.update_events(cs(invalidLkasSetting=True))
  check("C27", out == (True, True) and not sd.events.has(EN.invalidLkasSetting) and
        refs == (sd.events, sd.events_sp) and counters == sd.events.event_counters,
        "valid filter permits SP engagement without replacing event objects or advancing counters")
  sd.params.values["Mixed"] = False
  step(sd, events=[EN.invalidLkasSetting])
  check("C26", off and on and sd.events.has(EN.invalidLkasSetting), "switch off restores event")

  # C28: the port must preserve the actual DAS_settings signal all the way into SP's no-entry event.
  cp = structs.CarParams(carFingerprint=CAR.TESLA_MODEL_3.value, brand="tesla", pcmCruise=True)
  cp_sp = structs.CarParamsSP()
  state = CarState(cp, cp_sp)
  parsers = state.get_can_parsers(cp, cp_sp)
  msg = CANPacker(DBC[CAR.TESLA_MODEL_3][Bus.party]).make_can_msg("DAS_settings", CANBUS.autopilot_party,
                                                               {"DAS_autosteerEnabled": 1})
  _ = parsers[Bus.ap_party].vl["DAS_settings"]
  parsers[Bus.ap_party].update([(0, [msg])])
  ret, _ = state.update(parsers)
  ret.gearShifter, ret.cruiseState.available, ret.cruiseState.enabled = car.CarState.GearShifter.drive, True, True
  ret.doorOpen = ret.seatbeltUnlatched = ret.steerFaultPermanent = False
  events = CarEvents(cp).update(ret, structs.CarState(), structs.CarControl(), types.SimpleNamespace(longOwner='stock')).names
  sd = driver([])
  check("C28", ret.invalidLkasSetting and EN.invalidLkasSetting in events and
        step(sd, events=events) == (False, False) and sd.events.has(EN.invalidLkasSetting),
        "no filter: packed stock Autosteer signal -> true CarState -> no-entry")

  for owns_role in (False, True):
    calls = []
    cls = filter_plugin("earlier_filter", lambda *a: (calls.append(1) or [EN.invalidLkasSetting], []))
    if owns_role:
      cls.CLAIMS += (core_host.ENGAGEMENT,)
      cls.engagement = classmethod(lambda cls, sd: types.SimpleNamespace(state_machine=sd.state_machine, mads=sd.mads))
    sd = driver([cls], Params({plugins.FAULTS_PARAM: {"earlier_filter": "selfdrived.event_filter: RuntimeError('earlier')"}}))
    out = step(sd, events=[EN.buttonEnable])
    step(sd, events=[EN.invalidLkasSetting])
    check("C29", not calls and out == (True, True) and not sd.state_machine._h.latched and
          sd.events.has(EN.invalidLkasSetting) and sd.events_sp.has(ENSP.xpilotPluginFault),
          f"persisted filter fault survives restart without an axis latch; owns_role={owns_role}")
  return errors, records


def main() -> int:
  errors = []
  clock = Clock()
  core_host.now = clock
  ENGAGED = (State.enabled, True, True, [ET.PERMANENT, ET.WARNING])

  # C1 non-Tesla: nothing replaced
  plugins.PLUGINS = default_plugins()
  s = selfdrive()
  s.CP.brand = "mock"
  sm, mads, ue = s.state_machine, s.mads, s.update_events
  plugins.install_core(s)
  if s.state_machine is not sm or s.mads is not mads or s.update_events is not ue:
    errors.append("C1 install_core changed non-Tesla selfdrived")

  # C2 raise -> latched, fault alert, no re-engage
  FAULTS.clear()
  rep = Scripted()
  s = install(engagement_sm=rep)
  rep.out = ENGAGED
  step(s, events=[EN.buttonEnable])
  rep.raises = True
  out = step(s)
  latch_types = list(s.state_machine.current_alert_types)
  rep.raises, rep.out = False, ENGAGED
  later = step(s, events=[EN.buttonEnable])
  if out != (False, False) or later != (False, False) or "rep_eng" not in FAULTS:
    errors.append(f"C2 raise: out {out}, later {later}, faults {FAULTS}")
  if ET.IMMEDIATE_DISABLE not in latch_types or not s.events_sp.has(ENSP.xpilotPluginFault):
    errors.append(f"C2 no fault alert: types {latch_types}, events_sp {s.events_sp.names}")

  # C3 inconsistent outputs
  for bad in [(State.disabled, False, True, [ET.PERMANENT]), (State.enabled, False, True, [ET.PERMANENT])]:
    FAULTS.clear()
    rep = Scripted()
    s = install(engagement_sm=rep)
    rep.out = bad
    if step(s, events=[EN.buttonEnable]) != (False, False) or "rep_eng" not in FAULTS:
      errors.append(f"C3 {bad[:3]} kept control or no fault")

  # C4 floor disable events ignored by the plugin
  for ev, et in [(EN.pedalPressed, ET.USER_DISABLE), (EN.buttonCancel, ET.USER_DISABLE), (EN.pcmDisable, ET.USER_DISABLE),
                 (EN.canError, ET.IMMEDIATE_DISABLE), (EN.controlsMismatch, ET.IMMEDIATE_DISABLE)]:
    rep = Scripted()
    s = install(engagement_sm=rep)
    rep.out = ENGAGED
    step(s, events=[EN.buttonEnable])
    if step(s, events=[ev]) != (False, False) or et not in s.state_machine.current_alert_types:
      errors.append(f"C4 ignored {ev}: types {s.state_machine.current_alert_types}")

  # C5 soft-disable bound
  for state, ev in [(State.softDisabling, None), (State.enabled, EN.commIssue), (State.enabled, EN.selfdrivedLagging),
                    (State.enabled, EN.processNotRunning)]:
    rep = Scripted()
    s = install(engagement_sm=rep)
    rep.out = ENGAGED
    clock.t = 0.0
    step(s, events=[EN.buttonEnable])
    rep.out = (state, True, True, [ET.PERMANENT, ET.SOFT_DISABLE])
    held = []
    for t in (0.1, 2.0, 3.9, 4.2):
      clock.t = t
      held.append(step(s, events=[ev] if ev is not None else [])[0])
    if held != [True, True, True, False]:
      errors.append(f"C5 {state} {ev}: engaged at 0.1/2.0/3.9/4.2 s = {held}")

  # C6 engage gate
  rep = Scripted()
  s = install(engagement_sm=rep)
  rep.out = ENGAGED
  if step(s) != (False, False):
    errors.append("C6 engaged without an ENABLE event")
  if step(s, events=[EN.buttonEnable, EN.canError]) != (False, False):
    errors.append("C6 engaged with a floor NO_ENTRY event")
  if step(s, events=[EN.buttonEnable]) != (True, True):
    errors.append("C6 a driver ENABLE did not engage the replacement")

  # C7 PERMANENT always kept
  rep = Scripted()
  s = install(engagement_sm=rep)
  rep.out = (State.disabled, False, False, [])
  step(s, events=[EN.fcw, EN.stockAeb])
  if ET.PERMANENT not in s.state_machine.current_alert_types:
    errors.append("C7 PERMANENT dropped, FCW/AEB alerts hidden")

  # C8 / C9 fallback
  class Clearing:
    def update(self, ctx, CS, names, names_sp):
      return [], []

  s = install(fallback=Clearing())
  step(s, events=[EN.pedalPressed, EN.fcw, EN.stockAeb, EN.commIssue, EN.tooDistracted, EN.doorOpen])
  kept = {e for e in (EN.pedalPressed, EN.fcw, EN.stockAeb, EN.commIssue, EN.tooDistracted) if s.events.has(e)}
  if len(kept) != 5 or s.events.has(EN.doorOpen):
    errors.append(f"C8 floor after a clearing fallback: {sorted(kept)}, doorOpen {s.events.has(EN.doorOpen)}")

  class Replacing:
    def update(self, ctx, CS, names, names_sp):
      return Events(), names_sp

  class Raising:
    def update(self, ctx, CS, names, names_sp):
      raise RuntimeError("fallback")

  for fb in (Replacing(), Raising()):
    FAULTS.clear()
    s = install(fallback=fb)
    s.state_machine.state = State.enabled
    if step(s) != (False, False) or "rep_fb" not in FAULTS:
      errors.append(f"C9 {type(fb).__name__} fallback kept control or no fault")

  # C10 MADS replacement
  lkas = [types.SimpleNamespace(type=car.CarState.ButtonEvent.Type.lkas, pressed=True)]
  m = Mads()
  s = install(engagement_sm=Scripted(), mads=m)
  m.want = (True, True)
  step(s)
  if s.mads.enabled:
    errors.append("C10 MADS engaged without a trigger")
  step(s, CS=cs(buttonEvents=lkas))
  engaged = s.mads.enabled
  step(s, CS=cs(buttonEvents=lkas))
  if not engaged or s.mads.enabled:
    errors.append(f"C10 LKAS button: engage {engaged}, still engaged after a second press {s.mads.enabled}")
  step(s, CS=cs(buttonEvents=lkas))
  step(s, events=[EN.canError])
  if s.mads.enabled:
    errors.append("C10 MADS stayed engaged under a floor IMMEDIATE_DISABLE event")
  FAULTS.clear()
  m = Mads()
  s = install(engagement_sm=Scripted(), mads=m)
  m.want = (False, True)
  step(s, CS=cs(buttonEvents=lkas))
  if s.mads.active or "rep_eng" not in FAULTS:
    errors.append("C10 MADS active without enabled kept steering or no fault")
  m = Mads()
  s = install(engagement_sm=Scripted(), mads=m, params=Params({"Mads": False}))
  m.want = (True, True)
  step(s, CS=cs(buttonEvents=lkas))
  if s.mads.enabled_toggle or s.mads.enabled:
    errors.append("C10 MADS ignores the Mads param (toggle from the plugin)")

  # C11 latch across a selfdrived restart
  plugins.PLUGINS = default_plugins() + (role_plugin("rep_eng", core_host.ENGAGEMENT, "engagement", engagement(Scripted())),)
  rep = plugins.PLUGINS[-1].engagement(None).state_machine
  rep.out = ENGAGED
  s = selfdrive(Params({"Mads": True, plugins.FAULTS_PARAM: {"rep_eng": "earlier"}}))
  plugins.install_core(s)
  if step(s, events=[EN.buttonEnable]) != (False, False):
    errors.append("C11 a fault from earlier in the drive did not latch the restarted selfdrived")

  # C12 card gate
  plugins.PLUGINS = default_plugins()
  plugins.PLUGINS += (role_plugin("rep_eng", core_host.ENGAGEMENT, "engagement", engagement(Scripted())),)
  gate = core_host.CardGate(Params(), sm=types.SimpleNamespace(update=lambda t: None, alive={"selfdriveState": False}))
  if gate.alive():
    errors.append("C12 card sends while selfdriveState is not alive and a replacement owns a role")
  card = (ROOT / "openpilot/selfdrive/car/card.py").read_text()
  if "self.sm.all_alive(['carControl']) and self.plugins.core_alive()" not in card:
    errors.append("C12 card.py does not gate CI.apply on plugins.core_alive()")
  sd = (ROOT / "openpilot/selfdrive/selfdrived/selfdrived.py").read_text()
  if "plugins.install_core(self)" not in sd:
    errors.append("C1 selfdrived.py does not call plugins.install_core(self)")

  # C13 the actual discovered defaults run the same guarded path.
  plugins.PLUGINS = default_plugins()
  s = selfdrive()
  sm, mads, ue = s.state_machine, s.mads, s.update_events
  plugins.install_core(s)
  if (not isinstance(s.state_machine, core_host._GuardedStateMachine) or
      not isinstance(s.mads, core_host._GuardedMads) or s.update_events is ue or
      s.state_machine._inner is not sm or s.mads._inner is not mads):
    errors.append("C13 default owners bypassed the host or replaced SP's own instances")
  source = (ROOT / "openpilot/tesla/core_host.py").read_text()
  if any(n in source for n in ("DEFAULTS", "sp_engagement", "sp_safety_fallback", "sp_models")):
    errors.append("C13 core_host knows plugin ids")

  # C14 real SP objects, including MADS event edits, counters and shared alerts.
  traces = []
  sequences = {
    "replay-start": [([], {}, 3), ([EN.buttonCancel], {}, 1)],
    "ordinary": [([EN.pcmEnable], {}, 1), ([], {}, 2), ([EN.doorOpen], {}, 10), ([], {}, 1),
                 ([EN.steerUnavailable], {}, 1), ([EN.buttonEnable, EN.canError], {}, 1),
                 ([EN.buttonEnable, EN.doorOpen], {}, 1),
                 ([EN.buttonEnable], {}, 1), ([EN.buttonCancel], {}, 1),
                 ([EN.buttonEnable, EN.preEnableStandstill], {}, 1),
                 ([EN.preEnableStandstill, EN.commIssue], {}, 1), ([EN.doorOpen], {}, 2),
                 ([], {}, 1), ([EN.steerOverride], {}, 1), ([], {}, 1), ([EN.pcmDisable], {}, 1)],
    "soft-expiry": [([EN.buttonEnable], {}, 1), ([EN.doorOpen], {}, 305)],
    "mads": [([], {"buttonEvents": lkas}, 1), ([EN.pcmDisable], {}, 1),
             ([], {"buttonEvents": lkas}, 1), ([], {}, 1), ([], {"buttonEvents": lkas}, 1),
             ([EN.doorOpen], {"standstill": True}, 1), ([], {}, 1),
             ([EN.buttonEnable], {}, 1), ([EN.buttonCancel], {}, 1), ([], {}, 1)],
    "mads-soft": [([], {"buttonEvents": lkas}, 1), ([EN.doorOpen], {}, 305)],
    "mads-off": [([EN.buttonEnable], {}, 1), ([], {}, 1), ([EN.pedalPressed], {}, 1)],
    "mismatch": [([], {"buttonEvents": lkas}, 1), ([], {"lateral_off": True}, 100),
                 ([], {}, 1), ([], {"lateral_off": True}, 101)],
    "ignored-panda": [([], {"buttonEvents": lkas}, 1), ([], {"ignored_panda": True}, 205)],
  }
  for label, sequence in sequences.items():
    pair = []
    for wrapped in (False, True):
      params = Params({"Mads": label != "mads-off", "MadsUnifiedEngagementMode": True})
      sd = selfdrive(params)
      original_params = sp_mads.Params
      sp_mads.Params = lambda: params
      try:
        sd.mads = sp_mads.ModularAssistiveDrivingSystem(sd)
      finally:
        sp_mads.Params = original_params
      if wrapped:
        plugins.PLUGINS = default_plugins()
        plugins.install_core(sd)
        # Exercise the old guards too when the base install short-circuits.
        if not isinstance(sd.state_machine, core_host._GuardedStateMachine):
          h = core_host._Host(sd, [])
          sd.update_events = core_host._fallback(h, sd.update_events, None)
          sd.state_machine = core_host._GuardedStateMachine(h, sd.state_machine, "sp_engagement")
          sd.mads = core_host._GuardedMads(h, sd.mads, "sp_engagement", params.get_bool("Mads"))
      pair.append(sd)
    if label == "replay-start":
      for sd in pair:
        sd.state_machine.state = State.enabled
    frame = 0
    mismatch = False
    for names, inputs, count in sequence:
      for _ in range(count):
        frame += 1
        clock.t = frame * 0.01
        states = []
        for sd in pair:
          sd.sm["pandaStates"] = [types.SimpleNamespace(
            controlsAllowedLateral=not (inputs.get("lateral_off") or inputs.get("ignored_panda")),
            safetyModel=car.CarParams.SafetyModel.silent if inputs.get("ignored_panda") else car.CarParams.SafetyModel.tesla)]
          step(sd, CS=cs(**{k: v for k, v in inputs.items() if k not in ("lateral_off", "ignored_panda")}), events=names)
          sd.mads.read_params()
          states.append((sd.enabled, sd.active, str(sd.state_machine.state), list(sd.state_machine.current_alert_types),
                         sd.mads.enabled, sd.mads.active, str(sd.mads.state_machine.state), sd.mads.enabled_toggle,
                         sd.enabled_prev, list(sd.events.names), list(sd.events_sp.names),
                         sd.events.event_counters, sd.events_sp.event_counters))
        traces.append((label, frame, states))
        if states[0] != states[1] and not mismatch:
          code = "C19" if label == "replay-start" else "C14"
          errors.append(f"{code} {label} frame {frame}: bare/wrapped {[v[:11] for v in states]}")
          mismatch = True

  # C19 a host reset must not let the plugin engage itself on the next update (C6).
  rep = Scripted()
  s = install(engagement_sm=rep)
  s.state_machine.state = State.enabled
  s.state_machine.state = State.disabled
  rep.out = ENGAGED
  if step(s) != (False, False):
    errors.append("C19/C6 plugin engaged without ENABLE after a host reset")

  # C15 absent owner, returning None and raising hooks all latch both roles.
  class Missing(Plugin):
    @classmethod
    def engagement(cls, sd):
      raise RuntimeError("hook")

    @classmethod
    def safety_fallback(cls, ctx):
      raise RuntimeError("hook")

  for role, hook in ((core_host.ENGAGEMENT, "engagement"), (core_host.FALLBACK, "safety_fallback")):
    for mode in ("absent", "none", "raise"):
      plugins.PLUGINS = tuple(c for c in default_plugins() if role not in c.CLAIMS)
      if mode != "absent":
        cls = role_plugin("broken", role, hook, None)
        if mode == "raise":
          setattr(cls, hook, Missing.__dict__[hook])
        plugins.PLUGINS += (cls,)
      sd = selfdrive()
      plugins.install_core(sd)
      out = step(sd, events=[EN.buttonEnable])
      host = getattr(sd.state_machine, "_h", None)
      if out != (False, False) or host is None or not host.latched or not sd.events_sp.has(ENSP.xpilotPluginFault):
        errors.append(f"C15 {role} {mode}: {out}, no latch/fault")

  # C16 no dependency on either default plugin, or SP's MADS constructor.
  class Keep(Plugin):
    @classmethod
    def safety_fallback(cls, ctx):
      return types.SimpleNamespace(update=lambda ctx, CS, names, names_sp: (names, names_sp))

  plugins.PLUGINS = (role_plugin("own", core_host.ENGAGEMENT, "engagement",
                                types.SimpleNamespace(state_machine=StateMachine(), mads=None)),
                     type("OwnFallback", (Keep,), {"NAME": "own_fb", "CLAIMS": (core_host.FALLBACK,)}))
  original_mads = sp_mads.ModularAssistiveDrivingSystem
  def forbidden(*args):
    raise AssertionError("host constructed SP MADS")
  sp_mads.ModularAssistiveDrivingSystem = forbidden
  try:
    sd = selfdrive()
    plugins.install_core(sd)
    out = step(sd, CS=cs(buttonEvents=lkas), events=[EN.buttonEnable])
    sd.mads.read_params()
    if (out != (True, True) or sd.mads.enabled or sd.mads.active or sd.mads.enabled_toggle or
        sd.mads.state_machine.state != MadsState.disabled):
      errors.append(f"C16 independence: {out}, MADS {sd.mads.enabled}/{sd.mads.active}/{sd.mads.enabled_toggle}")
  except Exception as e:
    errors.append(f"C16 independence: {e!r}")
  finally:
    sp_mads.ModularAssistiveDrivingSystem = original_mads

  # C17 narrow ctx built once, output validation is atomic and preserves SP counters.
  contexts = []
  class Policy:
    def update(self, ctx, CS, names, names_sp):
      contexts.append(ctx)
      assert set(vars(ctx)) == {"sm", "CP", "CP_SP", "params"}
      assert ctx.params.get_bool("Mads")
      assert ctx.sm is sd.sm and ctx.CP is sd.CP and ctx.CP_SP is sd.CP_SP
      return [EN.seatbeltNotLatched], [ENSP.manualSteeringRequired]

  sd = install(fallback=Policy())
  refs = sd.events, sd.events_sp
  for _ in range(2):
    step(sd, events=[EN.fcw, EN.doorOpen])
  if (len(contexts) != 2 or contexts[0] is not contexts[1] or
      refs != (sd.events, sd.events_sp) or sd.events.names != sorted([EN.fcw, EN.seatbeltNotLatched]) or
      not sd.events_sp.has(ENSP.manualSteeringRequired)):
    errors.append("C17 narrow context / valid policy / event identity")

  for bad in ("non-list", "unknown", "unknown-sp", "raise"):
    class BadPolicy:
      def update(self, ctx, CS, names, names_sp):
        names.clear()
        names_sp.clear()
        if bad == "raise":
          raise RuntimeError("fallback")
        if bad == "non-list":
          return (), []
        return ([999999] if bad == "unknown" else []), ([999999] if bad == "unknown-sp" else [])

    FAULTS.clear()
    sd = install(fallback=BadPolicy())
    refs = sd.events, sd.events_sp
    step(sd, events=[EN.fcw, EN.doorOpen, EN.buttonEnable], events_sp=[ENSP.manualSteeringRequired])
    host = sd.state_machine._h
    if (not host.latched or sd.enabled or sd.mads.active or refs != (sd.events, sd.events_sp) or
        not all(sd.events.has(e) for e in (EN.fcw, EN.doorOpen, EN.buttonEnable)) or
        not sd.events_sp.has(ENSP.manualSteeringRequired) or not sd.events_sp.has(ENSP.xpilotPluginFault)):
      errors.append(f"C17 {bad}: did not restore events/floors and latch")

  # C18 startup has no selfdriveState yet; publishing opens the same gate.
  plugins.PLUGINS = default_plugins()
  subscriber = types.SimpleNamespace(update=lambda t: None, alive={"selfdriveState": False})
  gate = core_host.CardGate(Params(), sm=subscriber)
  if gate.alive() or gate.sm is not subscriber:
    errors.append("C18 default owners do not gate startup")
  subscriber.alive["selfdriveState"] = True
  if not gate.alive():
    errors.append("C18 gate did not recover when selfdrived published")
  subscriber.alive["selfdriveState"] = False
  if gate.alive():
    errors.append("C18 gate did not close on selfdrived timeout")

  tree = ast.parse((ROOT / 'openpilot/selfdrive/car/card.py').read_text())
  state = next(n for c in tree.body if isinstance(c, ast.ClassDef) and c.name == 'Car'
               for n in c.body if isinstance(n, ast.FunctionDef) and n.name == 'state_update')
  end = next(i for i, n in enumerate(state.body) if isinstance(n, ast.Assign) and any(isinstance(t, ast.Tuple) for t in n.targets))
  prefix = ast.Module(body=state.body[1:end], type_ignores=[])
  for alive in (False, True):
    sent = []
    card_instance = types.SimpleNamespace(can_sock=None, plugins=types.SimpleNamespace(observe_can=lambda c: [1], core_alive=lambda: alive),
                                can_callbacks=(None, sent.append))
    exec(compile(prefix, 'card.py', 'exec'), dict(self=card_instance, messaging=types.SimpleNamespace(drain_sock_raw=lambda *a, **kw: []),
                                                can_capnp_to_list=lambda c: c))
    if bool(sent) != alive:
      errors.append(f'C20 observe_can core_alive={alive}: sent={sent}')

  filter_errors, filter_cases = event_filter_checks()
  errors += filter_errors
  print(json.dumps({"errors": errors, "event_filter_cases": filter_cases, "sp_frames": len(traces),
                    "trace_sha256": hashlib.sha256(json.dumps(traces, sort_keys=True).encode()).hexdigest(),
                    "sequences": list(sequences)}, indent=1))
  return 1 if errors else 0


if __name__ == "__main__":
  sys.exit(main())
