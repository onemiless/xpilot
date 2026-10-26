#!/usr/bin/env python3
"""G8 host failure matrix and deterministic replay (written before implementation).

LO1: pinned SP changes CC/CAN beyond 0x2B9 counter/phase.
LO2/3: OEM+1 misses first cycle, missing OEM breaks 60 ms fallback, early
        STOCK->SP candidate is not retried with the same counter.
LO4/10: CC/SP pair, active/override/cancel and controller disagree; stock actuates.
LO5: refusal changes owner, lost ack reverses it, reset does not follow STOCK.
LO6/7: confirmation <1 s, cooldown <2 s, or gas/brake/standstill/AEB/fault/
       |aEgo|>=.35/TTC<3/OEM inactive/stale/endpoint gap>.20 allows request.
LO8: two refusals, mismatch>.5 s, fault or bad hook does not lock until disengage.
LO9: planner/LongControl not seeded or takeover exceeds +.6/-1.5 m/s^3 in 1 s.
LO15: runtime readers use startup capability, or settings mutate on switches.
LO16: DRAIN fails either direction at any phase 0..40 ms/jitter 0..10 ms;
       I1/I2 fail; abort changes owner or a non-abort permits a counter jump.
LO11-14 (HUD/shadow/manual/claim registration) belong to 49B, not this gate.

The Panda below is a Python contract model, NOT firmware or physical proof.
Host replay calls production controlsd/card/controller with deterministic inputs.
"""
import argparse
import hashlib
import json
import math
import subprocess
import traceback
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
BASE = '18b988ced1f24bc3ea8cf3620f7f499902f68867'


class Panda:
  def __init__(self):
    self.owner = 'stock'
    self.pending = None
    self.deadline = 0
    self.drain = None
    self.last = None
    self.oem = None
    self.reason = ''
    self.abort_jump = False
    self.trace = []

  def abort(self, reason):
    self.abort_jump = self.drain is not None
    self.pending, self.drain, self.reason = None, None, reason

  def tick(self, t):
    if self.pending is not None and t >= self.deadline:
      self.abort('DEADLINE')
    elif self.drain is not None and t - self.drain >= 50:
      self.abort('DRAIN_TIMEOUT')

  def request(self, t, target):
    self.tick(t)
    if target != self.owner and self.pending is None:
      self.pending, self.deadline, self.reason = target, t + 250, ''

  def frame(self, t, source, counter, envelope=(0., 0.), valid=True, aeb=False):
    self.tick(t)
    admitted, handoff = False, False
    if aeb and source == 'stock':
      self.abort('AEB')
      self.trace.append((t, source, counter, True, self.owner, 'AEB'))
      return True
    if self.pending is not None and not valid:
      self.abort('RX_INVALID')
    gap = math.inf if self.last is None else t - self.last[0]
    if source == 'stock':
      self.oem = (t, counter, envelope)
      if self.owner == 'stock':
        admitted = valid
      elif self.pending == 'stock':
        if self.drain is None:
          if counter == self.last[2]:
            self.drain = t
        elif counter != (self.last[2] + 1) % 8:
          self.abort('COUNTER')
        elif max(abs(a-b) for a, b in zip(envelope, self.last[3], strict=True)) > .200001:
          self.abort('ENVELOPE')
        elif not 5 <= gap <= 80:
          self.abort('DEADLINE')
        else:
          admitted, handoff = True, True
    elif self.owner == 'sp':
      admitted = self.drain is None and valid and self.oem is not None and t - self.oem[0] <= 60
    elif self.pending == 'sp' and self.oem is not None:
      if max(abs(a-b) for a, b in zip(envelope, self.oem[2], strict=True)) > .200001:
        self.abort('ENVELOPE')
      elif counter == (self.last[2] + 1) % 8 and 5 <= gap <= 80 and valid:
        admitted, handoff = True, True
    if admitted:
      if self.last is not None:
        if not self.abort_jump:
          assert counter == (self.last[2] + 1) % 8, (self.trace, t, source, counter)
        if handoff:
          assert 5 <= gap <= 80
      if handoff:
        self.owner, self.pending, self.drain = source, None, None
      self.last = (t, source, counter, envelope)
      self.abort_jump = False
    self.trace.append((t, source, counter, admitted, self.owner, self.reason))
    return admitted


