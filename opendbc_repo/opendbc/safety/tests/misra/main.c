#include "opendbc/safety/safety.h"
#include "../../../../../panda/board/crc.h"
#include "../../../../../panda/board/xpilot_rules.h"
#include "../../../../../panda/board/health.h"
#include "../../../../../panda/board/tesla_long.h"

// this file is checked by cppcheck

extern uint32_t microsecond_timer_get(void);

// Ignore misra-c2012-8.7 as these functions are only called from libsafety
SAFETY_UNUSED(heartbeat_engaged);

SAFETY_UNUSED(safety_rx_hook);
SAFETY_UNUSED(safety_tx_hook);
SAFETY_UNUSED(safety_fwd_hook);
SAFETY_UNUSED(safety_tick);
SAFETY_UNUSED(set_safety_hooks);
SAFETY_UNUSED(mads_heartbeat_engaged_check);
SAFETY_UNUSED(mads_set_alternative_experience);
SAFETY_UNUSED(get_acc_main_on_mismatches);

// xpilot keeps only the Tesla mode; these shared helpers are used by the removed modes
SAFETY_UNUSED(steer_torque_cmd_checks);
SAFETY_UNUSED(steer_angle_cmd_checks);
SAFETY_UNUSED(steer_curvature_cmd_checks);
SAFETY_UNUSED(get_longitudinal_allowed);
SAFETY_UNUSED(longitudinal_speed_checks);
SAFETY_UNUSED(longitudinal_gas_checks);
SAFETY_UNUSED(longitudinal_brake_checks);
SAFETY_UNUSED(longitudinal_interceptor_checks);
SAFETY_UNUSED(GET_BYTES_LE);
SAFETY_UNUSED(GET_BYTES_64_LE);
SAFETY_UNUSED(gen_crc_lookup_table_8);
SAFETY_UNUSED(gen_crc_lookup_table_16);
SAFETY_UNUSED(to_signed);
SAFETY_UNUSED(alternative_experience);

// App loader entry points are driven by USB/SPI, outside this checker translation unit.
SAFETY_UNUSED(xpilot_rules_control);
SAFETY_UNUSED(xpilot_rules_upload);
SAFETY_UNUSED(crc32_init);

SAFETY_UNUSED(safety_rx_fwd_hook);
SAFETY_UNUSED(tesla_long_control);
SAFETY_UNUSED(tesla_long_health);

SAFETY_UNUSED(sizeof(can_health_t));
