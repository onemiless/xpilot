"""Onroad Settings tile: a tap shows the park screen, where plugin switches and settings stay editable while driving."""
from openpilot.system.ui.lib.multilang import tr_noop
from openpilot.tesla.ui.tile import Tile
from openpilot.tesla.ui.tiles import TileSpec


class ParkTile(Tile):
  SPEC = TileSpec("park_view", tr_noop("Settings"), sizes=("small",), show="onroad", order=9)
  TRANSLATE_TITLE = True
  PARK = True

  def draw(self, rect, size, state):
    self.face(rect, "icons_mici/settings.png")
