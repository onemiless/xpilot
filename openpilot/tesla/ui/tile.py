"""Tile base and the shared Windows Phone tile look (UI v4). Format (TileSpec) and layout live in tiles.py.
The host keeps one texture per tile and redraws it only when state() returns something new (or the size changes).
Text fits the tile by itself: fonts grow with the tile (tiles.scale), shrink to fit the width down to tiles.MIN_FONT, then
the text is cut with "..."; draw_lines drops the last lines that do not fit, so list the important ones first."""
from typing import ClassVar

import pyray as rl

from openpilot.system.ui.lib.application import FONT_SCALE, FontWeight, gui_app
from openpilot.system.ui.lib.multilang import tr
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.tesla.ui.tiles import ACCENT, MIN_FONT, TileSpec, fit_lines, scale

DIM = rl.Color(255, 255, 255, 170)
TITLE, UNIT, SUB = 34, 46, 42  # font sizes on a small tile, px
PAD = 16


def text_w(weight: FontWeight, text: str, size: int) -> float:
  return measure_text_cached(gui_app.font(weight), text, size).x


def text(weight: FontWeight, s: str, x: float, y: float, size: int, color=rl.WHITE) -> None:
  # gui_app scales rl.draw_text_ex by FONT_SCALE but not rl.measure_text_ex; undo it so size is real px and text_w is exact
  rl.draw_text_ex(gui_app.font(weight), s, rl.Vector2(x, y), size / FONT_SCALE, 0, color)


def clip(weight: FontWeight, s: str, size: int, w: float) -> str:
  """s, or its longest prefix plus "..." that fits w px."""
  if text_w(weight, s, size) <= w:
    return s
  while s and text_w(weight, s + "...", size) > w:
    s = s[:-1]
  return s + "..."


def fit(weight: FontWeight, s: str, size: float, w: float) -> int:
  """size, made smaller (not below MIN_FONT) until s fits w px."""
  return fit_lines([text_w(weight, s, 100) / 100], w, size, size)[0]


def lines_in(rect: rl.Rectangle, lines: list[str], cap: float, weight=FontWeight.SEMI_BOLD, color=rl.WHITE) -> None:
  """Left-aligned lines from the top of rect, sized and cut to fit it."""
  size, n = fit_lines([text_w(weight, s, 100) / 100 for s in lines], rect.width, rect.height, cap)
  for i, line in enumerate(lines[:n]):
    text(weight, clip(weight, line, size, rect.width), rect.x, rect.y + i * (size + 6), size, color)


class Tile:
  """Subclass in openpilot/tesla/ui/slots/ or in a plugin's ui.py (hidden while the plugin is off); must construct with no
  arguments. Tiles draw only; a tap offroad opens PAGE (a native page from tesla/ui/pages, or a mici layout when MICI)."""
  SPEC: ClassVar[TileSpec]
  PAGE: ClassVar[type | None] = None
  MICI: ClassVar[bool] = False
  COLOR: ClassVar[tuple[int, int, int]] = ACCENT  # the chosen tiles.THEMES color, set on Tile by the host
  TRANSLATE_TITLE: ClassVar[bool] = False

  def state(self, sm):
    """What the face shows, as a comparable value; polled at most SPEC.hz times per second. sm has ui_state services plus
    SPEC.services. The face is redrawn only when this changes."""
    return None

  def draw(self, rect: rl.Rectangle, size: str, state) -> None:
    """Draw the face into rect (texture px, origin 0,0); size is the placed size name."""
    self.face(rect)

  def title_size(self, rect: rl.Rectangle) -> int:
    return int(TITLE * (1 + scale(rect.width, rect.height)) / 2)  # 34 px small, 51 px medium and up

  def face(self, rect: rl.Rectangle, icon: str | None = None) -> None:
    """Square tile in COLOR with the title at the bottom left and an optional centered icon (assets path)."""
    rl.draw_rectangle_rec(rect, rl.Color(*self.COLOR, 255))
    if icon is not None:
      side = int(min(rect.width, rect.height) * 0.36)
      tex = gui_app.texture(icon, side, side)
      rl.draw_texture_ex(tex, rl.Vector2(rect.x + (rect.width - tex.width) / 2, rect.y + (rect.height - tex.height) / 2 - 16), 0, 1, rl.WHITE)
    size = self.title_size(rect)
    title = tr(self.SPEC.title) if self.TRANSLATE_TITLE else self.SPEC.title
    text(FontWeight.MEDIUM, clip(FontWeight.MEDIUM, title, size, rect.width - 2 * PAD), rect.x + PAD,
         rect.y + rect.height - size - 14, size)

  def draw_value(self, rect: rl.Rectangle, value: str, unit: str = "", sub: str = "") -> None:
    """Face, then one big value (with an optional unit after it) centered above the title, and an optional line below it."""
    self.face(rect)
    s = scale(rect.width, rect.height)
    room = rect.width - 2 * PAD
    sub_size = fit(FontWeight.MEDIUM, sub, SUB * s, room) if sub else 0
    below = sub_size + 8 if sub else 0
    bottom = rect.height - self.title_size(rect) - 28
    unit_size = int(UNIT * s)
    unit_w = text_w(FontWeight.MEDIUM, unit, unit_size) + 8 if unit else 0
    size = fit(FontWeight.BOLD, value, max(MIN_FONT, min(170 * s, bottom - below - 24)), room - unit_w)
    value_w = text_w(FontWeight.BOLD, value, size)
    y = rect.y + (bottom - size - below) / 2
    x = rect.x + (rect.width - value_w - unit_w) / 2
    text(FontWeight.BOLD, value, x, y, size)
    if unit:
      text(FontWeight.MEDIUM, unit, x + value_w + 8, y + size - unit_size - size * 0.12, unit_size, DIM)
    if sub:
      sub = clip(FontWeight.MEDIUM, sub, sub_size, room)
      text(FontWeight.MEDIUM, sub, rect.x + (rect.width - text_w(FontWeight.MEDIUM, sub, sub_size)) / 2, y + size + 8, sub_size, DIM)

  def draw_lines(self, rect: rl.Rectangle, lines: list[str], size: int = 40) -> None:
    """Face, then left-aligned lines from the top, at most size px on a small tile (scaled up on bigger ones); lines that do
    not fit above the title are dropped from the end."""
    self.face(rect)
    box = rl.Rectangle(rect.x + PAD, rect.y + PAD, rect.width - 2 * PAD, rect.height - self.title_size(rect) - 14 - 2 * PAD)
    lines_in(box, lines, size * scale(rect.width, rect.height))
