// H1-H14: tools/sp_tesla_e2e/panda_rules_check.py. Real app-loader model, fake transport only.
#include <dlfcn.h>
#include <fstream>
#include <thread>

#include "common/tests/native_test.h"
#include "common/timing.h"
#include "common/util.h"
#include "openpilot/cereal/messaging/messaging.h"
#include "selfdrive/pandad/pandad.h"

struct Firmware {
  void *lib;
  int (*control)(unsigned int, unsigned int, unsigned int, uint8_t *);
  void (*ep2)(const uint8_t *, uint32_t);
  int (*mode)(uint16_t, uint16_t);
  int (*get_mode)();
  explicit Firmware(const char *path) {
    lib = dlopen(path, RTLD_NOW | RTLD_LOCAL);
    REQUIRE(lib);
    control = reinterpret_cast<decltype(control)>(dlsym(lib, "test_control"));
    ep2 = reinterpret_cast<decltype(ep2)>(dlsym(lib, "test_ep2"));
    mode = reinterpret_cast<decltype(mode)>(dlsym(lib, "set_safety_hooks"));
    get_mode = reinterpret_cast<decltype(get_mode)>(dlsym(lib, "get_current_safety_mode"));
    reinterpret_cast<void (*)()>(dlsym(lib, "test_init"))();
  }
};

struct FakeHandle : PandaCommsHandle {
  Firmware &fw;
  std::string failure;
  int writes = 0, clears = 0, heartbeats = 0;
  int clear_failures = 0, fallback_failures = 0;
  std::vector<int> requests;
  explicit FakeHandle(Firmware &fw, std::string failure) : fw(fw), failure(failure) {}
  void cleanup() override {}
  int control_write(uint8_t req, uint16_t p1, uint16_t p2, unsigned int) override {
    requests.push_back(req);
    if (req == 0xdc) {
      if (p1 == 19 && fallback_failures-- > 0) return -1;
      return fw.mode(p1, p2);
    }
    if (req == 0xf3) ++heartbeats;
    return 0;
  }
  int control_read(uint8_t req, uint16_t p1, uint16_t p2, uint8_t *data, uint16_t len, unsigned int timeout) override {
    if (req >= 0xa0 && req <= 0xa4) REQUIRE(timeout > 0 && timeout <= 10);
    requests.push_back(req);
    if (req == 0xdd) {
      memset(data, 0, len);
      uint32_t version = XPILOT_LONG_HEALTH_VERSION;
      memcpy(data, &version, sizeof(version));
      return 8;
    }
    if (req == 0xd2) {
      health_t health = {};
      health.safety_mode_pkt = fw.get_mode();
      memcpy(data, &health, sizeof(health));
      return sizeof(health);
    }
    if (req == 0xa4) {
      ++clears;
      if (clear_failures-- > 0) return -1;
    }
    if (req == 0xa0 && (failure == "caps" || failure == "bootstub")) return -1;
    uint8_t response[64] = {};
    int n = fw.control(req, p1, p2, response);
    if (req == 0xa0 && failure == "abi") response[4] = 2;
    if (req == 0xa0 && failure == "limits") response[8] = 7;
    if (req == 0xa2 && failure == "begin") response[7] = 3;
    if (req == 0xa3 && failure == "commit") response[7] = 5;
    if (req == 0xa1 && response[6] == 2) {
      if (failure == "crc") response[8] ^= 1;
      if (failure == "count") response[14] ^= 1;
      if (failure == "length") response[12] ^= 1;
    }
    memcpy(data, response, std::min(n, int(len)));
    return std::min(n, int(len));
  }
  int bulk_write(uint8_t endpoint, uint8_t *data, int len, unsigned int timeout) override {
    REQUIRE(endpoint == 2 && len == 64 && timeout > 0 && timeout <= 10);
    ++writes;
    fw.ep2(data, len);
    if (failure == "short") return 63;
    if (failure == "deadline") std::this_thread::sleep_for(std::chrono::milliseconds(2100));
    return len;
  }
  int bulk_read(uint8_t, uint8_t *, int, unsigned int) override { return 0; }
};

struct TestPanda : Panda {
  explicit TestPanda(std::unique_ptr<PandaCommsHandle> comms) { handle = std::move(comms); }
};

void tick(PandaSafety &safety, int frame, bool onroad=true) {
  if (frame % 10 == 0) safety.configureSafetyMode(onroad);
  safety.updateRules();
}

