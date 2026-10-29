"""Tile base and the shared Windows Phone tile look (UI v4). Format (TileSpec) and layout live in tiles.py.
The host keeps one texture per tile and redraws it only when state() returns something new (or the size changes).
Text uses one type scale on every tile (VALUE, LINE, TITLE; x2 on medium and up) at fixed rows, so faces line up; a value
too wide shrinks (not below tiles.MIN_FONT) on the same baseline, lines are cut with "..." and dropped from the end."""
from typing import ClassVar

import pyray as rl

from openpilot.system.ui.lib.application import FONT_SCALE, FontWeight, gui_app
from openpilot.system.ui.lib.multilang import tr
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.tesla.ui.tiles import ACCENT, TileSpec, fit_lines, scale

DIM = rl.Color(255, 255, 255, 170)
TITLE, UNIT, SUB = 34, 46, 42  # font sizes on a small tile, px
# Onroad faces share one type scale and fixed rows so every tile reads the same (small tile px, x2 on medium and up):
VALUE, LINE, VALUE_TOP = 96, 36, 36  # big value, text lines, top of the value row
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
  DOCK: ClassVar[str] = ""  # "view": onroad drawn beside the drive view until dragged into the drive grid; lowest order wins

  def docked(self, state) -> bool:
    """DOCK tiles only: whether this state is worth drawing beside the view (a mici route panel is drawn regardless)."""
    return True

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

  def value_row(self, rect: rl.Rectangle, value: str, x: float, room: float, unit: str = "") -> float:
    """The shared big value (VALUE px, smaller only if too wide) with its baseline on the fixed value row; returns the row
    bottom, where the sub line starts. Left-aligned at x when room is given by the caller, so tiles line up."""
    s = scale(rect.width, rect.height)
    nominal, unit_size = int(VALUE * s), int(LINE * s)
    unit_w = text_w(FontWeight.MEDIUM, unit, unit_size) + 8 if unit else 0
    size = fit(FontWeight.BOLD, value, nominal, room - unit_w)
    top = rect.y + VALUE_TOP * s
    text(FontWeight.BOLD, value, x, top + nominal - size, size)  # a shrunk value keeps the shared baseline
    if unit:
      text(FontWeight.MEDIUM, unit, x + text_w(FontWeight.BOLD, value, size) + 8, top + nominal - unit_size * 1.15, unit_size, DIM)
    return top + nominal + 8 * s

  def sub_line(self, rect: rl.Rectangle, sub: str, x: float, y: float, room: float) -> None:
    size = int(LINE * scale(rect.width, rect.height))
    text(FontWeight.MEDIUM, clip(FontWeight.MEDIUM, sub, size, room), x, y, size, DIM)

  def draw_value(self, rect: rl.Rectangle, value: str, unit: str = "", sub: str = "") -> None:
    """Face, then one big value (with an optional unit after it) on the shared value row, and an optional line below it.
    Every tile uses the same sizes and rows (VALUE/LINE/VALUE_TOP)."""
    self.face(rect)
    x, room = rect.x + PAD, rect.width - 2 * PAD
    bottom = self.value_row(rect, value, x, room, unit)
    if sub:
      self.sub_line(rect, sub, x, bottom, room)

  def draw_lines(self, rect: rl.Rectangle, lines: list[str], size: int = LINE) -> None:
    """Face, then left-aligned lines from the top at the shared LINE size (size is ignored, kept for old plugins), cut
    with "..."; lines that do not fit above the title are dropped from the end."""
    self.face(rect)
    s = scale(rect.width, rect.height)
    size, room = int(LINE * s), rect.width - 2 * PAD
    bottom = rect.y + rect.height - self.title_size(rect) - 14
    for i, line in enumerate(lines):
      y = rect.y + PAD + i * (size + 6 * s)
      if y + size > bottom:
        break
      text(FontWeight.SEMI_BOLD, clip(FontWeight.SEMI_BOLD, line, size, room), rect.x + PAD, y, size)
