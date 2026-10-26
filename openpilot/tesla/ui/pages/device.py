"""Device page (native): the actions of mici DeviceLayoutMici. Reset calibration, reboot and power off ask in a tici ConfirmDialog
and are blocked while engaged. Training guide, driver camera, terms and regulatory info open native sub-pages (training.py,
driver_camera.py, terms.py, regulatory.py); the training guide and driver camera only offroad."""
from openpilot.common.hardware import HARDWARE
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.sunnypilot.hardware.profile import has_driver_camera
from openpilot.system.ui.widgets.list_view import button_item, text_item
from openpilot.system.ui.lib.multilang import tr, tr_noop
from openpilot.tesla.ui.pages.base import ListPage, back, confirm
from openpilot.tesla.ui.pages.driver_camera import DriverCameraPage
from openpilot.tesla.ui.pages.regulatory import RegulatoryPage
from openpilot.tesla.ui.pages.terms import TermsPage
from openpilot.tesla.ui.pages.training import TrainingGuidePage

CALIB_DESC = tr_noop("openpilot calibrates continuously; resetting is rarely needed. Mount within 4 degrees left/right and 5 up/9 down.")


def _reset_calibration() -> None:
  for key in ("CalibrationParams", "LiveTorqueParameters", "LiveParametersV2", "LiveDelay"):
    ui_state.params.remove(key)
  ui_state.params.put_bool("OnroadCycleRequested", True, block=True)


class DevicePage(ListPage):
  def __init__(self):
    super().__init__()
    self.main = None  # set by the host
    offroad = ui_state.is_offroad
    params = ui_state.params
    guide, camera, terms, regulatory = TrainingGuidePage(on_done=back), DriverCameraPage(), TermsPage(), RegulatoryPage()
    power_off = button_item(tr("Power off"), tr("POWER OFF"), callback=lambda: confirm(tr("Are you sure you want to power off?"), "power off",
                                                                               lambda: params.put_bool("DoShutdown", True, block=True)))
    power_off.set_visible(lambda: not ui_state.ignition)
    camera_row = button_item(tr("Driver camera preview"), tr("PREVIEW"),
                             tr("Preview the cabin camera to check driver monitoring visibility. The vehicle must be off."),
                             callback=lambda: offroad() and self.open(tr("driver camera"), camera), enabled=offroad)
    camera_row.set_visible(has_driver_camera())  # C3XL has no cabin camera
    regulatory_row = button_item(tr("Regulatory info"), tr("VIEW"), callback=lambda: self.open(tr("regulatory"), regulatory))
    regulatory_row.set_visible(HARDWARE.get_device_type() == "mici")  # mici_fcc.html is the comma four label, wrong on C3/C3X/C3XL
    self.add(
      text_item(tr("Dongle ID"), lambda: params.get("DongleId") or tr("N/A")),
      text_item(tr("Serial"), lambda: params.get("HardwareSerial") or tr("N/A")),
      button_item(tr("Review training guide"), tr("REVIEW"), callback=lambda: offroad() and self.open(tr("training guide"), guide), enabled=offroad),
      camera_row,
      button_item(tr("Terms & conditions"), tr("VIEW"), callback=lambda: self.open(tr("terms"), terms)),
      regulatory_row,
      button_item(tr("Reset calibration"), tr("RESET"), tr(CALIB_DESC),
                  callback=lambda: confirm(tr("Are you sure you want to reset calibration?"), "reset", _reset_calibration)),
      button_item(tr("Reboot"), tr("REBOOT"), callback=lambda: confirm(tr("Are you sure you want to reboot?"), "reboot",
                                                               lambda: params.put_bool("DoReboot", True, block=True))),
      power_off,
    )

  def show_event(self):
    super().show_event()
