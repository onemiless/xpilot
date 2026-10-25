#!/usr/bin/env python3
"""S1 host replay, written before implementation. No phone, camera, GPU or real Params.

Failure cases fixed first:
F1 absent/unloaded phone: local every frame, zero wait.
F2 readiness/address/discovery order: host does not discover/connect; late peer recovers.
F3 truncated/oversized/fragmented IPC, bad BGM1 magic/version/type/flags/length.
F4 NaN/inf/zero raw, decoder error, missing/wrong/nonfinite parsed heads: trip lock.
F5 wrong session/epoch/frame/EOF, future timestamps, duplicate/out-of-order replies.
F6 late/burst/200ms-stalled sender: one-slot overwrite, deadline, no catch-up.
F7 frozen/crashed child, new epoch without dual IDR, reset/zero-pair, five warmups.
F8 local load/run failure propagates; phone never conceals a base failure.
F9 one publisher, four messages selected together, baseline bytes unchanged.
F13 trajectory/lead/pose/action jumps fail admission; fresh-only fade-in, no stale fade-out.

The replay executes unmodified main() and all real postprocessing/serialization. Only
VisionIPC, inference and input services are deterministic doubles. BGM1 peer is a
separate process. This proves host behavior, not QNN/profile or device timing.
"""
import argparse
import contextlib
import csv
import fcntl
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import time
import types
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'opendbc_repo')]

# Device parameters are deliberately isolated, including imports in DesireHelper/RELC.
class Params:
  def __init__(self, *args):
    pass

  def get(self, key, **kwargs):
    return 0.1 if key == 'LagdValueCache' else 10 if key == 'LaneTurnValue' else None

  def get_bool(self, key):
    return False

  def put_bool(self, key, value):
    assert not value, (key, value)

  def remove(self, key):
    pass


sys.modules['openpilot.common.params'] = types.SimpleNamespace(Params=Params, UnknownKeyName=KeyError)
from openpilot.cereal import messaging
from openpilot.selfdrive.modeld import modeld
from openpilot.selfdrive.modeld.parse_model_outputs import Parser
from openpilot.selfdrive.controls.lib.drive_helpers import clip_curvature
from openpilot.selfdrive.selfdrived.selfdrived import SelfdriveD
from openpilot.selfdrive.selfdrived.events import Events, ET
from openpilot.sunnypilot.selfdrive.selfdrived.events import EventsSP
from opendbc.car.structs import car

META = struct.Struct('<4sIQIIQQ30f')
ENVELOPE = struct.Struct('<QQQII')
OFFSET, SIZE = 256, 16384
SLICES = {'lane_lines': (0, 528), 'lane_lines_prob': (528, 536), 'road_edges': (536, 800),
          'meta': (800, 855), 'desire_pred': (855, 887), 'pose': (887, 899),
          'wide_from_device_euler': (899, 905), 'road_transform': (905, 917),
          'plan': (917, 1907), 'lead': (1907, 2051), 'lead_prob': (2051, 2054), 'desire_state': (2054, 2062)}


def raw_output(delta=0.):
  raw = np.full(2066, -2., dtype='<f4')
  plan = np.zeros((33, 15), dtype=np.float32)
  plan[:, 0] = np.array(modeld.ModelConstants.T_IDXS) * 20
  plan[:, 1] = delta
  plan[:, 3] = 20
  raw[917:1412] = plan.ravel()
  raw[887:893] = delta
  lead = np.zeros((3, 6, 4), dtype=np.float32)
  lead[:, :, 0] = 30 + delta
  lead[:, :, 2] = 20
  raw[1907:1979] = lead.ravel()
  raw[2062:2064] = [0.01 + delta, 0.1 + delta]
  return raw


def decode(raw):
  # Fake qualified common-model profile; production qualification belongs to P1.
  outs = Parser().parse_outputs({k: raw[a:b][None].copy() for k, (a, b) in SLICES.items()})
  outs['action'] = raw[2062:2064][None].copy()  # mean, then log-sigma; never four controls
  outs['raw_pred'] = raw.copy()
  return outs


def admit(local, remote):
  return all(np.max(np.abs(remote[k] - local[k])) <= .25 for k in ('plan', 'lead', 'pose', 'action') if k in local)


