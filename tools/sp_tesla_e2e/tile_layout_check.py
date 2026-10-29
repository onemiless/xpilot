#!/usr/bin/env python3
"""Big-screen tile check (UI v4, XPILOT.md §9): the pyray-free layout model in openpilot/tesla/ui/tiles.py, plus the text the
tile UI draws. Rendering and real touches are checked on the device by tools/sp_tesla_e2e/big_screen_e2e.py.

Failure cases listed before implementation:
- T1  grid cells overlap, the gap is not GAP px, or the grid leaves the screen / is not centered
- T2  two placed tiles overlap
- T3  a tile is placed outside the grid or on a cell the drive view blocks
- T4  a tile is left out while a position (in first-fit scan order) still fits it, or is placed after a free position that
      fits it (placement is not first-fit, or depends on anything but the list); a tile with a valid fixed position is not
      placed there, or a fixed position that is off the grid, on a drive view cell or on an earlier tile is not ignored
- T16 onroad the free cells are not scanned row by row from both edges inward (left and right columns fill alternately);
      offroad not column by column
- T5  offroad a tile is placed at another size than its saved size; onroad at a larger size than saved, or a size it does
      not support
- T6  the drive VIEW leaves the blocked cells, is not 536:240, is not centered in them, or onroad free cells are not 14
- T7  offroad-only tiles show onroad or onroad-only tiles show offroad
- T8  loading a saved layout: garbage gives no defaults, unknown ids, unsupported sizes or bad positions survive, an id
      appears twice, new tiles are not appended in (order, id) order at their default size, hidden tiles show, the park
      and drive layouts are not kept apart, the v1 format ({"tiles": [[id, size]]}) is not read as the park layout, or
      save/load does not round-trip
- T9  edit ops: resize does not cycle through the tile's sizes or drops the position, unpin/pin do not move between pinned
      and hidden, a drop does not fix the tile at the drop cell (pulled back inside the grid), does not free other fixed
      tiles it now overlaps, moves a tile onto the drive view, or reorders the list
- T17 edit bar: Park, Drive and Done buttons overlap each other or leave the screen; the default bar covers a tile or
      the add cell while a clear four-cell edge span exists; a moved bar is not clamped fully on screen
- T33 edit bar reserves cells (owner 2026-10-26): edit mode places tiles differently from normal mode, a tile dropped
      on a cell under the bar does not land there, or the add cell skips a free cell under the bar
- T10 hit tests: a gap hits a tile, a cell center does not map back to its cell, corner buttons leave their tile or
      overlap each other
- T11 gestures: a short press without movement is not a tap, a held press is not one long press, a moved press still
      taps or long-presses, or a drag does not end in one drop
- T12 TileSpec accepts no sizes, an unknown size, an unknown show value or hz <= 0
- T13 tile UI text (string constants in openpilot/tesla/ui and plugins' ui.py, plugin.json names, descriptions and setting
      labels) has non-ASCII characters (the device font lacks some, e.g. ‹ › · —, and draws "?")
- T14 edit mode: no "+" cell when tiles are hidden or left out and the grid has room, or the add list misses a hidden or
      left-out tile
- T15 a tile face is redrawn while its state and size are unchanged, not redrawn after either changes, or state() is
      polled more than hz times a second
- T18 tile text does not fit its tile: the font scale is not 1 on a small tile and 2 on bigger ones, a fitted font
      leaves the box width or height while above MIN_FONT, goes over its cap or under MIN_FONT, drops a line that fits,
      or keeps one that does not
- T19 saved drive view position is lost, or a missing/garbage/out-of-range position does not use the default
- T20 a view drag drops off screen or between cells; tiles, add cell, edit bar or overlay zones use the old position
- T21 a fixed tile covered by the moved view overlaps it instead of flowing into a free cell
- T22 an onroad tap or drag starting outside the view changes layout/pages or reaches a mici widget
- T23 HUD fractions/visibility/scale do not round-trip, old 3-value entries stop loading, or a bad scale loses position
- T24 a dragged/scaled HUD box leaves the view, changes aspect ratio, or its relative position changes when the view moves
- T25 TileSpec accepts a missing show, or a drive/park add list offers tiles belonging only to the other mode
- T26 adding, dropping or fixing a tile can occupy any cell inside any supported drive-view position
- T27 an existing theme changes when adding the black theme (Outline renamed Black by T30)
- T28 edit buttons are under 152 px high, overlap a tile/add cell/view, or leave the screen
- T29 built-in HUD defaults/fractions are lost, invalid entries survive, or protected indicators can hide
- T30 Black is missing or old Outline does not resolve to Black
- T31 a selected HUD box lacks resize controls, or a required box has a visibility toggle
- T32 HUD controls leave the screen, overlap their own box, or overlap each other
- T34 for any drive view position (owner 2026-10-28) the alert band overlaps the view, leaves the screen, is not as wide as
      the view, does not touch the view's top or bottom edge, or is not on the side (above/below) with more room; the
      navigation column overlaps the view, leaves the screen, is not as tall as the view, does not touch its left or right
      edge, or is not on the side with more room; onroad tiles still give up cells for either
- T35 moving the drive view (owner 2026-10-29) covers a tile, moves a tile it does not cover, or drops a tile while the
      cells the view left are free
- T36 navigation (owner 2026-10-29) is not beside the view by default or for a malformed saved flag, or a saved
      "moved into the grid" is not read
"""
import ast
import itertools
import json
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from openpilot.tesla.ui import tiles as T  # noqa: E402

