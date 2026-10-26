# G8 host contract (X-20261026-49A)

The wire authority is [xpilot-long-owner.md](xpilot-long-owner.md), including
DRAIN. Panda alone commits STOCK/SP. No device or firmware change in 49A.

## Transport and schema

The X-49 version/hash-gated health/A5 consumer is unchanged. Only the exact
jointly built 54-byte health ABI enables requests; the dependency at this base
still has the 49-byte header. Initial legacy firmware pins today's CP-selected
owner and exact CAN cadence. Once the ABI has been observed, loss of that ABI
selects STOCK and locks switching; it cannot reopen the legacy SP branch.
`carControlSP.longOwnerAvailable` therefore also remains set after ABI loss to
keep the controller in the owner-gated path. A valid reset health follows STOCK.

`carStateSP.teslaLongControl` appends OEM receive monotime, accState, acceleration
min/max and counter. card fills this from the existing Tesla CAN parser, not
host frame time. The parser's validated receive timestamp supplies freshness;
`carState.canValid` remains a separate veto. No existing field ordinal changes.

## Policy, veto and acknowledgment

`selfdrive/controls/lib/long_owner.py:desired_owner(ctx, CS)` is the plain 49B
hook point, returning None (no request), `stock` or `sp`. Production returns None;
the replay substitutes a test policy. ctx is Controls with CP/CP_SP, sm and LoC.
No plugin code, claim or HOST_CAPS is registered in 49A. 49B owns the exclusive
`role:long.owner` registration, manifest/required-hook validation and capability
advertisement together, under the explicit `openpilot/tesla/` scope boundary.
Generic role syntax alone does not make this hook available to plugins.

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
| 49B UI, shadow observer and policy readers | Outside 49A; must consume CC_SP, no claim of implementation here |

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
LO11-14 (HUD/shadow/manual/plugin registry) remain 49B work. G3 and X-50 remain
separate. Firmware/header integration and matching health ABI remain required.
The original step 1 transport alone cannot enable a plugin. The 49B surface
below adds the optional policy hook, manual setting and observer; it still
requires 49A runtime integration and jointly built firmware. Legacy firmware retains the
pre-G8 owner/cadence behavior. G3 mixed lateral and X-50 speed policy are separate.

## G8 plugin surface (X-20261026-49B)

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
trigger is left-stalk wiper button **first detent**, SCCM_leftStalk 0x249 on
bus 0, bits 14..15 = 1 (`1ST_DETENT`). This does not overlap the current
MADS/LKAS screen-touch input or the cruise cancel path. It still performs the
OEM wiper action; there is no frame suppression. Vehicles without that stalk
message have no manual trigger. Observe a real release (0) before arming;
first detent proposes the other acknowledged owner once per press. Holding,
second detent (2), SNA (3), initial held input, malformed/wrong-bus/stale (>100
ms) input cannot retrigger. No proposal unless engaged and ABI available.
Manual takes precedence over a valid plugin proposal in the same cycle;
a plugin fault suppresses that cycle's proposal.

**49A integration contract:** manual proposals are one-cycle pulses. The caller
must latch the proposed target through its 1 s desired debounce (and clear it
on acknowledgement/disengagement); do not discard it on the next None.
Both manual and plugin proposals must pass every host veto, cooldown, request
lock and Panda acknowledgement gate. A proposal is never a handoff proof.
49B does not edit controlsd/card/carcontroller; their integration remains 49A.

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
and 49A+firmware end-to-end handoff still require later integration.
