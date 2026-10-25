#include "selfdrive/pandad/pandad.h"
#include "openpilot/cereal/messaging/messaging.h"
#include "common/swaglog.h"
#include "common/timing.h"
#include <zlib.h>

namespace {
const std::vector<uint8_t> RULE_CAPS = {0x58, 0x50, 0x52, 0x31, 1, 0, 0, 16, 8, 8, 8, 8, 64, 0, 0, 0};

uint32_t read_le(const uint8_t *p, size_t n) {
  uint32_t v = 0;
  for (size_t i = 0; i < n; ++i) v |= uint32_t(p[i]) << (8 * i);
  return v;
}

bool status_ok(const std::vector<uint8_t> &s, uint8_t state) {
  return s.size() == 24 && read_le(s.data(), 4) == 0x31525058 && read_le(s.data() + 4, 2) == 1 &&
         s[6] == state && s[7] == 0 && s[15] == 0 && s[23] == 0;
}

bool non_car(uint16_t mode) {
  return mode == 0 || mode == 19 || mode == 17 || mode == 3;
}
}

void PandaSafety::configureSafetyMode(bool is_onroad) {
  if (was_onroad_ && !is_onroad) params_.putBool("XpilotPandaRulesFault", false);
  was_onroad_ = is_onroad;
  if (!is_onroad) {
    initialized_ = false;
    safety_configured_ = false;
    log_once_ = false;
    rules_deadline_ = 0;
    rules_step_ = RulesStep::CAPS;
    rules_caps_ = false;
    rules_revoked_ = false;
    car_params_.clear();
    return;
  }
  if (params_.getBool("XpilotPandaRulesFault") && !rules_revoked_) rulesFault();
  if (safety_configured_) return;
  updateMultiplexingMode();
  if (car_params_.empty()) car_params_ = fetchCarParams();
}

void PandaSafety::updateRules() {
  if (car_params_.empty()) return;
  if (loadRules()) {
    setSafetyMode(car_params_);
    safety_configured_ = true;
    car_params_.clear();
  }
}

void PandaSafety::rulesFault() {
  params_.putBool("XpilotPandaRulesFault", true);
  rules_step_ = RulesStep::DONE;
  if (!rules_caps_) rules_caps_ = panda_->rules_control(0xa0) == RULE_CAPS;
  if (rules_caps_) {
    panda_->rules_control(0xa4);
    auto s = panda_->rules_control(0xa1);
    rules_revoked_ = status_ok(s, 0) && s[14] == 0 && read_le(s.data() + 8, 4) == 0 && read_le(s.data() + 12, 2) == 0;
    if (!rules_revoked_) {
      panda_->set_safety_model(cereal::CarParams::SafetyModel::NO_OUTPUT, 0, 5);
      auto health = panda_->get_state(5);
      rules_revoked_ = health && health->safety_mode_pkt == uint16_t(cereal::CarParams::SafetyModel::NO_OUTPUT);
    }
  }
}

