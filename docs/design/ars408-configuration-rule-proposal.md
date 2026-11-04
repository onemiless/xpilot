# ARS408 configuration: bench-only design

The radar configuration and filter settings can persist in sensor NVM. Following
maintainer review, this proposal does not add a Panda runtime rule, host API,
plugin transmit permission, or vehicle-side configuration path for 0x200/0x202.
Configuration is a separate one-time bench operation using an appropriate CAN
tool; a filter query also uses a transmitted 0x202 request. This document does
not establish a safe procedure for a particular sensor or installation.

Before installation, separately verify the radar configuration and readback,
the dedicated bus-1 wiring, Tesla vehicle-bus flag, 500 kbit/s bit rate, and
the actual vehicle bus load. Where persistence matters, confirm it by a power
cycle and readback on the bench. Do not infer safe bus operation from a successful
configuration readback. The source of any observed CAN errors remains a separate
diagnostic question.

The runtime sensor-motion rules for 0x300/0x301 are covered by the existing
`template_clone.v1` sensor_input contract; they do not grant configuration
permission. Bench configuration, firmware checks, and radar target publication
require their own evidence and are not proven by this design note.
