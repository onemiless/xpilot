"""G8 failure matrix fixed before firmware code; real libsafety RX/FWD/TX + app ABI.

L0 capable init starts STOCK or incapable init starts SP without a request.
L1 malformed/non-Tesla/not-capable/disengaged/standstill request grants ownership.
L2 same-frame OEM AEB suppressed or SP accepted during AEB.
L3 gas/brake/raw Autopark doesn't abort pending/DRAIN.
L4 invalid/missing/stale RX, checksum/counter/quality or stale OEM permits commit.
L5 250ms deadline/retry renews or commits late; DRAIN >=50ms doesn't abort.
L6 stock->SP wrong counter commits; L7 SNA/reversed/envelope/long-limit violation.
L8 stock takeover without duplicate DRAIN then next counter/envelope.
L9 duplicate OEM forwarded or SP TX accepted during DRAIN/after stock commit.
L10 counter continuity and 5..80ms gap; L11 early SP candidate clears pending.
L12 randomized schedules, modulo8 and uint32 timer wrap break I1/I2.
L13 init/mode/SILENT retains state. L14 lost replies/retries reverse/extend.
L15 firmware/host health layout/version/snapshot diverge.
L16 release ignores capability. L17 rules grant 2B9 on any bus/length.
L18 IRQ or full queue lets owner decision race queue admission.
L19 healthy OEM25Hz/host100Hz at phase0..40ms+jitter0..10ms expires.
L20 AEB incorrectly subject to normal I1/I2.
L21 stock-owner cancel blocked or changes handoff samples.
L22 stock owner accepts non-cancel/actuating/bad-checksum cancel.
L23 cancel commits SP or clears a pending SP request.
L24 cancel accepted during stock AEB or DRAIN.
L25 relay/malformed OEM permits handoff; L26 normal SP host counter contract.
L27 full stock forwarding queue advances the handoff reference.
L28 valid STOCK cancel during pending SP is blocked or fails to clear pending.

DRAIN abort restores SP; normal SP counter sequencing is the host's contract.
Artifact: XPILOT_LONG_OWNER_CONTRACT_ARTIFACT=<path> python -m unittest <module>
"""
import hashlib
import json
import os
from pathlib import Path
import random
import re
import struct
import unittest

from opendbc.car.structs import CarParams
from opendbc.safety.tests.common import CANPackerSafety
from opendbc.safety.tests.libsafety import libsafety_py as lib

ROOT = Path(__file__).resolve().parents[4]
EVIDENCE = {"kind": "real-libsafety", "cases": {}}
STOCK, SP, NONE = 0, 1, 255
OK, MODE, ARGUMENT, NOT_CAPABLE, DISENGAGED, STANDSTILL, AEB, GAS, BRAKE, AUTOPARK = range(10)
RX_INVALID, OEM_STALE, OEM_INACTIVE, DEADLINE, COUNTER, ENVELOPE, LONG_LIMIT, RELAY, BUSY, SP_STALE, DRAIN_TIMEOUT = range(10, 21)


def long_msg(counter, bus=2, lo=375, hi=375, state=4, aeb=0):
  data = bytearray(8)
  data[1] = state << 4
  data[2] = aeb
  data[4] = (lo & 31) << 3
  data[5] = (lo >> 5) | ((hi & 15) << 4)
  data[6] = (hi >> 4) | ((counter & 7) << 5)
  data[7] = (0x2B9 + 2 + sum(data)) & 255
  return lib.make_CANPacket(0x2B9, bus, data)


