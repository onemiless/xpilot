#!/usr/bin/env python3
"""G8 host acceptance failure matrix, written before runtime implementation.

- LO1 owner pinned SP differs from today's process replay, except 0x2B9 counter/phase.
- LO2 SP counter is not latest OEM+1 modulo 8 on the first following host cycle;
  no OEM for 60 ms fails to restore the independent 25 Hz cadence.
- LO3 stock->SP candidate before 5 ms is not retried with the same counter when
  no new OEM arrived; handoff gap escapes inclusive 5..80 ms.
- LO4 request -> health ack -> carControlSP.longOwner, longActive, override,
  cancel and carcontroller branch do not agree in the same controlsd step.
- LO5 refusal/250 ms timeout reverses owner, a lost reply triggers reversal,
  or Panda reset fails to make host follow STOCK.
- LO6 desired!=acked for less than 1 s requests a switch, or any switch,
  refusal/timeout permits another request before the 2 s cooldown expires.
- LO7 gas/brake/standstill/stockAeb/accFault/abs(aEgo)>=0.35/TTC<3 s,
  OEM-SP endpoint gap>0.20 m/s^2, or inactive OEM permits a request.
- LO8 two consecutive refusals, ack mismatch>0.5 s, any fault, invalid hook
  result or hook exception fails to stop switching until disengage.
- LO9 SP start does not seed planner/MPC/LongControl from vEgo/aEgo, or
  takeover ramp exceeds 0.6/1.5 m/s^3 or lasts longer than 1 s.
- LO10 STOCK owner sends ACC_ON/accel, or a switch writes Params.
- LO11 big-screen or mici HUD misses owner, long active or last refusal reason.
- LO12 shadow OEM accState/accelMin/accelMax/counter and SP accel do not
  enter the existing rlog at 25 Hz while SP drives.
- LO13 manual switch is enabled by default, writable onroad, collides with
  MADS/LKAS/cancel, or bypasses host/Panda checks.
- LO14 role:long.owner is not exclusive, unclaimed hooks run, or docs,
  HOST_CAPS and manifest validation disagree with the implemented contract.
- LO15 active-owner readers use CP capability; capability readers are changed.
- LO16 SP->STOCK request expires under perfect 25 Hz OEM / healthy 100 Hz host
  while SP continues normal OEM+1 transmissions and Panda requires OEM=SP+1.

Current scope: a deterministic CONTRACT MODEL for LO16 only, not firmware,
card/controlsd E2E or a replacement for the other acceptance gates. Exit 1
means the required liveness property is false under the documented schedule.
Every intervening normal SP transmission is retained in timeline.csv.
"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / 'docs/design/xpilot-long-owner.md'


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--output', type=Path, required=True)
  args = parser.parse_args()
  args.output.mkdir(parents=True, exist_ok=True)

  contract = CONTRACT.read_text()
  clauses = ['SP -> STOCK: keep admitting SP while pending.',
             'equals `(last_accepted_SP_counter+1) mod 8`',
             'host sends SP counter 2 at 50 ms',
             'no request expiry with perfect 25 Hz OEM',
             '`PENDING_TIMEOUT_US = 250000`']
  assert all(clause in contract for clause in clauses), 'Contract changed: review LO16 model before rerunning'

  rows = []
  outcomes = []
  # 40 ms is four host cycles. Every possible integer-ms phase after OEM is
  # represented, including OEM and host sharing a tick (host runs first).
  for phase_ms in range(1, 11):
    last_sp_counter, last_sp_ms = 1, phase_ms
    request_ms = phase_ms + 1
    owner, pending = 'sp', True
    rows.append({'phase_ms': phase_ms, 'ms': phase_ms, 'source': 'sp', 'counter': 1,
                 'expected': 1, 'admitted': True, 'owner': owner, 'pending': pending, 'reason': 'initial SP owner'})
    rows.append({'phase_ms': phase_ms, 'ms': request_ms, 'source': 'request_stock', 'counter': '',
                 'expected': '', 'admitted': '', 'owner': owner, 'pending': pending, 'reason': 'all gates valid'})
    events = [(ms, 'oem') for ms in range(40, 261, 40)]
    events += [(ms + phase_ms, 'sp') for ms in range(40, 261, 40)]
    events.append((request_ms + 250, 'deadline'))
    for ms, source in sorted(events):
      if source == 'deadline':
        pending = False
        rows.append({'phase_ms': phase_ms, 'ms': ms, 'source': source, 'counter': '',
                     'expected': '', 'admitted': '', 'owner': owner, 'pending': pending, 'reason': 'DEADLINE at request+250 ms'})
        break
      counter = ((ms // 40) + (source == 'sp')) % 8
      expected = (last_sp_counter + 1) % 8
      admitted = source == 'sp' or (pending and counter == expected and 5 <= ms - last_sp_ms <= 80)
      reason = 'normal SP OEM+1' if source == 'sp' else 'counter mismatch'
      if source == 'sp':
        last_sp_counter, last_sp_ms = counter, ms
      elif admitted:
        owner, pending, reason = 'stock', False, 'handoff'
      rows.append({'phase_ms': phase_ms, 'ms': ms, 'source': source, 'counter': counter,
                   'expected': expected, 'admitted': admitted, 'owner': owner, 'pending': pending, 'reason': reason})
    outcomes.append({'phase_ms': phase_ms, 'owner': owner, 'deadline_ms': request_ms + 250, 'liveness_pass': owner == 'stock'})

  with (args.output / 'timeline.csv').open('w', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
  result = {'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
            'scope': 'contract model only; no runtime or libsafety executed',
            'inputs': {'oem_period_ms': 40, 'host_period_ms': 10, 'request_ms': 'phase_ms+1',
                       'pending_timeout_ms': 250, 'all_other_gates': 'valid, matching accel endpoints'},
            'source_sha256': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                              for p in (CONTRACT, Path(__file__))},
            'LO16': outcomes, 'LO1_LO15': 'NOT_RUN: runtime implementation blocked by LO16',
            'status': 'BLOCKED' if not all(o['liveness_pass'] for o in outcomes) else 'PASS'}
  (args.output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
  (args.output / 'SHA256SUMS').write_text(''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n'
                                               for p in sorted(args.output.iterdir()) if p.name in ('result.json', 'timeline.csv')))
  print(json.dumps(result, indent=2))
  return 1 if result['status'] == 'BLOCKED' else 0


if __name__ == '__main__':
  raise SystemExit(main())