def health_replay(messages, rows):
  """Run the real selfdrived event method; unrelated car/device inputs are healthy fixtures."""
  driver = SelfdriveD.__new__(SelfdriveD)
  driver.__dict__.update(params=Params(), CP=car.CarParams.new_message(brand='tesla', pcmCruise=True),
                         events=Events(), events_sp=EventsSP(), plugin_alerts=[], initialized=True,
                         enabled=True, big_model_loading=False, big_model_active=False, big_model_failed=False,
                         big_model_ready_t=-10, startup_event=None, dm_enabled=False, CS_prev=car.CarState.new_message(),
                         car_events=types.SimpleNamespace(update=lambda *a: Events()),
                         car_events_sp=types.SimpleNamespace(update=lambda *a: EventsSP()),
                         disengage_on_accelerator=False, is_ldw_enabled=False, calibrated_pose=None,
                         excessive_actuation=False, mismatch_counter=0, not_running_prev=None,
                         ignored_processes={'mapd'}, rk=types.SimpleNamespace(lagging=False),
                         camera_packets=['narrowRoadCameraState', 'wideRoadCameraState', 'cabinCameraState'],
                         sensor_packets=['accelerometer', 'gyroscope'], gps_location_service='gpsLocation',
                         cruise_mismatch_counter=0, last_steering_pressed_frame=0, distance_traveled=0,
                         mads=types.SimpleNamespace(enabled=False))
  services = ['modelV2', 'modelDataV2SP']
  sm = messaging.SubMaster(services, frequency=20)
  # Only model channels are replayed; all other services are fixed healthy inputs.
  others = ['controlsState', 'deviceState', 'managerState', 'extrinsicsCalibration', 'longitudinalPlanSP',
            'carControl', 'peripheralState', 'driverAssistance', 'deviceMotion', 'longitudinalPlan',
            'lateralManeuverPlan', 'alertDebug', 'userBookmark', 'vehicleParameters'] + driver.camera_packets + driver.sensor_packets + ['gpsLocation']
  for service in others:
    sm.data[service] = getattr(messaging.new_message(service), service)
    sm.seen[service] = False
    sm.recv_frame[service] = 0
    sm.alive[service] = sm.valid[service] = sm.freq_ok[service] = True
  sm.data['deviceState'].freeSpacePercent = 90
  sm.data['extrinsicsCalibration'].calStatus = 'calibrated'
  sm.data['controlsState'].lateralControlState.init('pidState')
  sm.data['managerState'].processes = [{'name': 'lean_modeld', 'running': True, 'shouldBeRunning': True}]
  sm.data['pandaStates'] = []
  driver.sm = sm
  cs = car.CarState.new_message(canValid=True, vEgo=20)
  events = []
  for i, row in enumerate(rows):
    group = [messaging.log_from_bytes(messages[i * 4 + j]) for j in (0, 3)]
    if i == 0:
      sm.update_msgs(row['publish'] / 1e9 - .05, group)
    sm.update_msgs(row['publish'] / 1e9, group)
    for service in others:
      sm.updated[service] = False
      sm.recv_frame[service] = sm.frame if service not in ('lateralManeuverPlan', 'alertDebug') else 0
    driver.update_events(cs)
    names = [driver.events.get_event_name(e) for e in driver.events.names]
    assert not driver.events.contains(ET.NO_ENTRY), names
    assert not set(names) & {'processNotRunning', 'modeldLagging', 'commIssue', 'commIssueAvgFreq'}, names
    events.append(names)
  return events


