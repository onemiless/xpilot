#include "../../../../../panda/board/crc.h"
#include "../../../../../panda/board/xpilot_rules.h"
#include "opendbc/safety/plugins/registry.h"
#define XPILOT_PARAM_SP_NAVASSIST 0x1000U
#include "opendbc/safety/tests/fixtures/navassist.h"
static const safety_plugin *const old_plugins[] = {&auto_speed_plugin, &ambient_light_plugin, &navassist_plugin};
int test_control(unsigned int req, unsigned int p1, unsigned int p2, uint8_t *resp) {
  return xpilot_rules_control(req, p1, p2, resp);
}
void test_ep2(const uint8_t *data, uint32_t len) { xpilot_rules_upload(data, len); }
void test_old_reset(void) { safety_plugins_reset(); navassist_reset(); }
void test_old_rx(unsigned int i, const CANPacket_t *msg) { safety_plugins_rx(msg); if (i == 2U) { navassist_rx(msg); } }
bool test_old_tx(unsigned int i, const CANPacket_t *msg) { const safety_plugin *p = safety_plugin_for(msg); return i == 2U ? old_plugins[i]->tx(msg) : ((p != NULL) && p->tx(msg)); }
uint32_t test_state(unsigned int i, unsigned int member) {
  const XpilotRuleState *s = &xpilot_rule_states[i];
  return member == 0U ? s->count : (member == 1U ? s->last_tx : s->session_ts);
}
uint32_t test_old_state(unsigned int i, unsigned int member) {
  if (i == 0U) { return member == 0U ? auto_speed_session_count : (member == 1U ? auto_speed_last_tx_ts : 0U); }
  if (i == 1U) { return member == 0U ? ambient_session_count : (member == 1U ? ambient_tx_ts : ambient_session_ts); }
  return member == 0U ? navassist_session_count : (member == 1U ? navassist_last_tx_ts : navassist_session_ts);
}
void test_init(void) { crc32_init(); }
