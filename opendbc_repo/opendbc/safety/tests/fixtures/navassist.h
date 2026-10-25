#pragma once

// xpilot plugin "navassist": one bounded Tesla DAS_bodyControls turn request on 0x3E9 (vehicle bus 1),
// cloned from a fresh idle car frame. Python side: xpilot-plugins plugins/navassist/turnsignal.py.
//
// Validated: 8-byte length, checksum, one of turn request 1/2/3, reason 8 (active) or 4 (cancel), every
// protected bit equal to the fresh RX template, counter = template counter + 1, >=100 ms between frames,
// a 200-frame/60 s session cap, and controls_allowed for an active request. Cancel (request 3) does not
// require controls_allowed: it only releases the lamp, so it is accepted even without a disengage. It never
// relaxes the stock Tesla mode: only this one address is owned, everything else is unchanged.

static bool navassist_template_valid = false;
static uint8_t navassist_template[8] = {0U, 0U, 0U, 0U, 0U, 0U, 0U, 0U};
static uint32_t navassist_template_ts = 0U;
static bool navassist_last_tx_valid = false;
static uint32_t navassist_last_tx_ts = 0U;
static uint32_t navassist_session_ts = 0U;
static uint16_t navassist_session_count = 0U;

static uint8_t navassist_checksum(const uint8_t *data) {
  uint32_t sum = 0xE9U + 0x03U;
  for (uint8_t i = 0U; i < 7U; i++) {
    sum += data[i];
  }
  return (uint8_t)(sum & 0xFFU);
}

static bool navassist_active(void) {
  return (current_safety_mode == SAFETY_TESLA) && tesla_has_vehicle_bus &&
         GET_FLAG(current_safety_param_sp, XPILOT_PARAM_SP_NAVASSIST);
}

static void navassist_rx(const CANPacket_t *msg) {
  // Keep only fresh idle frames (turn request 0, valid checksum): TX clones their protected bits.
  if ((msg->bus == 1U) && (msg->addr == 0x3E9U) && (GET_LEN(msg) == 8U) &&
      ((msg->data[1] & 0x03U) == 0U) && (navassist_checksum(msg->data) == msg->data[7])) {
    for (uint8_t i = 0U; i < 8U; i++) {
      navassist_template[i] = msg->data[i];
    }
    navassist_template_ts = microsecond_timer_get();
    navassist_template_valid = true;
  }
}

static bool navassist_tx(const CANPacket_t *msg) {
  const uint8_t request = msg->data[1] & 0x03U;
  const uint8_t reason = (msg->data[2] >> 1) & 0x0FU;
  const uint8_t counter = (msg->data[6] >> 4) & 0x0FU;
  const bool cancel = request == 3U;
  const bool request_ok = (request == 1U) || (request == 2U) || cancel;
  const bool active_reason_ok = (reason == 8U) && !cancel;
  const bool cancel_reason_ok = (reason == 4U) && cancel;
  const bool reason_ok = active_reason_ok || cancel_reason_ok;
  const bool checksum_ok = navassist_checksum(msg->data) == msg->data[7];
  const bool template_counter_ok = counter == (uint8_t)(((navassist_template[6] >> 4) + 1U) & 0x0FU);
  bool protected_bits = true;
  for (uint8_t i = 0U; i < 8U; i++) {
    if ((i != 1U) && (i != 2U) && (i != 6U) && (i != 7U)) {
      protected_bits = protected_bits && (msg->data[i] == navassist_template[i]);
    }
  }
  protected_bits = protected_bits &&
    ((msg->data[1] & 0xFCU) == (navassist_template[1] & 0xFCU)) &&
    ((msg->data[2] & 0xE1U) == (navassist_template[2] & 0xE1U)) &&
    ((msg->data[6] & 0x0FU) == (navassist_template[6] & 0x0FU));

  const uint32_t now = microsecond_timer_get();
  const bool template_fresh = navassist_template_valid &&
    (safety_get_ts_elapsed(now, navassist_template_ts) <= 1500000U);
  const bool rate_ok = !navassist_last_tx_valid || (safety_get_ts_elapsed(now, navassist_last_tx_ts) >= 100000U);
  const bool new_session = !navassist_last_tx_valid || (safety_get_ts_elapsed(now, navassist_session_ts) >= 60000000U);
  const bool session_ok = new_session || (navassist_session_count < 200U);
  const bool authority_ok = cancel || controls_allowed;
  const bool allowed = navassist_active() && request_ok && reason_ok && checksum_ok && template_counter_ok &&
                       protected_bits && template_fresh && rate_ok && session_ok && authority_ok;
  if (allowed) {
    if (new_session) {
      navassist_session_ts = now;
      navassist_session_count = 0U;
    }
    navassist_session_count++;
    navassist_last_tx_valid = true;
    navassist_last_tx_ts = now;
  }
  return allowed;
}

static void navassist_reset(void) {
  navassist_template_valid = false;
  navassist_last_tx_valid = false;
  navassist_template_ts = 0U;
  navassist_last_tx_ts = 0U;
  navassist_session_ts = 0U;
  navassist_session_count = 0U;
}

static const safety_plugin navassist_plugin = {
  .msg = {0x3E9, 1, 8, .check_relay = false, .disable_static_blocking = true},
  .active = navassist_active,
  .rx = navassist_rx,
  .tx = navassist_tx,
  .reset = navassist_reset,
};
