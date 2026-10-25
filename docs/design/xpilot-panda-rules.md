# Xpilot Panda runtime rules ABI v1

Contract for X-20261025-32 firmware and X-20261025-33 host. All integers are unsigned
little endian; offsets below are bytes. No C struct serialization, pointers, padding,
signature, per-plugin safetyParam bits, heap, or persistent storage. G14 stays fixed.
Limits: 8 rules, 4096 total bytes including the table header, 8 fields/rule,
8 exempt values/field, 8 legal tuples/rule. One RAM table buffer; BEGIN invalidates
the old table immediately. A failed upload leaves an empty active table.

## Table

16-byte header followed by exactly `count` variable-size rule records, no trailing bytes:

| Offset | Type | Value |
|---|---|---|
| 0 | u32 | magic 0x31525058 (`XPR1`) |
| 4 | u16 | ABI = 1 |
| 6 | u16 | header length = 16 |
| 8 | u16 | total length |
| 10 | u8 | rule count, 0..8 |
| 11 | u8 | reserved = 0 |
| 12 | u32 | reserved = 0 |

CRC32 is zlib/IEEE CRC32 over **all table bytes**, including this header. It is
supplied by BEGIN, not stored inside the table. An explicit empty table is this
header with total length 16/count 0; CLEAR instead reports committed CRC 0.

## Rule: template_clone.v1 (type 1)

| Offset | Type | Meaning |
|---|---|---|
| 0 | u16 | record length = 84 + 44*field_count + 36*tuple_count |
| 2 | u8 | type = 1 |
| 3 | u8 | field_count 0..8 |
| 4 | u32 | TX address (29-bit CAN) |
| 8 | u32 | RX address (29-bit CAN) |
| 12,13 | u8 each | TX bus, RX bus: both must be 1 |
| 14,15 | u8 each | TX length, RX length: equal, 1..8 |
| 16 | u32 | template_ttl_us, 1..0x7fffffff, fresh when age <= TTL |
| 20 | u32 | min_interval_us, 1..0x7fffffff, allowed when elapsed >= interval |
| 24 | u8 | session: 1 IDLE, 2 WINDOW |
| 25 | u8 | tuple_count 0..8 |
| 26 | u16 | max_count 1..65535 |
| 28 | u32 | IDLE idle_reset_us 1..0x7fffffff; WINDOW must be 0 |
| 32 | u32 | IDLE max_span_us (0 disables), WINDOW window_us (nonzero); <=0x7fffffff |
| 36 | u8 | counter kind: 0 none, 1 template_plus_one |
| 37,38 | u8 each | counter bit_offset, width; zero for none; width 1..32 |
| 39,40 | u8 each | TX checksum kind, checksum byte |
| 41,42 | u8 each | RX checksum kind, checksum byte |
| 43 | u8 | reserved = 0 |
| 44 | u8[8] | RX predicate mask |
| 52 | u8[8] | RX predicate value (bits outside mask must be zero) |
| 60 | u8[8] | copy_mask (TX bits equal corresponding RX template bits) |
| 68 | u8[8] | constant_mask |
| 76 | u8[8] | constant_value (bits outside mask must be zero) |

Checksum kind: 0 none (byte must be 0), 1 TESLA_SUM8. SUM8 adds all payload bytes
except checksum_byte and all four address bytes, modulo 256. RX uses RX address,
TX uses TX address. It has no per-address lookup. Byte must be within frame length.
Bits use Intel numbering (bit 0 = byte 0 LSB). All masks beyond frame length are 0.
Each TX bit must belong to exactly one of copy, constant, a field, counter, checksum.
Counter wraps modulo 2**width and is always template counter + 1, never last TX + 1.

Each 44-byte field record, starting at offset 84:

| Offset | Type | Meaning |
|---|---|---|
| 0,1 | u8 each | bit_offset, width 1..32, wholly within payload |
| 2 | u8 | exempt_count 0..8 |
| 3 | u8 | reserved = 0 |
| 4,8 | u32 each | inclusive min, max; min <= max < 2**width |
| 12 | u32[8] | exempt values within min..max; unused entries zero |

