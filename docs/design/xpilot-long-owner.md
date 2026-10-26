# G8 Tesla longitudinal owner contract (X-20261026-48 / X-20261026-49)

Status: **BLOCKED at the timing acceptance gate; proposed ABI, not implemented**.
Base: `1b52d017f4e6605cf6751963c92dbd899d38891c`. This document is the first
commit of X-48 so X-49 can review the wire contract. It is not a firmware
capability advertisement. Do not enable an engaged switch against this branch.
No device access or flashing is part of X-48. G3 lateral handoff is separate.

## Timing contradiction that must be resolved before implementation

The requested stock-to-SP handshake and X-49 phase rule admit this input, with
all engagement, freshness, acceleration and safety gates satisfied:

| Time (us) | Event | Required result |
|---|---|---|
| 0 | OEM counter 0 | Forward while stock owns longitudinal |
| 1000 | Request SP | Pending, keep stock |
| 10000 | First host cycle after OEM; SP counter 1 | Accept first valid OEM+1 and commit SP |
| 40000 | OEM counter 1 | Block because SP now owns longitudinal |
| 50000 | SP counter 2 | Accept |

The bus-0 counter sequence is 0,1,2, but the first output gap is **10 ms** and
the fixed 40 ms slot `[0,40000)` contains **both sources**. A counter check does
not prove the requested "never both sources accepted in the same 40 ms slot".
Changing the name of the counter generation to "slot" would weaken that gate.
This is a schedule counterexample, not a result from implemented firmware.

Adding a 40 ms minimum interval alone is insufficient: when every SP candidate
arrives 10 ms after the latest OEM, forwarding all OEM until takeover makes
every candidate too early. All requests then expire, even with perfect 25 Hz
OEM and a healthy host. Dropping the next OEM before a replacement SP frame is
secured contradicts make-before-break. Delaying an SP candidate requires a
buffer/scheduler and a defined arbitration rule against the next OEM; neither
is in the agreed immediate TX acceptance contract.

Similarly, "never a slot with neither" needs a slot origin and a jitter budget.
Two legal intervals near 40 ms can straddle a fixed slot boundary; a physical
bus guarantee also requires queue admission and bus transmission evidence,
not merely success from `safety_tx_hook` or `safety_fwd_hook`.

Resolution must explicitly choose a timing contract: allow and bound the
stock-to-SP phase advance (with counter slots distinct from wall-clock slots),
or change host scheduling / introduce a secured candidate scheduling protocol.
X-48 does not choose a weaker safety gate on behalf of the owner. Until this
is resolved, firmware implementation and enabling the release long flag are
blocked. The ABI below is reserved in this design only.

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

Validation order is mode, arguments, capability, relay, cruise, motion, AEB,
gas, brake, Autopark, RX, OEM freshness, target-specific checks, deadline,
counter, envelope, existing long checks. Rejecting a candidate for any handoff
failure clears pending and records that reason. Ordinary owner SP frames
continue to obey the existing actuation limits; rejection never grants OEM
ownership automatically. No Params writes or policy evaluation in firmware.

## Gates and constants (proposed)

- `PENDING_TIMEOUT_US = 250000` (250 ms), expired at age >= timeout.
- `OEM_FRESH_US = 60000` (60 ms), fresh at age <= limit; OEM expected 25 Hz.
- `SP_FRESH_US = 60000` (60 ms), required for stock takeover comparison.
- `ACCEL_TOL_RAW = 5` (0.20 m/s^2, DBC resolution 0.04 m/s^2).
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

## Counter handshakes (timing gate unresolved above)

STOCK -> SP: keep forwarding validated OEM while pending; remember the last
actually admitted OEM counter/envelope. Only an otherwise allowed SP frame
with `(last_forwarded_OEM_counter+1) mod 8`, matching envelope, can atomically
commit SP and be admitted. Other SP long actuation is blocked. Capability
without a request is not ownership. Stock cancel handling must be explicitly
tested independently of handoff; cancel cannot become a takeover backdoor.

SP -> STOCK: keep admitting SP while pending. On a validated OEM whose counter
equals the last accepted SP counter and whose envelope matches that SP frame,
commit STOCK while suppressing this duplicate OEM. Block subsequent SP TX;
the next valid OEM with the next counter is forwarded. Record the SP envelope
on actual accepted frames, not on rejected TX attempts. A missing next OEM
cannot be called a successful gap-free physical handoff.

AEB has priority over normal owner routing: inspect the CURRENT OEM payload,
forward its AEB frame even when owner SP, block host long TX, clear pending
with AEB. This emergency exception must be excluded explicitly from any normal
source exclusivity timing property, since AEB can arrive after an SP frame.
Owner itself is not silently reversed by AEB or refusal.

## Reset, routing and IRQ boundaries

`tesla_init`, every safety mode set and SILENT reset to STOCK, pending absent,
age zero, reason OK, no remembered counters/envelopes. Non-Tesla mode cannot
retain a Tesla capability. Existing heartbeat timeout -> SILENT and relay
behavior stay unchanged. Release/debug must both honor the long capability
bit after implementation; the ALLOW_DEBUG gate is still present at this stage.

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
No safety, MISRA, mutation, build-size or capture replay result may be claimed
as proof of G8 until implementation exists and those gates actually run.