ALL = tuple(T.SIZES)


def overlap(a, b) -> bool:
  return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


def inside(r, z) -> bool:
  return r[0] >= z[0] and r[1] >= z[1] and r[0] + r[2] <= z[0] + z[2] and r[1] + r[3] <= z[1] + z[3]


def cells(col, row, size):
  w, h = T.SIZES[size]
  return {(c, r) for c in range(col, col + w) for r in range(row, row + h)}


def check_grid(errors):
  rects = [T.cell_rect(c, r) for c in range(T.COLS) for r in range(T.ROWS)]
  errors += [f"T1 cells overlap {a} {b}" for a, b in itertools.combinations(rects, 2) if overlap(a, b)]
  if T.cell_rect(1, 0)[0] - (T.cell_rect(0, 0)[0] + T.CELL) != T.GAP or T.cell_rect(0, 1)[1] - (T.cell_rect(0, 0)[1] + T.CELL) != T.GAP:
    errors.append("T1 gap")
  x, y, w, h = T.size_rect(0, 0, (T.COLS, T.ROWS))
  if not inside((x, y, w, h), (0, 0, *T.SCREEN)) or abs(x - (T.SCREEN[0] - x - w)) > 1 or abs(y - (T.SCREEN[1] - y - h)) > 1:
    errors.append(f"T1 grid {(x, y, w, h)} not centered on screen")
  for c, r in itertools.product(range(T.COLS), range(T.ROWS)):
    x, y, w, h = T.cell_rect(c, r)
    if T.cell_at(x + w / 2, y + h / 2) != (c, r):
      errors.append(f"T10 cell ({c},{r}) center maps to {T.cell_at(x + w / 2, y + h / 2)}")


def check_view(errors):
  bx, by, bw, bh = T.DRIVE_CELLS
  area = T.size_rect(bx, by, (bw, bh))
  vx, vy, vw, vh = T.VIEW
  if not inside(T.VIEW, area):
    errors.append(f"T6 VIEW {T.VIEW} outside drive cells {area}")
  if abs(vw / vh - 536 / 240) > 0.01 * 536 / 240:
    errors.append(f"T6 VIEW aspect {vw / vh:.3f}")
  if abs((vx - area[0]) - (area[0] + area[2] - vx - vw)) > 1 or abs((vy - area[1]) - (area[1] + area[3] - vy - vh)) > 1:
    errors.append("T6 VIEW not centered in its cells")
  for c, r in itertools.product(range(T.COLS), range(T.ROWS)):
    if overlap(T.cell_rect(c, r), T.VIEW) and (c, r) not in T.blocked(True):
      errors.append(f"T6 VIEW overlaps free cell ({c},{r})")
  free = T.COLS * T.ROWS - len(T.blocked(True))
  if free != 14 or T.blocked(False):
    errors.append(f"T6 onroad free cells {free}, offroad blocked {T.blocked(False)}")


def first_fit_ok(errors, items, out, started, tag):
  """Independent replay: fixed tiles first, then first fit in scan order; also checks overlaps, bounds and sizes."""
  grid = {(c, r) for c in range(T.COLS) for r in range(T.ROWS)}
  taken = set(T.blocked(started))
  want = {}

  def tries(saved, sizes):
    return [saved] if not started else [s for s in sorted(sizes, key=lambda s: -T.area(s)) if T.area(s) <= T.area(saved)]
  flowing = []
  for tid, saved, sizes, anchor in items:
    got = None
    if anchor is not None:
      got = next(((*anchor, s) for s in tries(saved, sizes) if cells(*anchor, s) <= grid and not cells(*anchor, s) & taken), None)
    if got is None:
      flowing.append((tid, saved, sizes))
    else:
      want[tid] = got
      taken |= cells(*got)
  order = scan_order(started)
  for tid, saved, sizes in flowing:
    for size in tries(saved, sizes):
      pos = next((p for p in order if cells(*p, size) <= grid and not cells(*p, size) & taken), None)
      if pos is not None:
        want[tid] = (*pos, size)
        taken |= cells(*pos, size)
        break
  if out != want:
    errors.append(f"T4 {tag}: placed {out}, expected {want}")
    return
  for tid, saved, sizes, _ in items:
    got = out.get(tid)
    if got is not None and (got[2] not in sizes or (not started and got[2] != saved) or T.area(got[2]) > T.area(saved)):
      errors.append(f"T5 {tag} {tid} size {got[2]} saved {saved} sizes {sizes}")
  used = [cells(*p) for p in out.values()]
  if any(a & b for a, b in itertools.combinations(used, 2)):
    errors.append(f"T2 {tag} overlap")
  if any(u & T.blocked(started) or not u <= grid for u in used):
    errors.append(f"T3 {tag} off grid or on the drive view")


