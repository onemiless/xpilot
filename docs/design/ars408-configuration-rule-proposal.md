# Draft: bounded ARS408 configuration rules

This is a design proposal, not an implemented rule or a grant of CAN permission.
It is intended for review before implementing ARS408 parameter writes. The
existing sensor-motion proposal in onemiless/xpilot#63 covers 0x300 and 0x301;
it does not allow radar configuration.

## Scope

The user needs the CP ARS408 configuration and object-filter readback workflow.
On the dedicated radar bus (bus 1), 0x200 is an eight-byte RadarConfiguration
frame; 0x202 is a five-byte FilterCfg frame. A filter query is transmitted on
0x202 even though it is logically read-only. RadarState 0x201 and filter records
0x204 are incoming data and do not themselves require transmit permission.

This proposal does not authorize arbitrary raw CAN, vehicle actuation, arbitrary
addresses, or access to a bus shared with other devices. The existing bus-1
hardware assumption must be established for the actual installation. A plugin
claim is resource arbitration and does not grant Panda or OS permissions.

## Required authorization and failure behavior

* Configuration must require Tesla safety mode, the expected vehicle and radar
  buses, no relay malfunction, a current sensor state and bounded send rate.
* A write must require a reliably observed stationary vehicle and disengaged
  controls. A false `vehicle_moving` default before the first valid speed frame
  is not proof of standstill. Define the validated speed sources, freshness and
  timeout behavior explicitly; stale, invalid, unavailable or conflicting data
  must deny the write. The host must recheck immediately before sending.
* Query and write permissions must be specified independently. Query data must
  be limited to a well-formed, read-only object-filter query, not an arbitrary
  0x202 payload. Determine whether queries also require stationary state before
  finalizing the implementation.
* 0x200 must accept only reviewed field-scoped commands. The existing CP
  implementation permits maximum distance 200–250 m in 2 m steps, extended
  information, output off/Objects and an explicit NVM save; it rejects changes
  to sensor ID, quality, power, relay and sort fields. The implementation must
  check all unused bits and validate the field values rather than trusting UI.
* 0x202 must distinguish query and write, reject cluster and reserved index 15,
  validate reserved bits and validate index-specific raw minimum/maximum values.
  Filter writes may be stored by the radar; this is not the same as a volatile
  0x200 configuration change.
* NVM writes require a distinct, explicit operation and a documented readback
  and power-cycle verification boundary. A matching current state does not prove
  a write was physically transmitted or persisted.
* A request must expire or be cancelled when ownership, vehicle state, sensor
  freshness or the process lifecycle changes. No queued request may silently
  execute after restart. Repeated/stale responses must not confirm a new request.

## Verification required before implementation can be considered ready

Tests should be specified before implementation and exercise the real Panda
rule engine and host encoder, including malformed rule profiles; every supported
configuration field and filter index; wrong bus, address, length and ID type;
reserved bits and invalid bounds; missing, invalid, stale or moving vehicle
state; engaged controls; sensor loss; rate limits, clock wrap and revocation;
timeout and stale or mismatched readback; and NVM-specific behavior. Run the
complete safety suite, F4/H7/body builds, size comparison and MISRA. Keep actual
device firmware, installed software and road verification separate from those
offline results.

The plugin UI, transaction and status interfaces must be specified separately.
Neither a passing protocol test nor this document establishes device support,
real sensor readback, or permission to use the radar for driving.
