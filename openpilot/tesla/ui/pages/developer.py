"""Developer page (native): the params of mici DeveloperLayoutMici with the same release/offroad/CP gating.
SSH keys use the tici ssh_key_item (GitHub username keyboard + key fetch)."""
from openpilot.selfdrive.ui.mici.layouts.settings.common import restart_needed_callback
from openpilot.selfdrive.ui.mici.widgets.ssh_key import ssh_key_item
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import gui_app
from openpilot.system.ui.widgets import DialogResult
from openpilot.system.ui.widgets.confirm_dialog import ConfirmDialog
from openpilot.system.ui.lib.multilang import tr, tr_noop
from openpilot.tesla.ui.pages.base import ListPage, ParamToggle

ALPHA_DESC = tr_noop("Use alpha openpilot longitudinal control instead of stock ACC. This may disable Automatic Emergency Braking (AEB).")

EXCLUSIVE = ("JoystickDebugMode", "LongitudinalManeuverMode", "LateralManeuverMode")


class DeveloperPage(ListPage):
  def __init__(self):
    super().__init__()
    offroad = ui_state.is_offroad
    self.adb = ParamToggle(tr("Enable ADB"), "AdbEnabled", tr("Use Android Debug Bridge (ADB) over USB or the network."), enabled=offroad)
    self.ssh = ParamToggle(tr("Enable SSH"), "SshEnabled", tr("Access the device remotely using your SSH keys."))
    keys = ssh_key_item(tr("SSH keys"), tr("Grant SSH access to all public keys in your GitHub settings. Only enter your own username."))
    self.joystick = ParamToggle(tr("Joystick debug mode"), "JoystickDebugMode", tr("Control the car with a joystick for debugging."),
                                enabled=offroad, after=lambda v: self._exclusive("JoystickDebugMode", v))
    self.long = ParamToggle(tr("Longitudinal maneuver mode"), "LongitudinalManeuverMode", tr("Run longitudinal maneuvers for testing gas and brake control."),
                            enabled=lambda: ui_state.CP is not None and ui_state.has_longitudinal_control and offroad() and not ui_state.engaged,
                            after=lambda v: self._exclusive("LongitudinalManeuverMode", v, restart=True))
    self.lat = ParamToggle(tr("Lateral maneuver mode"), "LateralManeuverMode", tr("Run lateral maneuvers for testing steering control."),
                           enabled=lambda: ui_state.CP is not None and offroad() and not ui_state.engaged,
                           after=lambda v: self._exclusive("LateralManeuverMode", v, restart=True))
    self.alpha = ParamToggle(tr("Alpha longitudinal"), "AlphaLongitudinalEnabled", tr(ALPHA_DESC), enabled=lambda: not ui_state.engaged, after=self._alpha)
    self.alpha.item.set_visible(lambda: ui_state.CP is not None and ui_state.CP.alphaLongitudinalAvailable and not ui_state.is_release)
    self.debug = ParamToggle(tr("UI debug mode"), "ShowDebugInfo", tr("Show touch locations and the UI frame rate."),
                             after=lambda v: (gui_app.set_show_touches(v), gui_app.set_show_fps(v)))
    self.navigation = ParamToggle(tr("Navigation (alpha)"), "NavigationEnabled",
                                  tr("Turn-by-turn navigation with a route map. Set a destination from comma connect."))
    for t in (self.joystick, self.long, self.lat, self.navigation):
      t.item.set_visible(not ui_state.is_release)
    self.toggles = (self.adb, self.ssh, self.joystick, self.long, self.lat, self.alpha, self.debug, self.navigation)
    self.add(self.adb.item, self.ssh.item, keys, self.joystick.item, self.long.item, self.lat.item, self.alpha.item, self.debug.item,
             self.navigation.item)

  def _exclusive(self, key: str, value: bool, restart: bool = False) -> None:
    for other in EXCLUSIVE:
      if other != key:
        ui_state.params.put_bool(other, False, block=True)
    if restart:
      restart_needed_callback()
    self.refresh()

  def _alpha(self, value: bool) -> None:
    if not value:
      restart_needed_callback()
      return
    ui_state.params.put_bool("AlphaLongitudinalEnabled", False, block=True)  # on only after the warning is confirmed
    self.alpha.refresh()

    def confirmed(result):
      if result == DialogResult.CONFIRM and not ui_state.engaged:
        ui_state.params.put_bool("AlphaLongitudinalEnabled", True, block=True)
        restart_needed_callback()
      self.refresh()
    gui_app.push_widget(ConfirmDialog(tr("WARNING: alpha longitudinal control may disable Automatic Emergency Braking (AEB)"), tr("Enable"),
                                      callback=confirmed))

  def refresh(self) -> None:
    ui_state.update_params()
    if ui_state.CP is not None and (not ui_state.CP.alphaLongitudinalAvailable or ui_state.is_release):
      ui_state.params.remove("AlphaLongitudinalEnabled")
    for toggle in self.toggles:
      toggle.refresh()

  def show_event(self):
    super().show_event()
