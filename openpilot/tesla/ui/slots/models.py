"""Models tile of the SP model manager; hidden while another plugin owns role:perception.model."""
from openpilot.tesla.ui.pages.models import ModelsPage
from openpilot.tesla.ui.slots.settings import _Settings
from openpilot.tesla.ui.tiles import TileSpec
from openpilot.system.ui.lib.multilang import tr_noop


class ModelsTile(_Settings):
  SPEC = TileSpec("models", tr_noop("Models"), sizes=("small", "medium", "wide"), show="offroad", order=4)
  PAGE = ModelsPage
  ICON = "icons_mici/chestnut.png"
