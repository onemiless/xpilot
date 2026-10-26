# G8 host contract (X-20261026-49C)

The wire authority is [xpilot-long-owner.md](xpilot-long-owner.md), including
DRAIN. Panda alone commits STOCK/SP. This integration includes X-48 firmware commits; no device or flash.

## Transport and schema

The X-49 version/hash-gated health/A5 consumer is unchanged. Only the exact
jointly built X-48 54-byte health ABI enables requests: hash `0x25f8efcd`
(637071309). The merged health header and both consumers match. Initial legacy firmware pins today's CP-selected
owner and exact CAN cadence. Once the ABI has been observed, loss of that ABI
selects STOCK and locks switching; it cannot reopen the legacy SP branch.
`carControlSP.longOwnerAvailable` therefore also remains set after ABI loss to
keep the controller in the owner-gated path. A valid reset health follows STOCK.

`carStateSP.teslaLongControl` appends OEM receive monotime, accState, acceleration
min/max and counter. card fills this from the existing Tesla CAN parser, not
host frame time. The parser's validated receive timestamp supplies freshness;
`carState.canValid` remains a separate veto. No existing field ordinal changes.

## Policy, veto and acknowledgment

`selfdrive/controls/lib/long_owner.py:desired_owner(ctx, CS)` calls the exclusive
`plugins.long_owner_policy(params, CP, CP_SP)` callable, built once at controlsd
startup. It returns None (no proposal), `stock` or `sp`. The policy receives the
health-owned LongOwner context with available, owner, enabled, sp_accel and sm.
The optional `role:long.owner`, manifest required-hook validation and HOST_CAPS
advertisement ship together. No policy leaves health ownership unchanged.

controlsd follows the single Tesla Panda's acknowledged owner. A desired target
must remain unchanged for 1 s, and every completion/refusal starts a 2 s cooldown.
Requests need longitudinal capability, engaged OEM cruise and healthy fresh
inputs. Gas, brake, standstill, stock AEB, ACC/Panda faults, |aEgo| >= 0.35,
closing lead TTC < 3 s, OEM age > 60 ms, inactive OEM accState (outside 3/4),
invalid/SNA envelope or either endpoint gap > 0.20 m/s² veto a request. For SP,
comparison uses the preceding LongControl output; for a STOCK->SP candidate it
uses aEgo, the ramp seed. Panda rechecks the actual transmitted envelope.

Two consecutive host/Panda refusals, request/ack mismatch > 0.5 s, health fault,
ABI loss, or invalid/throwing policy lock switching until disengage. A refusal
preserves the acknowledged owner; a lost reply never triggers an opposite
request. Historical refusal codes are not counted as a new immediate refusal:
after a request, wait for pending/clear reason, a changed reason, or the 250 ms
deadline. A successful ack resets the consecutive refusal count. The lock blocks
switching; it does not invent a different owner or replace normal disengagement.

## CC/CAN and bumpless takeover

CC_SP is built in the same state_control call as CC. longActive, cruise override,
cancel and Tesla CAN use that owner. controlsd publishes the CC_SP pair before
CC, with CC_SP.controlMonoTime equal to the CC event timestamp; card rejects
missing/stale/invalid or unpaired CC_SP. selfdrived/plannerd subscribe normally,
so stale owner messages participate in communication fault checks.

STOCK sends no ACC_ON/accel, with cancel-only behavior retained. The necessary
exception is an explicit pending SP request: candidate ACC_ON frames are sent
at 100 Hz with the same latest OEM+1 until ack, allowing retry after a <5 ms
Panda rejection. Accepted owner remains STOCK until health commits SP.

SP sends latest OEM counter+1 mod 8 on the first card cycle after each new OEM
receive timestamp. If OEM is absent for 60 ms, independent 40 ms sending resumes
with incrementing counters. OEM recovery re-locks to OEM+1. Firmware freshness
and actuation gates still apply; fallback is not permission to actuate stale CAN.
Pending STOCK/DRAIN does not alter host cadence or remove intervening SP frames.

A STOCK->SP transition/candidate starts at aEgo and limits jerk to +0.6/-1.5 m/s³
for at most 1 s. The regular target resumes after that window; larger subsequent
target changes remain the normal planner's responsibility. Planner resets its
speed filter, cruise/output acceleration and MPC state/history from vEgo/aEgo;
LongControl resets/seeds its integral and previous output. Initial pinned SP
never starts a takeover ramp, preserving today's behavior.

## Owner reader audit

| Reader | Runtime source / disposition |
|---|---|
| controlsd longActive, override, cancel | Same local CC_SP owner; legacy non-PCM speed behavior retained without ABI |
| Tesla carcontroller ACC branch | CC_SP owner/request; CP only in explicitly pinned legacy branch |
| longitudinal planner reset, MPC seed and acceleration state | CC_SP owner, reset while STOCK and seed on STOCK->SP |
| LongControl | Receives CC.longActive from that same owner; seeded by controlsd on takeover |
| card control apply | Paired CC/CC_SP only; no CP mutation |
| CarEvents brakeHold / PCM engagement | CC_SP owner; stock owner uses OEM cruise engagement |
| selfdrived radar faults, stock braking FCW suppression, personality buttons | CC_SP owner |
| selfdrived resumeBlocked / cruiseMismatch | PCM policy treats Tesla stock owner as OEM cruise |
| VCruiseHelper speed source | G8 STOCK uses OEM set speed; existing non-PCM SP behavior otherwise |
| PlannerSP SCC/SLA long_enabled and cached SLA.pcm_op_long | Refreshed from CC_SP every planner cycle |
| SP speed_limit_pre_active_alert | CC_SP owner, matching SLA runtime branch |
| sunnypilot car/interfaces._cleanup_unsupported_params | Startup capability; retained, never called for an owner switch |
| speed_limit/helpers.set_speed_limit_assist_availability | Capability/setting availability, retained; no per-switch Params mutation |
| VCruiseHelper initialization, button timers, SP cruise minimum/enabled policy | Configured PCM/button semantics, retained; stock speed branch explicitly wins |
| UI, shadow observer and policy readers | Same health-owned context / live valid CC_SP; no CP owner inference |

