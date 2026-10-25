// H14: production SPI retry/transfer and LockEx; only the ioctl peer is simulated.
#include <algorithm>
#include <array>
#include <atomic>
#include <cassert>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <future>
#include <mutex>
#include <random>
#include <string>
#include <sys/file.h>
#include <thread>
#include <unistd.h>

#include "common/tests/native_test.h"
#include "common/timing.h"

#define SPI_BUF_SIZE 4096
#define SPILOG(...)
#define LOGW(...)
struct spi_ioc_transfer { uint64_t tx_buf, rx_buf; uint32_t len; };
static void put_crc(uint8_t *data, uint32_t len);

class TestSpi {
public:
  int spi_fd;
  inline static std::recursive_mutex hw_lock;
  bool connected = true;
  uint8_t tx_buf[SPI_BUF_SIZE], rx_buf[SPI_BUF_SIZE];
  struct __attribute__((packed)) spi_header { uint8_t endpoint; uint32_t id; uint16_t tx_len, rx_len; } header;
  struct __attribute__((packed)) spi_resp_header { uint8_t status; uint32_t id; uint16_t len; };
  uint32_t xfer_count = 0;
  int requests = 0;
#include "spi_declarations.inc"
  int lltransfer(spi_ioc_transfer &t);
};

#include "spi_under_test.inc"

int TestSpi::lltransfer(spi_ioc_transfer &t) {
  if (t.tx_buf == reinterpret_cast<uint64_t>(tx_buf)) {
    ++requests;
    rx_buf[0] = SPI_SLAVE_READY;
  } else {
    spi_resp_header response = {uint8_t(requests == 1 ? SPI_NACK : SPI_ACK), header.id, 0};
    memcpy(rx_buf, &response, sizeof(response));
    put_crc(rx_buf, sizeof(response));
  }
  return t.len;
}

int main() {
  return run_native_test([] {
    auto *file = std::tmpfile();
    REQUIRE(file);
    TestSpi spi;
    spi.spi_fd = fileno(file);
    std::unique_lock held(TestSpi::hw_lock);
    std::promise<void> started;
    auto result = std::async(std::launch::async, [&] {
      started.set_value();
      return spi.spi_transfer_retry(2, nullptr, 0, nullptr, 0, 5);
    });
    started.get_future().wait();
    std::this_thread::sleep_for(std::chrono::milliseconds(50));
    bool waiting = result.wait_for(std::chrono::milliseconds(0)) == std::future_status::timeout;
    held.unlock();
    int ret = result.get();
    std::fclose(file);
    REQUIRE(waiting);
    REQUIRE(ret == 0 && spi.requests == 2);
  });
}