A frame is exempt only when it has at least one field and **every** field value
occurs in that field's exempt list. Exempt frames skip controls_allowed and all
session budget checks/updates; they still update last TX for the interval gate.
All other TX requires controls_allowed. auto_speed and ambient have no exemptions.
Navassist uses request 3 and reason 4 as exemptions, permitting cancel to release
lamps even after disengagement or budget exhaustion.

Then each 36-byte legal tuple:

| Offset | Type | Meaning |
|---|---|---|
| 0 | u8 | nonzero field_mask, only existing field indices |
| 1 | u8[3] | reserved = 0 |
| 4 | u32[8] | values indexed by field; unselected entries zero; selected within range |

No tuples means range validation alone. Otherwise at least one tuple must match
all its selected fields. Different tuples are alternatives (OR).

IDLE starts on first accepted nonexempt TX or an idle gap >= idle_reset_us since
last accepted TX. Within a session count < max_count and (if enabled) age < max_span_us.
WINDOW starts on first accepted nonexempt TX; age >= window_us starts a new window.
Count < max_count within the window. Rejected TX changes no runtime state.
All elapsed times use uint32 subtraction, including wrap; host must refresh templates
and reset modes within a timer cycle (no multi-wrap elapsed-time guarantee).

## Ownership and gates

Tesla mode owns all addresses in its TX and RX inventory, plus forwarded/blocked
0x27D, independent of bus/length/configuration. Current set: 0x118, 0x145, 0x155,
0x257, 0x27D, 0x286, 0x2B9, 0x311, 0x370, 0x3DF, 0x488. Loader rejects these
TX addresses and duplicate TX addresses. Mode-owned TX always uses only the mode
hook, with its existing whitelist bus/length gate. For non-mode TX, exactly one
matching rule decides; absent match blocks. Rules require TESLA, vehicle bus,
bus 1 and no relay malfunction. Silent/noOutput never invoke a rule.

## Control requests (app firmware only)

These requests are intentionally unimplemented in bootstub/body firmware. Before
**any** EP2 write, host must read CAPS and validate the app magic/ABI/limits;
bootstub EP2 is flash programming and must never receive a rule record.
ControlPacket param1 is USB wValue (u16); param2 is USB wIndex (u16).
All operations below use control reads, including mutations (no OUT data stage).

| Request | Name | param1 | param2 | response |
|---|---|---|---|---|
| 0xA0 | CAPS | 0 | 0 | 16 bytes below |
| 0xA1 | STATUS | 0 | 0 | 24 bytes below |
| 0xA2 | BEGIN | total_len | count | STATUS; allocates upload_id |
| 0xA3 | COMMIT | CRC32 low 16 | CRC32 high 16 | STATUS |
| 0xA4 | CLEAR | 0 | 0 | STATUS |

BEGIN(total_len,count,crc32) is represented by BEGIN control + an initial EP2
metadata record carrying crc32. This is required because USB has only two u16
arguments. COMMIT repeats that CRC; both must match. No implicit COMMIT.
BEGIN and COMMIT refused in car modes (anything except SILENT, NOOUTPUT,
ALLOUTPUT, ELM327). CLEAR allowed in every mode and cancels upload/state.
Boot starts empty. Every successful set into a non-car mode clears table/upload;
every mode set resets runtime state. Entering a car mode aborts an unfinished upload.
No table/fault affects the base mode's arming. Host X-33 reports feature fault,
CLEARs on error, then still performs normal 0xDF/0xDC setup.

CAPS: `u32 magic=0x31525058, u16 abi=1, u16 max_len=4096, u8 max_rules=8,
u8 max_fields=8, u8 max_tuples=8, u8 max_exempt=8, u16 ep2_record_len=64,
u16 reserved=0`.

