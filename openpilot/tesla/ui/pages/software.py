"""Software page (native): the rows of mici SoftwareLayoutMici (version, check/download update, install, target branch,
uninstall), reusing its description parsing and updater signalling. The target branch list is a native sub-page."""
import subprocess
import threading
import time

from openpilot.common.time_helpers import system_time_valid
from openpilot.selfdrive.ui.mici.layouts.settings.software import UPDATER_TIMEOUT, CheckUpdateButton, _split_description
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import gui_app
from openpilot.system.ui.lib.multilang import tr
from openpilot.system.ui.widgets.confirm_dialog import alert_dialog
from openpilot.system.ui.widgets.list_view import ButtonAction, ListItem, button_item, text_item
from openpilot.tesla.ui.pages.base import ListPage, confirm


def _signal_updater(sig: str) -> None:
  threading.Thread(target=lambda: subprocess.run(f"pkill -{sig} -f {CheckUpdateButton.UPDATER_PROC}", shell=True), daemon=True).start()


def _current() -> tuple[str, str]:
  desc = _split_description(ui_state.params.get("UpdaterCurrentDescription") or "")
  if desc:
    version, branch, commit, date = desc
    return f"{version} ({date})", f"{branch} ({commit})"
  return ui_state.params.get("Version") or tr("N/A"), ui_state.params.get("GitBranch") or tr("N/A")


class UpdateChecker:
  """The CheckUpdateButton state machine as a row value: waiting -> updater state -> idle result."""

  def __init__(self):
    self.waiting_t: float | None = None  # signalled, updater not yet out of idle
    self.responding = False
    self.done_t: float | None = None  # result shown for 3 s
    self.result = ""

  def press(self, value: str) -> None:
    if not system_time_valid():
      gui_app.push_widget(alert_dialog(tr("Please connect to Wi-Fi to update.")))
      return
    self.check("SIGHUP" if value == tr("download update") else "SIGUSR1")

  def check(self, sig: str = "SIGUSR1") -> None:
    self.waiting_t, self.responding, self.done_t, self.result = time.monotonic(), False, None, tr("checking...")
    _signal_updater(sig)

  def busy(self) -> bool:
    return self.waiting_t is not None or self.responding

  def value(self) -> str:
    params = ui_state.params
    state = params.get("UpdaterState") or ""
    now = time.monotonic()
    if self.waiting_t is not None:
      if state != "idle":
        self.waiting_t, self.responding = None, True
      elif now - self.waiting_t > UPDATER_TIMEOUT:
        self.waiting_t, self.done_t, self.result = None, now, tr("updater failed to respond")
      return self.result
    if self.responding:
      if state == "idle":
        self.responding, self.done_t = False, now
        self.result = tr("up to date") if self.result == tr("checking...") else self.result
      else:
        self.result = state
      return self.result
    if int(params.get("UpdateFailedCount") or 0) > 0:
      return tr("failed to update")
    if params.get_bool("UpdaterFetchAvailable"):
      return tr("download update")
    if self.done_t is not None and now - self.done_t < 3.0:
      return tr("up to date") if self.result in (tr("checking..."), "") else self.result
    return ""


class BranchPage(ListPage):
  def __init__(self, on_select):
    super().__init__()
    params = ui_state.params
    branches = [b for b in (params.get("UpdaterAvailableBranches") or "").split(",") if b]
    for b in [params.get("GitBranch") or "", "devel-staging", "devel", "nightly", "nightly-dev", "master"]:
      if b in branches:
        branches.remove(b)
        branches.insert(0, b)
    current = params.get("UpdaterTargetBranch") or ""
    for b in branches:
      self.add(button_item(b, tr("CURRENT") if b == current else tr("SELECT"), callback=lambda b=b: on_select(b), enabled=ui_state.is_offroad))

  def show_event(self):
    super().show_event()


class SoftwarePage(ListPage):
  def __init__(self):
    super().__init__()
    self.checker = UpdateChecker()
    params = ui_state.params
    value = self.checker.value
    action = ButtonAction(lambda: tr("DOWNLOAD") if value() == tr("download update") else tr("CHECK"),
                          enabled=lambda: not ui_state.started and not self.checker.busy())
    check = ListItem(lambda: tr("Update: {}").format(value()) if value() else tr("Check for update"), action_item=action,
                     callback=lambda: self.checker.press(value()))
    install = button_item(lambda: tr("Install {}").format(self._new_version()), tr("INSTALL"), callback=lambda: threading.Thread(
      target=lambda: params.put_bool("DoReboot", True, block=True), daemon=True).start())
    install.set_visible(lambda: ui_state.is_offroad() and params.get_bool("UpdateAvailable"))
    branch = button_item(tr("Target branch"), lambda: params.get("UpdaterTargetBranch") or tr("SELECT"), callback=self._choose_branch,
                         enabled=ui_state.is_offroad)
    branch.set_visible(not params.get_bool("IsTestedBranch"))
    self.add(
      text_item(tr("Version"), lambda: _current()[0]),
      text_item(tr("Branch"), lambda: _current()[1]),
      check, install, branch,
      button_item(tr("Uninstall sunnypilot"), tr("UNINSTALL"), callback=lambda: confirm(tr("Are you sure you want to uninstall?"), "uninstall",
                                                                                lambda: params.put_bool("DoUninstall", True, block=True))),
    )

  def _new_version(self) -> str:
    desc = _split_description(ui_state.params.get("UpdaterNewDescription") or "")
    return f"{desc[0]} ({desc[1]})" if desc else ""

  def _choose_branch(self) -> None:
    if not ui_state.params.get("UpdaterAvailableBranches"):
      gui_app.push_widget(alert_dialog(tr("Failed to get available branches. Ensure you're connected to the internet and try again.")))
      return
    self.open(tr("Target branch"), BranchPage(self._select_branch))

  def _select_branch(self, branch: str) -> None:
    if ui_state.is_offroad():
      ui_state.params.put("UpdaterTargetBranch", branch, block=True)
      self.checker.check()
    self.open(None, None)  # back to this page

  def show_event(self):
    super().show_event()
