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
    return rl.Rectangle(view.x + 195, view.y + 8, 270, 52)

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
    rl.draw_rectangle_rounded(rect, .2, 4, rl.Color(0, 0, 0, 190))
    rl.draw_text_ex(font, f"{str(cc.longOwner).upper()} | {'ACTIVE' if cc.longActive else 'IDLE'}",
                    rl.Vector2(rect.x + 8, rect.y + 3), 22, 0, color)
    rl.draw_text_ex(font, (cc.longOwnerRefusal or 'OK')[:30], rl.Vector2(rect.x + 8, rect.y + 29), 16, 0, color)
