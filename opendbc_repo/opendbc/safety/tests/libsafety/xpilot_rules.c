#include "../../../../../panda/board/crc.h"
#include "../../../../../panda/board/xpilot_rules.h"
int test_control(unsigned int req, unsigned int p1, unsigned int p2, uint8_t *resp) {
  return xpilot_rules_control(req, p1, p2, resp);
}
void test_ep2(const uint8_t *data, uint32_t len) { xpilot_rules_upload(data, len); }
uint32_t test_state(unsigned int i, unsigned int member) {
  const XpilotRuleState *s = &xpilot_rule_states[i];
  if (member == 3U) { return s->template_valid; }
  if (member == 4U) { return s->tx_valid; }
  return member == 0U ? s->count : (member == 1U ? s->last_tx : s->session_ts);
}
void test_init(void) { crc32_init(); }