void run(Firmware &fw, const std::string &out) {
  Params params;
  MessageBuilder cp, sp;
  auto car = cp.initEvent().initCarParams();
  car.initSafetyConfigs(1)[0].setSafetyModel(cereal::CarParams::SafetyModel::TESLA);
  sp.initEvent().initCarParamsSP().setSafetyParam(1);
  // Params carry root CarParams messages, not cereal Events.
  capnp::MallocMessageBuilder car_message, sp_message;
  car_message.setRoot(car.asReader());
  sp_message.setRoot(sp.getRoot<cereal::Event>().getCarParamsSP().asReader());
  auto car_words = capnp::messageToFlatArray(car_message);
  auto car_bytes = car_words.asBytes();
  auto sp_words = capnp::messageToFlatArray(sp_message);
  auto sp_bytes = sp_words.asBytes();
  params.put("CarParams", reinterpret_cast<const char *>(car_bytes.begin()), car_bytes.size());
  params.put("CarParamsSP", reinterpret_cast<const char *>(sp_bytes.begin()), sp_bytes.size());
  params.putBool("FirmwareQueryDone", true);
  params.putBool("ControlsReady", true);
  std::ofstream trace(out + "/trace.jsonl");
  std::ofstream(out + "/review-trace.jsonl").close();
  for (const std::string failure : {"", "host", "caps", "bootstub", "abi", "limits", "crc", "count", "length", "begin", "commit", "deadline", "short", "changed-caps", "churn", "param"}) {
    if (const char *only = getenv("PANDA_RULES_CASE"); only && failure != only) continue;
    fw.mode(19, 0);
    bool success = failure.empty() || failure == "host";
    std::string blob = util::read_file(out + (failure == "host" ? "/rules.bin" : "/reference.bin"));
    params.put("XpilotPandaRules", failure == "param" ? "broken" : blob);
    params.putBool("XpilotPandaRulesFault", false);
    auto handle = std::make_unique<FakeHandle>(fw, failure);
    auto *h = handle.get();
    TestPanda panda(std::move(handle));
    PandaSafety safety(&panda);
    double longest = 0;
    for (int i = 0; i < 120 && fw.get_mode() != 10; ++i) {
      if (failure == "churn") {
        params.put("XpilotPandaRules", util::read_file(out + (i % 2 ? "/rules.bin" : "/empty.bin")));
        std::this_thread::sleep_for(std::chrono::milliseconds(21));
      }
      int before = h->writes;
      double start = millis_since_boot();
      if (i % 10 == 0) panda.send_heartbeat(false, false);
      tick(safety, i);
      longest = std::max(longest, millis_since_boot() - start);
      REQUIRE(h->writes - before <= 1);
      if (failure == "changed-caps" && h->failure != "caps") {
        uint8_t status[64] = {};
        fw.control(0xa1, 0, 0, status);
        if (status[6] == 2 && fw.get_mode() != 10) {
          params.put("XpilotPandaRules", util::read_file(out + "/empty.bin"));
          h->failure = "caps";
        }
      }
    }
    REQUIRE(fw.get_mode() == 10);
    REQUIRE(params.getBool("XpilotPandaRulesFault") == !success);
    if (failure == "caps" || failure == "bootstub" || failure == "abi" || failure == "limits") REQUIRE(h->writes == 0);
    uint8_t status[64] = {};
    fw.control(0xa1, 0, 0, status);
    if (success) {
      REQUIRE(status[6] == 2 && status[14] == (failure == "host" ? 1 : 3));
      REQUIRE(h->writes > 1 && h->heartbeats >= (failure == "host" ? 1 : 2));
      REQUIRE(longest < 100);
      int before = h->writes;
      params.put("XpilotPandaRules", util::read_file(out + "/empty.bin"));
      safety.configureSafetyMode(true);
      REQUIRE(h->writes == before);
      fw.control(0xa1, 0, 0, status);
      REQUIRE(status[6] == 2 && !params.getBool("XpilotPandaRulesFault"));
      params.putBool("XpilotPandaRulesFault", true);
      safety.configureSafetyMode(true);
      fw.control(0xa1, 0, 0, status);
      REQUIRE(status[6] == 0 && fw.get_mode() == 10);
      int clears = h->clears;
      safety.configureSafetyMode(true);
      REQUIRE(h->clears == clears);
      safety.configureSafetyMode(false);
      fw.mode(19, 0);
      for (int i = 0; i < 20; ++i) tick(safety, i);
      fw.control(0xa1, 0, 0, status);
      REQUIRE(status[6] == 2 && status[14] == 0 && fw.get_mode() == 10);
    } else if (failure != "caps" && failure != "bootstub" && failure != "abi" && failure != "limits") {
      REQUIRE(h->clears >= 1 && status[6] == 0);
    }
    trace << "{\"case\":\"" << (failure.empty() ? "success-revoke-reload" : failure) << "\",\"writes\":" << h->writes
          << ",\"heartbeats\":" << h->heartbeats << ",\"clears\":" << h->clears << ",\"max_step_ms\":" << longest << "}\n";
  }
}

