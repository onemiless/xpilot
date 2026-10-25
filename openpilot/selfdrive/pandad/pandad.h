#pragma once

#include <string>

#include "common/params.h"
#include "selfdrive/pandad/panda.h"

void pandad_main_thread(std::string serial);

// deprecated devices
static const std::vector<cereal::PandaState::PandaType> SUPPORTED_PANDA_TYPES = {
  cereal::PandaState::PandaType::DOS,
  cereal::PandaState::PandaType::RED_PANDA,
  cereal::PandaState::PandaType::TRES,
  cereal::PandaState::PandaType::CUATRO,
};


class PandaSafety {
public:
  PandaSafety(Panda *panda) : panda_(panda) {}
  void configureSafetyMode(bool is_onroad);
  bool getOffroadMode();

private:
  void updateMultiplexingMode();
  bool loadRules();
  void rulesFault();
  enum class RulesStep { CAPS, CLEAR, BEGIN, METADATA, DATA, COMMIT, STATUS, DONE };
  RulesStep rules_step_ = RulesStep::CAPS;
  std::string rules_;
  uint16_t upload_id_ = 0;
  size_t rules_offset_ = 0;
  double rules_deadline_ = 0;
  bool rules_caps_ = false;
  bool rules_revoked_ = false;
  cereal::CarParams::SafetyModel configured_mode_ = cereal::CarParams::SafetyModel::SILENT;
  std::vector<std::string> fetchCarParams();
  void setSafetyMode(const std::vector<std::string> &params_string);

  bool initialized_ = false;
  bool log_once_ = false;
  bool safety_configured_ = false;
  bool prev_obd_multiplexing_ = false;
  Panda *panda_;
  Params params_;
};