def peer(path, conn):
  fd = os.open(path, os.O_RDWR)
  saved = None
  while True:
    command = conn.recv()
    if command == 'crash':
      os._exit(7)
    if command in ('freeze', 'stall'):
      fcntl.lockf(fd, fcntl.LOCK_EX, SIZE, 0)
      conn.send('frozen')
      if command == 'stall':
        time.sleep(.2)
      else:
        conn.recv()
      fcntl.lockf(fd, fcntl.LOCK_UN, SIZE, 0)
      continue
    scenario, epoch, now, expected_inputs = command
    meta = META.unpack(os.pread(fd, META.size, 0))
    assert meta[:2] == (b'XMS1', 1)
    session, frame, eof = meta[2], meta[3], meta[5]
    assert meta[4] == frame and meta[6] == eof
    assert meta[7:25] == tuple(np.eye(3, dtype=np.float32).ravel()) * 2
    assert len(meta[25:]) == 12
    np.testing.assert_allclose(meta[25:], expected_inputs, atol=1e-7, rtol=0)
    raw = raw_output(.1)
    flags, paired, version, kind, length = 0, 3, 2, 0x11, 8280
    arrival = eof + 10_000_000
    if scenario in ('nan', 'inf', 'zero'):
      raw[:] = {'nan': np.nan, 'inf': np.inf, 'zero': 0}[scenario]
    if scenario.startswith('jump_'):
      raw[{'jump_plan': 918, 'jump_lead': 1907, 'jump_pose': 887, 'jump_action': 2062}[scenario]] = 1000
    if scenario == 'version':
      version = 3
    if scenario == 'type':
      kind = 0x7f
    if scenario == 'flags':
      flags = 4
    if scenario == 'length':
      length = 8272
    if scenario == 'session':
      session ^= 1
    if scenario == 'epoch_zero':
      epoch = 0
    if scenario == 'ipc_flags':
      paired = 4
    if scenario == 'frame':
      frame += 1
    if scenario == 'eof':
      eof -= 1
    if scenario == 'future':
      arrival = now + 1
    if scenario == 'future_eof':
      eof = now + 1
    if scenario == 'stale':
      frame -= 1
      eof -= 50_000_000
    if scenario == 'late':
      arrival = eof + 46_000_000
    if scenario == 'unpaired':
      paired = 0
    if scenario in ('reset', 'zero_pair'):
      flags = 1 if scenario == 'reset' else 2
    wire = struct.pack('<4sBBHIIQ', b'BAD!' if scenario == 'magic' else b'BGM1', version, kind, flags, frame, length, eof)
    wire += raw.tobytes() + bytes(32)
    if scenario == 'truncated':
      wire = wire[:400]
    if scenario == 'oversized':
      wire += b'x'
    data = ENVELOPE.pack(session, epoch, arrival, paired, len(wire)) + wire
    if scenario == 'replay':
      data = saved
    if scenario == 'partial':
      data = data[:20]
    saved = data
    for _ in range(200 if scenario == 'burst' else 1):
      fcntl.lockf(fd, fcntl.LOCK_EX, SIZE - OFFSET, OFFSET)
      padded = data + bytes(SIZE - OFFSET - len(data))
      os.pwrite(fd, bytes(ENVELOPE.size), OFFSET)
      os.pwrite(fd, padded[ENVELOPE.size:], OFFSET + ENVELOPE.size)
      os.pwrite(fd, padded[:ENVELOPE.size - 4], OFFSET)
      os.pwrite(fd, padded[ENVELOPE.size - 4:ENVELOPE.size], OFFSET + ENVELOPE.size - 4)
      fcntl.lockf(fd, fcntl.LOCK_UN, SIZE - OFFSET, OFFSET)
    assert os.fstat(fd).st_size == SIZE
    conn.send((data, META.pack(*meta)))


class EndReplay(Exception):
  pass


