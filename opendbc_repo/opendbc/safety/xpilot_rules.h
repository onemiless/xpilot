#pragma once

// ABI v1 offsets and semantics: docs/design/xpilot-panda-rules.md.
static uint8_t xpilot_rule_table[XPILOT_RULES_BYTES];
static uint16_t xpilot_rule_offsets[XPILOT_RULES_MAX];
static uint8_t xpilot_rule_count = 0U;
static uint16_t xpilot_rule_len = 0U;
static uint32_t xpilot_rule_crc = 0U;
static uint8_t xpilot_rule_status = 0U;
static uint8_t xpilot_rule_error = 0U;
static uint16_t xpilot_upload_id = 0U;
static uint16_t xpilot_upload_len = 0U;
static uint16_t xpilot_upload_received = 0U;
static uint8_t xpilot_upload_count = 0U;
static bool xpilot_upload_metadata = false;
static uint32_t xpilot_upload_crc = 0U;

typedef struct {
  uint8_t template_data[8];
  uint32_t template_ts;
  uint32_t last_tx;
  uint32_t session_ts;
  uint16_t count;
  bool template_valid;
  bool tx_valid;
} XpilotRuleState;
static XpilotRuleState xpilot_rule_states[XPILOT_RULES_MAX];

static uint32_t xpilot_read_le(const uint8_t *p, uint8_t len) {
  uint32_t value = 0U;
  for (uint8_t i = 0U; i < 4U; i++) {
    if (i < len) {
      value |= (uint32_t)p[i] << ((uint32_t)i * 8U);
    }
  }
  return value;
}

static bool xpilot_non_car_mode(uint16_t mode) {
  return (mode == SAFETY_SILENT) || (mode == SAFETY_NOOUTPUT) ||
         (mode == SAFETY_ALLOUTPUT) || (mode == SAFETY_ELM327);
}

static bool xpilot_mode_owned(uint32_t addr) {
  // Union of Tesla RX/TX inventories and selective forwarding addresses, on every bus/length.
  static const uint32_t owned[] = {0x118U, 0x145U, 0x155U, 0x257U, 0x27DU, 0x286U,
                                 0x2B9U, 0x311U, 0x370U, 0x3DFU, 0x488U};
  bool found = false;
  for (uint8_t i = 0U; i < (sizeof(owned) / sizeof(owned[0])); i++) {
    found = found || (owned[i] == addr);
  }
  return found;
}

static void xpilot_rules_reset(void) {
  for (uint8_t i = 0U; i < XPILOT_RULES_MAX; i++) {
    xpilot_rule_states[i] = (XpilotRuleState){0};
  }
}

static void xpilot_rules_clear(void) {
  xpilot_rule_count = 0U;
  xpilot_rule_len = 0U;
  xpilot_rule_crc = 0U;
  xpilot_rule_status = 0U;
  xpilot_rule_error = 0U;
  xpilot_upload_len = 0U;
  xpilot_upload_received = 0U;
  xpilot_upload_count = 0U;
  xpilot_upload_metadata = false;
  xpilot_upload_crc = 0U;
  xpilot_upload_id++;
  if (xpilot_upload_id == 0U) {
    xpilot_upload_id = 1U;
  }
  xpilot_rules_reset();
}

static inline void xpilot_rules_fail(uint8_t error) {
  xpilot_rules_clear();
  xpilot_rule_error = error;
}

static uint32_t xpilot_field_max(uint8_t width) {
  return 0xFFFFFFFFU >> (32U - (uint32_t)width);
}

static uint32_t xpilot_field_value(const uint8_t *data, uint8_t offset, uint8_t width) {
  uint32_t value = 0U;
  for (uint8_t i = 0U; i < 32U; i++) {
    if (i < width) {
      const uint32_t bit = (uint32_t)offset + i;
      value |= ((uint32_t)(data[bit / 8U] >> (bit % 8U)) & 1U) << i;
    }
  }
  return value;
}

static bool xpilot_valid_field(uint8_t offset, uint8_t width, uint8_t length) {
  return (width > 0U) && (width <= 32U) && (((uint32_t)offset + width) <= ((uint32_t)length * 8U));
}

static bool xpilot_cover(uint8_t *coverage, uint8_t offset, uint8_t width) {
  bool valid = true;
  for (uint8_t i = 0U; i < 32U; i++) {
    if (i < width) {
      const uint32_t bit = (uint32_t)offset + i;
      const uint8_t mask = (uint8_t)(1U << (bit % 8U));
      valid = valid && ((coverage[bit / 8U] & mask) == 0U);
      coverage[bit / 8U] |= mask;
    }
  }
  return valid;
}

