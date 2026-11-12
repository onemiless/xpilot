"""Duplicate/backward receive times must reject bad frequency without killing a consumer.

Failures: initial zero mean, zero recent-window mean, backward timestamps;
SubMaster.update_msgs must continue receiving and recover on normal intervals.
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openpilot.cereal import messaging
from openpilot.cereal.messaging import FrequencyTracker


class TestFrequencyTracker(unittest.TestCase):
  def test_nonpositive_and_normal_intervals(self):
    for times, expected in (([1., 1.], False), ([1., .9], False), ([1., 1.05, 1.1], True)):
      with self.subTest(times=times):
        tracker = FrequencyTracker(20., 20., True)
        for now in times:
          tracker.record_recv_time(now)
        self.assertIs(tracker.valid, expected)

  def test_zero_recent_window(self):
    tracker = FrequencyTracker(20., 20., True)
    for now in [1., 1.2] + [1.2] * 20:
      tracker.record_recv_time(now)
    self.assertFalse(tracker.valid)

  def test_submaster_survives_and_recovers(self):
    shm_dir = '/dev/shm' if Path('/dev/shm').is_dir() else '/tmp'
    with tempfile.TemporaryDirectory(prefix='msgq_', dir=shm_dir) as queue_dir, \
         patch.dict(os.environ, OPENPILOT_PREFIX=Path(queue_dir).name.removeprefix('msgq_')):
      sm = messaging.SubMaster(['carState'], frequency=100.)
      packet = messaging.new_message('carState', valid=True).as_reader()
      sm.update_msgs(1., [packet])
      sm.update_msgs(1., [packet])
      self.assertFalse(sm.freq_ok['carState'])
      self.assertTrue(sm.valid['carState'])
      for i in range(1, 21):
        sm.update_msgs(1. + i * .01, [packet])
      self.assertTrue(sm.updated['carState'])
      self.assertTrue(sm.freq_ok['carState'])
      self.assertTrue(sm.valid['carState'])
      del sm
