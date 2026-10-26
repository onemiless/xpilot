"""Core G8 owner indication in the shared big-screen/mici HUD overlay path."""
import pyray as rl

from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import FontWeight, gui_app
from openpilot.tesla.ui.plugin_ui import HudOverlay


class LongOwnerChip(HudOverlay):
  def __init__(self):
    super().__init__()
    self.state = None

  def default_rect(self, view):
    return rl.Rectangle(view.x + view.width - 140, view.y + 8, 132, 24)

  def update(self):
    sm = ui_state.sm
    cc = sm['carControlSP']
    self.state = cc if sm.alive['carControlSP'] and sm.valid['carControlSP'] and cc.longOwnerAvailable else None

  def _render(self, rect):
    if self.state is None:
      return
    cc = self.state
    font = gui_app.font(FontWeight.MEDIUM)
    color = rl.ORANGE if cc.longOwnerRefusal else rl.WHITE
    rl.draw_rectangle_rounded(rect, .3, 4, rl.Color(0, 0, 0, 150))
    # one short line: owner, ON while it controls, and the refusal reason only while there is one
    label = ("SP" if str(cc.longOwner) == "sp" else "STOCK") + (" ON" if cc.longActive else "")
    if cc.longOwnerRefusal:
      label += " " + str(cc.longOwnerRefusal)[:12]
    rl.draw_text_ex(font, label, rl.Vector2(rect.x + 6, rect.y + 3), 18, 0, color)