class OwnerDrive:
  """Drive the production dispatcher; seed STOCK for handoff cases, leave init untouched in L0."""
  def __init__(self, safety=None, start=0, param=1, stock=True):
    self.s = safety if safety is not None else lib.libsafety
    self.s.set_safety_hooks(CarParams.SafetyModel.tesla, param)
    if stock:
      self.s.test_long_fixture_stock()
    self.s.init_tests()
    self.packer = CANPackerSafety("tesla_model3_party")
    self.now = start
    self.outputs = []
    self.set_time(start)
    self.peers()
    self.rx(long_msg(0))

  def set_time(self, ts):
    self.now = ts
    self.s.set_timer(ts & 0xffffffff)

  def rx(self, msg, available=True):
    valid = lib.ffi.new('bool *')
    fwd = self.s.safety_rx_fwd_hook(msg, valid, available)
    if fwd == 0 and msg.addr == 0x2B9:
      self.outputs.append((self.now, STOCK, int(msg.data[6] >> 5)))
    return fwd, bool(valid[0])

  def peer(self, name, values):
    return self.rx(self.packer.make_can_msg_safety(name, 2 if name == 'DAS_steeringControl' else 0, values))

  def peers(self):
    for name, values in (
      ('DI_speed', {'DI_vehicleSpeed': 36}),
      ('ESP_B', {'ESP_vehicleSpeed': 36, 'ESP_wheelSpeedsQF': 1, 'ESP_vehicleStandstillSts': 0}),
      ('EPAS3S_sysStatus', {}), ('DI_systemStatus', {}),
      ('ESP_status', {'ESP_driverBrakeApply': 1}),
      ('DI_state', {'DI_cruiseState': 2}), ('UI_warning', {}), ('DAS_steeringControl', {}),
    ):
      self.peer(name, values)

  def reply(self, target=None, p2=0):
    buf = lib.ffi.new('uint8_t[8]')
    if target is None:
      self.s.test_long_snapshot(buf)
    else:
      assert self.s.test_long_request(target, p2, buf) == 8
    return struct.unpack('<BBBBHH', bytes(buf))

  @property
  def owner(self):
    return self.reply()[1]

  def tx(self, counter, **kw):
    msg = long_msg(counter, bus=0, **kw)
    accepted = self.s.safety_tx_hook(msg)
    if accepted:
      self.outputs.append((self.now, SP, counter & 7))
    return bool(accepted)

  def takeover(self):
    assert self.reply(SP)[2] == SP
    self.set_time(self.now + 10_000)
    assert self.tx(1)
    assert self.owner == SP


def assert_handoffs(case, outputs):
  for a, b in zip(outputs, outputs[1:]):
    case.assertEqual(b[2], (a[2] + 1) % 8, (a, b))
    if a[1] != b[1]:
      case.assertTrue(5_000 <= b[0] - a[0] <= 80_000, (a, b))


