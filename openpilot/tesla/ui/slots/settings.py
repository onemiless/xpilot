"""Built-in settings tiles (offroad only): each opens its native page from tesla/ui/pages."""
import pyray as rl

from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import FontWeight
from openpilot.system.ui.lib.multilang import tr, trn, tr_noop
from openpilot.tesla.plugins import FAULTS_PARAM, LOAD_ERRORS, resolve
from openpilot.tesla.ui.offroad import offroad_alerts
from openpilot.tesla.ui.pages.developer import DeveloperPage
from openpilot.tesla.ui.pages.device import DevicePage
from openpilot.tesla.ui.pages.home import HomePage
from openpilot.tesla.ui.pages.network import NetworkPage
from openpilot.tesla.ui.pages.plugins import PluginsPage
from openpilot.tesla.ui.pages.software import SoftwarePage
from openpilot.tesla.ui.pages.tesla import TeslaPage
from openpilot.tesla.ui.pages.toggles import TogglesPage
from openpilot.tesla.ui.tile import DIM, PAD, SUB, Tile, lines_in
from openpilot.tesla.ui.tiles import TileSpec, scale

SIZES = ("medium", "small", "wide")


class _Settings(Tile):
  ICON: str | None = None
  TRANSLATE_TITLE = True

  def lines(self, state) -> list[str]:
    """Live text under the top edge (medium and larger); state is what state() returned."""
    return []

  def draw(self, rect, size, state):
    self.face(rect, self.ICON)
    if size != "small":
      icon_top = (rect.height - int(min(rect.width, rect.height) * 0.36)) / 2 - 16  # face() centers the icon there
      box = rl.Rectangle(rect.x + PAD, rect.y + 14, rect.width - 2 * PAD, icon_top - 22)
      lines_in(box, self.lines(state), SUB * scale(rect.width, rect.height), FontWeight.MEDIUM, DIM)


class StatusTile(_Settings):
  SPEC = TileSpec("status", tr_noop("Status"), sizes=("medium", "wide", "small"), show="offroad", hz=0.5, order=0)
  PAGE = HomePage
  ICON = "icons_mici/alerts_bell.png"

  def state(self, sm):
    alerts = offroad_alerts(ui_state.params)
    return len(alerts), ui_state.params.get_bool("UpdateAvailable")

  def lines(self, state):
    count, update = state
    return [trn("{} alert", "{} alerts", count).format(count) if count else tr("No alerts")] + ([tr("Update available")] if update else [])


class TogglesTile(_Settings):
  SPEC = TileSpec("toggles", tr_noop("Toggles"), sizes=SIZES, show="offroad", order=1)
  PAGE = TogglesPage
  ICON = "icons_mici/settings.png"


class PluginsTile(_Settings):
  SPEC = TileSpec("plugins", tr_noop("Plugins"), sizes=SIZES, show="offroad", hz=0.5, order=2)
  PAGE = PluginsPage
  ICON = "icons/link.png"

  def state(self, sm):
    active, conflicts = resolve(ui_state.params)
    return len(active), len(conflicts), len(LOAD_ERRORS) + len(ui_state.params.get(FAULTS_PARAM) or {})

  def lines(self, state):
    active, conflicts, failed = state
    return [tr("{} on").format(active)] + ([trn("{} conflict", "{} conflicts", conflicts).format(conflicts)] if conflicts else []) + \
           ([tr("{} failed").format(failed)] if failed else [])


class TeslaTile(_Settings):
  SPEC = TileSpec("tesla", tr_noop("Tesla"), sizes=SIZES, show="offroad", order=3)
  PAGE = TeslaPage
  ICON = "icons_mici/wheel.png"


class NetworkTile(_Settings):
  SPEC = TileSpec("network", tr_noop("Network"), sizes=("small", "medium", "wide"), show="offroad", hz=0.5, order=5)
  PAGE = NetworkPage
  ICON = "icons_mici/settings/network/wifi_strength_full.png"

  def state(self, sm):
    return str(sm["deviceState"].networkType).replace("NetworkType.", "")

  def lines(self, state):
    return [state]


class DeviceTile(_Settings):
  SPEC = TileSpec("device", tr_noop("Device"), sizes=("small", "medium", "wide"), show="offroad", order=6)
  PAGE = DevicePage
  ICON = "icons_mici/settings/device_icon.png"


class SoftwareTile(_Settings):
  SPEC = TileSpec("software", tr_noop("Software"), sizes=("small", "medium", "wide"), show="offroad", hz=0.5, order=7)
  PAGE = SoftwarePage
  ICON = "icons_mici/settings/software.png"

  def state(self, sm):
    return ui_state.params.get_bool("UpdateAvailable")

  def lines(self, state):
    return [tr("Update available")] if state else []


class DeveloperTile(_Settings):
  SPEC = TileSpec("developer", tr_noop("Developer"), sizes=("small", "medium", "wide"), show="offroad", order=8)
  PAGE = DeveloperPage
  ICON = "icons_mici/settings/developer_icon.png"