static uint8_t xpilot_validate_rule(const uint8_t *r) {
  uint8_t error = 0U;
  const uint8_t length = r[14];
  const uint8_t fields = r[3];
  const uint8_t tuples = r[25];
  const uint32_t addr = xpilot_read_le(&r[4], 4U);
  const uint32_t ttl = xpilot_read_le(&r[16], 4U);
  const uint32_t interval = xpilot_read_le(&r[20], 4U);
  const uint32_t idle = xpilot_read_le(&r[28], 4U);
  const uint32_t span = xpilot_read_le(&r[32], 4U);
  if ((r[2] != 1U) || (fields > 8U) || (tuples > 8U)) {
    error = 6U;
  } else if ((addr > 0x1FFFFFFFU) || (xpilot_read_le(&r[8], 4U) > 0x1FFFFFFFU) ||
             (r[12] != 1U) || (r[13] != 1U) || (length == 0U) || (length > 8U) || (r[15] != length) ||
             (ttl == 0U) || (ttl > 0x7FFFFFFFU) || (interval == 0U) || (interval > 0x7FFFFFFFU) ||
             (idle > 0x7FFFFFFFU) || (span > 0x7FFFFFFFU) || (xpilot_read_le(&r[26], 2U) == 0U) ||
             ((r[24] != 1U) && (r[24] != 2U)) || ((r[24] == 1U) && (idle == 0U)) ||
             ((r[24] == 2U) && ((idle != 0U) || (span == 0U))) || (r[43] != 0U)) {
    error = 7U;
  } else if (xpilot_mode_owned(addr)) {
    error = 9U;
  } else {
    uint8_t coverage[8] = {0U, 0U, 0U, 0U, 0U, 0U, 0U, 0U};
    for (uint8_t i = 0U; i < 8U; i++) {
      if (((r[52U + i] & r[44U + i]) != r[52U + i]) ||
          ((r[76U + i] & r[68U + i]) != r[76U + i]) ||
          ((i >= length) && ((r[44U + i] | r[52U + i] | r[60U + i] | r[68U + i] | r[76U + i]) != 0U))) {
        error = 7U;
      }
      if ((r[60U + i] & r[68U + i]) != 0U) {
        error = 8U;
      }
      coverage[i] = r[60U + i] | r[68U + i];
    }
    for (uint8_t i = 0U; i < 8U; i++) {
      if (i < fields) {
        const uint8_t *f = &r[84U + (44U * i)];
        const uint32_t low = xpilot_read_le(&f[4], 4U);
        const uint32_t high = xpilot_read_le(&f[8], 4U);
        if (!xpilot_valid_field(f[0], f[1], length) || (f[2] > 8U) || (f[3] != 0U)) {
          error = 7U;
        } else {
          if ((low > high) || (high > xpilot_field_max(f[1]))) {
            error = 7U;
          }
          if (!xpilot_cover(coverage, f[0], f[1])) {
            error = 8U;
          }
          for (uint8_t j = 0U; j < 8U; j++) {
            const uint32_t v = xpilot_read_le(&f[12U + (4U * j)], 4U);
            if (((j < f[2]) && ((v < low) || (v > high))) || ((j >= f[2]) && (v != 0U))) {
              error = 7U;
            }
          }
        }
      }
    }
    if (r[36] == 1U) {
      if (!xpilot_valid_field(r[37], r[38], length)) {
        error = 7U;
      } else if (!xpilot_cover(coverage, r[37], r[38])) {
        error = 8U;
      } else {
      }
    } else if ((r[36] != 0U) || (r[37] != 0U) || (r[38] != 0U)) {
      error = 7U;
    } else {
    }
    for (uint8_t i = 0U; i < 2U; i++) {
      const uint8_t kind = r[39U + (2U * i)];
      const uint8_t byte = r[40U + (2U * i)];
      if (((kind == 0U) && (byte != 0U)) || (kind > 1U) || ((kind == 1U) && (byte >= length))) {
        error = 7U;
      } else if ((i == 0U) && (kind == 1U)) {
        if (!xpilot_cover(coverage, byte * 8U, 8U)) {
          error = 8U;
        }
      } else {
      }
    }
    for (uint8_t i = 0U; i < 8U; i++) {
      if ((i < length) && (coverage[i] != 0xFFU)) {
        error = 8U;
      }
    }
    for (uint8_t i = 0U; i < 8U; i++) {
      if (i < tuples) {
        const uint8_t *t = &r[84U + (44U * fields) + (36U * i)];
        if ((t[0] == 0U) || (((uint32_t)t[0] >> fields) != 0U) || ((t[1] | t[2] | t[3]) != 0U)) {
          error = 7U;
        }
        for (uint8_t j = 0U; j < 8U; j++) {
          const uint32_t v = xpilot_read_le(&t[4U + (4U * j)], 4U);
          if (((t[0] >> j) & 1U) != 0U) {
            if (j < fields) {
              const uint8_t *f = &r[84U + (44U * j)];
              if ((v < xpilot_read_le(&f[4], 4U)) || (v > xpilot_read_le(&f[8], 4U))) {
                error = 7U;
              }
            }
          } else if (v != 0U) {
            error = 7U;
          } else {
          }
        }
      }
    }
  }
  return error;
}

