"""Core G8 owner tile, placed around the drive view like any tile (hide it in the editor); never drawn on the video."""
from openpilot.tesla.ui.tile import Tile
from openpilot.tesla.ui.tiles import TileSpec


def owner_state(sm) -> tuple[str, str, str]:
  """(owner, ON while it controls, refusal) from a live, valid carControlSP with the panda owner ABI; else a dash."""
  cc = sm['carControlSP']
  if not (sm.alive['carControlSP'] and sm.valid['carControlSP'] and cc.longOwnerAvailable):
    return "-", "", "no owner ABI"
  return ("SP" if str(cc.longOwner) == "sp" else "STOCK"), ("ON" if cc.longActive else ""), str(cc.longOwnerRefusal)[:16]


class LongOwnerTile(Tile):
  SPEC = TileSpec("long_owner", "Long owner", sizes=("small", "wide"), services=("carControlSP",), show="onroad", hz=5, order=15)

  def state(self, sm):
    return owner_state(sm)

  def draw(self, rect, size, state):
    self.draw_value(rect, *state)