class TestTeslaLongOwnerContract(unittest.TestCase):
  def setUp(self):
    self.d = OwnerDrive()

  def test_L0_initial_owner(self):
    d = self.d
    for param in (0, 1, 2, 3):
      with self.subTest(param=param):
        d.s.set_safety_hooks(CarParams.SafetyModel.tesla, param)
        self.assertEqual(d.reply()[1:5], (SP if param & 1 else STOCK, NONE, OK, 0))
    d = OwnerDrive(stock=False)
    self.assertEqual(d.rx(long_msg(1))[0], -1)
    d.set_time(10_000)
    self.assertTrue(d.tx(2))
    self.assertEqual(d.reply()[1:4], (SP, NONE, OK))

  def test_L1_request_boundaries(self):
    d = self.d
    for target, p2 in ((2, 0), (65535, 0), (SP, 1)):
      self.assertEqual(d.reply(target, p2)[1:4], (STOCK, NONE, ARGUMENT))
    d.s.set_safety_hooks(CarParams.SafetyModel.silent, 0)
    self.assertEqual(d.reply(SP)[1:4], (STOCK, NONE, MODE))
    d = OwnerDrive(param=0)
    self.assertEqual(d.reply(SP)[3], NOT_CAPABLE)
    d = OwnerDrive()
    d.peer('DI_state', {'DI_cruiseState': 0})
    self.assertEqual(d.reply(SP)[3], DISENGAGED)
    d.peer('DI_state', {'DI_cruiseState': 2})
    d.peer('ESP_B', {'ESP_wheelSpeedsQF': 1, 'ESP_vehicleStandstillSts': 1})
    self.assertEqual(d.reply(SP)[3], STANDSTILL)

  def test_L2_L20_same_frame_AEB_production_order(self):
    d = self.d
    d.takeover()
    self.assertEqual(d.reply(STOCK)[2], STOCK)
    d.set_time(40_000)
    self.assertEqual(d.rx(long_msg(1, aeb=1)), (0, True))
    self.assertEqual(d.reply()[1:4], (SP, NONE, AEB))
    self.assertFalse(d.tx(2))
    self.assertEqual(d.outputs[-2][1:], (SP, 1))
    self.assertEqual(d.outputs[-1][1:], (STOCK, 1))
    EVIDENCE['cases']['L2_L20'] = d.outputs

  def test_L3_veto_pending_and_DRAIN(self):
    for drain in (False, True):
      for name, values, reason in (
        ('DI_systemStatus', {'DI_accelPedalPos': 10}, GAS),
        ('ESP_status', {'ESP_driverBrakeApply': 2}, BRAKE),
        *(( 'DI_state', {'DI_cruiseState': 2, 'DI_autoparkState': state}, AUTOPARK) for state in (3, 4, 9)),
      ):
        with self.subTest(drain=drain, reason=reason, values=values):
          d = OwnerDrive()
          if drain:
            d.takeover()
            d.reply(STOCK)
            d.set_time(40_000)
            d.rx(long_msg(1))
          else:
            d.reply(SP)
          d.peer(name, values)
          self.assertEqual(d.reply()[1:4], (SP if drain else STOCK, NONE, reason))

  def test_L4_RX_failures(self):
    d = self.d
    cfg = d.s.current_safety_config
    for i in range(cfg.rx_checks_len):
      for member, value in (('msg_seen', False), ('valid_checksum', False), ('valid_quality_flag', False),
                            ('wrong_counters', 5), ('lagging', True)):
        with self.subTest(index=i, member=member):
          status = cfg.rx_checks[i].status
          old = getattr(status, member)
          setattr(status, member, value)
          self.assertEqual(d.reply(SP)[3], RX_INVALID)
          setattr(status, member, old)
    d.reply(SP)
    bad = long_msg(1)
    bad.data[7] ^= 1
    d.set_time(40_000)
    self.assertFalse(d.rx(bad)[1])
    self.assertEqual(d.reply()[2:4], (NONE, RX_INVALID))
    d = OwnerDrive()
    d.set_time(60_001)
    d.peers()
    self.assertEqual(d.reply(SP)[3], OEM_STALE)
    d = OwnerDrive()
    d.set_time(100_001)
    d.rx(long_msg(1))
    self.assertEqual(d.reply(SP)[3], RX_INVALID)

  def test_L5_L14_deadline_retry_snapshot_and_lost_reply(self):
    d = self.d
    self.assertEqual(d.reply(SP)[1:4], (STOCK, SP, OK))
    d.set_time(20_000)
    self.assertEqual(d.reply(SP)[4], 20)
    self.assertEqual(d.reply(STOCK)[2], SP)
    for ts in range(40_000, 240_001, 40_000):
      d.set_time(ts)
      d.peers()
      d.rx(long_msg(ts // 40_000))
    d.set_time(249_999)
    self.assertEqual(d.reply()[2], SP)
    d.set_time(250_000)
    self.assertEqual(d.reply()[1:5], (STOCK, NONE, DEADLINE, 0))
    d = OwnerDrive()
    d.takeover()
    self.assertEqual(d.reply()[1:4], (SP, NONE, OK))
    d.reply(STOCK)
    d.set_time(40_000)
    d.rx(long_msg(1))
    d.set_time(89_999)
    d.peers()
    self.assertEqual(d.reply()[2], STOCK)
    d.set_time(90_000)
    self.assertEqual(d.reply()[1:4], (SP, NONE, DRAIN_TIMEOUT))
    self.assertTrue(d.tx(3))  # host can resume after the blocked DRAIN slot
    EVIDENCE['cases']['DRAIN_abort_counter_jump'] = d.outputs

  def test_L6_L7_stock_to_SP_candidates(self):
    for counter, lo, hi, reason in ((2, 375, 375, None), (1, 511, 511, ENVELOPE),
                                    (1, 376, 375, ENVELOPE), (1, 369, 375, ENVELOPE),
                                    (1, 375, 381, ENVELOPE), (1, 375, 426, LONG_LIMIT)):
      with self.subTest(counter=counter, lo=lo, hi=hi):
        d = OwnerDrive()
        # long limit is tested within the envelope of an unsafe OEM request
        if reason == LONG_LIMIT:
          d.rx(long_msg(1, hi=426))
          counter = 2
        d.reply(SP)
        d.set_time(10_000)
        self.assertFalse(d.tx(counter, lo=lo, hi=hi))
        self.assertEqual(d.owner, STOCK)
        if reason is None:
          self.assertEqual(d.reply()[2], SP)
        else:
          self.assertEqual(d.reply()[2:4], (NONE, reason))
    for lo, hi in ((370, 380), (375, 375)):
      d = OwnerDrive()
      d.reply(SP)
      d.set_time(5_000)
      self.assertTrue(d.tx(1, lo=lo, hi=hi))

  def test_L8_L9_DRAIN_commit_and_abort(self):
    d = self.d
    d.takeover()
    d.reply(STOCK)
    d.set_time(40_000)
    self.assertEqual(d.rx(long_msg(1))[0], -1)
    self.assertEqual(d.owner, SP)
    d.set_time(50_000)
    self.assertFalse(d.tx(2))
    d.set_time(80_000)
    d.peers()
    self.assertEqual(d.rx(long_msg(2))[0], 0)
    self.assertEqual(d.reply()[1:4], (STOCK, NONE, OK))
    self.assertFalse(d.tx(3))
    assert_handoffs(self, d.outputs)
    EVIDENCE['cases']['DRAIN_commit'] = d.outputs
    for kw, reason in (({'counter': 3}, COUNTER), ({'counter': 2, 'lo': 369}, ENVELOPE),
                       ({'counter': 2, 'state': 2}, OEM_INACTIVE), ({'counter': 2, 'aeb': 1}, AEB)):
      d = OwnerDrive()
      d.takeover()
      d.reply(STOCK)
      d.set_time(40_000)
      d.rx(long_msg(1))
      d.set_time(80_000)
      d.peers()
      d.rx(long_msg(**kw))
      self.assertEqual(d.reply()[1:4], (SP, NONE, reason))
      if reason != AEB:
        self.assertTrue(d.tx(3))
    for state in range(16):
      d = OwnerDrive()
      d.takeover()
      d.set_time(40_000)
      d.rx(long_msg(1, state=state))
      self.assertEqual(d.reply(STOCK)[3], OK if state in (3, 4) else OEM_INACTIVE)

  def test_L10_L11_early_candidate_and_timer_wrap(self):
    for start in (0, 0xffffd000):
      d = OwnerDrive(start=start)
      d.reply(SP)
      d.set_time(start + 4_999)
      self.assertFalse(d.tx(1))
      self.assertEqual(d.reply()[2], SP)
      d.set_time(start + 5_000)
      self.assertTrue(d.tx(1))
      d.reply(STOCK)
      d.set_time(start + 40_000)
      d.peers()
      d.rx(long_msg(1))
      d.set_time(start + 80_000)
      d.peers()
      self.assertEqual(d.rx(long_msg(2))[0], 0)
      assert_handoffs(self, d.outputs)

  def test_L12_L19_all_phases_jitter_and_random_interleavings(self):
    summaries = []
    for phase in range(0, 40_001, 1_000):
      for jitter in range(0, 10_001, 1_000):
        d = OwnerDrive()
        rng = random.Random(phase * 13 + jitter)
        d.reply(SP)
        request_ts = 0
        switched_sp = False
        last_oem = 0
        last_sent = None
        # healthy host cycles at 100Hz; scheduling delay 0..jitter follows a new OEM.
        ready = phase % 10_000 + rng.randint(0, jitter)
        host_delay = 0
        for ts in range(1_000, 601_000, 1_000):
          d.set_time(ts)
          if ts % 20_000 == 0:
            d.peers()
          if ts % 40_000 == 0:
            last_oem = (last_oem + 1) % 8
            host_delay = rng.randint(0, jitter)
            ready = ts + host_delay
            d.rx(long_msg(last_oem))
          if ts >= phase and (ts - phase) % 10_000 == 0 and ts >= ready and last_sent != last_oem:
            if d.tx((last_oem + 1) % 8):
              last_sent = last_oem
          if d.owner == SP and not switched_sp:
            self.assertLess(ts - request_ts, 250_000)
            switched_sp = True
            request_ts = ts
            self.assertEqual(d.reply(STOCK)[2], STOCK)
          elif switched_sp and d.owner == STOCK:
            self.assertLess(ts - request_ts, 250_000)
            break
          self.assertEqual(d.reply()[3], OK, (phase, jitter, ts, d.reply()))
        self.assertTrue(switched_sp and d.owner == STOCK, (phase, jitter, d.outputs, d.reply()))
        assert_handoffs(self, d.outputs)
        summaries.append({'phase_us': phase, 'jitter_us': jitter, 'stock_commit_us': ts,
                          'max_handoff_gap_us': max(b[0]-a[0] for a,b in zip(d.outputs,d.outputs[1:]) if a[1]!=b[1])})
    EVIDENCE['cases']['L12_L19'] = summaries

  @unittest.skipUnless(os.environ.get('XPILOT_LONG_OWNER_CAPTURE'), 'capture path not configured')
  def test_capture_replay_with_injected_requests(self):
    capture = Path(os.environ['XPILOT_LONG_OWNER_CAPTURE'])
    inventory = json.loads(capture.read_text())
    frames = inventory['bus2_frames']
    self.assertEqual(len(frames), 1743)
    d = OwnerDrive()
    t0 = frames[0]['t_ns'] // 1000
    commits = []
    refusals = {}
    requested = False
    for i, frame in enumerate(frames):
      ts = frame['t_ns'] // 1000 - t0 + 40_000
      d.set_time(ts)
      d.peers()  # isolated replay: healthy simulated other ECUs, captured OEM unchanged
      msg = lib.make_CANPacket(0x2B9, 2, bytes.fromhex(frame['data']))
      before = d.owner
      self.assertTrue(d.rx(msg)[1], i)
      if d.owner != before:
        commits.append({'index':i,'owner':d.owner})
      before = d.owner
      if i % 20 == 0:
        reply = d.reply(SP if before == STOCK else STOCK)
        requested = reply[2] != NONE
        if reply[3]:
          refusals[str(reply[3])] = refusals.get(str(reply[3]), 0) + 1
      lo = ((msg.data[5] & 15) << 5) | (msg.data[4] >> 3)
      hi = ((msg.data[6] & 31) << 4) | (msg.data[5] >> 4)
      d.set_time(ts + 10_000)
      d.tx((frame['counter']+1)%8,lo=lo,hi=hi)
      after = d.owner
      if after != before:
        commits.append({'index':i,'owner':after})
      if requested and d.reply()[3]:
        reason = str(d.reply()[3])
        refusals[reason] = refusals.get(reason,0)+1
        requested=False
    self.assertTrue(commits)
    self.assertTrue(any(c['owner']==SP for c in commits))
    # OEM capture is ~40Hz, not the synthetic 25Hz liveness premise.
    EVIDENCE['cases']['capture']={'frames':len(frames),'capture_sha256':inventory['sha256'],
      'inventory_sha256':hashlib.sha256(capture.read_bytes()).hexdigest(),'commits':commits,'refusals':refusals,
      'scope':'all OEM payloads unchanged; other ECUs and SP peer simulated'}

  def test_L13_reset_SILENT(self):
    for mode in (CarParams.SafetyModel.tesla, CarParams.SafetyModel.silent, CarParams.SafetyModel.noOutput):
      d = OwnerDrive()
      d.takeover()
      d.reply(STOCK)
      d.set_time(40_000)
      d.rx(long_msg(1))
      d.s.set_safety_hooks(mode, 1 if mode == CarParams.SafetyModel.tesla else 0)
      self.assertEqual(d.reply()[1:5], (SP if mode == CarParams.SafetyModel.tesla else STOCK, NONE, OK, 0))
      self.assertFalse(d.tx(2))

  def test_L15_health_ABI_and_DBC(self):
    d = self.d
    d.reply(SP)
    d.set_time(12_345)
    buf = lib.ffi.new('uint8_t[54]')
    self.assertEqual(d.s.test_long_health(buf), 54)
    self.assertEqual(struct.unpack('<BBHB', bytes(buf)[49:]), (STOCK, SP, 12, OK))
    text = (ROOT/'panda/board/health.h').read_text()
    fields = re.findall(r'(uint\d+_t)\s+(\w+);', text.split('struct __attribute__((packed)) health_t {')[1].split('};')[0])
    self.assertEqual(struct.calcsize('<'+''.join({'uint8_t':'B','uint16_t':'H','uint32_t':'I'}[t] for t,n in fields)),54)
    version = int.from_bytes(hashlib.sha256(text.encode()).digest()[:4], 'little')
    import subprocess
    old = subprocess.check_output(['git','show','1b52d017f:panda/board/health.h'],cwd=ROOT)
    self.assertNotEqual(hashlib.sha256(old).digest()[:4], hashlib.sha256(text.encode()).digest()[:4])
    EVIDENCE['cases']['L15'] = {'health_size':54, 'health_version':f'0x{version:08x}', 'offsets':[49,50,51,53]}
    states = re.search(r'^VAL_ 697 DAS_accState (.*);',(ROOT/'opendbc_repo/opendbc/dbc/tesla_model3_party.dbc').read_text(),re.M)[1]
    values = {name:int(val) for val,name in re.findall(r'(\d+) "([^"]+)"',states)}
    self.assertEqual((values['ACC_HOLD'],values['ACC_ON']),(3,4))

  def test_L16_release_real_libsafety(self):
    safety = lib.ffi.dlopen(lib._build_libsafety(release=True))
    d = OwnerDrive(safety=safety)
    d.takeover()
    d.s.set_safety_hooks(CarParams.SafetyModel.tesla, 0)
    self.assertEqual(d.reply(SP)[3], NOT_CAPABLE)

  def test_L17_mode_owned_inventory(self):
    for bus in range(3):
      for length in range(1,9):
        self.assertTrue(self.d.s.test_long_mode_owned(0x2B9, bus, length))

  def test_L18_forward_queue_unavailable_does_not_commit(self):
    d = self.d
    d.takeover()
    d.reply(STOCK)
    d.set_time(40_000)
    d.rx(long_msg(1))
    d.set_time(80_000)
    d.peers()
    self.assertEqual(d.rx(long_msg(2),available=False)[0], -1)
    self.assertEqual(d.owner, SP)
    # Board critical regions cover the production dispatcher and queue admission.
    for name in ('bxcan','fdcan'):
      text=(ROOT/f'panda/board/drivers/{name}.h').read_text()
      pos=text.index('safety_rx_fwd_hook(')
      self.assertGreater(text.rfind('ENTER_CRITICAL()',0,pos),text.rfind('EXIT_CRITICAL()',0,pos))
      self.assertLess(text.index('can_send(',pos),text.index('EXIT_CRITICAL()',pos))
    text=(ROOT/'panda/board/drivers/can_common.h').read_text().split('void can_send(')[1]
    self.assertLess(text.index('ENTER_CRITICAL()'),text.index('safety_tx_hook('))
    self.assertLess(text.index('can_push('),text.index('EXIT_CRITICAL()'))

  def test_L21_stock_cancel_preserves_handoff_reference(self):
    for engaged in (False, True):
      d = OwnerDrive()
      d.s.set_controls_allowed(engaged)
      d.set_time(5_000)
      self.assertTrue(d.s.safety_tx_hook(long_msg(6, bus=0, state=13)))
      self.assertEqual(d.reply()[1:4], (STOCK, NONE, OK))
      d.s.set_controls_allowed(True)
      self.assertEqual(d.reply(SP)[2], SP)
      d.set_time(10_000)
      self.assertTrue(d.tx(1))  # last forwarded OEM is still counter 0
      self.assertEqual(d.reply()[1:4], (SP, NONE, OK))
    EVIDENCE['cases']['L21'] = {'cancel_allowed_controls_on_and_off': True, 'handoff_uses_OEM_counter': 0}

  def test_L22_stock_blocks_everything_except_inactive_cancel(self):
    for state in range(16):
      for lo, hi in ((375, 375), (374, 375), (375, 376)):
        d = OwnerDrive()
        self.assertEqual(bool(d.s.safety_tx_hook(long_msg(1, bus=0, state=state, lo=lo, hi=hi))),
                         state == 13 and lo == hi == 375, (state, lo, hi))
        self.assertEqual(d.owner, STOCK)
    for aeb in (1, 2, 3):
      self.assertFalse(self.d.s.safety_tx_hook(long_msg(1, bus=0, state=13, aeb=aeb)))
    for bad in ('checksum', 'extended', 'length', 'bus'):
      msg = long_msg(1, bus=0, state=13)
      if bad == 'checksum':
        msg.data[7] ^= 1
      elif bad == 'extended':
        msg.extended = 1
      elif bad == 'length':
        msg.data_len_code = 7
      else:
        msg.bus = 1
      self.assertFalse(self.d.s.safety_tx_hook(msg), bad)

  def test_L23_pending_SP_cancel_is_never_a_candidate(self):
    for lo, hi in ((374, 375), (375, 376)):
      d = OwnerDrive()
      d.reply(SP)
      d.set_time(10_000)
      self.assertFalse(d.s.safety_tx_hook(long_msg(1, bus=0, state=13, lo=lo, hi=hi)))
      self.assertEqual(d.reply()[1:5], (STOCK, SP, OK, 10))
      self.assertTrue(d.tx(1))
      self.assertEqual(d.reply()[1:4], (SP, NONE, OK))

  def test_L24_cancel_blocked_by_stock_AEB_and_DRAIN(self):
    d = self.d
    d.rx(long_msg(1, aeb=1))
    self.assertFalse(d.s.safety_tx_hook(long_msg(2, bus=0, state=13)))
    self.assertEqual(d.owner, STOCK)
    d = OwnerDrive()
    d.takeover()
    d.reply(STOCK)
    d.set_time(40_000)
    d.rx(long_msg(1))
    self.assertFalse(d.s.safety_tx_hook(long_msg(2, bus=0, state=13)))
    self.assertEqual(d.reply()[1:4], (SP, STOCK, OK))

  def test_L25_relay_and_malformed_OEM_abort_pending(self):
    d = self.d
    d.reply(SP)
    d.s.set_relay_malfunction(True)
    self.assertFalse(d.tx(1))
    self.assertEqual(d.reply()[1:4], (STOCK, NONE, RELAY))
    self.assertFalse(d.s.safety_tx_hook(long_msg(1, bus=0, state=13)))
    for malformed in ('extended', 'length'):
      d = OwnerDrive()
      d.reply(SP)
      msg = long_msg(1)
      if malformed == 'extended':
        msg.extended = 1
      else:
        msg.data_len_code = 7
      self.assertEqual(d.rx(msg), (-1, False))
      self.assertEqual(d.reply()[1:4], (STOCK, NONE, RX_INVALID))
      self.assertFalse(d.tx(1))

  def test_L26_normal_SP_counter_is_host_responsibility(self):
    d = self.d
    d.takeover()
    for counter in (1, 4, 0):
      d.set_time(d.now + 10_000)
      self.assertTrue(d.tx(counter))
      self.assertEqual(d.owner, SP)
    EVIDENCE['cases']['L26'] = {'accepted_counters': [1, 1, 4, 0], 'scope': 'Panda checks continuity only at handoff; host must lock OEM+1'}

  def test_L27_full_queue_keeps_last_forwarded_OEM(self):
    d = self.d
    d.set_time(10_000)
    self.assertEqual(d.rx(long_msg(1), available=False), (-1, True))
    d.reply(SP)
    d.set_time(15_000)
    self.assertTrue(d.tx(1))
    self.assertEqual(d.owner, SP)

  def test_L28_pending_SP_cancel_allowed_and_clears_pending(self):
    d = self.d
    d.reply(SP)
    d.set_time(10_000)
    bad = long_msg(1, bus=0, state=13)
    bad.data[7] ^= 1
    self.assertFalse(d.s.safety_tx_hook(bad))
    self.assertEqual(d.reply()[1:5], (STOCK, SP, OK, 10))
    self.assertTrue(d.s.safety_tx_hook(long_msg(1, bus=0, state=13)))
    self.assertEqual(d.reply()[1:5], (STOCK, NONE, DISENGAGED, 0))
    self.assertFalse(d.tx(1))
    d.reply(SP)
    self.assertTrue(d.tx(1))
    self.assertEqual(d.owner, SP)

  @classmethod
  def tearDownClass(cls):
    if path := os.environ.get('XPILOT_LONG_OWNER_CONTRACT_ARTIFACT'):
      paths=[Path(__file__),ROOT/'docs/design/xpilot-long-owner.md',ROOT/'panda/board/health.h',
             ROOT/'opendbc_repo/opendbc/safety/modes/tesla.h',ROOT/'opendbc_repo/opendbc/safety/safety.h']
      EVIDENCE['sha256']={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
      Path(path).write_text(json.dumps(EVIDENCE,indent=2,sort_keys=True)+'\n')


if __name__ == '__main__':
  unittest.main()