static inline uint8_t xpilot_rules_validate(void) {
  uint8_t error = 0U;
  const uint8_t *h = xpilot_rule_table;
  if ((xpilot_read_le(h, 4U) != XPILOT_RULES_MAGIC) || (xpilot_read_le(&h[4], 2U) != XPILOT_RULES_ABI) ||
      (xpilot_read_le(&h[6], 2U) != 16U) || (h[11] != 0U) || (xpilot_read_le(&h[12], 4U) != 0U)) {
    error = 6U;
  } else if ((xpilot_read_le(&h[8], 2U) != xpilot_upload_len) || (h[10] != xpilot_upload_count)) {
    error = 2U;
  } else {
    uint32_t offset = 16U;
    for (uint8_t i = 0U; i < XPILOT_RULES_MAX; i++) {
      if ((i < xpilot_upload_count) && (error == 0U)) {
        if ((offset + 84U) > xpilot_upload_len) {
          error = 2U;
        } else {
          const uint8_t *r = &h[offset];
          const uint32_t len = xpilot_read_le(r, 2U);
          if ((r[3] > 8U) || (r[25] > 8U) || (len != (84U + (44U * r[3]) + (36U * r[25]))) ||
              ((offset + len) > xpilot_upload_len)) {
            error = 2U;
          } else {
            error = xpilot_validate_rule(r);
            for (uint8_t j = 0U; j < XPILOT_RULES_MAX; j++) {
              if ((j < i) && (xpilot_read_le(&h[xpilot_rule_offsets[j] + 4U], 4U) == xpilot_read_le(&r[4], 4U))) {
                error = 9U;
              }
            }
            xpilot_rule_offsets[i] = (uint16_t)offset;
            offset += len;
          }
        }
      }
    }
    if ((error == 0U) && (offset != xpilot_upload_len)) {
      error = 2U;
    }
  }
  return error;
}

static bool xpilot_checksum(const uint8_t *data, uint8_t length, uint32_t addr, uint8_t kind, uint8_t byte) {
  bool valid = true;
  if (kind == 1U) {
    uint32_t sum = (addr & 0xFFU) + ((addr >> 8U) & 0xFFU) + ((addr >> 16U) & 0xFFU) + ((addr >> 24U) & 0xFFU);
    for (uint8_t i = 0U; i < 8U; i++) {
      if ((i < length) && (i != byte)) {
        sum += data[i];
      }
    }
    valid = (uint8_t)(sum & 0xFFU) == data[byte];
  }
  return valid;
}

static void xpilot_rules_rx(const CANPacket_t *msg) {
  if ((current_safety_mode == SAFETY_TESLA) && tesla_has_vehicle_bus) {
    for (uint8_t i = 0U; i < XPILOT_RULES_MAX; i++) {
      if (i < xpilot_rule_count) {
        const uint8_t *r = &xpilot_rule_table[xpilot_rule_offsets[i]];
        if ((msg->addr == xpilot_read_le(&r[8], 4U)) && (msg->bus == r[13]) && (GET_LEN(msg) == r[15])) {
          bool valid = xpilot_checksum(msg->data, r[15], msg->addr, r[41], r[42]);
          for (uint8_t j = 0U; j < 8U; j++) {
            valid = valid && ((msg->data[j] & r[44U + j]) == r[52U + j]);
          }
          if (valid) {
            XpilotRuleState *s = &xpilot_rule_states[i];
            for (uint8_t j = 0U; j < 8U; j++) {
              s->template_data[j] = msg->data[j];
            }
            s->template_ts = microsecond_timer_get();
            s->template_valid = true;
          }
        }
      }
    }
  }
}

