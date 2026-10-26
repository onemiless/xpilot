# G8 host contract (X-20261026-49)

The firmware wire authority is [xpilot-long-owner.md](xpilot-long-owner.md),
including the coordinator DRAIN amendment of 2026-10-26. Host never edits frames
or withholds normal OEM+1 SP sends during pending STOCK/DRAIN; health alone
commits the runtime owner. Firmware and host ship together, no device in X-49.

## Transport and schema

`PandaState` appends owner availability, owner, pending, age, refusal and health
version. `CarControlSP` appends stock/SP owner, request (255 absent), availability,
last reason, switch lock, long active and matching carControl event timestamp.
Existing field ordinals/types are unchanged. `CarControlSP` dataclass mirrors
those fields for card conversion. No new service or per-switch Params.

pandad reads 0xDD before 0xD2. Only the exact jointly built health.h SHA256 hash
and 54-byte layout enable 0xA5. The SCons define remains zero while the dependency
has no owner fields; unknown extended/short health is rejected. Legacy 49-byte
health remains readable and never enables requests. DRAIN_TIMEOUT refusal 20 is accepted. No malformed/short reply
changes owner; the next health read resolves an uncertain request. Requests
are edge-triggered at 10 Hz, bounded to 5 ms, Tesla engaged/onroad only through
controlsd. No blind opposite request or automatic safety-mode change.

Native failure matrix precedes implementation in
`selfdrive/pandad/tests/test_long_owner.cc`. Future runtime gates and checks are
in `tools/sp_tesla_e2e/long_owner_check.py` LO1-LO16; a model is not device proof.

## Capability and limits

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
