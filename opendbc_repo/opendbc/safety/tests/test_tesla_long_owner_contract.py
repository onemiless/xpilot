"""G8 failure matrix, fixed BEFORE firmware implementation.

L1 request accepted while disengaged / not capable / non-Tesla / malformed.
L2 same-frame OEM AEB blocked or SP TX admitted during AEB.
L3 gas / brake / raw Autopark while engaged fails to clear pending.
L4 invalid/missing RX, checksum/counter/quality failure, stale OEM permits commit.
L5 deadline >=250 ms changes owner or retry extends deadline.
L6 stock->SP accepts counter other than last forwarded OEM+1 modulo 8.
L7 stock->SP accepts SNA/reversed/out-of-envelope/unsafe accel candidate.
L8 SP->stock commits without OEM counter == last accepted SP+1 or envelope match.
L9 SP->stock forwards duplicate OEM or admits SP after commit.
L10 handoff duplicates/skips counter or violates the inclusive 5..80 ms gap.
L11 pending is cleared by a <5 ms candidate instead of waiting for retry.
L12 random OEM RX/SP TX interleaving duplicates/skips bus-0 counter or changes
    owner without all gates; include modulo wrap and uint32 timer wrap.
L13 tesla_init/mode set/SILENT retains owner/pending/counter/envelope state.
L14 lost reply/health read/repeated request blindly reverses or renews pending.
L15 health offsets/size/version/reason/age differ across firmware and host.
L16 RELEASE=1 ignores Tesla long capability bit.
L17 runtime rules grant mode-owned 0x2B9 (any bus/length).
L18 IRQ preemption or queue admission races invalidate owner/source decision.
L19 healthy 25 Hz OEM / 100 Hz OEM+1 host makes stock request expire.
L20 unqualified source exclusivity suppresses mandatory AEB passthrough.

ONLY L10/L19/L20 schedule reasoning and L15 base layout facts are checked
here. This is a requirements reproducer, NOT libsafety/firmware E2E coverage of
L1-L20. Firmware implementation has not started; continuation is in handoff/X-20261026-48.md.
Set XPILOT_LONG_OWNER_CONTRACT_ARTIFACT to save repeatable JSON evidence.
"""

import hashlib
import json
import os
from pathlib import Path
import random
import re
import struct
import unittest


ROOT = Path(__file__).resolve().parents[4]
EVIDENCE = {"kind": "requirements-counterexample-not-firmware", "cases": {}}


class TestTeslaLongOwnerContract(unittest.TestCase):
  def test_L10_allowed_phase_advance_preserves_counter_and_bounded_gap(self):
    rng = random.Random(48)
    traces = []
    for delay_us in [5_000, 10_000, *(rng.randrange(5_000, 10_001) for _ in range(100))]:
      output = [(0, "stock", 0), (delay_us, "sp", 1), (40_000 + delay_us, "sp", 2)]
      self.assertTrue(all(b[2] == (a[2] + 1) % 8 for a, b in zip(output, output[1:])))
      self.assertGreaterEqual(delay_us, 5_000)
      self.assertLessEqual(delay_us, 80_000)
      traces.append({"bus0": output, "gaps_us": [b[0] - a[0] for a, b in zip(output, output[1:])]})
    EVIDENCE["cases"]["L10"] = traces

  def test_L19_OEM_plus_one_host_can_keep_stock_request_pending_forever(self):
    last_sp_counter = 1
    trace = [{"time_us": 10_000, "source": "sp", "counter": last_sp_counter}]
    request_us = 11_000
    for oem_us in range(40_000, request_us + 250_000, 40_000):
      oem_counter = (oem_us // 40_000) % 8
      self.assertEqual(oem_counter, last_sp_counter)
      self.assertNotEqual(oem_counter, (last_sp_counter + 1) % 8)
      trace.append({"time_us": oem_us, "source": "stock", "counter": oem_counter, "admit": False})
      last_sp_counter = (oem_counter + 1) % 8
      trace.append({"time_us": oem_us + 10_000, "source": "sp", "counter": last_sp_counter, "admit": True})
    EVIDENCE["cases"]["L19"] = {"request_us": request_us, "deadline_us": request_us + 250_000,
                                  "stock_candidates_admitted": 0, "no_expiry_requirement_satisfied": False, "trace": trace}

  def test_L20_AEB_is_an_explicit_exception_to_normal_source_exclusivity(self):
    output = [(0, "sp", "normal"), (10_000, "stock", "AEB")]
    self.assertEqual({source for time, source, _ in output if time < 40_000}, {"stock", "sp"})
    EVIDENCE["cases"]["L20"] = output

  def test_L15_base_health_layout_and_DBC_active_states(self):
    health = ROOT / "panda/board/health.h"
    dbc = ROOT / "opendbc_repo/opendbc/dbc/tesla_model3_party.dbc"
    body = health.read_text().split("struct __attribute__((packed)) health_t {", 1)[1].split("};", 1)[0]
    formats = {"uint8_t": "B", "uint16_t": "H", "uint32_t": "I"}
    fields = re.findall(r"(uint\d+_t)\s+(\w+);", body)
    base_format = "<" + "".join(formats[kind] for kind, _ in fields)
    self.assertEqual(struct.calcsize(base_format), 49)
    self.assertEqual(struct.calcsize(base_format + "BBHB"), 54)
    states = re.search(r"^VAL_ 697 DAS_accState (.*);", dbc.read_text(), re.MULTILINE)[1]
    values = {name: int(value) for value, name in re.findall(r'(\d+) "([^"]+)"', states)}
    self.assertEqual({name: values[name] for name in ("ACC_HOLD", "ACC_ON", "ACC_CANCEL_RADAR_BLIND", "APC_BACKWARD")},
                     {"ACC_HOLD": 3, "ACC_ON": 4, "ACC_CANCEL_RADAR_BLIND": 2, "APC_BACKWARD": 5})
    EVIDENCE["cases"]["L15"] = {"base_health_bytes": 49, "proposed_health_bytes": 54, "acc_states": values}

  @classmethod
  def tearDownClass(cls):
    if path := os.environ.get("XPILOT_LONG_OWNER_CONTRACT_ARTIFACT"):
      paths = [Path(__file__), ROOT / "docs/design/xpilot-long-owner.md", ROOT / "panda/board/health.h",
               ROOT / "opendbc_repo/opendbc/dbc/tesla_model3_party.dbc"]
      EVIDENCE["sha256"] = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
      Path(path).write_text(json.dumps(EVIDENCE, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
  unittest.main()