def replay(module, output, name, scenarios, source=None, conn=None, epoch=1, local_failure=None, paced=False, action_width=2):
  clock = types.SimpleNamespace(now=1_000_000_000)
  rows, messages, wire_log, metadata_log, subscribers = [], [], [], [], []
  local_runs = []
  original_new_message = messaging.new_message
  current = 0
  wall_start = time.monotonic()

  def stock_probe():
    assert source is None, 'stock entry probed Chestnut/phone USB'
    return True

  class Vision:
    width, height, buffer_len = 1344, 760, 4

    def __init__(self, *args):
      self.frame_id = self.timestamp_sof = self.timestamp_eof = 0
      self.main = args[1] == modeld.VisionStreamType.VISION_STREAM_NARROW_ROAD

    @staticmethod
    def available_streams(*args, **kwargs):
      return [modeld.VisionStreamType.VISION_STREAM_NARROW_ROAD, modeld.VisionStreamType.VISION_STREAM_WIDE_ROAD]

    def connect(self, *args):
      return True

    def recv(self):
      nonlocal current
      if self.main:
        current += 1
        if current > len(scenarios):
          raise EndReplay
        if paced:
          time.sleep(max(0, wall_start + current * .05 - time.monotonic()))
        clock.now = 1_000_000_000 + current * 50_000_000 + 20_000_000
      self.frame_id = current
      self.timestamp_sof = 1_000_000_000 + current * 50_000_000
      self.timestamp_eof = self.timestamp_sof
      return b'fake VisionBuf'

  class Local:
    vision_input_names = ('img', 'big_img')

    def __init__(self, width, height, chestnut):
      assert not chestnut
      if local_failure == 'load':
        raise RuntimeError('local load failed')
      self.chestnut = False
      self.prev_desire = np.zeros(8, dtype=np.float32)

    def run(self, bufs, transforms, inputs, *args):
      local_runs.append(current)
      if local_failure == 'run':
        raise RuntimeError('local run failed')
      scenario = scenarios[current - 1]
      frame_epoch = epoch
      if isinstance(scenario, tuple):
        scenario, frame_epoch = scenario
      if conn is not None and scenario != 'absent':
        pulse = np.where(inputs['desire_pulse'] - self.prev_desire > .99, inputs['desire_pulse'], 0).copy()
        pulse[0] = 0
        expected = np.concatenate([pulse, inputs['traffic_convention'], inputs['action_t']])
        conn.send((scenario, frame_epoch, clock.now, expected))
        assert conn.poll(2), 'fake peer stalled'
        wire, meta = conn.recv()
        wire_log.append(wire)
        metadata_log.append(meta)
      if scenario == 'late':
        clock.now += 30_000_000
      if scenario == 'past_deadline':
        clock.now += 25_000_001
      if scenario == 'deadline':
        clock.now += 25_000_000
      self.prev_desire[:] = inputs['desire_pulse']
      heads = decode(raw_output())
      if action_width == 4:
        heads['action'] = np.concatenate([heads['action'], np.full((1, 2), -2.)], axis=1)
      elif action_width == 0:
        del heads['action']
      return heads

  class Inputs:
    def __init__(self, *args):
      self.values = {service: original_new_message(service).__getattr__(service) for service in args[0]}
      self.values['deviceState'].deviceType = 'tici'
      self.values['narrowRoadCameraState'].sensor = 'ar0231'
      self.values['carState'].vEgo = 20
      self.values['carControl'].latActive = True
      self.values['extrinsicsCalibration'].rpyCalib = [0, 0, 0]
      self.seen = self.updated = dict.fromkeys(args[0], True)

    def __getitem__(self, name):
      return self.values[name]

    def update(self, *args):
      self.values['narrowRoadCameraState'].frameId = current

  class Publisher:
    def __init__(self, services):
      assert services == ['modelV2', 'drivingModelData', 'cameraOdometry', 'modelDataV2SP']
      self.real = messaging.PubMaster(services)
      subscribers.extend(messaging.sub_sock(s, timeout=1000) for s in services)

    def send(self, service, msg):
      data = msg.to_bytes()
      self.real.send(service, data)
      received = subscribers[['modelV2', 'drivingModelData', 'cameraOdometry', 'modelDataV2SP'].index(service)].receive()
      assert received == data
      messages.append(data)
      if service == 'modelDataV2SP':
        selected = 'phone' if source is not None and source.selected else 'local'
        rows.append({'case': name, 'epoch': source.epoch if source else 0, 'frameId': current,
                     'EOF': 1_000_000_000 + current * 50_000_000,
                     'arrival': source.arrival_ns if source else 0, 'publish': clock.now, 'source': selected,
                     'reject_reason': source.reason if source else '', 'weight': source.weight if source else 0,
                     'host_publish_ns': time.monotonic_ns() if paced else 0})

  with contextlib.ExitStack() as stack:
    for attr, value in {'VisionIpcClient': Vision, 'ModelState': Local, 'SubMaster': Inputs, 'PubMaster': Publisher,
                        'chestnut_compiled': stock_probe, 'chestnut_present': lambda: False,
                        'cable_connected': lambda: source is not None,
                        'get_warp_matrix': lambda *args: np.eye(3, dtype=np.float32),
                        'time': types.SimpleNamespace(monotonic=lambda: clock.now / 1e9, perf_counter=lambda: clock.now / 1e9),
                        'get_demo_car_params': lambda: types.SimpleNamespace(longitudinalActuatorDelay=.2, brand='tesla')}.items():
      stack.enter_context(patch.object(module, attr, value))
    stack.enter_context(patch.object(messaging, 'new_message', lambda s, **kw: original_new_message(s, logMonoTime=clock.now, **kw)))
    if source is not None:
      stack.enter_context(patch('openpilot.tesla.model_source.nanos_since_boot', lambda: clock.now))
    with contextlib.suppress(EndReplay):
      if source is None:
        module.main(demo=True)
      else:
        module.run_with_model_source(source, demo=True)
  assert len(local_runs) == len(scenarios)
  assert len(messages) == 4 * len(scenarios)
  (output / f'{name}.rlog').write_bytes(b''.join(messages))
  (output / f'{name}.wire').write_bytes(b''.join(struct.pack('<I', len(w)) + w for w in wire_log))
  (output / f'{name}.meta').write_bytes(b''.join(metadata_log))
  (output / f'{name}.health.json').write_text(json.dumps(health_replay(messages, rows)) + '\n')
  for i, row in enumerate(rows):
    group = [messaging.log_from_bytes(data) for data in messages[i * 4:i * 4 + 4]]
    assert [m.which() for m in group] == ['modelV2', 'drivingModelData', 'cameraOdometry', 'modelDataV2SP']
    m, d, p, _ = group
    assert m.modelV2.frameId == d.drivingModelData.frameId == p.cameraOdometry.frameId == row['frameId']
    assert m.modelV2.timestampEof == p.cameraOdometry.timestampEof == row['EOF']
    assert m.modelV2.big == d.drivingModelData.big == (row['source'] == 'phone')
    delta = row['weight'] * .1
    assert abs(m.modelV2.position.y[0] - delta) < 1e-6
    assert abs(m.modelV2.leadsV3[0].x[0] - 30 - delta) < 1e-5
    assert abs(p.cameraOdometry.trans[0] - delta) < 1e-6
    expected_curvature = (.01 + delta) / 400 if action_width else 0
    assert abs(m.modelV2.action.desiredCurvature - expected_curvature) < 1e-6
    clipped, _ = clip_curvature(20, 0, m.modelV2.action.desiredCurvature, 0)
    assert abs(clipped) <= 5 / 400 * .01
  if paced:
    intervals = np.diff([r['host_publish_ns'] for r in rows]) / 1e9
    assert abs(np.mean(intervals) - .05) < .005 and max(intervals) < .1, intervals
  return rows, messages


