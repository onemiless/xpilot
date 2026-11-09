"""Optional model candidate boundary. The plugin owns codec/profile qualification.

IPC v1: private 0600 file, 16384 bytes, two latest-only slots. Metadata at 0
is META; reply at 256 is ENVELOPE followed by exactly one BGM1 v2 REPLY.
Each writer/reader takes a nonblocking POSIX byte-range lock over its slot.
The child clears the envelope, writes the body/header, then commits length last;
zero length is unpublished, including when the child dies during a write.
The child opens path itself; it never holds a VisionBuf or a cereal publisher.
"""
import fcntl
import mmap
import os
from pathlib import Path
import secrets
import struct
import tempfile
import time
from collections import deque
from statistics import median

import numpy as np

META = struct.Struct('<4sIQIIQQ30f')
ENVELOPE = struct.Struct('<QQQII')
REPLY = struct.Struct('<4sBBHIIQ')
OFFSET, SIZE = 256, 16384
DEADLINE_NS = 60_000_000  # Match upstream modeld BIG_REPLY_DEADLINE_MS.
ALIVE_NS = 2_000_000_000  # Wait for the current reply while any reply arrived this recently (dev-sp-npu BigReplyLatch).
FADE_FRAMES = 10  # 0.5 s cross-fade between phone and local outputs at 20 Hz (dev-sp-npu SourceBlender).
HEAD_SHAPES = {
  'plan': (1, 33, 15), 'plan_stds': (1, 33, 15),
  'lane_lines': (1, 4, 33, 2), 'lane_lines_stds': (1, 4, 33, 2), 'lane_lines_prob': (1, 8),
  'road_edges': (1, 2, 33, 2), 'road_edges_stds': (1, 2, 33, 2),
  'lead': (1, 3, 6, 4), 'lead_stds': (1, 3, 6, 4), 'lead_prob': (1, 3),
  'pose': (1, 6), 'pose_stds': (1, 6), 'road_transform': (1, 6), 'road_transform_stds': (1, 6),
  'wide_from_device_euler': (1, 3), 'wide_from_device_euler_stds': (1, 3),
  'meta': (1, 55), 'desire_state': (1, 8), 'desire_pred': (1, 4, 8),
}


def nanos_since_boot():
  return time.clock_gettime_ns(getattr(time, 'CLOCK_BOOTTIME', time.CLOCK_MONOTONIC))


class FrameDelayEstimator:
  """Median EOF->publish of the last 100 waited frames, clipped to [10, 100] ms; replaces the fixed DT_MDL frame delay."""
  def __init__(self):
    self.samples = deque(maxlen=100)
    self.value = 50.

  def update(self, eof_to_publish_ms):
    self.samples.append(eof_to_publish_ms)
    self.value = float(min(max(median(self.samples), 10.), 100.))


