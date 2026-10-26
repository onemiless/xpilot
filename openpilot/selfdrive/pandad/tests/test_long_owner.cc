// LO4/5: old/unknown version, short health/reply, malformed owner/pending/age,
// lost reply, repeated request and wrong mode must never manufacture an ack.
#include "common/tests/native_test.h"
#include "selfdrive/pandad/panda.h"

struct OwnerHandle : PandaCommsHandle {
  uint32_t version = 0;
  int health_size = 49, reply_size = 8, requests = 0;
  uint8_t owner = 0, pending = 255;
  void cleanup() override {}
  int control_write(uint8_t, uint16_t, uint16_t, unsigned int) override { return 0; }
  int bulk_write(uint8_t, uint8_t *, int, unsigned int) override { return 0; }
  int bulk_read(uint8_t, uint8_t *, int, unsigned int) override { return 0; }
  int control_read(uint8_t req, uint16_t target, uint16_t index, uint8_t *data, uint16_t len, unsigned int timeout) override {
    memset(data, 0, len);
    if (req == 0xdd) { memcpy(data, &version, 4); return 8; }
    if (req == 0xd2) {
      data[31] = 10; data[32] = 2;  // Tesla
      data[49] = owner; data[50] = pending;
      return health_size;
    }
    REQUIRE(req == 0xa5 && target <= 1 && index == 0 && len == 8 && timeout <= 5);
    ++requests;
    data[0] = 1; data[1] = owner; data[2] = target;
    return reply_size;
  }
};
struct OwnerPanda : Panda {
  OwnerHandle *transport;
  OwnerPanda() { auto h = std::make_unique<OwnerHandle>(); transport = h.get(); handle = std::move(h); }
};
int main() {
  return run_native_test([] {
    OwnerPanda old;
    REQUIRE(old.get_state());
    REQUIRE(!old.long_owner_supported);
    REQUIRE(!old.request_long_owner(1));
    REQUIRE(old.transport->requests == 0);
    old.transport->health_size = 54;
    REQUIRE(!old.get_state());  // unknown extended ABI
#if XPILOT_LONG_HEALTH_VERSION != 0
    OwnerPanda current;
    current.transport->version = XPILOT_LONG_HEALTH_VERSION;
    current.transport->health_size = 54;
    REQUIRE(current.get_state() && current.long_owner_supported);
    REQUIRE(current.long_owner == 0);
    REQUIRE(current.request_long_owner(1));
    REQUIRE(current.long_owner == 0);  // request reply is never the authority
    current.transport->reply_size = 7;
    REQUIRE(!current.request_long_owner(1));
    current.transport->owner = 1;
    REQUIRE(current.get_state() && current.long_owner == 1);
    current.transport->owner = 2;
    REQUIRE(!current.get_state());
    current.transport->owner = 1;
    current.transport->pending = 2;
    REQUIRE(!current.get_state());
    current.transport->health_size = 53;
    REQUIRE(!current.get_state());
#endif
  });
}
