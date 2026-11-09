#pragma once
#include <media/cam_req_mgr.h>

#include <algorithm>
#include <cassert>
#include <cstdint>
#include <deque>
#include <optional>
#include <vector>

// Cabin completion notifications can precede SOF delivery; preserve request order.
class CabinReady {
 public:
  struct Action {
    cam_req_mgr_message event;
    bool sync_failed;
  };
  explicit CabinReady(size_t slots) : slots_(slots) { assert(slots > 0); }
  bool push(const cam_req_mgr_message &event, uint64_t now) {
    auto id = event.u.frame_msg.request_id;
    if (!id || id <= completed_ || (!queue_.empty() && id <= queue_.back().event.u.frame_msg.request_id) ||
        queue_.size() >= slots_.size())
      return false;
    queue_.push_back({event, now});
    return true;
  }
  void notify(uint64_t id, bool bps, bool success, uint64_t now) {
    if (!id || id <= completed_) return;
    auto &s = slots_[id % slots_.size()];
    if (s.id != id) s = Slot{id};
    auto &status = bps ? s.bps : s.ife;
    auto &at = bps ? s.bps_at : s.ife_at;
    if (!status) {
      status = success ? 1 : -1;
      at = now;
    }
  }
  std::optional<Action> pop(uint64_t now) {
    if (queue_.empty()) return std::nullopt;
    const auto &p = queue_.front();
    auto id = p.event.u.frame_msg.request_id;
    const auto &s = slots_[id % slots_.size()];
    bool known = s.id == id;
    bool failed = false, done = false;
    if (known && s.ife) {
      failed = s.ife < 0 || s.ife_at > p.start + 100000000ULL;
      auto bps_start = std::max(p.start, s.ife_at);
      if (!failed && s.bps) {
        failed = s.bps < 0 || s.bps_at > bps_start + 50000000ULL;
        done = true;
      } else if (!failed && now >= bps_start + 50000000ULL) {
        failed = true;
        done = true;
      }
      done = done || failed;
    } else if (now >= p.start + 100000000ULL) {
      failed = true;
      done = true;
    }
    if (!done) return std::nullopt;
    Action result{p.event, failed};
    queue_.pop_front();
    completed_ = id;
    slots_[id % slots_.size()] = Slot{};
    return result;
  }
  int timeout_ms(uint64_t now) const {
    if (queue_.empty()) return 1000;
    const auto &p = queue_.front();
    auto id = p.event.u.frame_msg.request_id;
    const auto &s = slots_[id % slots_.size()];
    uint64_t deadline = p.start + 100000000ULL;
    if (s.id == id && s.ife) {
      if (s.ife < 0 || s.bps) return 0;
      deadline = std::max(p.start, s.ife_at) + 50000000ULL;
    }
    return now >= deadline ? 0 : int((deadline - now + 999999) / 1000000);
  }
  bool empty() const { return queue_.empty(); }
  void clear() {
    queue_.clear();
    std::fill(slots_.begin(), slots_.end(), Slot{});
    completed_ = 0;
  }

 private:
  struct Pending {
    cam_req_mgr_message event;
    uint64_t start;
  };
  struct Slot {
    uint64_t id = 0;
    int ife = 0, bps = 0;
    uint64_t ife_at = 0, bps_at = 0;
  };
  std::vector<Slot> slots_;
  std::deque<Pending> queue_;
  uint64_t completed_ = 0;
};