void review_case(Firmware &fw, const std::string &out, const std::string &name) {
  Params params;
  params.put("XpilotPandaRules", util::read_file(out + "/reference.bin"));
  params.putBool("XpilotPandaRulesFault", false);
  auto handle = std::make_unique<FakeHandle>(fw, "");
  auto *h = handle.get();
  TestPanda panda(std::move(handle));
  if (name == "H10") params.putBool("XpilotPandaRulesFault", true);
  PandaSafety safety(&panda);
  if (name == "H10") {
    safety.configureSafetyMode(false);
    REQUIRE(params.getBool("XpilotPandaRulesFault"));
  }
  fw.mode(19, 0);
  for (int i = 0; i < 120 && fw.get_mode() != 10; ++i) tick(safety, i);
  REQUIRE(fw.get_mode() == 10);
  int writes = h->writes;
  if (name == "H10") {
    REQUIRE(writes == 0 && params.getBool("XpilotPandaRulesFault"));
    auto modes = std::count(h->requests.begin(), h->requests.end(), 0xdc);
    for (int i = 0; i < 100; ++i) tick(safety, i);
    REQUIRE(h->writes == 0 && params.getBool("XpilotPandaRulesFault"));
    REQUIRE(std::count(h->requests.begin(), h->requests.end(), 0xdc) == modes);
  } else if (name == "H11") {
    h->clear_failures = 2;
    h->fallback_failures = 2;
    params.putBool("XpilotPandaRulesFault", true);
    int clears = h->clears;
    safety.configureSafetyMode(true);
    REQUIRE(h->clears == clears + 1 && fw.get_mode() == 10);
    safety.configureSafetyMode(true);
    REQUIRE(h->clears == clears + 2 && fw.get_mode() == 10);
    safety.configureSafetyMode(true);
    REQUIRE(h->clears == clears + 3);
    int verified = h->clears;
    safety.configureSafetyMode(true);
    REQUIRE(h->clears == verified && params.getBool("XpilotPandaRulesFault"));
  } else if (name == "H12") {
    fw.mode(0, 0);
    auto before = h->requests.size();
    for (int i = 0; i < 100; ++i) tick(safety, i);
    REQUIRE(fw.get_mode() == 0 && h->writes == writes);
    REQUIRE(std::find(h->requests.begin() + before, h->requests.end(), 0xdc) == h->requests.end());
  } else if (name == "H13") {
    auto before = std::count(h->requests.begin(), h->requests.end(), 0xd2);
    for (int i = 0; i < 100; ++i) tick(safety, i);
    REQUIRE(std::count(h->requests.begin(), h->requests.end(), 0xd2) == before);
    params.putBool("XpilotPandaRulesFault", true);
    int clears = h->clears;
    for (int i = 1; i < 10; ++i) tick(safety, i);
    REQUIRE(h->clears == clears);
    tick(safety, 10);
    REQUIRE(h->clears == clears + 1);
  }
  std::ofstream trace(out + "/review-trace.jsonl", std::ios::app);
  trace << "{\"case\":\"" << name << "\",\"writes\":" << h->writes << ",\"clears\":" << h->clears << "}\n";
}

int main(int argc, char **argv) {
  return run_native_test([&] { REQUIRE(argc == 3); Firmware fw(argv[1]); run(fw, argv[2]); if (const char *name = getenv("PANDA_RULES_CASE"); name && name[0] == 'H') {
      review_case(fw, argv[2], name);
    } else {
      for (const char *review : {"H10", "H11", "H12", "H13"}) review_case(fw, argv[2], review);
    } });
}
