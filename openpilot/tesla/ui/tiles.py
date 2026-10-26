"""Tile layout model of the big-screen UI v4 (XPILOT.md §9): grid, tile sizes, first-fit placement around the drive view,
the saved layout, edit operations, touch gestures and the redraw decision. No pyray here, so
tools/sp_tesla_e2e/tile_layout_check.py runs it on any host.

Grid: COLS x ROWS cells of CELL px with GAP px between them, centered on the 2160x1080 screen. A tile covers SIZES[size]
cells (cols, rows). There are two layouts, "park" and "drive", each a list of (id, size, fixed position or None).
Tiles with a fixed position go there first; the rest go, in list order, to the first position that fits: parked scanning
column by column (Win 8 landscape), driving row by row from both edges inward (left and right fill alternately).
Onroad the cells under the mici driving view (DRIVE_CELLS) are blocked and a tile shrinks to a smaller supported size
when its saved size does not fit."""
from dataclasses import dataclass, field
from typing import Literal

SCREEN = (2160, 1080)
COLS, ROWS = 8, 4
CELL, GAP = 250, 12
PITCH = CELL + GAP
ORIGIN = ((SCREEN[0] - COLS * PITCH + GAP) // 2, (SCREEN[1] - ROWS * PITCH + GAP) // 2)
SIZES = {"small": (1, 1), "medium": (2, 2), "wide": (4, 2), "large": (4, 4)}
THEMES = {"Cobalt": (0, 80, 239), "Teal": (0, 140, 140), "Emerald": (0, 128, 0), "Crimson": (162, 0, 37), "Violet": (106, 0, 255),
          "Graphite": (52, 56, 66), "Black": (0, 0, 0)}  # tile colors (WP accents); every tile uses the chosen one
ACCENT = THEMES["Cobalt"]

DRIVE_CELLS = (1, 0, 6, 3)  # col, row, cols, rows under the mici driving view
MICI = (536, 240)
LAYOUT_PARAM = "XpilotTileLayout"
layout_version = 0  # bumped on every layout save in this process; HUD readers reload on change
THEME_PARAM = "XpilotTileTheme"  # a THEMES name
LAYOUT_SPEC = {LAYOUT_PARAM: {"type": "JSON"}, THEME_PARAM: {"type": "STRING"}}  # kept by plugin_params.ParamsView as files, not compiled params

LONG_PRESS = 0.6  # s held without moving: enters edit mode
SLOP = 24         # px a press may move and still tap / long-press


def area(size: str) -> int:
  w, h = SIZES[size]
  return w * h


def size_rect(col: int, row: int, cells: tuple[int, int]) -> tuple[int, int, int, int]:
  """Screen px (x, y, w, h) of a block of cells (cols, rows) anchored at (col, row)."""
  return ORIGIN[0] + col * PITCH, ORIGIN[1] + row * PITCH, cells[0] * PITCH - GAP, cells[1] * PITCH - GAP


def cell_rect(col: int, row: int) -> tuple[int, int, int, int]:
  return size_rect(col, row, (1, 1))


def tile_rect(placed: tuple[int, int, str]) -> tuple[int, int, int, int]:
  col, row, size = placed
  return size_rect(col, row, SIZES[size])


def view(position=DRIVE_CELLS[:2]) -> tuple[int, int, int, int]:
  x, y, w, h = size_rect(*position, DRIVE_CELLS[2:])
  scale = min(w / MICI[0], h / MICI[1])
  vw, vh = round(MICI[0] * scale), round(MICI[1] * scale)
  return x + (w - vw) // 2, y + (h - vh) // 2, vw, vh


VIEW = view()  # default mici view, 536:240 centered in DRIVE_CELLS


def view_position(saved) -> tuple[int, int]:
  """Saved drive-view cell; malformed or out-of-grid input uses the centered default."""
  drive = saved.get("drive") if isinstance(saved, dict) else None
  pos = drive.get("view") if isinstance(drive, dict) else None
  if (isinstance(pos, list) and len(pos) == 2 and all(type(v) is int for v in pos)
      and 0 <= pos[0] <= COLS - DRIVE_CELLS[2] and 0 <= pos[1] <= ROWS - DRIVE_CELLS[3]):
    return tuple(pos)
  return DRIVE_CELLS[:2]


def hud_layout(saved) -> dict:
  """Valid saved plugin HUD fractions and visibility; invalid entries use the widget default."""
  drive = saved.get("drive") if isinstance(saved, dict) else None
  hud = drive.get("hud") if isinstance(drive, dict) else None
  if not isinstance(hud, dict):
    return {}
  return {name: [*entry[:2], entry[2] or name in HUD_REQUIRED] for name, entry in hud.items() if isinstance(name, str) and isinstance(entry, list)
          and len(entry) == 3 and all(type(v) in (int, float) and 0 <= v <= 1 for v in entry[:2]) and type(entry[2]) is bool}


def hud_rect(entry, default, view) -> tuple[float, float, float, float]:
  """Clamp a HUD rectangle to its view; saved fractions locate its top left."""
  vx, vy, vw, vh = view
  x, y, w, h = default
  w, h = min(w, vw), min(h, vh)
  if entry is not None:
    x, y = vx + entry[0] * vw, vy + entry[1] * vh
  return min(max(x, vx), vx + vw - w), min(max(y, vy), vy + vh - h), w, h


HUD_REQUIRED = {"builtin:steering_wheel", "builtin:model_source", "builtin:blind_spot_indicators"}


def hud_buttons(boxes, view) -> dict:
  """Hide/Show corners, nearest free position when HUD boxes overlap."""
  vx, vy, vw, vh = view
  bw, bh, gap = 144, 64, 8
  out = {}
  for name, (x, y, w, h) in boxes.items():
    if name in HUD_REQUIRED:
      continue
    bx = min(max(x + w - bw - gap, vx), vx + vw - bw)
    by = y + gap if w >= bw + 2 * gap and h >= bh + 2 * gap else y - bh - gap
    if by < vy:
      by = y + h + gap
    by = min(by, vy + vh - bh)
    xs = {bx, vx, vx + vw - bw}
    ys = {by, vy, vy + vh - bh}
    for px, py, pw, ph in out.values():
      xs.update((px - bw - gap, px + pw + gap))
      ys.update((py - bh - gap, py + ph + gap))
    candidates = sorted(((cx, cy) for cx in xs for cy in ys if vx <= cx <= vx + vw - bw and vy <= cy <= vy + vh - bh),
                        key=lambda p: (abs(p[0] - bx) + abs(p[1] - by), p[1], p[0]))
    cx, cy = next(((cx, cy) for cx, cy in candidates
                  if all(cx + bw <= px or px + pw <= cx or cy + bh <= py or py + ph <= cy for px, py, pw, ph in out.values())), (bx, by))  # no free spot: keep the corner, never crash the editor
    out[name] = (cx, cy, bw, bh)
  return out


def builtin_hud_boxes(view) -> dict:
  """Current mici footprints; safety indications may move but never hide."""
  x, y, w, h = view
  return {"builtin:blind_spot_indicators": (x + 20, y + 100, w - 40, 128),
          "builtin:torque_bar": (x + w / 2 - 155, y + h - 90, 310, 90),
          "builtin:set_speed": (x, y, 192, 162),
          "builtin:steering_wheel": (x + 10, y + h - 100, 110, 100),
          "builtin:model_source": (x + w - 85, y + h - 66, 75, 52)}


MIN_FONT = 24  # px; smaller is not readable from the driver's seat


def scale(w: float, h: float) -> float:
  """Font scale of a tile face: 1 for a small tile, 2 for medium and up (the short side sets it)."""
  return min(2.0, max(1.0, min(w, h) / CELL))


def fit_lines(widths: list[float], w: float, h: float, cap: float, gap: float = 6) -> tuple[int, int]:
  """(font px, lines shown) for lines stacked in a w x h box; widths are each line's width per font px. The font is the
  largest <= cap at which every shown line fits the width and all fit the height, but not below MIN_FONT: a line still too
  wide there is cut by the caller, and lines that do not fit the height are dropped from the end (put the important first)."""
  if not widths:
    return int(cap), 0
  n = len(widths)
  while n > 1 and (h + gap) / n - gap < MIN_FONT:
    n -= 1
  by_width = min((w / k for k in widths[:n] if k > 0), default=cap)
  return int(max(MIN_FONT, min(cap, (h + gap) / n - gap, by_width))), n


def blocked(started: bool, view_pos=DRIVE_CELLS[:2]) -> set[tuple[int, int]]:
  if not started:
    return set()
  c, r = view_pos
  w, h = DRIVE_CELLS[2:]
  return {(col, row) for col in range(c, c + w) for row in range(r, r + h)}


@dataclass(frozen=True)
class TileSpec:
  id: str                                   # unique
  title: str                                # drawn at the bottom left of the tile
  sizes: tuple[str, ...] = ("small", "medium")  # supported sizes, the first is the default; onroad needs "small"
  services: tuple[str, ...] = ()            # every service state() reads; empty means it reads ui_state.sm
  show: Literal["always", "onroad", "offroad"] = field(kw_only=True)
  hz: float = 10.0                          # max state() polls per second; the face is redrawn only when state changes
  order: int = 100                          # default position, lower first

  def __post_init__(self):
    if not self.sizes or any(s not in SIZES for s in self.sizes):
      raise ValueError(f"tile {self.id}: sizes {self.sizes} not in {tuple(SIZES)}")
    if self.show not in ("always", "onroad", "offroad"):
      raise ValueError(f"tile {self.id}: unknown show {self.show}")
    if not self.hz > 0:
      raise ValueError(f"tile {self.id}: hz must be > 0")


def shows(spec: TileSpec, started: bool) -> bool:
  return spec.show == "always" or (spec.show == "onroad") == started


def scan(started: bool) -> list[tuple[int, int]]:
  """Cell order for tiles without a fixed position."""
  if not started:
    return [(c, r) for c in range(COLS) for r in range(ROWS)]
  edges = [c for i in range(COLS // 2) for c in (i, COLS - 1 - i)]  # 0, 7, 1, 6, ...
  return [(c, r) for r in range(ROWS) for c in edges]


def _cells(col: int, row: int, size: str) -> set[tuple[int, int]] | None:
  w, h = SIZES[size]
  if col < 0 or row < 0 or col + w > COLS or row + h > ROWS:
    return None
  return {(col + i, row + j) for i in range(w) for j in range(h)}


def place(items: list[tuple[str, str, tuple[str, ...], tuple[int, int] | None]], started: bool, view_pos=DRIVE_CELLS[:2]) -> dict[str, tuple[int, int, str]]:
  """items: (id, saved size, supported sizes, fixed position or None) in list order. Returns id -> (col, row, size) for
  every tile that fits. Fixed tiles first (a position off the grid, on the drive view or on an earlier tile is ignored),
  then the rest in scan order. Onroad a tile tries its saved size, then each smaller supported size, largest first."""
  taken = set(blocked(started, view_pos))
  out = {}

  def sizes_for(saved, sizes):
    return [saved] if not started else sorted((s for s in sizes if area(s) <= area(saved)), key=lambda s: -area(s))

  def take(tid, pos, size) -> bool:
    cells = _cells(*pos, size)
    if cells is None or cells & taken:
      return False
    taken.update(cells)
    out[tid] = (*pos, size)
    return True
  flowing = [(tid, saved, sizes) for tid, saved, sizes, fixed in items
             if fixed is None or not any(take(tid, fixed, s) for s in sizes_for(saved, sizes))]
  order = scan(started)
  for tid, saved, sizes in flowing:
    any(take(tid, pos, size) for size in sizes_for(saved, sizes) for pos in order)
  return out


def cell_at(x: float, y: float) -> tuple[int, int] | None:
  """Grid cell under a screen point; a gap belongs to the cell left of / above it."""
  col, row = int((x - ORIGIN[0]) // PITCH), int((y - ORIGIN[1]) // PITCH)
  return (col, row) if 0 <= col < COLS and 0 <= row < ROWS else None


def hit(placed: dict[str, tuple[int, int, str]], x: float, y: float) -> str | None:
  for tid, p in placed.items():
    rx, ry, rw, rh = tile_rect(p)
    if rx <= x < rx + rw and ry <= y < ry + rh:
      return tid
  return None


BAR_NAMES = ("park", "drive", "done")


def bar_buttons(placed: dict[str, tuple[int, int, str]], started: bool, view_pos=DRIVE_CELLS[:2]) -> dict[str, tuple[int, int, int, int]]:
  """Seat-sized buttons over four cells of an edge row, never the Driving view; three never fit stacked in one cell. The
  bar reserves no cells: it takes the span covering the fewest tiles and the "+" cell (bottom middle first)."""
  w, h, gap = 320, 160, 10
  rows = [0 if view_pos[1] else ROWS - 1] if started else [ROWS - 1, 0]
  used = {c for col, row, size in placed.values() for c in _cells(col, row, size)}
  add = add_cell(placed, started, view_pos)
  used |= {add} if add else set()
  span = {(c, r) for r in range(ROWS) for c in range(COLS)} - blocked(started, view_pos)
  options = [(row, c0) for row in rows for c0 in (2, 1, 3, 0, 4) if {(c0 + i, row) for i in range(4)} <= span]
  row, c0 = min(options, key=lambda o: len({(o[1] + i, o[0]) for i in range(4)} & used))
  x0 = cell_rect(c0, row)[0] + (4 * PITCH - GAP - 3 * w - 2 * gap) // 2
  y = cell_rect(c0, row)[1] + (CELL - h) // 2
  return {name: (x0 + i * (w + gap), y, w, h) for i, name in enumerate(BAR_NAMES)}


def corner_buttons(rect) -> dict[str, tuple[float, float, float]]:
  """Edit mode circles (cx, cy, r) on the selected tile: unpin top right, resize bottom right."""
  x, y, w, h = rect
  r = 30
  return {"unpin": (x + w - r - 8, y + r + 8, r), "resize": (x + w - r - 8, y + h - r - 8, r)}


def add_cell(placed: dict[str, tuple[int, int, str]], started: bool, view_pos=DRIVE_CELLS[:2]) -> tuple[int, int] | None:
  """First free cell (column-major): edit mode draws the "+" (add tiles) there."""
  taken = set(blocked(started, view_pos))
  for col, row, size in placed.values():
    w, h = SIZES[size]
    taken |= {(col + i, row + j) for i in range(w) for j in range(h)}
  return next(((c, r) for c in range(COLS) for r in range(ROWS) if (c, r) not in taken), None)


# saved layout: {"park": {"tiles": [[id, size, col, row], ...], "hidden": [id, ...]}, "drive": {...}}; col/row None = not fixed.
# v1 {"tiles": [[id, size]], "hidden": [...]} is the park layout.

def arrange(specs: list[TileSpec], saved, mode: str) -> tuple[list[tuple[str, str, tuple[int, int] | None]], list[str]]:
  """(pinned [(id, size, fixed)] in order, hidden ids) of one layout ("park"/"drive") from the saved layout; specs are the
  tiles that belong to it. Unknown ids, unsupported sizes, bad positions, duplicates and malformed entries are dropped;
  tiles in neither list are appended in (order, id) order at their default size."""
  specs = [s for s in specs if shows(s, mode == "drive")]
  by_id = {s.id: s for s in specs}
  saved = saved if isinstance(saved, dict) else {}
  layout = saved.get(mode) if mode in saved else (saved if mode == "park" and "tiles" in saved else {})
  layout = layout if isinstance(layout, dict) else {}
  tiles = layout.get("tiles") if isinstance(layout.get("tiles"), list) else []
  hidden_in = layout.get("hidden") if isinstance(layout.get("hidden"), list) else []
  pinned, seen = [], set()
  for entry in tiles:
    if not (isinstance(entry, list) and len(entry) in (2, 4) and entry[0] in by_id) or entry[0] in seen:
      continue
    tid, size = entry[:2]
    fixed = tuple(entry[2:]) if len(entry) == 4 and all(type(v) is int for v in entry[2:]) else None
    seen.add(tid)
    pinned.append((tid, size if size in by_id[tid].sizes else by_id[tid].sizes[0], fixed))
  hidden = []
  for tid in hidden_in:
    if isinstance(tid, str) and tid in by_id and tid not in seen:
      seen.add(tid)
      hidden.append(tid)
  pinned += [(s.id, s.sizes[0], None) for s in sorted(specs, key=lambda s: (s.order, s.id)) if s.id not in seen]
  return pinned, hidden


def saved(pinned, hidden: list[str]) -> dict:
  return {"tiles": [[tid, size, *(fixed or (None, None))] for tid, size, fixed in pinned], "hidden": list(hidden)}


def resize(pinned, tid: str, sizes: tuple[str, ...]):
  """Next size in the tile's sizes, wrapping; the position stays."""
  return [(i, sizes[(sizes.index(s) + 1) % len(sizes)] if s in sizes else sizes[0], f) if i == tid else (i, s, f) for i, s, f in pinned]


def unpin(pinned, hidden, tid: str):
  return [e for e in pinned if e[0] != tid], [*hidden, tid]


def pin(pinned, hidden, tid: str, sizes: tuple[str, ...]):
  """Hidden or left-out tile to the end of the list at its smallest size, not fixed, so it fits wherever a cell is free."""
  smallest = min(sizes, key=area)
  return [e for e in pinned if e[0] != tid] + [(tid, smallest, None)], [i for i in hidden if i != tid]


def move(pinned, placed: dict[str, tuple[int, int, str]], tid: str, cell: tuple[int, int], started: bool, view_pos=DRIVE_CELLS[:2]):
  """Drop tid on cell: its top left is fixed there (pulled back inside the grid); other fixed tiles it overlaps are no
  longer fixed. A drop that would cover the drive view changes nothing. List order stays."""
  size = placed[tid][2] if tid in placed else next(s for i, s, _ in pinned if i == tid)
  w, h = SIZES[size]
  pos = (min(max(cell[0], 0), COLS - w), min(max(cell[1], 0), ROWS - h))
  cells = _cells(*pos, size)
  if cells & blocked(started, view_pos):
    return list(pinned)
  out = []
  for i, s, f in pinned:
    if i == tid:
      out.append((i, s, pos))
    elif f is not None and i in placed and placed[i][:2] == f and _cells(*placed[i]) & cells:
      out.append((i, s, None))
    else:
      out.append((i, s, f))
  return out


def addable(pinned, hidden, placed) -> list[str]:
  """The "Add tiles" list: hidden tiles, then visible pinned tiles left out for lack of room."""
  return list(hidden) + [e[0] for e in pinned if e[0] not in placed]


class Gesture:
  """One finger on the tile screen: press/move/release feed it screen points; tick(now) runs every frame.
  Events: "tap" (released before LONG_PRESS, moved < SLOP), "long" (held LONG_PRESS without moving, once),
  "drag" (moved >= SLOP, once), "drop" (released after a drag)."""

  def __init__(self):
    self.start = None  # (x, y, t) of the press
    self.pos = None
    self.long = self.drag = False

  def press(self, x, y, t):
    self.start, self.pos, self.long, self.drag = (x, y, t), (x, y), False, False

  def move(self, x, y, t):
    if self.start is None:
      return None
    self.pos = (x, y)
    if not self.drag and (x - self.start[0]) ** 2 + (y - self.start[1]) ** 2 >= SLOP ** 2:
      self.drag = True
      return "drag"
    return None

  def tick(self, now):
    if self.start is None or self.long or self.drag or now - self.start[2] < LONG_PRESS:
      return None
    self.long = True
    return "long"

  def release(self, x, y, t):
    if self.start is None:
      return None
    event = self.move(x, y, t)
    self.start = None
    if self.drag or event:
      return "drop"
    return None if self.long else "tap"


class Poll:
  """Per-tile redraw decision: state() is polled at most hz times a second, the face redrawn only when the polled state
  or the size key differs from what the cached texture shows."""
  _UNSET = object()

  def __init__(self, hz: float):
    self.period, self.next = 1.0 / hz, float("-inf")
    self.shown = self._UNSET

  def due(self, now: float) -> bool:
    if now < self.next - 1e-3 * self.period:
      return False
    self.next = now + self.period
    return True

  def changed(self, state, key) -> bool:
    if self.shown is not self._UNSET and self.shown == (state, key):
      return False
    self.shown = (state, key)
    return True

  def invalidate(self) -> None:
    self.shown = self._UNSET
