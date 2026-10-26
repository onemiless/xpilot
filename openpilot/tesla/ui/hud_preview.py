"""Static built-in HUD previews for the drive-layout editor."""
import pyray as rl

from openpilot.selfdrive.ui.mici.onroad.hud_renderer import FONT_SIZES
from openpilot.selfdrive.ui.mici.onroad.torque_bar import TorqueBar
from openpilot.system.ui.lib.application import FontWeight, gui_app
from openpilot.system.ui.lib.multilang import tr

_torque_bar = None


def draw(name: str, rect: rl.Rectangle) -> None:
  global _torque_bar
  if name == "steering_wheel":
    icon = gui_app.texture("icons_mici/wheel.png", 50, 50)
    x = int(rect.x + 21 + icon.width / 2)
    y = int(rect.y + rect.height - 14 - icon.height / 2)
    rl.draw_texture_pro(icon, rl.Rectangle(0, 0, icon.width, icon.height), rl.Rectangle(x, y, icon.width, icon.height),
                        rl.Vector2(icon.width / 2, icon.height / 2), 0.0, rl.Color(255, 255, 255, 230))
  elif name == "model_source":
    icon = gui_app.texture("icons_mici/chestnut_green.png", 60, 44)
    pos = rl.Vector2(rect.x + rect.width - 10 - icon.width, rect.y + rect.height - 14 - (50 + icon.height) / 2)
    rl.draw_texture_ex(icon, pos, 0.0, 1.0, rl.WHITE)
  elif name == "set_speed":
    x, y, radius = rect.x, rect.y, 162 // 2
    rl.draw_circle_gradient(rl.Vector2(x + radius, y + radius), radius, rl.Color(0, 0, 0, 127), rl.BLANK)
    color = rl.Color(255, 255, 255, 230)
    rl.draw_text_ex(gui_app.font(FontWeight.DISPLAY), "60", rl.Vector2(x + 17, y - 4), FONT_SIZES.set_speed, 0, color)
    rl.draw_text_ex(gui_app.font(FontWeight.SEMI_BOLD), tr("MAX"), rl.Vector2(x + 25, y + FONT_SIZES.set_speed - 3),
                    FONT_SIZES.max_speed, 0, color)
  elif name == "blind_spot_indicators":
    left = gui_app.texture("icons_mici/onroad/blind_spot_left.png", 108, 128)
    right = gui_app.texture("icons_mici/onroad/blind_spot_left.png", 108, 128, flip_x=True)
    y = int(rect.y + 100)
    rl.draw_texture_ex(left, rl.Vector2(int(rect.x + 20), y), 0.0, 1.0, rl.WHITE)
    rl.draw_texture_ex(right, rl.Vector2(int(rect.x + rect.width - 20 - right.width), y), 0.0, 1.0, rl.WHITE)
  elif name == "torque_bar":
    if _torque_bar is None:
      _torque_bar = TorqueBar(demo=True)
    _torque_bar.update_filter(0.0)
    _torque_bar.render(rect)