static bool xpilot_rule_tx(const CANPacket_t *msg, const uint8_t *r, XpilotRuleState *s) {
  const uint32_t now = microsecond_timer_get();
  bool allowed = s->template_valid && (safety_get_ts_elapsed(now, s->template_ts) <= xpilot_read_le(&r[16], 4U)) &&
                 (!s->tx_valid || (safety_get_ts_elapsed(now, s->last_tx) >= xpilot_read_le(&r[20], 4U)));
  bool exempt = r[3] > 0U;
  uint32_t values[8] = {0U, 0U, 0U, 0U, 0U, 0U, 0U, 0U};
  for (uint8_t i = 0U; i < 8U; i++) {
    allowed = allowed && ((msg->data[i] & r[60U + i]) == (s->template_data[i] & r[60U + i])) &&
              ((msg->data[i] & r[68U + i]) == r[76U + i]);
    if (i < r[3]) {
      const uint8_t *f = &r[84U + (44U * i)];
      values[i] = xpilot_field_value(msg->data, f[0], f[1]);
      allowed = allowed && (values[i] >= xpilot_read_le(&f[4], 4U)) && (values[i] <= xpilot_read_le(&f[8], 4U));
      bool field_exempt = false;
      for (uint8_t j = 0U; j < 8U; j++) {
        if (j < f[2]) {
          field_exempt = field_exempt || (values[i] == xpilot_read_le(&f[12U + (4U * j)], 4U));
        }
      }
      exempt = exempt && field_exempt;
    }
  }
  bool tuple_ok = r[25] == 0U;
  for (uint8_t i = 0U; i < 8U; i++) {
    if (i < r[25]) {
      const uint8_t *t = &r[84U + (44U * r[3]) + (36U * i)];
      bool match = true;
      for (uint8_t j = 0U; j < 8U; j++) {
        if (((t[0] >> j) & 1U) != 0U) {
          match = match && (values[j] == xpilot_read_le(&t[4U + (4U * j)], 4U));
        }
      }
      tuple_ok = tuple_ok || match;
    }
  }
  allowed = allowed && tuple_ok && xpilot_checksum(msg->data, r[14], msg->addr, r[39], r[40]);
  if (r[36] == 1U) {
    allowed = allowed && (xpilot_field_value(msg->data, r[37], r[38]) ==
              ((xpilot_field_value(s->template_data, r[37], r[38]) + 1U) & xpilot_field_max(r[38])));
  }
  bool new_session = s->count == 0U;
  if (r[24] == 1U) {
    new_session = new_session || (s->tx_valid && (safety_get_ts_elapsed(now, s->last_tx) >= xpilot_read_le(&r[28], 4U)));
  } else {
    new_session = new_session || (safety_get_ts_elapsed(now, s->session_ts) >= xpilot_read_le(&r[32], 4U));
  }
  const bool span_ok = (r[24] == 2U) || (xpilot_read_le(&r[32], 4U) == 0U) ||
                       (safety_get_ts_elapsed(now, s->session_ts) < xpilot_read_le(&r[32], 4U));
  allowed = allowed && (controls_allowed || exempt) &&
            (exempt || new_session || ((s->count < xpilot_read_le(&r[26], 2U)) && span_ok));
  if (allowed) {
    if (!exempt) {
      if (new_session) {
        s->count = 0U;
        s->session_ts = now;
      }
      s->count++;
    }
    s->last_tx = now;
    s->tx_valid = true;
  }
  return allowed;
}

static bool xpilot_rules_tx(const CANPacket_t *msg) {
  bool allowed = false;
  if ((current_safety_mode == SAFETY_TESLA) && tesla_has_vehicle_bus && !relay_malfunction && (msg->bus == 1U)) {
    for (uint8_t i = 0U; i < XPILOT_RULES_MAX; i++) {
      if (i < xpilot_rule_count) {
        const uint8_t *r = &xpilot_rule_table[xpilot_rule_offsets[i]];
        if ((msg->addr == xpilot_read_le(&r[4], 4U)) && (GET_LEN(msg) == r[14])) {
          allowed = xpilot_rule_tx(msg, r, &xpilot_rule_states[i]);
        }
      }
    }
  }
  return allowed;
}
