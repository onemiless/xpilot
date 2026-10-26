# G8 Tesla longitudinal owner contract (X-20261026-48 / X-20261026-49)

Status: **ABI 1 implementation in progress**. Base:
`1b52d017f4e6605cf6751963c92dbd899d38891c`. Coordinator timing decision
2026-10-26 supersedes the original wall-clock slot requirements. The document
is a wire contract, not a claim of tested/deployed firmware. No device access
or flashing in X-48. G3 lateral handoff is separate.

## Accepted timing contract: counter slots, I1 / I2

I1: a single owner decides each normal bus-0 0x2B9 admission. Consecutive
accepted counters increase by exactly +1 modulo 8 across a handoff; no duplicate
counter is admitted. Ownership changes only on an admitted handoff frame.
Emergency OEM AEB passthrough is an explicit exception to normal counter/source
arbitration: it must never be delayed by a continuity gate.

I2: the gap between the last old-source frame and first new-source frame is
inclusive **5000..80000 us** (5..80 ms). This allows a one-off phase change,
with a counter jump permitted only after DRAIN abort. Outside handoffs OEM freshness remains 60 ms at 25 Hz.
Offline tests prove safety RX/TX/FWD verdicts; physical queueing, arbitration
and bus timing require later vehicle validation.

STOCK -> SP example: OEM counter 0 forwarded at 0 ms; SP request at 1 ms;
SP counter 1 at 3 ms blocked with pending intact (too early); retry counter 1
at 10 ms accepted, commits SP; OEM counter 1 at 40 ms blocked; SP counter 2
at 50 ms accepted. Handoff gap 10 ms, counters 0,1,2.

SP -> STOCK uses DRAIN to guarantee progress with the normal OEM+1 host
phase lock. SP counter 1 at 10 ms; stock request at 11 ms; OEM counter 1 at
40 ms is blocked and enters DRAIN. SP counter 2 at 50 ms is blocked. OEM
counter 2 at 80 ms passes the gates/envelope, commits STOCK and is forwarded.
The handoff gap is 70 ms with counters 1,2. No host cadence change is required.

While DRAIN is active, owner remains SP but host 0x2B9 TX is blocked. DRAIN
expires at age >= **50000 us** or on any failed accept condition. Abort clears
pending, records the refusal and restores normal SP acceptance without changing
owner. Its next frame may jump a counter; this is the sole normal I1 exception
and the vehicle DI is expected to tolerate it (physical validation remains).
The 250 ms request deadline still applies throughout DRAIN.

The executable property must retain every intervening host transmission and
assert both directions commit before deadline with perfect 25 Hz OEM, healthy
100 Hz host, every phase 0..40 ms and jitter 0..10 ms. I1/I2 apply; only DRAIN
abort may produce an SP counter jump. The previous open liveness contradiction
is superseded by this coordinator decision (2026-10-26).

## Authority and request (proposed ABI 1)

Panda alone owns `tesla_long_owner`: `0 STOCK`, `1 SP`. No owner NONE. One
value decides both host 0x2B9:0 acceptance and OEM 0x2B9:2 -> 0 forwarding.
The long safety parameter bit is capability, not current ownership. Runtime
rules cannot grant this mode-owned address on any bus or at another length.

App-only control **read** `0xA5`: USB wValue / ControlPacket.param1 is target
owner (0/1), wIndex / param2 must be zero. No OUT stage. The number is free in
the base app, body and bootstub dispatchers; no body/bootstub handler is added.
Only Tesla mode can accept a request. The host must first verify the jointly
shipped health version via 0xDD, and never treat a short reply as acceptance.

Eight-byte reply, explicitly serialized little endian:

| Offset | Type | Meaning |
|---|---|---|
| 0 | u8 | ABI = 1 |
| 1 | u8 | Acked current owner, 0/1 |
| 2 | u8 | Pending target 0/1; 255 means no pending request |
| 3 | u8 | Last refusal reason (below) |
| 4 | u16 | Pending age in ms, floor(elapsed_us/1000); zero if not pending |
| 6 | u16 | Reserved zero |

The reply is a snapshot after processing, never a promise that ownership has
changed. No transaction ID/epoch. Resolve a lost reply by health readback;
never send the opposite target to undo an uncertain result. A repeat of the
same pending target does not extend its deadline. A request for the current
owner is a no-op and does not reverse/replace a pending request. An opposite
request while pending is refused BUSY. A new valid request clears the refusal
reason; a successful handoff leaves OK. Refusals preserve the current owner.

## Health packet change (proposed, not yet applied)

Append to the packed `panda/board/health.h` struct, preserving every old field:

| Offset | C field | Type | Meaning |
|---|---|---|---|
| 49 | tesla_long_owner_pkt | u8 | 0 STOCK / 1 SP |
| 50 | tesla_long_pending_pkt | u8 | 0 STOCK / 1 SP / 255 absent |
| 51 | tesla_long_pending_age_ms_pkt | u16 | Same age as request reply |
| 53 | tesla_long_refusal_pkt | u8 | Same reason as request reply |

Base size 49 -> 54 bytes, within USBPACKET_MAX_SIZE. Firmware version is not a
manually incremented integer: `panda/SConscript` hashes health.h into
`HEALTH_PACKET_VERSION`, and the Python library computes the same hash. The
header change MUST change that version and ship with X-49's C++ health consumer
and the Python field mapping. The exact new hash is recorded only after the
real header is changed and built. Non-Tesla modes report STOCK, absent pending,
age zero; refusals to a request may still report MODE/ARGUMENT.

| Code | Name | Refusal condition |
|---|---|---|
| 0 | OK | No refusal since reset/new valid request |
| 1 | MODE | Not Tesla safety mode |
| 2 | ARGUMENT | Unknown target or nonzero param2 |
| 3 | NOT_CAPABLE | Long safety parameter bit not set |
| 4 | DISENGAGED | DI cruise not engaged |
| 5 | STANDSTILL | Vehicle not moving |
| 6 | AEB | Stock AEB active, including current OEM payload |
| 7 | GAS | Gas pressed |
| 8 | BRAKE | Brake pressed |
| 9 | AUTOPARK | Raw DI Autopark active, not only the old rising-edge latch |
| 10 | RX_INVALID | Missing, bad checksum/counter/quality or stale required RX |
| 11 | OEM_STALE | No validated fresh OEM 0x2B9 |
| 12 | OEM_INACTIVE | Stock target but OEM accState not active |
| 13 | DEADLINE | Pending elapsed >= 250000 us |
| 14 | COUNTER | Candidate counter mismatch |
| 15 | ENVELOPE | Candidate acceleration envelope mismatch / SNA |
| 16 | LONG_LIMIT | Candidate fails existing Tesla longitudinal checks |
| 17 | RELAY | Relay malfunction |
| 18 | BUSY | Conflicting pending target |
| 19 | SP_STALE | Stock target without a recent accepted SP frame |
| 20 | DRAIN_TIMEOUT | DRAIN elapsed >= 50000 us without a valid next OEM |

Validation order is mode, arguments, capability, relay, cruise, motion, AEB,
gas, brake, Autopark, RX, OEM freshness, target-specific checks, deadline,
counter, envelope, existing long checks. A failed safety gate clears pending and records its reason. Counter mismatch
or a gap below 5 ms only blocks that candidate, retaining pending until a
valid candidate or the deadline; these waiting conditions are not refusals. Ordinary owner SP frames
continue to obey the existing actuation limits; rejection never grants OEM
ownership automatically. No Params writes or policy evaluation in firmware.

## Gates and constants (proposed)

- `DRAIN_TIMEOUT_US = 50000` (50 ms), expired at age >= timeout.
- `PENDING_TIMEOUT_US = 250000` (250 ms), expired at age >= timeout.
- `OEM_FRESH_US = 60000` (60 ms), fresh at age <= limit; OEM expected 25 Hz.
- `SP_FRESH_US = 60000` (60 ms), required for stock takeover comparison.
- `ACCEL_TOL_RAW = 5` (0.20 m/s^2, DBC resolution 0.04 m/s^2).
- `HANDOFF_MIN_GAP_US = 5000` (5 ms), inclusive.
- `HANDOFF_MAX_GAP_US = 80000` (80 ms), inclusive.
- Counter modulus 8; no frame rewriting, synthesized counter or AEB marker.

Request and every RX/TX frame must re-evaluate all pending conditions. Health
read/request also expire pending, so idle CAN cannot preserve a timed-out
request. The existing safety tick supplies eventual expiry when no traffic
or health requests arrive; no candidate may commit after the deadline.
Required RX entries must have been seen, valid checksum/quality, counter error
count below the existing threshold, no lag flag, and current elapsed age no
more than max(100000 us, 2 expected RX periods). The ignored 2 Hz UI_status2
frequency check remains ignored; its presence does not relax other RX checks.

DI cruise uses Tesla's existing engaged decoding. Autopark veto uses raw
DI_autoparkState 3/4/9, including rising while cruise is already engaged.
OEM accState active set comes from **tesla_model3_party.dbc** at the base:
`3 ACC_HOLD`, `4 ACC_ON`. In particular, 2 is ACC_CANCEL_RADAR_BLIND and
5 is APC_BACKWARD. A stock request needs active OEM on request and commit.

