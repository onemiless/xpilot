#pragma once

#include <array>
#include <cstdint>
#include <ctime>
#include <functional>
#include <list>
#include <memory>
#include <optional>
#include <string>
#include <vector>

#include "openpilot/cereal/gen/cpp/car.capnp.h"
#include "openpilot/cereal/gen/cpp/log.capnp.h"
#include "panda/board/health.h"
#include "panda/board/can.h"
#include "selfdrive/pandad/panda_comms.h"

#define USB_TX_SOFT_LIMIT   (0x100U)
#define USBPACKET_MAX_SIZE  (0x40)

#define RECV_SIZE (0x4000U)

#define CAN_REJECTED_BUS_OFFSET   0xC0U
#define CAN_RETURNED_BUS_OFFSET 0x80U

#define PANDA_BUS_OFFSET 4

struct __attribute__((packed)) can_header {
  uint8_t reserved : 1;
  uint8_t bus : 3;
  uint8_t data_len_code : 4;
  uint8_t rejected : 1;
  uint8_t returned : 1;
  uint8_t extended : 1;
  uint32_t addr : 29;
  uint8_t checksum : 8;
};

struct can_frame {
  long address;
  std::string dat;
  long src;
};


class Panda {
protected:
  std::unique_ptr<PandaCommsHandle> handle;

public:
  Panda(std::string serial);

  cereal::PandaState::PandaType hw_type = cereal::PandaState::PandaType::UNKNOWN;

  bool connected();
  bool comms_healthy();
  std::string hw_serial();

  // Static functions
  static std::vector<std::string> list();

  // Panda functionality
  cereal::PandaState::PandaType get_hw_type();
  void set_safety_model(cereal::CarParams::SafetyModel safety_model, uint16_t safety_param=0U, unsigned int timeout=TIMEOUT);
  void set_alternative_experience(uint16_t alternative_experience, uint16_t safety_param_sp=0U);
  std::string serial_read(int port_number = 0);
  void set_uart_baud(int uart, int rate);
  void set_fan_speed(uint16_t fan_speed);
  uint16_t get_fan_speed();
  void set_ir_pwr(uint16_t ir_pwr);
  std::optional<health_t> get_state(unsigned int timeout=TIMEOUT);
  std::optional<can_health_t> get_can_state(uint16_t can_number);
  void set_loopback(bool loopback);
  std::optional<std::vector<uint8_t>> get_firmware_version();
  bool up_to_date();
  std::optional<std::string> get_serial();
  void set_power_saving(bool power_saving);
  void enable_deepsleep();
  void send_heartbeat(bool engaged, bool engaged_mads);
  void set_can_speed_kbps(uint16_t bus, uint16_t speed);
  void set_can_fd_auto(uint16_t bus, bool enabled);
  void set_data_speed_kbps(uint16_t bus, uint16_t speed);
  void set_canfd_non_iso(uint16_t bus, bool non_iso);
  void can_send(const capnp::List<cereal::CanData>::Reader &can_data_list);
  bool can_receive(std::vector<can_frame>& out_vec);
  void can_reset_communications();
  std::vector<uint8_t> rules_control(uint8_t request, uint16_t param1=0, uint16_t param2=0);
  bool long_owner_supported = false;
  uint32_t health_version = 0;
  uint8_t long_owner = 0, long_pending = 255, long_refusal = 0;
  uint16_t long_pending_age_ms = 0;
  bool request_long_owner(uint8_t target);
  bool rules_write(std::array<uint8_t, 64> &record);

protected:
  bool packet_versions_checked = false;
  bool long_owner_capable = false;

  // for unit tests
  uint8_t receive_buffer[RECV_SIZE + sizeof(can_header) + 64];
  uint32_t receive_buffer_size = 0;

  Panda() {}
  void pack_can_buffer(const capnp::List<cereal::CanData>::Reader &can_data_list,
                         std::function<void(uint8_t *, size_t)> write_func);
  bool unpack_can_buffer(uint8_t *data, uint32_t &size, std::vector<can_frame> &out_vec);
  uint8_t calculate_checksum(uint8_t *data, uint32_t len);
};
