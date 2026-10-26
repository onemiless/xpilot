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

Step 1 publishes no policy hook, claim, setting or HOST_CAPS entry yet. The
transport schema alone cannot enable a plugin. Legacy firmware retains the
pre-G8 owner/cadence behavior. G3 mixed lateral and X-50 speed policy are separate.