STATUS: `u32 magic, u16 abi, u8 state (0 EMPTY,1 UPLOADING,2 COMMITTED),
u8 error, u32 committed_crc32, u16 committed_len, u8 committed_count,
u8 reserved=0, u16 upload_id, u16 received_len, u16 expected_len,
u8 expected_count, u8 reserved=0`. After upload/validation failure committed crc/len/count are zero.
Upload IDs increment per BEGIN, skip zero, and are invalidated by CLEAR/mode change.

## EP2 records

Exactly 64 bytes per write (USB or SPI endpoint 2). 8-byte header:
`u16 upload_id, u16 offset, u16 length, u16 flags`, then <=56 data bytes; all unused
bytes must be zero. Only contiguous records are accepted; duplicates, overlaps,
gaps, zero-length data and wrong IDs abort upload. Flags: 1 = metadata, with offset
0, length 4, data = expected table CRC32; must be first and occur exactly once.
Flags 0 = data, offset starts at 0 and advances by length until total_len. Other flags
are rejected. Records after failure cannot restore the upload; host must BEGIN again.
Firmware bounds work to one record per handler. Host sends one record per pandad
loop iteration with heartbeat continuing, an overall deadline, then COMMIT and
STATUS comparison of committed CRC/count. Deadline is host-owned, no firmware wait.

Errors (STATUS.error, sticky until BEGIN/CLEAR/successful mode change):
0 OK, 1 MODE, 2 LENGTH, 3 STATE, 4 RECORD, 5 CRC, 6 ABI, 7 RULE,
8 COVERAGE, 9 OWNERSHIP. Any upload/validation error invalidates the whole table;
car-mode mutation refusal preserves an already committed table but sets MODE.

## Host manifest and Params (X-20261025-33)

`rules` is a JSON list, default `[]`. Each entry has exactly these keys; all
numeric values are JSON integers (booleans are refused). `type` is
`"template_clone.v1"`; its suffix fixes ABI 1, with no separate per-rule ABI field.
No `safety` field is accepted, including null or an old `safety.profile`.

- `tx_address`, `rx_address`: numeric 29-bit addresses; `tx_bus`, `rx_bus`: 1.
- `tx_length`, `rx_length`: equal, 1..8.
- `template_ttl_us`, `min_interval_us`, `session`, `max_count`, `idle_reset_us`:
  exactly the table semantics above. Session is numeric 1 IDLE or 2 WINDOW.
- IDLE requires `max_span_us`; WINDOW requires `window_us` instead. Both map to
  record offset 32; WINDOW requires `idle_reset_us=0`.
- `counter`: `{kind, bit_offset, width}`; numeric kinds 0/1 as above.
- `tx_checksum`, `rx_checksum`: `{kind, checksum_byte}`; numeric kinds 0/1.
- `predicate_mask`, `predicate_value`, `copy_mask`, `constant_mask`,
  `constant_value`: lists of exactly eight byte integers, zero beyond length.
- `fields`: list of `{bit_offset, width, min, max, exempt_values}`.
- `legal_tuples`: list of `{field_mask, values}`. `values` has exactly one entry
  per field, zero for unselected fields; encoder pads remaining slots to eight.

Manifest load checks complete TX coverage, masks, counters, checksums, field and
session bounds as well as ownership/bus/type/size. Numeric `can.tx` address/bus
pairs must equal the rules' TX pairs. `RULE_ABI`/`HOST_CAPS.rule_abi` are 1.
The sole byte encoder is `openpilot/tesla/panda_rules.py`; T32 fixtures import it.
No production host validation reads legacy safety.json/headers.

`XpilotPandaRules` is BYTES: **complete table bytes followed immediately by a
4-byte little-endian CRC32 of those table bytes**. Total param size is 20..4100;
there is no extra length prefix or JSON envelope. Table header supplies length
and count. CRC is not part of the firmware table; pandad sends it in the metadata
record and COMMIT. Card writes this synchronously after setup and before CP/SP
publication, even for zero rules. Manager/onroad transition clears the param;
missing/malformed bytes cause a feature fault, never reuse of the previous table.
The drive snapshot records the trailing CRC as an integer.