def scan_order(started):
  if not started:
    return [(c, r) for c in range(T.COLS) for r in range(T.ROWS)]
  edges = [c for pair in zip(range(T.COLS // 2), range(T.COLS - 1, T.COLS // 2 - 1, -1)) for c in pair]  # 0,7,1,6,...
  return [(c, r) for r in range(T.ROWS) for c in edges]


def check_place(errors):
  rng = random.Random(4)
  for n in range(400):
    items = []
    for i in range(rng.randint(0, 20)):
      sizes = tuple(rng.sample(ALL, rng.randint(1, len(ALL))))
      anchor = (rng.randint(-1, T.COLS), rng.randint(-1, T.ROWS)) if rng.random() < 0.4 else None
      items.append((f"t{i}", rng.choice(sizes), sizes, anchor))
    for started in (False, True):
      out = T.place(items, started)
      first_fit_ok(errors, items, out, started, f"n={n} started={started}")
      if T.place(list(items), started) != out:
        errors.append("T4 placement not deterministic")
  onroad = T.place([(f"s{i}", "small", ("small",), None) for i in range(20)], True)
  if len(onroad) != 14:
    errors.append(f"T6 {len(onroad)} small tiles onroad, expected 14")
  if [onroad[f"s{i}"][:2] for i in range(5)] != [(0, 0), (7, 0), (0, 1), (7, 1), (0, 2)]:
    errors.append(f"T16 onroad fill order {[onroad[f's{i}'][:2] for i in range(5)]}")
  park = T.place([(f"s{i}", "small", ("small",), None) for i in range(5)], False)
  if [park[f"s{i}"][:2] for i in range(5)] != [(0, 0), (0, 1), (0, 2), (0, 3), (1, 0)]:
    errors.append(f"T16 offroad fill order {[park[f's{i}'][:2] for i in range(5)]}")
  fixed = T.place([("a", "small", ("small",), None), ("b", "medium", ("medium",), (6, 2)), ("c", "small", ("small",), (6, 2))], False)
  if fixed.get("b") != (6, 2, "medium") or fixed.get("a") != (0, 0, "small") or fixed.get("c") != (0, 1, "small"):
    errors.append(f"T4 fixed positions {fixed}")


def check_show(errors):
  for show, started in itertools.product(("always", "onroad", "offroad"), (False, True)):
    spec = T.TileSpec("x", "X", show=show)
    if T.shows(spec, started) != (show == "always" or (show == "onroad") == started):
      errors.append(f"T7 show={show} started={started}")


def check_view_saved(errors):
  default = T.DRIVE_CELLS[:2]
  bad = [None, [], 3, "x", {}, {"drive": None}, {"drive": []}]
  bad += [{"drive": {"view": v}} for v in (None, [], [1], [0, 0, 0], "1,0", {}, [True, 0], [0.5, 0], [-1, 0], [3, 0], [0, 2])]
  for value in bad:
    if T.view_position(value) != default:
      errors.append(f"T19 invalid position {value!r}")
  for col, row in itertools.product(range(3), range(2)):
    position = T.view_position(json.loads(json.dumps({"drive": {"view": [col, row]}})))
    if position != (col, row):
      errors.append(f"T19 position lost: {(col, row)} -> {position}")


def check_view_moved(errors):
  items = [(str(i), "small", ("small",), None) for i in range(20)]
  for pos in itertools.product(range(3), range(2)):
    view = T.view(pos)
    if not inside(view, (0, 0, *T.SCREEN)) or view[:2] != (T.VIEW[0] + (pos[0] - 1) * T.PITCH, T.VIEW[1] + pos[1] * T.PITCH):
      errors.append(f"T20 unsnapped/off-screen view {pos}: {view}")
    placed = T.place(items, True, pos)
    blocked = {(c, r) for c in range(pos[0], pos[0] + 6) for r in range(pos[1], pos[1] + 3)}
    if T.blocked(True, pos) != blocked or any(cells(*p) & blocked for p in placed.values()) or len(placed) != 14:
      errors.append(f"T20 tiles use old view position {pos}")
    if T.add_cell({}, True, pos) in blocked or any(overlap(r, view) for r in T.bar_buttons(placed, True, pos).values()):
      errors.append(f"T20 add cell/bar uses old view position {pos}")
    pinned = [("x", "small", None)]
    cell = next(iter(blocked))
    if T.move(pinned, {"x": (7, 0, "small")}, "x", cell, True, pos) != pinned:
      errors.append(f"T20 tile drop enters moved view {pos}")


def check_view_covered(errors):
  items = [("fixed", "small", ("small",), (0, 0))] + [(str(i), "small", ("small",), None) for i in range(13)]
  placed = T.place(items, True, (0, 0))
  if placed.get("fixed") != (7, 0, "small") or len(placed) != 14:
    errors.append(f"T21 covered fixed tile did not flow: {placed}")


def check_view_move(errors):
  for old, new in itertools.permutations(itertools.product(range(3), range(2)), 2):
    free = [(c, r) for c in range(T.COLS) for r in range(T.ROWS) if (c, r) not in T.blocked(True, old)]
    items = [(f"t{c}{r}", "small", ("small",), (c, r)) for c, r in free]
    placed = T.place(items, True, old)
    pinned = T.move_view([i[:2] + i[3:] for i in items], placed, old, new)
    after = T.place([(i, s, ("small",), f) for i, s, f in pinned], True, new)
    moved = {i for i in placed if cells(*placed[i]) & T.blocked(True, new)}
    if len(after) != 14 or any(after[i] != placed[i] for i in placed if i not in moved):
      errors.append(f"T35 view {old}->{new}: {placed} -> {after}")
  placed = {"a": (3, 0, "small"), "b": (0, 0, "small")}
  got = T.move_view([("a", "small", None), ("b", "small", None), ("c", "small", None)], placed, (1, 1), (1, 0))
  if got != [("a", "small", (3, 3)), ("b", "small", (0, 0)), ("c", "small", None)]:
    errors.append(f"T35 view up does not move the covered tile down: {got}")


def check_nav(errors):
  for saved in (None, {}, {"drive": {}}, {"drive": {"nav_snap": 0}}, {"drive": {"nav_snap": True}}, {"drive": []}):
    if not T.nav_snapped(saved):
      errors.append(f"T36 navigation not beside the view for {saved}")
  if T.nav_snapped({"drive": {"nav_snap": False}}):
    errors.append("T36 navigation moved into the grid not read")


def check_spec(errors):
  for bad in ({"sizes": ()}, {"sizes": ("huge",)}, {"show": "never"}, {"hz": 0}):
    try:
      T.TileSpec("bad", "Bad", **{"show": "always", **bad})
      errors.append(f"T12 accepted {bad}")
    except ValueError:
      pass


def specs():
  return [T.TileSpec("a", "A", show="always", sizes=("medium", "small"), order=1), T.TileSpec("b", "B", show="always", order=2),
          T.TileSpec("c", "C", show="always", sizes=("wide", "medium", "small"), order=2), T.TileSpec("d", "D", show="always", order=0)]


def check_layout(errors):
  sp = specs()
  default = T.arrange(sp, None, "park")
  if default != ([("d", "small", None), ("a", "medium", None), ("b", "small", None), ("c", "wide", None)], []):
    errors.append(f"T8 defaults {default}")
  for garbage in ("x", 5, [1], {"park": "x"}, {"park": {"tiles": [[1, 2], ["a"]], "hidden": 3}}, {"tiles": "x"}):
    if T.arrange(sp, garbage, "park") != default:
      errors.append(f"T8 garbage {garbage!r} gives {T.arrange(sp, garbage, 'park')}")
  saved = {"park": {"tiles": [["c", "small", 3, 1], ["zz", "small", 0, 0], ["a", "large", None, None], ["c", "wide", 0, 0], ["d", "small", "x", 2]],
                    "hidden": ["b", "zz"]},
           "drive": {"tiles": [["b", "medium", 7, 3]], "hidden": []}}
  pinned, hidden = T.arrange(sp, saved, "park")
  if pinned != [("c", "small", (3, 1)), ("a", "medium", None), ("d", "small", None)] or hidden != ["b"]:
    errors.append(f"T8 load {pinned} {hidden}")
  drive = T.arrange(sp, saved, "drive")
  if drive != ([("b", "medium", (7, 3)), ("d", "small", None), ("a", "medium", None), ("c", "wide", None)], []):
    errors.append(f"T8 drive layout {drive}")
  if T.arrange(sp, {"tiles": [["c", "small"]], "hidden": ["b"]}, "park") != ([("c", "small", None), ("d", "small", None), ("a", "medium", None)], ["b"]):
    errors.append(f"T8 v1 format {T.arrange(sp, {'tiles': [['c', 'small']], 'hidden': ['b']}, 'park')}")
  if T.arrange(sp, {"tiles": [["c", "small"]]}, "drive") != default:
    errors.append("T8 v1 format leaks into the drive layout")
  both = {"park": T.saved(pinned, hidden), "drive": T.saved(*drive)}
  if T.arrange(sp, json.loads(json.dumps(both)), "park") != (pinned, hidden) or T.arrange(sp, json.loads(json.dumps(both)), "drive") != drive:
    errors.append("T8 save/load does not round trip")


def check_edit(errors):
  sizes = {s.id: s.sizes for s in specs()}
  pinned, hidden = [("a", "medium", None), ("b", "small", None), ("c", "wide", (4, 0))], []
  seen = []
  p = pinned
  for _ in range(4):
    p = T.resize(p, "c", sizes["c"])
    seen.append([e for e in p if e[0] == "c"][0][1:])
  if seen != [("medium", (4, 0)), ("small", (4, 0)), ("wide", (4, 0)), ("medium", (4, 0))] or [e[0] for e in p] != ["a", "b", "c"]:
    errors.append(f"T9 resize cycle {seen}")
  p, h = T.unpin(pinned, hidden, "b")
  if p != [("a", "medium", None), ("c", "wide", (4, 0))] or h != ["b"]:
    errors.append(f"T9 unpin {p} {h}")
  p, h = T.pin(p, h, "b", sizes["b"])
  if p != [("a", "medium", None), ("c", "wide", (4, 0)), ("b", "small", None)] or h:
    errors.append(f"T9 pin {p} {h}")
  placed = T.place([(i, s, sizes[i], a) for i, s, a in pinned], False)  # a (0,0) medium, b (0,2) small, c (4,0) wide
  got = T.move(pinned, placed, "a", (7, 3), False)
  if got != [("a", "medium", (6, 2)), ("b", "small", None), ("c", "wide", (4, 0))]:
    errors.append(f"T9 drop a at (7,3): {got}")
  got = T.move(pinned, placed, "b", (5, 1), False)
  if got != [("a", "medium", None), ("b", "small", (5, 1)), ("c", "wide", None)]:
    errors.append(f"T9 drop b on fixed c: {got}")
  if T.move(pinned, placed, "b", (2, 1), True) != pinned:
    errors.append("T9 drop on the drive view changed the layout")


def check_hits(errors):
  placed = T.place([("a", "medium", ("medium",), None), ("b", "small", ("small",), None)], False)
  ax, ay, aw, ah = T.tile_rect(placed["a"])
  if T.hit(placed, ax + aw + T.GAP / 2, ay + 10) is not None or T.hit(placed, ax + 10, ay + 10) != "a":
    errors.append("T10 hit in a gap or miss inside a tile")
  for size in ALL:
    r = T.size_rect(0, 0, T.SIZES[size])
    buttons = T.corner_buttons(r)
    for name, (cx, cy, rad) in buttons.items():
      if not inside((cx - rad, cy - rad, 2 * rad, 2 * rad), r):
        errors.append(f"T10 {name} button leaves a {size} tile")
    (ux, uy, ur), (sx, sy, sr) = buttons["unpin"], buttons["resize"]
    if (ux - sx) ** 2 + (uy - sy) ** 2 < (ur + sr) ** 2:
      errors.append(f"T10 corner buttons overlap on a {size} tile")
  if T.add_cell(placed, False) != (0, 3) or T.add_cell(T.place([("x", "large", ("large",), None), ("y", "large", ("large",), None)], False), False) is not None:
    errors.append("T14 add cell")
  pinned = [("a", "medium", None), ("big", "large", None), ("big2", "large", None), ("big3", "large", None)]
  items = [(i, s, (s,), a) for i, s, a in pinned]
  if T.addable(pinned, ["h"], T.place(items, False)) != ["h", "big2", "big3"]:
    errors.append(f"T14 addable {T.addable(pinned, ['h'], T.place(items, False))}")


def bar_clear_span(placed, started, pos) -> bool:
  """A four-cell edge-row span outside the view that holds no tile and no add cell."""
  used = set().union(*(cells(*p) for p in placed.values())) if placed else set()
  add = T.add_cell(placed, started, pos)
  used |= {add} if add else set()
  rows = [0 if pos[1] else T.ROWS - 1] if started else [T.ROWS - 1, 0]
  return any(not ({(c0 + i, r) for i in range(4)} & (used | T.blocked(started, pos))) for r in rows for c0 in range(T.COLS - 3))


def check_bar(errors):
  rng = random.Random(7)
  for n in range(200):
    items = [(f"t{i}", rng.choice(ALL), ALL, None) for i in range(rng.randint(0, 14))]
    for started in (False, True):
      placed = T.place(items, started)
      b = T.bar_buttons(placed, started)
      rects = list(b.values())
      if set(b) != {"park", "drive", "done"} or any(overlap(x, y) for x, y in itertools.combinations(rects, 2)):
        errors.append(f"T17 bar buttons {b}")
        return
      if any(r[3] < 152 for r in rects):
        errors.append(f"T28 bar buttons too short: {b}")
      if any(not inside(r, (0, 0, *T.SCREEN)) for r in rects):
        errors.append(f"T17 bar off screen {b}")
      if started:
        if any(overlap(r, T.VIEW) for r in rects):
          errors.append(f"T28 drive bar covers the view {b}")
      taken = set().union(*(cells(*p) for p in placed.values())) if placed else set()
      free = [c for c in T.scan(False) if c not in taken]
      add = T.add_cell(placed, started)
      if bar_clear_span(placed, started, T.DRIVE_CELLS[:2]):
        tile_rects = [T.tile_rect(p) for p in placed.values()] + ([T.cell_rect(*add)] if add else [])
        if any(overlap(r, t) for r in rects for t in tile_rects):
          errors.append(f"T17 n={n} bar covers a tile or the + cell while a clear span exists: {b}")
  # T33: no reserved cells; a drop under the bar lands there and the add cell may sit under it.
  items = [(f"t{i}", "small", ("small",), None) for i in range(4)]
  placed = T.place(items, False)
  bar = T.bar_buttons(placed, False)
  under = next(c for c in T.scan(False) if any(overlap(T.cell_rect(*c), r) for r in bar.values()))
  pinned = T.move([(i, s, None) for i, s, _, _ in items], placed, "t0", under, False)
  moved = T.place([(i, s, (s,), f) for i, s, f in pinned], False)
  if moved["t0"][:2] != under:
    errors.append(f"T33 drop on bar cell {under} landed at {moved['t0'][:2]}")
  full = [(f"t{i}", "small", ("small",), None) for i in range(T.COLS * T.ROWS - 1)]
  if T.add_cell(T.place(full, False), False) is None:
    errors.append("T33 add cell missing with one free cell")
  for at in ((-10000, -10000), (10000, 10000)):
    moved = T.bar_buttons({}, False, at=at)
    if any(not inside(r, (0, 0, *T.SCREEN)) for r in moved.values()):
      errors.append(f"T17 moved bar not clamped: {at} -> {moved}")


def check_gesture(errors):
  g = T.Gesture()
  if g.press(0, 0, 0.0) or g.release(5, 5, 0.2) != "tap":
    errors.append("T11 short press is not a tap")
  g = T.Gesture()
  g.press(0, 0, 0.0)
  longs = [g.tick(t / 10) for t in range(20)]
  if longs.count("long") != 1 or longs.index("long") != round(T.LONG_PRESS * 10) or g.release(0, 0, 2.0) is not None:
    errors.append(f"T11 long press {longs}")
  g = T.Gesture()
  g.press(0, 0, 0.0)
  events = [g.move(i * 10, 0, i / 100) for i in range(10)] + [g.tick(1.0)]
  if events.count("drag") != 1 or "long" in events or g.release(90, 0, 1.0) != "drop":
    errors.append(f"T11 drag {events}")
  g = T.Gesture()
  if g.release(0, 0, 0.0) is not None or g.tick(5.0) is not None:
    errors.append("T11 release or tick without press")


def check_poll(errors):
  p = T.Poll(10)
  calls = [t for t in range(100) if p.due(t / 100)]  # 1 s at 100 fps
  if len(calls) != 10:
    errors.append(f"T15 polled {len(calls)} times in 1 s at hz=10")
  p = T.Poll(10)
  seq = [p.changed(s, k) for s, k in (((1, "a"), "small"), ((1, "a"), "small"), ((2, "a"), "small"), ((2, "a"), "medium"), ((2, "a"), "medium"))]
  if seq != [True, False, True, True, False]:
    errors.append(f"T15 redraws {seq}")
  p.invalidate()
  if not p.changed((2, "a"), "medium"):
    errors.append("T15 no redraw after invalidate")


def check_fit(errors):
  sizes = {n: T.size_rect(0, 0, c)[2:] for n, c in T.SIZES.items()}
  if [T.scale(*sizes[n]) for n in ("small", "medium", "wide", "large")] != [1, 2, 2, 2]:
    errors.append(f"T18 scale {[T.scale(*sizes[n]) for n in sizes]}")
  if T.fit_lines([], 200, 200, 40) != (40, 0):
    errors.append("T18 no lines")
  rng = random.Random(7)
  for _ in range(2000):
    widths = [rng.uniform(0.2, 12) for _ in range(rng.randint(1, 6))]
    w, h, cap = rng.uniform(100, 1000), rng.uniform(20, 900), rng.uniform(24, 150)
    size, n = T.fit_lines(widths, w, h, cap)
    if not (1 <= n <= len(widths)) or not (T.MIN_FONT <= size <= max(cap, T.MIN_FONT)):
      errors.append(f"T18 {widths} {w:.0f}x{h:.0f} cap {cap:.0f} gave {size}, {n}")
      break
    if size > T.MIN_FONT and (n * (size + 6) - 6 > h + 1 or any(k * size > w + 1 for k in widths[:n])):
      errors.append(f"T18 {widths} {w:.0f}x{h:.0f} gave {size} px, {n} lines: leaves the box")
      break
    if n < len(widths) and (n + 1) * (T.MIN_FONT + 6) - 6 <= h:
      errors.append(f"T18 {widths} {w:.0f}x{h:.0f} dropped a line that fits at MIN_FONT")
      break
    if n > 1 and n * (T.MIN_FONT + 6) - 6 > h:
      errors.append(f"T18 {widths} {w:.0f}x{h:.0f} kept {n} lines that do not fit")
      break


def ascii_strings(path: Path):
  tree = ast.parse(path.read_text())
  docs = {id(n.body[0].value) for n in ast.walk(tree) if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef))
          and n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
  for node in ast.walk(tree):
    if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs and not node.value.isascii():
      yield node.lineno, node.value


def check_text(errors):
  pkg = ROOT / "openpilot/tesla"
  for path in sorted(list((pkg / "ui").rglob("*.py")) + list((pkg / "plugins").glob("*/ui.py"))):
    for line, text in ascii_strings(path):
      errors.append(f"T13 {path.relative_to(ROOT)}:{line} non-ASCII {text!r}")
  for path in sorted((pkg / "plugins").glob("*/plugin.json")):
    m = json.loads(path.read_text())
    for text in [m.get("name", ""), m.get("description", "")] + [s.get("label", "") for s in m.get("settings", [])]:
      if not text.isascii():
        errors.append(f"T13 {path.relative_to(ROOT)} non-ASCII {text!r}")


def check_hud(errors):
  if not hasattr(T, "hud_layout") or not hasattr(T, "hud_rect"):
    errors.extend(["T23 saved HUD layout unsupported", "T24 bounded HUD rectangles unsupported"])
    return
  default = (20, 30, 120, 48)
  view = (0, 0, *T.MICI)
  entry = [0.4, 0.3, False]
  saved = json.loads(json.dumps({"drive": {"hud": {"example": entry}}}))
  if T.hud_layout(saved) != {"example": entry}:
    errors.append("T23 HUD position/visibility did not round trip")
  scaled = [0.4, 0.3, False, 1.5]
  if T.hud_layout({"drive": {"hud": {"example": scaled}}}) != {"example": scaled}:
    errors.append("T23 4-value HUD entry did not round trip")
  for bad_scale in (0.6, "1", True):
    got = T.hud_layout({"drive": {"hud": {"example": [0.4, 0.3, False, bad_scale]}}}).get("example")
    if got is None or got[:3] != entry or T.hud_scale(got) != 1.0:
      errors.append(f"T23 bad HUD scale lost position/visibility: {bad_scale!r} -> {got}")
  required = next(iter(T.HUD_REQUIRED))
  got = T.hud_layout({"drive": {"hud": {required: [0.4, 0.3, False, 0.75]}}}).get(required)
  if got != [0.4, 0.3, True, 1.0]:
    errors.append(f"T23 required HUD shrank/hid: {got}")
  bad = [None, [], {}, {"drive": []}, {"drive": {"hud": []}}]
  bad += [{"drive": {"hud": {"example": v}}} for v in
          (None, [], [0, 0], [0, 0, 1], [True, 0, True], [-0.1, 0, True], [0, 1.1, True], [float("nan"), 0, True], [0, float("inf"), True])]
  for raw in bad:
    if T.hud_layout(raw) or T.hud_rect(T.hud_layout(raw).get("example"), default, view) != default:
      errors.append(f"T23 invalid HUD entry did not use default: {raw}")
  for x, y in itertools.product((-10000, 0, .5, 1, 10000), repeat=2):
    r = T.hud_rect([x, y, True], default, view)
    if not inside(r, view):
      errors.append(f"T24 HUD leaves view: {r}")
  r = T.hud_rect([0, 0, True, 1.5], default, view)
  if r[2:] != (default[2] * 1.5, default[3] * 1.5) or abs(r[2] / r[3] - default[2] / default[3]) > 1e-9 or not inside(r, view):
    errors.append(f"T24 scaled HUD size/aspect/bounds: {r}")
  too_large = (20, 30, T.MICI[0] * 2, 120)
  r = T.hud_rect([1, 1, True, 1.5], too_large, view)
  if not inside(r, view) or abs(r[2] / r[3] - too_large[2] / too_large[3]) > 1e-9 or r[2] != T.MICI[0]:
    errors.append(f"T24 oversized HUD did not shrink to fit: {r}")
  for pos in itertools.product(range(3), range(2)):
    vx, vy, vw, vh = T.view(pos)
    rect = T.hud_rect(entry, (vx + 20, vy + 30, 120, 48), (vx, vy, vw, vh))
    if abs((rect[0] - vx) / vw - entry[0]) > 1e-6 or abs((rect[1] - vy) / vh - entry[1]) > 1e-6:
      errors.append(f"T24 HUD fractions changed with view: {pos}")


def check_explicit_show(errors):
  try:
    T.TileSpec("missing", "Missing")
    errors.append("T25 TileSpec accepted missing show")
  except TypeError:
    pass
  specs = [T.TileSpec(s, s, show=s) for s in ("always", "onroad", "offroad")]
  for mode in ("drive", "park"):
    pinned, hidden = T.arrange(specs, {mode: {"hidden": [s.id for s in specs]}}, mode)
    ids = T.addable(pinned, hidden, {})
    if set(ids) != {s.id for s in specs if T.shows(s, mode == "drive")}:
      errors.append(f"T25 wrong add list for {mode}: {ids}")


def check_view_exclusion(errors):
  for pos in itertools.product(range(3), range(2)):
    blocked = T.blocked(True, pos)
    pinned = [("tile", "small", None)]
    placed = T.place([("tile", "small", ("small",), None)], True, pos)
    for cell in blocked:
      if T.move(pinned, placed, "tile", cell, True, pos) != pinned:
        errors.append(f"T26 tile dropped in view {pos}: {cell}")
      fixed = T.place([("tile", "small", ("small",), cell)], True, pos)
      if any(cells(*p) & blocked for p in fixed.values()):
        errors.append(f"T26 fixed tile inside view {pos}: {cell}")
    filled = T.place([(str(i), "small", ("small",), None) for i in range(32)], True, pos)
    if T.add_cell(filled, True, pos) is not None or T.add_cell({}, True, pos) in blocked:
      errors.append(f"T26 add cell inside view {pos}")


def check_themes(errors):
  existing = {"Cobalt": (0, 80, 239), "Teal": (0, 140, 140), "Emerald": (0, 128, 0), "Crimson": (162, 0, 37),
              "Violet": (106, 0, 255), "Graphite": (52, 56, 66)}
  if T.THEMES != {**existing, "Black": (0, 0, 0)}:
    errors.append(f"T30 Black missing/changed existing theme: {T.THEMES}")


def check_builtin_hud(errors):
  required = {"builtin:steering_wheel", "builtin:model_source", "builtin:blind_spot_indicators"}
  if not hasattr(T, "builtin_hud_boxes") or not hasattr(T, "HUD_REQUIRED"):
    errors.append("T29 built-in HUD defaults/protection missing")
    return
  boxes = T.builtin_hud_boxes((0, 0, *T.MICI))
  if set(boxes) != required | {"builtin:torque_bar", "builtin:set_speed"} or not required <= T.HUD_REQUIRED:
    errors.append(f"T29 wrong built-in boxes/protection: {boxes}, {T.HUD_REQUIRED}")
  for name, default in boxes.items():
    if not inside(default, (0, 0, *T.MICI)):
      errors.append(f"T29 default box outside view: {name} {default}")
    raw = {"drive": {"hud": {name: [.2, .1, False]}}}
    entry = T.hud_layout(json.loads(json.dumps(raw)))[name]
    if entry != [.2, .1, name in required]:
      errors.append(f"T29 visibility/fractions: {name} {entry}")
    for bad in ([0, 0, 1], [float("nan"), 0, True], [0, float("inf"), True], [True, 0, True], [0, 2, True]):
      if name in T.hud_layout({"drive": {"hud": {name: bad}}}):
        errors.append(f"T29 invalid saved entry survives: {name} {bad}")
  items = [(str(i), "small", ("small",), None) for i in range(40)]
  for pos in itertools.product(range(3), range(2)):
    placed = T.place(items, True, pos)
    bar = T.bar_buttons(placed, True, pos)
    if any(overlap(r, T.view(pos)) or r[3] < 152 or not inside(r, (0, 0, *T.SCREEN)) for r in bar.values()):
      errors.append(f"T28 moved view/bar/tiles overlap: {pos}")


def check_hud_controls(errors):
  if not hasattr(T, "hud_controls"):
    errors.extend(["T31 HUD controls missing", "T32 HUD control placement missing"])
    return
  boxes = [(0, 0, 120, 48), (2040, 0, 120, 48), (0, 1032, 120, 48), (2040, 1032, 120, 48), T.VIEW]
  for box in boxes:
    for required in (False, True):
      controls = T.hud_controls(box, required)
      expected = {"smaller", "bigger"} if required else {"toggle", "smaller", "bigger"}
      if set(controls) != expected:
        errors.append(f"T31 wrong HUD controls required={required}: {controls}")
        return
      if any(not inside(r, (0, 0, *T.SCREEN)) or overlap(r, box) for r in controls.values()):
        errors.append(f"T32 HUD controls leave screen/cover box: {box} {controls}")
        return
      if any(overlap(a, b) for a, b in itertools.combinations(controls.values(), 2)):
        errors.append(f"T32 HUD controls overlap: {controls}")
        return


def main() -> int:
  errors: list[str] = []
  for check in (check_grid, check_view, check_place, check_show, check_spec, check_layout, check_edit, check_hits, check_bar, check_gesture,
                check_poll, check_fit, check_text, check_view_saved, check_view_moved, check_view_covered, check_view_move, check_nav,
                check_hud, check_explicit_show, check_view_exclusion, check_themes, check_builtin_hud, check_hud_controls):
    try:
      check(errors)
    except Exception as e:
      errors.append(f"{check.__name__} raised {e!r}")
  screen = (0, 0, *T.SCREEN)
  for col in range(T.COLS - T.DRIVE_CELLS[2] + 1):
    for row in range(T.ROWS - T.DRIVE_CELLS[3] + 1):
      view = T.view((col, row))
      x, y, w, h = view
      band, nav = T.alert_band(view), T.nav_column(view)
      above, below = y, T.SCREEN[1] - y - h
      left, right = x, T.SCREEN[0] - x - w
      if not (inside(band, screen) and not overlap(band, view) and band[0] == x and band[2] == w and band[3] == max(above, below)
              and (band[1] + band[3] == y if above > below else band[1] == y + h)):
        errors.append(f"T34 alert band {band} for view {view}")
      if not (inside(nav, screen) and not overlap(nav, view) and nav[1] == y and nav[3] == h and nav[2] == max(left, right)
              and (nav[0] + nav[2] == x if left > right else nav[0] == x + w)):
        errors.append(f"T34 navigation column {nav} for view {view}")
      if len(T.blocked(True, (col, row))) != T.DRIVE_CELLS[2] * T.DRIVE_CELLS[3]:
        errors.append(f"T34 onroad cells reserved beyond the view at {(col, row)}")
  print(json.dumps({"view": T.VIEW, "origin": T.ORIGIN, "errors": errors}, indent=1))
  return 1 if errors else 0


if __name__ == "__main__":
  sys.exit(main())
