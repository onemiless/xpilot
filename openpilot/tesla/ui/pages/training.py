"""Training guide (native): the steps of mici TrainingGuide (selfdrive/ui/mici/layouts/onboarding.py) on one big-screen page:
attention notice, driver monitoring intro, cabin camera check, data upload choice. The guide writes what the mici steps write
(IsDriverViewEnabled, RecordFront) and calls on_done after the last step; the caller writes CompletedTrainingVersion if it must.
Buttons are offroad-only."""
from __future__ import annotations

import math
from collections.abc import Callable

import pyray as rl

from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.selfdrive.ui.mici.layouts.onboarding import DriverCameraSetupDialog, TrainingGuideDMTutorial
from openpilot.selfdrive.ui.ui_state import device, ui_state
from openpilot.sunnypilot.hardware.profile import has_driver_camera
from openpilot.system.ui.lib.application import gui_app
from openpilot.system.ui.lib.multilang import tr
from openpilot.system.ui.widgets import Widget
from openpilot.system.ui.widgets.button import Button, ButtonStyle
from openpilot.tesla.ui.pages.base import BUTTON_H, PAD, body, draw_card

ATTENTION, PRE_DM, CHECK, RECORD, BAD_FACE = range(5)
CAMERA_W = 1100


class TrainingGuidePage(Widget):
  def __init__(self, on_done: Callable[[], None]):
    super().__init__()
    self._on_done = on_done
    self._step = ATTENTION
    self._active = False
    self._camera = DriverCameraSetupDialog()
    self._progress = FirstOrderFilter(0.0, 0.5, 1 / gui_app.target_fps)
    self._check = Button(tr("next"), lambda: self._go(RECORD), button_style=ButtonStyle.PRIMARY)
    self._check.set_enabled(lambda: ui_state.is_offroad() and self._progress.x >= 0.999)

    def button(text, callback, style=ButtonStyle.NORMAL):
      b = Button(text, callback, button_style=style)
      b.set_enabled(ui_state.is_offroad)
      return b

    def nxt():
      self._go(self._step + 1)
    self._steps = {
      ATTENTION: (tr("what is sunnypilot?"), body(tr("1. sunnypilot is a driver assistance system.\n" +
                                                     "2. You must pay attention at all times.\n" +
                                                     "3. You must be ready to take over at any time.\n" +
                                                     "4. You are fully responsible for driving the car.")),
                  [button(tr("next"), nxt, ButtonStyle.PRIMARY)]),
      PRE_DM: (tr("driver monitoring check"), body(tr("Next, we'll check if the device can detect the driver properly.\n" +
                                                      "sunnypilot uses the cabin camera to check if the driver is distracted.\n" +
                                                      "If it does not have a clear view of the driver, unplug and remount before continuing.")),
               [button(tr("next"), nxt, ButtonStyle.PRIMARY)]),
      CHECK: ("", body(tr("driver monitoring check\n\nLook straight ahead until the green bar is full.")),
              [button(tr("?"), lambda: self._go(BAD_FACE)), self._check]),
      RECORD: (tr("driver camera data"), body(tr("Do you want to share video data for training?\n" +
                                                 "Sharing your data with comma helps improve openpilot and sunnypilot for everyone.")),
               [button(tr("no, don't upload"), lambda: self._record(False)),
                button(tr("allow data uploading"), lambda: self._record(True), ButtonStyle.PRIMARY)]),
      BAD_FACE: (tr("looking for driver"), body(tr("Make sure the device can see your face.\n" +
                                                   "Remount if your face is blocked, or driver monitoring has difficulty tracking your face.")),
                 [button(tr("back"), lambda: self._go(CHECK))]),
    }

    def on_timeout():  # like mici: stop the driver monitoring model when the device sleeps
      if self._active:
        ui_state.params.put_bool("IsDriverViewEnabled", False)

    device.add_interactive_timeout_callback(on_timeout)

  def _go(self, step: int) -> None:
    if step != ATTENTION and not has_driver_camera():
      self._on_done()  # C3XL: no cabin camera, so no driver monitoring check or camera upload choice
      return
    if step == PRE_DM:
      ui_state.params.put_bool("IsDriverViewEnabled", True)  # get the driver monitoring model ready for the check
    elif step == CHECK and self._step == PRE_DM:
      self._camera.show_event()
      self._progress.x = 0.0
    self._step = step

  def _record(self, allow: bool) -> None:
    if ui_state.is_offroad():
      ui_state.params.put_bool("RecordFront", allow)
      self._on_done()

  def show_event(self):
    super().show_event()
    self._active = True
    self._step = ATTENTION
    device.set_override_interactive_timeout(300)

  def hide_event(self):
    super().hide_event()
    self._active = False
    device.set_override_interactive_timeout(None)
    ui_state.params.put_bool("IsDriverViewEnabled", False)

  def _update_state(self):
    if self._step not in (CHECK, BAD_FACE) or not ui_state.is_offroad():
      return
    # mici TrainingGuideDMTutorial._update_state
    if device.awake and not ui_state.params.get_bool("IsDriverViewEnabled"):
      ui_state.params.put_bool("IsDriverViewEnabled", True)
    sm = ui_state.sm
    if sm.recv_frame.get("driverMonitoringState", 0) == 0:
      return
    face = self._camera.driver_state_renderer.get_driver_data().faceOrientation
    limit = TrainingGuideDMTutorial.LOOKING_THRESHOLD_DEG
    looking = len(face) == 3 and abs(math.degrees(face[0])) < limit and abs(math.degrees(face[1])) < limit
    if ((sm["driverMonitoringState"].visionPolicyState.faceDetected and looking) or self._progress.x > 0.99) and self._step == CHECK:
      duration = TrainingGuideDMTutorial.PROGRESS_DURATION * (2 if self._progress.x < 0.25 else 1)
      self._progress.x = min(1.0, self._progress.x + 1.0 / (duration * gui_app.target_fps))
    else:
      self._progress.update(0.0)

  def _render(self, rect: rl.Rectangle):
    title, text, buttons = self._steps[self._step]
    if self._step != CHECK:
      draw_card(rect, title, text, buttons)
      return
    cam = rl.Rectangle(rect.x + PAD, rect.y + PAD, CAMERA_W, rect.height - 2 * PAD)
    self._camera.render(cam)
    rl.draw_rectangle_rec(rl.Rectangle(cam.x, cam.y + cam.height - 16, cam.width * self._progress.x, 16), rl.Color(0, 255, 64, 255))
    side = rl.Rectangle(cam.x + CAMERA_W, rect.y, rect.x + rect.width - cam.x - CAMERA_W, rect.height)
    draw_card(side, title, text, [])
    for i, b in enumerate(buttons):
      b.render(rl.Rectangle(side.x + PAD, side.y + side.height - (2 - i) * (BUTTON_H + PAD), side.width - 2 * PAD, BUTTON_H))