Card compiles live, enabled claim winners in host order. Duplicate TX addresses
(including differently spelled claims) or aggregate 8-rule/4096-byte overflow drop
the offending plugin from card, record `card.rules` in `XpilotPluginFaults`, and
retain preceding and subsequent valid plugins. Setup/init failures are excluded
before compilation. There is no profile translation or plugin safetyParam bit.

`XpilotPandaRulesFault` is BOOL, cleared on manager start or an observed
onroad->offroad edge in the same pandad process. The initial false before
receiving deviceState never clears a previous fault. Loader errors latch it for
the drive. Runtime `record_fault` for any rule-owning plugin also sets it
synchronously: every runtime hook exception remains critical for rule ownership.
Init/setup/compiler rejection happens before the table exists and only faults
that plugin. While latched, no table (including an empty table) is uploaded.

Pandad revokes with CLEAR, then independently reads STATUS and verifies EMPTY,
zero count/length/CRC. If that cannot be verified, it requests NO_OUTPUT and
requires health readback in NO_OUTPUT. Only verified revocation sets the local
revoked latch; otherwise the next 10 Hz check retries with bounded calls. Base
arming still proceeds during the normal initial arming sequence. An already
configured onroad Panda is never automatically re-armed or reloaded after SILENT,
reset, or a NO_OUTPUT fallback; its mismatch stays visible to selfdrived.
Reload only occurs in the next normal offroad->onroad arming sequence, including
a Panda reset while offroad. Disabled plugins take effect at the next card start.

Arming, drive-state and fault checks remain at 10 Hz. Only an active upload steps
at 100 Hz, with at most one 64-byte EP2 record per iteration, including metadata.
The idle fast path returns before any health/Params reads. Heartbeats retain the
existing 10 Hz path. Overall upload deadline is 2000 ms; changing params does not
extend it. Rule controls, EP2 and loader health reads use 5 ms transport timeouts.
SPI excludes mutex/flock wait from the transfer timeout; the overall upload
deadline still includes wall time. USB finite controls do not retry forever,
and SPI rule requests do not inherit the old 100 ms minimum or unbounded retry.
CAPS must match app magic, ABI, limits, record size and reserved bytes before
CLEAR/BEGIN or any EP2 write. Bootstub/body have no valid app CAPS. On failure,
CLEAR is attempted only after app capabilities were established on this connection,
including when a changed param forces another CAPS check after a previous COMMIT. COMMIT is
followed by an independent STATUS read matching state/error/CRC/length/count.
`card.state_update` gates observe_can sends on the same `core_alive` as controls.

Host proof: `safety_manifest_check.py` G7-G12, `manifest_check.py` M18,
`plugin_host_check.py` P45-P48, `store_check.py` S40-S41, `phase4_check.py` C20.
`panda_rules_check.py --output <dir>` drives the production C++ PandaSafety/Panda
through a fake transport into the real T32 C loader, with H1-H13 fault injection.
H14 compiles the production SPI retry/transfer/LockEx source, simulates only the
ioctl peer, and holds the real lock for 50 ms before a NACK/ACK exchange.
Build `scons openpilot/selfdrive/pandad/pandad
openpilot/selfdrive/pandad/tests/test_panda_rules` first. Output includes table
bytes, trace.jsonl, native.log, result.json and SHA-256 input identities; no device.

## Migration / proof boundary

Production includes only the runtime engine. auto_speed.h, ambient_light.h,
registry.h, gen_safety.py and safety.json remain solely as differential/reference
fixtures in this task; delete them after X-33 lands and differential gates pass.
The pinned navassist header is a test fixture, not production firmware. Its old
cancel budget consumption is the sole deliberate equivalence difference.
No signature or network authentication work is included. No device/flash proof.