class ModelSource:
  def __init__(self, directory, decode, admit):
    # Only bounded, pure Python adapters belong here; native/network work stays in the child.
    self.decode, self.admit = decode, admit
    for stale in Path(directory).glob('model-source-*'):
      stale.unlink()
    self.fd, self.path = tempfile.mkstemp(prefix='model-source-', dir=directory)
    try:
      os.ftruncate(self.fd, SIZE)
      self.buffer = mmap.mmap(self.fd, SIZE)
    except BaseException:
      os.close(self.fd)
      os.unlink(self.path)
      raise
    self.buffer[:] = bytes(SIZE)
    self.session = secrets.randbits(64)
    self.epoch = self.last_frame = self.fresh = self.arrival_ns = self.reply_ns = 0
    self.frame = self.eof = 0
    self.fade, self.w, self.remote_last = 0, 0., None
    self.selected = self.locked = self.sent = self.alive = False
    self.reason = 'absent'

  def close(self):
    self.buffer.close()
    os.close(self.fd)
    os.unlink(self.path)

  def _slot(self, offset, size, data=None):
    try:
      fcntl.lockf(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB, size, offset)
    except OSError:
      return None
    try:
      if data is not None:
        self.buffer[offset:offset + len(data)] = data
        return True
      result = self.buffer[offset:offset + size]
      self.buffer[offset:offset + ENVELOPE.size] = bytes(ENVELOPE.size)
      return result
    finally:
      fcntl.lockf(self.fd, fcntl.LOCK_UN, size, offset)

  def send_frame(self, main, extra, transforms, inputs, prev_desire):
    if main.frame_id < self.frame:
      self.epoch = self.last_frame = self.fresh = 0
      self.fade, self.w, self.remote_last = 0, 0., None
    self.frame, self.eof = main.frame_id, main.timestamp_eof
    pulse = np.where(inputs['desire_pulse'] - prev_desire > .99, inputs['desire_pulse'], 0).copy()
    pulse[0] = 0
    values = np.concatenate([transforms['img'].ravel(), transforms['big_img'].ravel(),
                             pulse, inputs['traffic_convention'], inputs['action_t']])
    data = META.pack(b'XMS1', 1, self.session, self.frame, extra.frame_id, self.eof, extra.timestamp_eof, *values)
    self.sent = bool(self._slot(0, OFFSET, data))

  def _reject(self, reason, lock=False):
    self.reason = reason
    self.locked |= lock
    if lock:
      self.fresh = 0
    return None

  def _candidate(self, local):
    self.arrival_ns = 0
    if self.locked:
      return None
    if not self.sent:
      return self._reject('meta_busy')
    data = self._slot(OFFSET, SIZE - OFFSET)
    if data is None:
      return self._reject('ipc_busy')
    session, epoch, arrival, paired, length = ENVELOPE.unpack_from(data)
    self.arrival_ns = arrival
    if not length:
      return self._reject('absent')
    if length != 8320:
      return self._reject('wire_length', True)
    if session != self.session:
      return self._reject('session', True)
    self.reply_ns = max(self.reply_ns, arrival)
    if not epoch or paired & ~3:
      return self._reject('epoch_flags', True)
    if epoch < self.epoch:
      return self._reject('old_epoch')
    magic, version, kind, flags, frame, length, eof = REPLY.unpack_from(data, ENVELOPE.size)
    if (magic, version, kind, length) != (b'BGM1', 2, 0x11, 8280) or flags & ~3:
      return self._reject('wire_abi', True)
    now = nanos_since_boot()
    if arrival < eof:
      return self._reject('arrival_before_eof')
    if eof > now or arrival > now:
      return self._reject('future_timestamp', True)
    old_frame = frame < self.frame or (frame > self.frame and eof < self.eof)
    if not old_frame and (frame != self.frame or eof != self.eof):
      return self._reject('frame_eof', True)
    if not old_frame and frame <= self.last_frame and epoch == self.epoch:
      return self._reject('replay')
    # Late means the phone missed EOF+60ms. A reply that arrived in time stays usable when the host itself runs past the
    # deadline (local model, decode, admission, an overslept poll): the local output would be just as late.
    if arrival > eof + DEADLINE_NS:
      return self._reject('old_frame' if old_frame else 'late')
    new_epoch = epoch > self.epoch
    if new_epoch:
      self.epoch, self.fresh, self.last_frame = epoch, 0, 0
      if paired != 3:
        return self._reject('need_dual_idr')
    if paired != 3:
      self.fresh = 0
      return self._reject('need_dual_idr')
    if flags:
      if flags & 1 and not new_epoch:
        return self._reject('reset_without_epoch', True)
      self.fresh = 0
      self.last_frame = frame
      return self._reject('warmup_flags')
    raw = np.frombuffer(data, dtype='<f4', count=2066, offset=ENVELOPE.size + REPLY.size).copy()
    if not np.isfinite(raw).all() or not np.any(raw):
      return self._reject('raw_values', True)
    if old_frame:
      return self._reject('old_frame')
    try:
      remote = self.decode(raw.copy())
      shapes = HEAD_SHAPES | ({'action': (1, 2)} if 'action' in remote else {})
      if any(k not in remote or remote[k].shape != shape or not np.isfinite(remote[k]).all() for k, shape in shapes.items()):
        return self._reject('head_shape_values', True)
      local_heads = {k: local[k].copy() for k in HEAD_SHAPES}
      if 'action' in local:
        # Stock get_action_from_model reads only the first two means, even with log-sigma present.
        local_heads['action'] = local['action'][:, :2].copy()
        if 'action' not in remote:
          return self._reject('local_profile', True)
      if not self.admit(local_heads, remote):
        return self._reject('admission', True)
    except Exception:
      return self._reject('adapter_error', True)
    self.last_frame = frame
    self.fresh += 1
    if self.fresh <= 5:
      self.reason = 'warmup'
      return None
    self.reason = ''
    remote['raw_pred'] = raw
    return remote

  def pick(self, local):
    remote = self._candidate(local)
    self.alive = not self.locked and self.reply_ns > 0 and nanos_since_boot() - self.reply_ns < ALIVE_NS
    while self.alive and self.reason in ('absent', 'old_frame', 'ipc_busy'):
      remaining = self.eof + DEADLINE_NS - nanos_since_boot()
      if remaining <= 0:
        break
      time.sleep(min(.001, remaining / 1e9))
      remote = self._candidate(local)
    self.selected = remote is not None
    if self.selected:
      self.remote_last = remote
    self.fade = min(FADE_FRAMES, self.fade + 1) if self.selected else max(0, self.fade - 1)
    self.w = self.fade / FADE_FRAMES
    if self.w >= 1:
      return remote, True
    if self.w <= 0:
      return local, False
    # Cross-fade every head; a fallback frame fades out against the last accepted phone output.
    out = dict(local)
    for k, a in local.items():
      b = self.remote_last.get(k)
      if b is not None and b.shape == a.shape:
        out[k] = a * (1 - self.w) + b * self.w
      elif k == 'action' and b is not None:
        out[k] = a.copy()
        out[k][:, :2] = a[:, :2] * (1 - self.w) + b[:, :2] * self.w
    return out, self.selected