bool PandaSafety::loadRules() {
  if (params_.getBool("XpilotPandaRulesFault")) {
    if (!rules_revoked_) rulesFault();
    return true;
  }
  auto latest = params_.get("XpilotPandaRules");
  if (rules_step_ == RulesStep::CAPS || latest != rules_) {
    rules_ = latest;
    rules_step_ = RulesStep::CAPS;
    if (rules_deadline_ == 0) rules_deadline_ = millis_since_boot() + 2000;
  }
  if (rules_step_ == RulesStep::DONE) return true;
  auto health = panda_->get_state(5);
  if (!health || !non_car(health->safety_mode_pkt) || millis_since_boot() >= rules_deadline_) {
    rulesFault();
    return true;
  }
  auto fail = [this]() { rulesFault(); return true; };
  if (rules_step_ == RulesStep::CAPS) {
    if (panda_->rules_control(0xa0) != RULE_CAPS) return fail();
    rules_caps_ = true;
    rules_step_ = RulesStep::CLEAR;
  } else if (rules_step_ == RulesStep::CLEAR) {
    if (!status_ok(panda_->rules_control(0xa4), 0)) return fail();
    if (rules_.size() < 20 || rules_.size() > 4100) return fail();
    const auto *b = reinterpret_cast<const uint8_t *>(rules_.data());
    size_t len = rules_.size() - 4;
    if (read_le(b, 4) != 0x31525058 || read_le(b + 4, 2) != 1 || read_le(b + 6, 2) != 16 ||
        read_le(b + 8, 2) != len || b[10] > 8 || b[11] != 0 || read_le(b + 12, 4) != 0 ||
        crc32(0, b, len) != read_le(b + len, 4)) return fail();
    rules_step_ = RulesStep::BEGIN;
  } else if (rules_step_ == RulesStep::BEGIN) {
    auto s = panda_->rules_control(0xa2, rules_.size() - 4, uint8_t(rules_[10]));
    if (!status_ok(s, 1) || read_le(s.data() + 20, 2) != rules_.size() - 4 || s[22] != uint8_t(rules_[10])) return fail();
    upload_id_ = read_le(s.data() + 16, 2);
    if (upload_id_ == 0) return fail();
    rules_offset_ = 0;
    rules_step_ = RulesStep::METADATA;
  } else if (rules_step_ == RulesStep::METADATA || rules_step_ == RulesStep::DATA) {
    bool metadata = rules_step_ == RulesStep::METADATA;
    size_t len = metadata ? 4 : std::min(size_t(56), rules_.size() - 4 - rules_offset_);
    std::array<uint8_t, 64> record = {};
    record[0] = upload_id_ & 0xff;
    record[1] = upload_id_ >> 8;
    record[2] = rules_offset_ & 0xff;
    record[3] = rules_offset_ >> 8;
    record[4] = len;
    record[6] = metadata ? 1 : 0;
    memcpy(record.data() + 8, rules_.data() + (metadata ? rules_.size() - 4 : rules_offset_), len);
    if (!panda_->rules_write(record)) return fail();
    if (!metadata) rules_offset_ += len;
    rules_step_ = rules_offset_ == rules_.size() - 4 ? RulesStep::COMMIT : RulesStep::DATA;
  } else if (rules_step_ == RulesStep::COMMIT) {
    const auto *crc = reinterpret_cast<const uint8_t *>(rules_.data() + rules_.size() - 4);
    if (!status_ok(panda_->rules_control(0xa3, read_le(crc, 2), read_le(crc + 2, 2)), 2)) return fail();
    rules_step_ = RulesStep::STATUS;
  } else if (rules_step_ == RulesStep::STATUS) {
    auto s = panda_->rules_control(0xa1);
    if (!status_ok(s, 2) || read_le(s.data() + 8, 4) != read_le(reinterpret_cast<const uint8_t *>(rules_.data() + rules_.size() - 4), 4) ||
        read_le(s.data() + 12, 2) != rules_.size() - 4 || s[14] != uint8_t(rules_[10])) return fail();
    rules_step_ = RulesStep::DONE;
    return true;
  }
  return false;
}

void PandaSafety::updateMultiplexingMode() {
  // Initialize to ELM327 without OBD multiplexing for initial fingerprinting
  if (!initialized_) {
    prev_obd_multiplexing_ = false;
    panda_->set_safety_model(cereal::CarParams::SafetyModel::ELM327, 1U);
    initialized_ = true;
  }

  // Switch between multiplexing modes based on the OBD multiplexing request
  bool obd_multiplexing_requested = params_.getBool("ObdMultiplexingEnabled");
  if (obd_multiplexing_requested != prev_obd_multiplexing_) {
    const uint16_t safety_param = obd_multiplexing_requested ? 0U : 1U;
    panda_->set_safety_model(cereal::CarParams::SafetyModel::ELM327, safety_param);
    rules_step_ = RulesStep::CAPS;
    prev_obd_multiplexing_ = obd_multiplexing_requested;
    params_.putBool("ObdMultiplexingChanged", true);
  }
}

// TODO-SP: Use structs instead of vector
std::vector<std::string> PandaSafety::fetchCarParams() {
  if (!params_.getBool("FirmwareQueryDone")) {
    return {};
  }

  if (!log_once_) {
    LOGW("Finished FW query, Waiting for params to set safety model");
    log_once_ = true;
  }

  if (!params_.getBool("ControlsReady")) {
    return {};
  }
  return {params_.get("CarParams"), params_.get("CarParamsSP")};
}

// TODO-SP: Use structs instead of vector
void PandaSafety::setSafetyMode(const std::vector<std::string> &params_string) {
  AlignedBuffer aligned_buf;
  AlignedBuffer aligned_buf_sp;

  capnp::FlatArrayMessageReader cmsg(aligned_buf.align(params_string[0].data(), params_string[0].size()));
  cereal::CarParams::Reader car_params = cmsg.getRoot<cereal::CarParams>();

  capnp::FlatArrayMessageReader cmsg_sp(aligned_buf_sp.align(params_string[1].data(), params_string[1].size()));
  cereal::CarParamsSP::Reader car_params_sp = cmsg_sp.getRoot<cereal::CarParamsSP>();

  auto safety_configs = car_params.getSafetyConfigs();
  uint16_t alternative_experience = car_params.getAlternativeExperience();
  uint16_t safety_param_sp = car_params_sp.getSafetyParam();

  cereal::CarParams::SafetyModel safety_model = safety_configs[0].getSafetyModel();
  uint16_t safety_param = safety_configs[0].getSafetyParam();

  LOGW("setting safety model: %d, param: %d, alternative experience: %d, param_sp: %d", (int)safety_model, safety_param, alternative_experience, safety_param_sp);
  panda_->set_alternative_experience(alternative_experience, safety_param_sp);
  panda_->set_safety_model(safety_model, safety_param);
}

bool PandaSafety::getOffroadMode() {
  auto offroad_mode = params_.getBool("OffroadMode");
  return offroad_mode;
}
