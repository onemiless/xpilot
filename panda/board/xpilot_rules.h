#pragma once

// App-only loader; bootstub's EP2 remains the flash writer.
static void xpilot_write_le(uint8_t *p, uint32_t value, uint8_t length) {
  for (uint8_t i = 0U; i < 4U; i++) {
    if (i < length) {
      p[i] = (uint8_t)((value >> (8U * (uint32_t)i)) & 0xFFU);
    }
  }
}

static unsigned int xpilot_rules_status_packet(uint8_t *resp) {
  for (uint8_t i = 0U; i < 24U; i++) {
    resp[i] = 0U;
  }
  xpilot_write_le(resp, XPILOT_RULES_MAGIC, 4U);
  xpilot_write_le(&resp[4], XPILOT_RULES_ABI, 2U);
  resp[6] = xpilot_rule_status;
  resp[7] = xpilot_rule_error;
  xpilot_write_le(&resp[8], xpilot_rule_crc, 4U);
  xpilot_write_le(&resp[12], xpilot_rule_len, 2U);
  resp[14] = xpilot_rule_count;
  xpilot_write_le(&resp[16], xpilot_upload_id, 2U);
  xpilot_write_le(&resp[18], xpilot_upload_received, 2U);
  xpilot_write_le(&resp[20], xpilot_upload_len, 2U);
  resp[22] = xpilot_upload_count;
  return 24U;
}

static unsigned int xpilot_rules_control(unsigned int request, unsigned int param1, unsigned int param2, uint8_t *resp) {
  unsigned int length = 24U;
  if (request == 0xA0U) {
    for (uint8_t i = 0U; i < 16U; i++) {
      resp[i] = 0U;
    }
    xpilot_write_le(resp, XPILOT_RULES_MAGIC, 4U);
    xpilot_write_le(&resp[4], XPILOT_RULES_ABI, 2U);
    xpilot_write_le(&resp[6], XPILOT_RULES_BYTES, 2U);
    resp[8] = XPILOT_RULES_MAX;
    resp[9] = 8U;
    resp[10] = 8U;
    resp[11] = 8U;
    resp[12] = 64U;
    length = 16U;
  } else {
    if (request == 0xA4U) {
      xpilot_rules_clear();
    } else if ((request == 0xA2U) || (request == 0xA3U)) {
      if (!xpilot_non_car_mode(current_safety_mode)) {
        xpilot_rule_error = 1U;
      } else if (request == 0xA2U) {
        xpilot_rules_clear();
        if ((param1 < 16U) || (param1 > XPILOT_RULES_BYTES) || (param2 > XPILOT_RULES_MAX)) {
          xpilot_rules_fail(2U);
        } else {
          xpilot_upload_len = (uint16_t)param1;
          xpilot_upload_count = (uint8_t)param2;
          xpilot_rule_status = 1U;
        }
      } else {
        uint8_t error = 0U;
        const uint32_t crc = param1 | (param2 << 16U);
        if ((xpilot_rule_status != 1U) || !xpilot_upload_metadata) {
          error = 3U;
        } else if (xpilot_upload_received != xpilot_upload_len) {
          error = 2U;
        } else if ((crc != xpilot_upload_crc) || (crc32(xpilot_rule_table, xpilot_upload_len) != crc)) {
          error = 5U;
        } else {
          error = xpilot_rules_validate();
        }
        if (error != 0U) {
          xpilot_rules_fail(error);
        } else {
          xpilot_rule_len = xpilot_upload_len;
          xpilot_rule_count = xpilot_upload_count;
          xpilot_rule_crc = crc;
          xpilot_rule_status = 2U;
          xpilot_rule_error = 0U;
          xpilot_rules_reset();
        }
      }
    } else {
    }
    length = xpilot_rules_status_packet(resp);
  }
  return length;
}

static void xpilot_rules_upload(const uint8_t *data, uint32_t len) {
  if (!xpilot_non_car_mode(current_safety_mode)) {
    xpilot_rule_error = 1U;
  } else {
    uint8_t error = 0U;
    if (xpilot_rule_status != 1U) {
      error = 3U;
    } else if (len != 64U) {
      error = 4U;
    } else {
      const uint32_t id = xpilot_read_le(data, 2U);
      const uint32_t offset = xpilot_read_le(&data[2], 2U);
      const uint32_t size = xpilot_read_le(&data[4], 2U);
      const uint32_t flags = xpilot_read_le(&data[6], 2U);
      if ((id != xpilot_upload_id) || (size == 0U) || (size > 56U) || (flags > 1U)) {
        error = 4U;
      } else {
        for (uint8_t i = 0U; i < 56U; i++) {
          if ((i >= size) && (data[8U + i] != 0U)) {
            error = 4U;
          }
        }
        if (flags == 1U) {
          if (xpilot_upload_metadata || (offset != 0U) || (size != 4U)) {
            error = 4U;
          } else if (error == 0U) {
            xpilot_upload_crc = xpilot_read_le(&data[8], 4U);
            xpilot_upload_metadata = true;
          } else {
          }
        } else if (!xpilot_upload_metadata || (offset != xpilot_upload_received) || ((offset + size) > xpilot_upload_len)) {
          error = 4U;
        } else if (error == 0U) {
          for (uint8_t i = 0U; i < 56U; i++) {
            if (i < size) {
              xpilot_rule_table[offset + i] = data[8U + i];
            }
          }
          xpilot_upload_received += (uint16_t)size;
        } else {
        }
      }
    }
    if (error != 0U) {
      xpilot_rules_fail(error);
    }
  }
}
