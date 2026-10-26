#pragma once

static uint16_t tesla_long_age_ms(void) {
  return (tesla_long_pending == TESLA_LONG_NONE) ? 0U : (uint16_t)(safety_get_ts_elapsed(microsecond_timer_get(), tesla_long_request_ts) / 1000U);
}

static void tesla_long_request(uint16_t target, uint16_t param2) {
  tesla_long_update();
  uint8_t reason = TESLA_LONG_OK;
  if (current_safety_mode != SAFETY_TESLA) {
    reason = TESLA_LONG_MODE;
  } else if ((target > TESLA_LONG_SP) || (param2 != 0U)) {
    reason = TESLA_LONG_ARGUMENT;
  } else if (target == tesla_long_owner) {
    // A lost reply cannot reverse a pending handoff.
  } else if (tesla_long_pending != TESLA_LONG_NONE) {
    if (target != tesla_long_pending) {
      reason = TESLA_LONG_BUSY;
    }
  } else {
    reason = tesla_long_gate((uint8_t)target);
    if (reason == TESLA_LONG_OK) {
      tesla_long_pending = (uint8_t)target;
      tesla_long_request_ts = microsecond_timer_get();
      tesla_long_refusal = TESLA_LONG_OK;
    }
  }
  if (reason != TESLA_LONG_OK) {
    tesla_long_refusal = reason;
  }
}


static void tesla_long_reply(uint8_t *resp) {
  tesla_long_update();
  uint16_t age = tesla_long_age_ms();
  resp[0] = 1U;
  resp[1] = tesla_long_owner;
  resp[2] = tesla_long_pending;
  resp[3] = tesla_long_refusal;
  resp[4] = age & 0xFFU;
  resp[5] = age >> 8;
  resp[6] = 0U;
  resp[7] = 0U;
}

static unsigned int tesla_long_control(uint16_t target, uint16_t param2, uint8_t *resp) {
  tesla_long_request(target, param2);
  tesla_long_reply(resp);
  return 8U;
}

static void tesla_long_health(struct health_t *health) {
  tesla_long_update();
  health->tesla_long_owner_pkt = tesla_long_owner;
  health->tesla_long_pending_pkt = tesla_long_pending;
  health->tesla_long_pending_age_ms_pkt = tesla_long_age_ms();
  health->tesla_long_refusal_pkt = tesla_long_refusal;
}