def main():
  args = argparse.ArgumentParser()
  args.add_argument('--output', type=Path, required=True)
  args.add_argument('--baseline', default='48c8d50b1a1a8a6c05283450f01c63b52e15ba3a')
  opts = args.parse_args()
  out = opts.output.resolve()
  out.mkdir(parents=True, exist_ok=False)
  from openpilot.tesla.model_source import ModelSource
  baseline_code = subprocess.check_output(['git', 'show', f'{opts.baseline}:openpilot/selfdrive/modeld/modeld.py'], cwd=ROOT)
  baseline = types.ModuleType('baseline_modeld')
  exec(compile(baseline_code, 'baseline_modeld.py', 'exec'), baseline.__dict__)
  (out / 'baseline_modeld.py').write_bytes(baseline_code)
  rows, reference = replay(baseline, out, 'baseline', ['absent'] * 60)
  replay_rows, actual = replay(modeld, out, 'disabled', ['absent'] * 60)
  assert reference == actual, 'default modeld output changed'
  rows.extend(replay_rows)
  covered = []
  ctx = multiprocessing.get_context('fork')
  with tempfile.TemporaryDirectory() as directory:
    def execute(name, scenarios, epoch=1, decoder=decode, admission=admit, paced=False, stall=False, action_width=2):
      source = ModelSource(directory, decoder, admission)
      parent, child = ctx.Pipe()
      process = ctx.Process(target=peer, args=(source.path, child))
      process.start()
      try:
        if stall:
          parent.send('stall')
          assert parent.poll(2) and parent.recv() == 'frozen'
        records, _ = replay(modeld, out, name, scenarios, source, parent, epoch, paced=paced, action_width=action_width)
        rows.extend(records)
        return records, source
      finally:
        process.terminate()
        process.join(2)
        source.close()

    for case in ('magic', 'version', 'type', 'flags', 'length', 'truncated', 'oversized',
                 'nan', 'inf', 'zero', 'session', 'epoch_zero', 'ipc_flags', 'frame', 'eof', 'future', 'future_eof',
                 'jump_plan', 'jump_lead', 'jump_pose', 'jump_action'):
      records, source = execute(case, ['good'] * 10 + [case] + ['good'] * 8)
      assert records[9]['source'] == 'phone'
      assert all(r['source'] == 'local' for r in records[10:]), case
      assert source.locked, case
      covered.append(case)
    execute('absent', ['absent'] * 60)
    assert (out / 'absent.rlog').read_bytes() == (out / 'baseline.rlog').read_bytes()
    records, _ = execute('stock_action_mean_sigma', ['good'] * 15, action_width=4)
    assert records[-1]['source'] == 'phone'
    records, _ = execute('stock_plan_action', ['good'] * 15, action_width=0)
    assert records[-1]['source'] == 'phone'
    records, _ = execute('recovery', ['absent'] * 5 + ['good'] * 10 + ['late'] * 4 + ['good'] * 10)
    assert records[14]['source'] == records[-1]['source'] == 'phone'
    assert all(r['source'] == 'local' for r in records[15:24])
    for case in ('reset', 'zero_pair', 'unpaired'):
      records, _ = execute(case, [case] + ['good'] * 10)
      assert all(r['source'] == 'local' for r in records[:5])
    records, _ = execute('replay', ['good'] * 10 + ['replay'])
    assert records[-1]['source'] == 'local'
    records, _ = execute('partial', ['good'] * 10 + ['partial'] + ['good'] * 10)
    assert records[10]['source'] == 'local' and records[-1]['source'] == 'phone'
    records, _ = execute('epochs', ['good'] * 10 + [('good', 2)] * 10 + [('good', 1)] + [('good', 2)] * 10)
    assert all(r['source'] == 'local' for r in records[10:15])
    assert records[19]['source'] == records[-1]['source'] == 'phone'
    assert records[20]['reject_reason'] == 'old_epoch'
    records, source = execute('reset_same_epoch', ['good'] * 10 + ['reset'] + ['good'] * 10)
    assert source.locked and all(r['source'] == 'local' for r in records[10:])
    records, _ = execute('burst', ['burst'] * 15)
    assert records[-1]['source'] == 'phone'
    records, _ = execute('deadline', ['good'] * 10 + ['deadline', 'past_deadline'])
    assert records[-2]['source'] == 'phone' and records[-1]['source'] == 'local'
    records, _ = execute('stale', ['absent'] * 10 + ['stale'] + ['good'] * 10)
    assert records[10]['source'] == 'local' and records[-1]['source'] == 'phone'
    records, _ = execute('stall_200ms', ['absent'] * 5 + ['good'] * 55, paced=True, stall=True)
    assert records[-1]['source'] == 'phone'
    records, _ = execute('unqualified', ['good'] * 12, admission=lambda a, b: False)
    assert all(r['source'] == 'local' for r in records)
    for case, decoder in [('shape', lambda raw: decode(raw) | {'pose': np.ones((1, 5))}),
                          ('raw_action', lambda raw: decode(raw) | {'action': np.ones((1, 4))}),
                          ('missing', lambda raw: {k: v for k, v in decode(raw).items() if k != 'plan'}),
                          ('parsed_nan', lambda raw: decode(raw) | {'pose': np.full((1, 6), np.nan)}),
                          ('decoder_error', lambda raw: {}['bad'])]:
      records, source = execute(case, ['good'] * 12, decoder=decoder)
      assert source.locked and all(r['source'] == 'local' for r in records)
    for failure in ('load', 'run'):
      source = ModelSource(directory, decode, admit)
      try:
        replay(modeld, out, f'local_{failure}', ['absent'], source, local_failure=failure)
        raise AssertionError('local failure was swallowed')
      except RuntimeError as error:
        assert str(error) == f'local {failure} failed'
      finally:
        source.close()
    # A real stopped peer holds both slot locks. Host must never wait for it.
    source = ModelSource(directory, decode, admit)
    parent, child = ctx.Pipe()
    process = ctx.Process(target=peer, args=(source.path, child))
    process.start()
    parent.send('freeze')
    assert parent.poll(2) and parent.recv() == 'frozen'
    records, _ = replay(modeld, out, 'frozen', ['absent'] * 60, source, paced=True)
    rows.extend(records)
    process.kill()
    process.join(2)
    records, _ = replay(modeld, out, 'crashed', ['absent'] * 60, source, paced=True)
    rows.extend(records)
    source.close()
    # The actual discovery path must reject an old host before it loses the default role.
    from openpilot.tesla import plugins
    plugin_path = Path(directory) / 'entry_probe'
    plugin_path.mkdir()
    (plugin_path / '__init__.py').write_text('')
    (plugin_path / 'plugin.json').write_text(json.dumps({'id': 'entry_probe', 'api': 1, 'category': 'perception_model',
                                                       'use': 'onroad', 'entry': 'plugin:Candidate', 'priority': 100,
                                                       'claims': ['role:perception.model']}))
    (plugin_path / 'plugin.py').write_text('''from openpilot.selfdrive.modeld.modeld import run_with_model_source
from openpilot.tesla.model_source import ModelSource
from openpilot.tesla.plugin import Plugin
class Candidate(Plugin):
  pass
''')
    with patch.object(plugins.mf, 'INSTALL_DIR', Path(directory)):
      healthy, errors, _ = plugins._discover()
      assert not errors
      with patch.object(modeld, 'run_with_model_source'):
        del modeld.run_with_model_source
        older, errors, _ = plugins._discover()
      assert 'entry_probe' in errors
    for classes, expected in ((healthy, 'entry_probe'), (older, 'sp_models')):
      items = [(c.NAME, c.PRIORITY, c.CLAIMS) for c in classes]
      winners, _ = plugins.mf.resolve(items, {c.NAME for c in classes})
      assert expected in winners
      assert len([c for c in classes if c.NAME in winners and 'role:perception.model' in c.CLAIMS]) == 1
    (out / 'entry-capability.json').write_text(json.dumps({'current_owner': 'entry_probe', 'old_host_owner': 'sp_models',
                                                         'old_host_error': errors['entry_probe']}, indent=2) + '\n')
  with (out / 'frames.csv').open('w') as f:
    writer = csv.DictWriter(f, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
  result = {'pass': True, 'baseline': opts.baseline,
            'head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
            'cases': ['F1', 'F2 host readiness', 'F3 host IPC/wire', 'F4', 'F5', 'F6', 'F7 host child', 'F8', 'F9', 'F13 host'],
            'malformed_cases': covered, 'frames': len(rows), 'default_byte_identical': True,
            'source_sha256': {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in
                              ('openpilot/selfdrive/modeld/modeld.py', 'openpilot/tesla/model_source.py',
                               'tools/sp_tesla_e2e/model_source_e2e.py', 'XPILOT.md')},
            'command': sys.argv, 'python': sys.version, 'numpy': np.__version__,
            'host_cadence': {case: {'frames': len(selected),
                                    'mean_ms': float(np.mean(np.diff(selected)) / 1e6),
                                    'max_ms': float(max(np.diff(selected)) / 1e6)}
                             for case in ('frozen', 'crashed', 'stall_200ms')
                             if (selected := [r['host_publish_ns'] for r in rows if r['case'] == case])},
            'limits': ['synthetic inference/VisionIPC and deterministic BOOTTIME', 'no phone QNN, TCP/HEVC or device execution',
                       'real modeld postprocessing, Capnp/msgq and selfdrived.update_events; unrelated car/device inputs are healthy fixtures']}
  (out / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
  np.savez(out / 'inputs.npz', local_raw=raw_output(), phone_raw=raw_output(.1))
  files = sorted(p for p in out.iterdir() if p.is_file())
  (out / 'SHA256SUMS').write_text(''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n' for p in files))
  print(json.dumps(result, indent=2))


if __name__ == '__main__':
  with tempfile.TemporaryDirectory(prefix='msgq_model_source_', dir='/tmp' if sys.platform == 'darwin' else '/dev/shm') as msgq_dir:
    os.environ['OPENPILOT_PREFIX'] = Path(msgq_dir).name.removeprefix('msgq_')
    main()