Accel min/max are raw unsigned 9-bit values, physical value `raw*0.04 - 15`
m/s^2. SNA=511 and reversed min/max reject handoff. Compare corresponding
endpoints in both directions: absolute min difference <= 5 AND absolute max
difference <= 5. Do not equate overlapping intervals to continuity. Stock->SP
also runs every existing Tesla accel, reverse prevention, AEB and actuation
gate. Normal stock OEM forwarding retains the base safety behavior.

## Counter handshakes

STOCK -> SP: keep forwarding validated OEM while pending; remember the last
actually admitted OEM counter/envelope. Only an otherwise allowed SP frame
with `(last_forwarded_OEM_counter+1) mod 8`, gap in 5000..80000 us and
matching envelope, can atomically
commit SP and be admitted. Other SP long actuation is blocked. Capability
without a request is not ownership. With long capability and owner STOCK, host 0x2B9 including cancel is blocked.
Without long capability, the original non-actuating cancel-only path remains.

SP -> STOCK: keep admitting SP while pending until a validated OEM counter
equals `last_accepted_SP_counter` (duplicate). Block that OEM and enter DRAIN
atomically; remember its start time and block SP TX. In DRAIN only the next OEM
counter `(last_accepted_SP_counter+1) mod 8` with valid gates, matching envelope
and gap 5000..80000 us commits STOCK and is forwarded. A bad counter, envelope,
accept condition, DRAIN timeout or request deadline aborts DRAIN and clears
pending with its refusal reason; owner stays SP and SP acceptance resumes.
Record SP envelope/counter on actual admissions, never rejected attempts.
A missing next OEM cannot be called a successful gap-free physical handoff.

AEB has priority over normal owner routing: inspect the CURRENT OEM payload,
forward its AEB frame even when owner SP, block host long TX, clear pending
with AEB. This emergency exception must be excluded explicitly from any normal
source exclusivity timing property, since AEB can arrive after an SP frame.
Owner itself is not silently reversed by AEB or refusal.

## Reset, routing and IRQ boundaries

`tesla_init`, every safety mode set and SILENT reset to STOCK, pending absent,
age zero, reason OK, DRAIN inactive, no remembered counters/envelopes. Non-Tesla mode cannot
retain a Tesla capability. Existing heartbeat timeout -> SILENT and relay
behavior stay unchanged. Release/debug must both honor the long capability
bit; implementation removes only that ALLOW_DEBUG gate.

F4 `bxcan.h` and H7 `fdcan.h` both call safety_fwd_hook BEFORE safety_rx_hook.
Their addr-only forwarding hook cannot inspect same-frame AEB/checksum/counter.
The implementation needs a narrow packet-aware Tesla 0x2B9 RX dispatcher that
validates that frame once, decides forwarding and RX state in one transaction,
then returns the forwarding decision. Other addresses retain their order.
Tests must drive this actual order, rather than call RX then test FWD.

Use the existing nested ENTER_CRITICAL / EXIT_CRITICAL primitives across each
RX routing + safety-state transaction and each host TX check + queue admission.
Protect request, health snapshot, mode reset and tick accesses to the same
state. A lock only inside tesla_tx_hook is insufficient: after unlock an OEM
IRQ could change owner before the already-accepted SP frame enters the queue.
Queue-full / peripheral FIFO arbitration and wire completion are separate from
libsafety acceptance and need an explicit physical output proof boundary.
Fixed bounded state, no heap, no generic rules-engine forwarding permission.

## Scope / evidence

Future implementation touches Tesla safety, narrow safety dispatch, libsafety
tests/bindings, app control/health, F4/H7 CAN receive and transmit admission,
mode/tick critical sections, Python health keys, and XPILOT.md section 6.
X-49 owns host consumers, shadow logging and manual engaged requests. X-50 owns
the speed policy. No source edits in selfdrive/, openpilot/, plugins or device.

Old fork reference, read-only at
`onemiless/opendbc@65cc8fdbcb6aaaf0533ba296c9e6fe5422a696e8`: tesla.h uses an
AEB=3 internal marker then flips ownership on a normal SP TX; carcontroller.py
uses a pending SP takeover/ramp. This is not counter/phase proof and is not
copied into this request/health ABI.

The timing reproducer and failure matrix live in
`opendbc_repo/opendbc/safety/tests/test_tesla_long_owner_contract.py`.
It is a requirements check, not a replacement for firmware/libsafety E2E.
Safety, MISRA, mutation, build-size and capture replay results must identify
the exact source and actual gate status; DRAIN liveness and abort exceptions must be exercised explicitly.

## X-20261026-49A host integration

Host controlsd/card/CAN owner and veto implementation, runtime reader audit,
49B hook/claim/HOST_CAPS boundary and verification limits are documented in
[xpilot-long-owner-host.md](xpilot-long-owner-host.md). The executable Python
model now implements DRAIN, including intervening host sends, abort and AEB
exceptions; it does not replace the firmware's actual RX/TX/FWD tests.