def drain_replay():
  traces = []
  for phase in range(41):
    for jitter in range(11):
      p = Panda()
      p.frame(0, 'stock', 0)
      p.request(1, 'sp')
      events = [(t, 0) for t in range(40, 401, 40)]
      # Every host cycle is retained; a new OEM triggers OEM+1. Pending SP
      # retries the same candidate until Panda acknowledges it.
      events += [(phase + 10*k + (jitter if k % 2 else 0), 1) for k in range(40)]
      sent_oem = None
      requested_stock = False
      transitions = []
      for t, host in sorted(events):
        before = p.owner
        if not host:
          p.frame(t, 'stock', (t // 40) % 8)
        elif p.oem is not None:
          if p.owner == 'sp' and not requested_stock:
            p.request(t + .1, 'stock')
            requested_stock = True
          if p.owner == 'sp' and sent_oem != p.oem[0] or p.pending == 'sp':
            p.frame(t, 'sp', (p.oem[1] + 1) % 8)
            sent_oem = p.oem[0]
        if p.owner != before:
          transitions.append((t, p.owner))
        if requested_stock and p.owner == 'stock':
          break
      assert [o for _, o in transitions] == ['sp', 'stock'], (phase, jitter, p.trace)
      assert transitions[0][0] < 251 and transitions[1][0] < p.deadline
      traces.append({'phase_ms': phase, 'jitter_ms': jitter, 'transitions': transitions,
                     'trace': p.trace})
  p = Panda()
  p.frame(0, 'stock', 0)
  p.request(1, 'sp')
  assert not p.frame(3, 'sp', 1)
  assert p.pending == 'sp'
  assert p.frame(10, 'sp', 1)
  p.request(11, 'stock')
  assert not p.frame(40, 'stock', 1)
  assert not p.frame(50, 'sp', 2)
  p.tick(90)
  assert p.owner == 'sp' and p.reason == 'DRAIN_TIMEOUT'
  p.frame(91, 'stock', 3)
  assert p.frame(100, 'sp', 4)  # The single documented abort jump exception.
  p.request(101, 'stock')
  assert p.frame(102, 'stock', 4, aeb=True) and p.owner == 'sp' and p.pending is None
  return {'schedules': traces, 'abort_aeb_trace': p.trace}


class SM(dict):
  def __init__(self, **messages):
    super().__init__(messages)
    self.frame = 0
    self.logMonoTime = dict.fromkeys(messages, 0)
    self.valid = dict.fromkeys(messages, True)
    self.alive = dict.fromkeys(messages, True)
    self.seen = dict.fromkeys(messages, True)
    self.updated = dict.fromkeys(messages, True)

  def all_checks(self, service_list=None):
    return all(self.valid[s] and self.alive[s] for s in (self if service_list is None else service_list))

  def all_alive(self, services):
    return all(self.alive[s] for s in services)


class PM:
  def __init__(self):
    self.sent = {}

  def send(self, service, message):
    self.sent[service] = message.as_reader() if hasattr(message, 'as_reader') else message


def host_replay():
  from openpilot.cereal import custom, log
  from opendbc.car import Bus, structs
  from opendbc.car.structs import car
  from opendbc.car.tesla.interface import CarInterface
  from opendbc.can import CANParser
  from openpilot.selfdrive.controls import controlsd
  from openpilot.selfdrive.controls.lib import long_owner
  from openpilot.selfdrive.controls.lib.longcontrol import LongControl
  from openpilot.selfdrive.car.card import Car
  from openpilot.selfdrive.car.helpers import convert_carControlSP, convert_to_capnp

  CP = CarInterface.get_non_essential_params('TESLA_MODEL_Y')
  CP.openpilotLongitudinalControl = True
  CP_SP = structs.CarParamsSP(pcmCruiseSpeed=True)
  cs = car.CarState.new_message(vEgo=20., vEgoRaw=20., aEgo=.1, canValid=True, vCruise=90., vCruiseCluster=90.)
  cs.cruiseState.enabled = True
  health = log.PandaState.new_message(safetyModel='tesla', longOwnerAvailable=True, longOwner=0)
  sp = custom.CarStateSP.new_message()
  sp.teslaLongControl = {'monoTime': 1, 'accState': 4, 'accelMin': .1, 'accelMax': .1}
  sm = SM(carState=cs, carStateSP=sp, pandaStates=[health], radarState=log.RadarState.new_message(),
          selfdriveState=log.SelfdriveState.new_message(enabled=True, active=True),
          longitudinalPlan=log.LongitudinalPlan.new_message(aTarget=.1), onroadEvents=[])
  ctx = NS(CP=CP, CP_SP=CP_SP, sm=sm, LoC=NS(last_output_accel=.1))

  def tick(owner, t, desired='sp'):
    sm.logMonoTime['pandaStates'] = int(t * 1e9)
    sp.teslaLongControl.monoTime = int(t * 1e9)
    cc_sp = custom.CarControlSP.new_message()
    with patch.object(long_owner, 'desired_owner', return_value=desired):
      owner.update(ctx, cs, cc_sp, t)
    return cc_sp

  o = long_owner.LongOwner(CP)
  assert tick(o, 1).longOwnerRequest == 255
  assert tick(o, 1.99).longOwnerRequest == 255
  req = tick(o, 2)
  assert req.longOwnerRequest == 1 and str(req.longOwner) == 'stock'
  health.longOwnerPending = 1
  assert str(tick(o, 2.1).longOwner) == 'stock'
  health.longOwner, health.longOwnerPending = 1, 255
  ack = tick(o, 2.2)
  assert str(ack.longOwner) == 'sp' and ack.longOwnerRequest == 255
  assert tick(o, 3.2, 'stock').longOwnerRequest == 255
  requested = tick(o, 4.21, 'stock')
  assert requested.longOwnerRequest == 0, requested.to_dict()
  health.longOwnerRefusal = 15
  refused = tick(o, 4.3, 'stock')
  assert str(refused.longOwner) == 'sp' and refused.longOwnerRequest == 255
  tick(o, 5.3, 'stock')
  assert tick(o, 6.31, 'stock').longOwnerRequest == 0
  health.longOwnerRefusal = 20
  assert tick(o, 6.4, 'stock').longOwnerLocked
  assert str(tick(o, 10, 'stock').longOwner) == 'sp'
  health.longOwner = 0  # reset, not an implicit host reversal
  assert str(tick(o, 10.1, 'stock').longOwner) == 'stock'
  sm['selfdriveState'].enabled = False
  assert not tick(o, 10.2).longOwnerLocked
  sm['selfdriveState'].enabled = True
  health.longOwnerRefusal = 0

  # Each veto is exercised at the request boundary with otherwise valid inputs.
  vetoes = []
  for name, value in [('gasPressed', True), ('brakePressed', True), ('standstill', True),
                      ('stockAeb', True), ('accFaulted', True), ('aEgo', .35), ('aEgo', -.35)]:
    old = getattr(cs, name)
    setattr(cs, name, value)
    x = long_owner.LongOwner(CP)
    tick(x, 20)
    blocked = tick(x, 21)
    assert blocked.longOwnerRequest == 255 and blocked.longOwnerRefusal, name
    vetoes.append((name, value, blocked.longOwnerRefusal))
    setattr(cs, name, old)
  for field, value in [('accState', 2), ('accelMin', .34), ('accelMax', .34)]:
    old = getattr(sp.teslaLongControl, field)
    setattr(sp.teslaLongControl, field, value)
    x = long_owner.LongOwner(CP)
    tick(x, 22)
    assert tick(x, 23).longOwnerRequest == 255, field
    setattr(sp.teslaLongControl, field, old)
  lead = sm['radarState'].leadOne
  lead.present, lead.dRel, lead.vRel = True, 29., -10.
  x = long_owner.LongOwner(CP)
  tick(x, 24)
  assert tick(x, 25).longOwnerRequest == 255
  lead.dRel = 30.
  assert tick(long_owner.LongOwner(CP), 26).longOwnerRequest == 255
  lead.present = False
  x = long_owner.LongOwner(CP)
  tick(x, 30)
  tick(x, 31)
  assert tick(x, 31.5).longOwnerLocked is False
  assert tick(x, 31.51).longOwnerLocked
  x = long_owner.LongOwner(CP)
  tick(x, 40)
  sm.alive['pandaStates'] = False
  assert tick(x, 41).longOwnerLocked
  sm.alive['pandaStates'] = True
  for bad in ['invalid', 1, float('nan')]:
    assert tick(long_owner.LongOwner(CP), 50, bad).longOwnerLocked
  with patch.object(long_owner, 'desired_owner', side_effect=ValueError('bad policy')):
    out = custom.CarControlSP.new_message()
    long_owner.LongOwner(CP).update(ctx, cs, out, 51)
    assert out.longOwnerLocked
  health.longOwnerAvailable = False
  assert str(tick(long_owner.LongOwner(CP), 52).longOwner) == 'sp'
  health.longOwnerAvailable = True

  # Execute the actual state_control/publish path, using a deterministic lateral
  # controller. Base methods are loaded from the immutable task base for LO1.
  old_ns = {'__name__': 'long_owner_baseline'}
  exec(subprocess.check_output(['git', 'show', f'{BASE}:openpilot/selfdrive/controls/controlsd.py'], cwd=ROOT, text=True), old_ns)
  old_controls = old_ns['Controls']
  old_can_ns = {}
  exec(subprocess.check_output(['git', 'show', f'{BASE}:opendbc_repo/opendbc/car/tesla/carcontroller.py'], cwd=ROOT, text=True), old_can_ns)

  def controls(cls, available=False, owner=1):
    c = cls.__new__(cls)
    c.CP, c.CP_SP, c.CI = CP, convert_to_capnp(CP_SP), CarInterface(CP, CP_SP)
    c.LoC = LongControl(CP, c.CP_SP)
    c.VM = c.CI.CC.VM
    c.LaC = NS(reset=lambda: None, update=lambda *a: (0., 0., log.ControlsState.LateralAngleState.new_message()))
    c.curvature = c.desired_curvature = 0.
    c.calibrated_pose, c.steer_limited_by_safety, c.plugin_lat = None, False, []
    c.get_lat_active = lambda sm: True
    c.long_owner = long_owner.LongOwner(CP)
    c.sm = SM(**sm)
    c.sm['pandaStates'] = [log.PandaState.new_message(safetyModel='tesla', longOwnerAvailable=available, longOwner=owner)]
    c.sm.update(vehicleParameters=log.VehicleParameters.new_message(stiffnessFactor=1., steerRatio=15.),
                modelV2=log.ModelDataV2.new_message(), lateralManeuverPlan=log.LateralManeuverPlan.new_message(),
                lateralDelay=log.LateralDelay.new_message(lateralDelay=.1), carOutput=car.CarOutput.new_message(),
                driverMonitoringState=log.DriverMonitoringState.new_message(), driverAssistance=log.DriverAssistance.new_message(),
                selfdriveStateSP=custom.SelfdriveStateSP.new_message())
    c.sm.valid.update(dict.fromkeys(c.sm, True))
    c.sm.alive.update(dict.fromkeys(c.sm, True))
    c.sm.logMonoTime.update(dict.fromkeys(c.sm, 0))
    c.pm = PM()
    return c

  baseline, legacy, pinned = controls(old_controls), controls(controlsd.Controls), controls(controlsd.Controls, True)
  baseline.CI.CC = old_can_ns['CarController']({Bus.party: 'tesla_model3_party'}, CP, CP_SP)
  cc_hash = hashlib.sha256()
  can_hash = hashlib.sha256()
  dynamic = controls(controlsd.Controls, True, 0)
  counter_rows = []
  decoder = CANParser('tesla_model3_party', [('DAS_control', 25)], 0)
  base_decoder = CANParser('tesla_model3_party', [('DAS_control', 25)], 0)
  ramp = []
  for i in range(240):
    now = 1_000_000_000 + i * 10_000_000
    for c in [baseline, legacy, pinned, dynamic]:
      c.sm.frame = i
      c.sm.logMonoTime['carState'] = now
      c.sm.logMonoTime['pandaStates'] = now
      c.sm['longitudinalPlan'].aTarget = .1 + .05 * math.sin(i / 10)
    outputs = []
    for c in [baseline, legacy, pinned]:
      cc, lac = c.state_control()
      c.publish(cc, lac)
      outputs.append(cc.to_dict())
    assert outputs[0] == outputs[1] == outputs[2], i
    cc_hash.update(json.dumps(outputs[0], sort_keys=True).encode())
    for c in [baseline, legacy, pinned, dynamic]:
      c.CI.CS.out = cs
      c.CI.CS.hands_on_level = 0
      c.CI.CS.das_control = {'DAS_controlCounter': (i // 4) % 8}
      c.CI.CS.das_control_mono_time = now - (i % 4) * 10_000_000
    cc_base = baseline.pm.sent['carControl'].carControl
    cc_new = legacy.pm.sent['carControl'].carControl
    old_act, old_can = baseline.CI.apply(cc_base, structs.CarControlSP(), now)
    new_act, new_can = legacy.CI.apply(cc_new, convert_carControlSP(legacy.pm.sent['carControlSP'].carControlSP), now)
    assert old_can == new_can and old_act.to_dict() == new_act.to_dict(), i
    can_hash.update(repr(old_can).encode())
    _, locked_can = pinned.CI.apply(pinned.pm.sent['carControl'].carControl,
                                    convert_carControlSP(pinned.pm.sent['carControlSP'].carControlSP), now)
    assert [m for m in locked_can if m[0] != 0x2B9] == [m for m in old_can if m[0] != 0x2B9]
    for addr, dat, bus in locked_can:
      if addr == 0x2B9:
        decoder.update([(now, [(addr, dat, bus)])])
        assert decoder.vl['DAS_control']['DAS_controlCounter'] == ((i // 4) + 1) % 8, (i, addr, bus, dat.hex(), decoder.vl['DAS_control'])
        old_long = [m for m in old_can if m[0] == 0x2B9]
        base_decoder.update([(now, old_long)])
        def signals(cp):
          return {k: v for k, v in cp.vl['DAS_control'].items() if k not in ('DAS_controlCounter', 'DAS_controlChecksum')}
        assert signals(decoder) == signals(base_decoder), i
        counter_rows.append((i, dat.hex()))
    if i == 4:
      dynamic.sm['pandaStates'][0].longOwner = 1
    cc, lac = dynamic.state_control()
    dynamic.publish(cc, lac)
    pair = dynamic.pm.sent['carControlSP'].carControlSP
    assert pair.controlMonoTime == dynamic.pm.sent['carControl'].logMonoTime
    assert cc.longActive == pair.longActive == (i >= 4)
    assert not cc.cruiseControl.override and not cc.cruiseControl.cancel
    out, sends = dynamic.CI.apply(dynamic.pm.sent['carControl'].carControl, convert_carControlSP(pair), now)
    if i < 4:
      assert all(m[0] != 0x2B9 for m in sends)
    elif any(m[0] == 0x2B9 for m in sends):
      ramp.append((now, out.accel))
  assert len(counter_rows) == 60
  assert abs(ramp[0][1] - cs.aEgo) < 1e-6
  for (t0, a0), (t1, a1) in zip(ramp, ramp[1:], strict=False):
    if t1 - ramp[0][0] <= 1_000_000_000:
      assert -1.5 * (t1-t0)/1e9 - 1e-6 <= a1-a0 <= .6 * (t1-t0)/1e9 + 1e-6

  # Real card gate rejects an unpaired event before invoking apply or plugins.
  c = Car.__new__(Car)
  c.initialized_prev = True
  c.sm = SM(carControl=cc, carControlSP=pair)
  c.sm.logMonoTime['carControl'] = pair.controlMonoTime + 1
  c.plugins = NS(core_alive=lambda: True)
  c.CP = CP
  c.controls_update(cs, cc, pair)
  c.sm.logMonoTime['carControl'] = pair.controlMonoTime
  c.CI, c.pm = dynamic.CI, PM()
  c.plugins.control_sends = lambda *args: []
  c.controls_update(cs, dynamic.pm.sent['carControl'].carControl, pair)
  assert 'sendcan' in c.pm.sent

  # Drive the actual CAN controller at 100 Hz with frozen OEM observations.
  # Both jerk bounds must bind, and a missing OEM must restore 25 Hz sending.
  stress_ramps = []
  for target, jerk in [(2., .6), (-3., -1.5)]:
    ci = CarInterface(CP, CP_SP)
    ci.CS.out, ci.CS.hands_on_level = cs, 0
    ci.CS.das_control = {'DAS_controlCounter': 0}
    ci.CS.das_control_mono_time = 1_000_000_000
    ci.CC.long_owner_last = False
    command = car.CarControl.new_message(enabled=True, longActive=True)
    command.actuators.accel = target
    pair_sp = structs.CarControlSP(longOwner='sp', longOwnerAvailable=True, longActive=True)
    rows = []
    for i in range(110):
      now = 1_000_000_000 + i * 10_000_000
      if i % 4 == 0:
        ci.CS.das_control['DAS_controlCounter'] = (i // 4) % 8
        ci.CS.das_control_mono_time = now
      applied, sends = ci.apply(command.as_reader(), pair_sp, now)
      if any(m[0] == 0x2B9 for m in sends):
        if i <= 100:
          assert abs(applied.accel - (cs.aEgo + jerk*i*.01)) < 1e-6
        else:
          assert applied.accel == target
        rows.append((i, applied.accel))
    stress_ramps.append(rows)
  fallback_times, fallback_counters = [], []
  ci.CC.takeover_time = None
  for i in range(16):
    now = 3_000_000_000 + i * 10_000_000
    if i == 0:
      ci.CS.das_control_mono_time = now
    _, sends = ci.apply(command.as_reader(), pair_sp, now)
    for addr, data, bus in sends:
      if addr == 0x2B9:
        decoder.update([(now, [(addr, data, bus)])])
        fallback_times.append(i * 10)
        fallback_counters.append(int(decoder.vl['DAS_control']['DAS_controlCounter']))
  assert fallback_times == [0, 60, 100, 140], fallback_times
  assert all(b == (a+1) % 8 for a, b in zip(fallback_counters, fallback_counters[1:], strict=False))
  candidate_ci = CarInterface(CP, CP_SP)
  candidate_ci.CS.out, candidate_ci.CS.hands_on_level = cs, 0
  candidate_ci.CS.das_control = {'DAS_controlCounter': 0}
  candidate_ci.CS.das_control_mono_time = 4_000_000_000
  candidate_pair = structs.CarControlSP(longOwner='stock', longOwnerAvailable=True, longOwnerRequest=1)
  p = Panda()
  p.frame(0, 'stock', 0, (.1, .1))
  p.request(1, 'sp')
  candidates = []
  for ms in (3, 13):
    _, sends = candidate_ci.apply(command.as_reader(), candidate_pair, 4_000_000_000 + ms*1_000_000)
    msg = next(m for m in sends if m[0] == 0x2B9)
    decoder.update([(4_000_000_000 + ms*1_000_000, [msg])])
    signals = decoder.vl['DAS_control']
    cntr = int(signals['DAS_controlCounter'])
    accepted = p.frame(ms, 'sp', cntr, (signals['DAS_accelMin'], signals['DAS_accelMax']))
    candidates.append((ms, cntr, accepted))
  assert candidates == [(3, 1, False), (13, 1, True)], candidates
  assert p.owner == 'sp'
  return {'frames': 240, 'cc_sha256': cc_hash.hexdigest(), 'legacy_can_sha256': can_hash.hexdigest(),
          'counter_frames': counter_rows, 'takeover_ramp': ramp, 'vetoes': vetoes,
          'stress_ramps': stress_ramps, 'fallback_ms': fallback_times, 'early_candidates': candidates}


def planner_replay():
  import numpy as np
  from openpilot.cereal import custom, log
  from opendbc.car import structs
  from opendbc.car.structs import car
  from opendbc.car.tesla.interface import CarInterface
  from openpilot.selfdrive.controls.lib import longitudinal_planner as lp
  CP = CarInterface.get_non_essential_params('TESLA_MODEL_Y')
  CP.openpilotLongitudinalControl = True
  CS = car.CarState.new_message(vEgo=20., aEgo=.1, vCruise=90., vCruiseCluster=90.)
  ccsp = custom.CarControlSP.new_message(longOwner='stock', longOwnerAvailable=True)
  sm = SM(carState=CS, carControlSP=ccsp, controlsState=log.ControlsState.new_message(),
          selfdriveState=log.SelfdriveState.new_message(enabled=True),
          radarState=log.RadarState.new_message(), modelV2=log.ModelDataV2.new_message())
  # Keep unrelated SCC/map/boost policies out of the replay; MPC and owner
  # reset/seed paths run without a solver stub.
  with patch.object(lp.LongitudinalPlannerSP, '__init__', return_value=None), \
       patch.object(lp.LongitudinalPlannerSP, 'update'), \
       patch.object(lp.LongitudinalPlannerSP, 'update_targets', side_effect=lambda self, sm, v, a, cruise: (cruise, a)), \
       patch.object(lp, 'AccelBoost', return_value=NS(update=lambda sm, a, m, c: a)):
    planner = lp.LongitudinalPlanner(CP, structs.CarParamsSP())
    planner.plugin_long_planner = []
    planner.v_desired_filter.x, planner.output_a_target = 100., 2.
    planner.update(sm)
    assert abs(planner.v_desired_filter.x - CS.vEgo) < .1
    ccsp.longOwner = 'sp'
    planner.mpc.a_prev[:] = 2.
    captures = []
    run = planner.mpc.run

    def capture_run():
      captures.append({'x0': planner.mpc.x0.tolist(), 'a_prev': planner.mpc.a_prev.tolist()})
      run()

    from openpilot.selfdrive.controls.lib.longitudinal_mpc_lib import long_mpc
    baseline = lp.LongitudinalMpc()
    baseline.set_cur_state(CS.vEgo, CS.aEgo)
    with patch.object(long_mpc.cloudlog, 'warning') as warnings:
      baseline.update(sm['radarState'])
      baseline_rc = [int(str(call.args[-1]).rsplit(' ', 1)[-1]) for call in warnings.call_args_list]
    with patch.object(planner.mpc, 'set_cur_state', wraps=planner.mpc.set_cur_state) as seed, \
         patch.object(planner.mpc, 'run', side_effect=capture_run), \
         patch.object(long_mpc.cloudlog, 'warning') as warnings:
      planner.update(sm)
      candidate_rc = [int(str(call.args[-1]).rsplit(' ', 1)[-1]) for call in warnings.call_args_list]
    assert seed.call_args_list[0].args == (CS.vEgo, CS.aEgo)
    assert captures[0]['x0'][1:] == [CS.vEgo, CS.aEgo]
    assert all(abs(a - CS.aEgo) < 1e-6 for a in captures[0]['a_prev'])
    assert baseline_rc == candidate_rc, (baseline_rc, candidate_rc)
    if not candidate_rc:
      assert abs(planner.mpc.a_solution[0] - CS.aEgo) < 1e-5
    assert abs(planner.v_desired_filter.x - CS.vEgo) < .1
    assert np.isfinite(planner.output_a_target)
  return {'seed_v': CS.vEgo, 'seed_a': CS.aEgo, 'baseline_solver_failures': baseline_rc,
          'candidate_solver_failures': candidate_rc, 'pre_solve': captures,
          'accels': planner.mpc.a_solution.tolist(), 'v_filter': planner.v_desired_filter.x,
          'a_target': float(planner.output_a_target)}


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--output', required=True, type=Path)
  args = parser.parse_args()
  args.output.mkdir(parents=True, exist_ok=True)
  result = {'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
            'scope': 'deterministic host replay and Python Panda model; no device/firmware/physical proof',
            'checks': {}, 'deferred': ['LO11-14: 49B HUD/shadow/manual/plugin registration']}
  for name, check in [('LO16_DRAIN', drain_replay), ('LO1_10_15_host', host_replay), ('LO9_planner', planner_replay)]:
    try:
      result['checks'][name] = {'rc': 0, 'evidence': check()}
    except Exception:
      result['checks'][name] = {'rc': 1, 'error': traceback.format_exc()}
  paths = subprocess.check_output(['git', 'diff', '--name-only', BASE], cwd=ROOT, text=True).splitlines()
  paths += [str(Path(__file__).relative_to(ROOT))]
  result['sha256'] = {p: hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in sorted(set(paths))
                      if (ROOT/p).is_file() and p != 'result.json' and not p.endswith('REPORT.md')}
  result['status'] = 'PASS' if all(c['rc'] == 0 for c in result['checks'].values()) else 'FAIL'
  planner = result['checks'].get('LO9_planner', {}).get('evidence', {})
  if planner.get('candidate_solver_failures'):
    result['gaps'] = ['Native MPC QP fails identically without owner changes; pre-solve seed verified, solved trajectory unproven.']
    if result['status'] == 'PASS':
      result['status'] = 'PASS_WITH_GAPS'
  (args.output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
  print(json.dumps({k: v if k != 'checks' else {n: {x: y for x, y in c.items() if x != 'evidence'} for n, c in v.items()}
                    for k, v in result.items() if k != 'sha256'}, indent=2))
  return int(result['status'] == 'FAIL')


if __name__ == '__main__':
  raise SystemExit(main())