## Evidence and limits

Run `PYTHONPATH=.:opendbc_repo:msgq_repo:tinygrad_repo python3
 tools/sp_tesla_e2e/long_owner_check.py --output <evidence-dir>` (one shell line).
The single result.json contains source hashes, traces, frame hashes and rc.
Failure matrix precedes implementation; no new unit tests. LO1-10/15 execute
production controlsd/card/CAN paths with deterministic inputs and a lateral
stub. LO9 captures the real MPC pre-solve seed. Native QP failure also reproduces
with unchanged standalone MPC in this Mac environment; solved-trajectory proof
is a reported gap. SCC/map/boost policies are stubbed in that focused MPC replay.
LO16 is a Python Panda contract model across phase 0..40 ms / jitter 0..10 ms,
with all intervening host sends, I1/I2, early retry, DRAIN abort and AEB exception.
It is not libsafety, multiprocess IPC, physical bus timing or vehicle proof.
LO17/18 execute the real collector/manual-to-host path, one-cycle latch, veto,
cooldown, refusal/ack/disengagement/fault reset and new/legacy Python health/A5.
plugin_host_check covers hook/manual/HUD companion contracts. X-48 supplies
real libsafety G8 tests; G3 and X-50 remain separate.
## G8 plugin surface (X-20261026-49B + 49C)

`Plugin.long_owner(CP, CP_SP, params)` returns None or an object exposing
`desired(ctx, CS) -> None | "stock" | "sp"`. The exclusive claim is
`role:long.owner`; its manifest must require `hooks: ["long_owner"]` so an old
host refuses it. `HOST_CAPS.hooks` advertises `long_owner`, API remains 1.
Priority/id resolution chooses at most one policy at controlsd startup. This
is optional, not a required role. No policy means keep the health-acknowledged
owner. Claims cannot grant CAN permissions or change safety/capability bits.

The exported collector is `plugins.long_owner_policy(params, CP, CP_SP)`.
For Tesla it returns a callable `desired_owner(ctx, CS)` (even without a plugin,
so the core manual input/shadow observer can run); for other brands None.
Construct once per drive. Pass a context with `available: bool` (the exact
verified firmware ABI), `owner: "stock" | "sp"` (acknowledged by health),
`enabled: bool` (engaged), `sp_accel: float` (current SP command, m/s²), and
`sm` for plugin observations. Call every control cycle, including while STOCK
owns or disabled. This surface never writes ownership or sends CAN/USB.
Constructor errors, missing desired, exceptions and non-None values other than
the two exact strings call `record_fault`; the hook remains disabled for the
drive, including controlsd restarts via `XpilotPluginFaults`. No loser is
promoted after a fault. A failed call returns None, retaining owner.

Core `XpLongOwnerManual` is a persistent BOOL default 0, editable only offroad
in both Tesla settings pages and sampled when creating the callable. The
trigger is the rising edge of exactly four active touches on the vehicle bus:
`UI_status2` 0x3DF bus 1, `UI_activeTouchPoints` byte 3 (the same signal
CarStateExt already parses). A fresh 8-byte frame and an observed non-four
sample are required before arming; an initial/continued four-finger hold cannot
retrigger. Stale (>100 ms), wrong-bus or malformed frames cannot arm or fire.
Three/five fingers do not propose an owner change. MADS retains exact configured
count matching; four fingers cannot activate the three-finger MADS option.
MADS configured to four fingers shares the gesture; choose three/five when using
manual longitudinal switching. The wiper button keeps only its stock function.
No proposal unless engaged and the health ABI is available.
Manual takes precedence over a valid plugin proposal in the same cycle;
a plugin fault suppresses that cycle's proposal.

Manual proposals are one-cycle pulses, marked separately by the collector.
The host holds the manual target ahead of subsequent plugin proposals through
the 1 s desired debounce and pending request. Health commit, host/Panda refusal,
lock or disengagement clears it. None from a plugin never invents a new manual
edge. Both proposal sources pass every host veto, cooldown, lock and Panda
acknowledgement gate. A hook call fault suppresses the manual pulse and locks
switching until disengage; the faulted policy stays disabled for the drive.

Core `LongOwnerChip` uses the existing HudOverlay discovery/render path on
both big screen and mici. UI subscribes carControlSP and draws only a live,
valid message with `longOwnerAvailable`. It displays STOCK/SP, ACTIVE/IDLE and
last host refusal reason (30 visible characters), using the existing font,
translucent dark background and orange refusal indication. It is a normal
editable overlay. Old/missing firmware produces no chip.

The same callable observes bus-2 OEM 0x2B9 without transmitting. While verified
owner is SP, fresh 8-byte OEM frames produce at most one 25 Hz
`cloudlog.event("xpilot.long_owner_shadow", ...)`: CAN monotime, `accState`,
physical `accelMin`, `accelMax`, 3-bit `counter`, and current `spAccel`.
Existing swaglog IPC → logmessaged → logMessage → loggerd writes the rlog;
no service/schema/uploader is added. Samples are observations, not validated
safety admissions. Missing frames are not synthesized; late backlogs are not
replayed at burst rate. These logs cannot prove physical actuation or DRAIN
liveness. Host fixtures exercise decoded fields/rate/gates; real rlog capture
and physical host/firmware handoff still require later vehicle validation.
