"""Big-screen host (C3/C3X/C3XL, 2160x1080), UI v4: Windows Phone / Win 8 style tiles (XPILOT.md §9, layout in tiles.py).
Onroad: drag the mici view; neutral tiles flow around it, then plugin ScreenOverlays. Other touches are ignored.
Alerts and navigation are drawn in a side column outside the view.
Offroad: the tile screen (tap opens a tile's page, long press enters edit mode), or a full-screen detail page: back
button, parent name, title, and a native page (tesla/ui/pages) drawn here in screen pixels. The mici texture is not
drawn (viewport() -> None) except during onboarding and for mici pages pushed above main (Models, training guide, terms,
regulatory, cabin camera), which show at 3x in FALLBACK_VIEW. Without a viewport every touch maps far outside the mici
screen, so the mici widgets running underneath never react. tici dialogs (ConfirmDialog, Keyboard) the native pages
push onto the nav stack are hidden in the mici pass and drawn full-screen here."""
from __future__ import annotations  # pyray types are functions: rl.Rectangle | None fails at import

from contextlib import contextmanager
import math
import time

import pyray as rl

from openpilot.cereal import messaging
from openpilot.common.hardware import HARDWARE
from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.sunnypilot.hardware.profile import HardwareProfile, get_hardware_profile
from openpilot.system.ui.lib.application import MousePos, gui_app, FontWeight
from openpilot.system.ui.lib.multilang import tr
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.widgets.list_view import button_item, text_item
from openpilot.tesla.plugin_params import ParamsView
from openpilot.tesla.ui import hud_preview, offroad, plugin_ui, tiles
from openpilot.tesla.ui.offroad import BACK, CONTENT, TITLE_H
from openpilot.tesla.ui.pages.base import ListPage
from openpilot.tesla.ui.pages.device import DevicePage
from openpilot.tesla.ui.pages.onboarding import OnboardingPage
from openpilot.tesla.ui.plugin_ui import MAX_FAILURES, EnabledPlugins, HudOverlay, Overlays, ScreenOverlay, load
from openpilot.tesla.ui.tile import Tile, text, text_w
from openpilot.tesla.ui.tiles import LAYOUT_PARAM, LAYOUT_SPEC, SCREEN, THEME_PARAM, THEMES

DIM = rl.Color(150, 152, 162, 255)
SHADE = rl.Color(0, 0, 0, 120)      # over unselected tiles in edit mode
PRESSED = rl.Color(0, 0, 0, 60)


def is_big_screen() -> bool:
  return HARDWARE.get_device_type() in ("tici", "tizi") or get_hardware_profile() == HardwareProfile.C3XL


@contextmanager
def scaled(s: float):
  """Draw logical coordinates at s times size. gui_app patched scissor for its own texture scale, so swap it."""
  prev = rl.begin_scissor_mode
  rl.begin_scissor_mode = lambda x, y, w, h: rl._orig_begin_scissor_mode(int(x * s), int(y * s), int(math.ceil(w * s)), int(math.ceil(h * s)))
  rl.rl_push_matrix()
  rl.rl_scalef(s, s, 1.0)
  try:
    yield
  finally:
    rl.rl_pop_matrix()
    rl.begin_scissor_mode = prev


def _zones(view) -> dict:
  """ScreenOverlay zones: the strips around the current view and the view itself."""
  x, y, w, h = view
  return {"left": rl.Rectangle(0, 0, x, SCREEN[1]), "right": rl.Rectangle(x + w, 0, SCREEN[0] - x - w, SCREEN[1]),
          "top": rl.Rectangle(x, 0, w, y), "bottom": rl.Rectangle(x, y + h, w, SCREEN[1] - y - h), "view": rl.Rectangle(*view)}


class _Entry:
  def __init__(self, plugin: str | None, tile: Tile):
    self.plugin, self.tile, self.spec = plugin, tile, tile.SPEC
    self.poll, self.texture, self.state, self.failures = tiles.Poll(tile.SPEC.hz), None, None, 0


class AddTilesPage(ListPage):
  """Edit mode "+": one row per hidden or left-out tile; tapping one pins it."""

  def __init__(self, entries, pin):
    super().__init__()
    if not entries:
      self.add(text_item(tr("No tiles to add"), "", tr("Every tile is on the screen.")))
    for e in entries:
      self.add(button_item(tr(e.spec.title) if e.plugin is None else e.spec.title, tr("ADD"), callback=lambda tid=e.spec.id: pin(tid)))

  def show_event(self):
    super().show_event()


class BigScreenHost:
  size = SCREEN

  def __init__(self):
    self._params = Params()
    self._layout_params = ParamsView(self._params, LAYOUT_SPEC)
    self._entries: dict[str, _Entry] = {}
    for name, tile in load(Tile):  # core slots/ and each plugin's ui.py
      if tile.SPEC.id in self._entries:
        cloudlog.error(f"tile id {tile.SPEC.id} used twice, keeping the first")
        continue
      self._entries[tile.SPEC.id] = _Entry(name, tile)
    self._overlays = Overlays(ScreenOverlay, self._params)
    self._hud_overlays = Overlays(HudOverlay, self._params)
    self._enabled = EnabledPlugins(self._params)
    services = sorted({svc for e in self._entries.values() for svc in e.spec.services})
    self._sm = messaging.SubMaster(services) if services else None
    try:
      saved = self._layout_params.get(LAYOUT_PARAM)
    except Exception:
      saved = None
    self._view_position = tiles.view_position(saved)
    self._hud, self._hud_drag, self._hud_selected = tiles.hud_layout(saved), None, None
    self._view_drag = False
    self.onroad_panels = None
    self._screen_override, self._last_gear, self._last_engaged = None, None, False
    specs = [e.spec for e in self._entries.values()]
    self._layouts = {mode: tiles.arrange(specs, saved, mode) for mode in ("park", "drive")}
    self._layout = "park"  # the layout the tile screen shows and edit mode edits; onroad always shows "drive"
    self._placed_key = None
    self._placed: dict[str, tuple[int, int, str]] = {}
    self._main = None
    self._pages: dict[str, object] = {}
    self._stack: list[tuple[str, object | None]] = []  # open detail pages (title, native page or None for a mici page)
    self._shown = None  # the native page that last got show_event
    self._gesture = tiles.Gesture()
    self._edit = False
    self._selected: str | None = None
    self._drag: str | None = None
    self._bar_at, self._bar_drag = None, False
    self._back_pressed = False
    self._view: tuple | None = None  # set each frame by viewport(); to_logical runs on the touch thread
    self._now, self._faced = time.monotonic(), {}  # set each frame by viewport(): tile faces drawn before the screen frame
    self._mode = "native"
    self._host_pass = False
    self._onboarding = OnboardingPage(self._onboarding_done)
    self._theme()

  def _theme(self) -> None:
    """Apply the tile color chosen on the Toggles page; read when the tile screen shows again."""
    try:
      name = self._layout_params.get(THEME_PARAM)
      color = THEMES.get("Black" if name == "Outline" else name, tiles.ACCENT)
    except Exception:
      color = tiles.ACCENT
    if self._mode == "onroad" and color != THEMES["Black"]:
      color = (32, 32, 32)
    if color != Tile.COLOR:
      Tile.COLOR = color
      for e in self._entries.values():
        e.poll.invalidate()

  def bind_settings(self, main, _settings) -> None:
    self._main = main

  def open_settings(self) -> None:
    self.open_tile("toggles")

  # layout

  def _visible(self, mode: str) -> list[tuple[str, str, tuple[str, ...], tuple[int, int] | None]]:
    enabled = self._enabled.get()
    out = []
    for tid, size, fixed in self._layouts[mode][0]:
      e = self._entries[tid]
      if tid == "models" and "sp_models" not in enabled:
        continue
      if e.failures < MAX_FAILURES and (e.plugin is None or e.plugin in enabled):
        out.append((tid, size, e.spec.sizes, fixed))
    return out

  def _place(self, mode: str) -> dict[str, tuple[int, int, str]]:
    onroad = mode == "drive" and self._mode == "onroad"
    items = [item for item in self._visible(mode) if not onroad or item[0] != "lean_navigation"]
    reserved = ()
    if onroad:
      x, _, w, _ = tiles.alert_column(self._drive_view())
      reserved = tuple((c, r) for c in range(tiles.COLS) for r in range(tiles.ROWS)
                       if tiles.cell_rect(c, r)[0] < x + w and tiles.cell_rect(c, r)[0] + tiles.CELL > x)
    key = (mode, tuple(items), self._view_position, reserved)
    if key != self._placed_key:
      self._placed_key, self._placed = key, tiles.place(items, mode == "drive", self._view_position, reserved)
    return self._placed

  def _save(self, pinned=None, hidden=None) -> None:
    """Replace the edited layout and write both."""
    if pinned is not None:
      self._layouts[self._layout] = (pinned, self._layouts[self._layout][1] if hidden is None else hidden)
    try:
      saved = {mode: tiles.saved(*layout) for mode, layout in self._layouts.items()}
      saved["drive"]["view"] = list(self._view_position)
      saved["drive"]["hud"] = self._hud
      self._layout_params.put(LAYOUT_PARAM, saved)
      tiles.layout_version += 1
    except Exception:
      cloudlog.exception("tile layout not saved")

  def _pin(self, tid: str) -> None:
    self._save(*tiles.pin(*self._layouts[self._layout], tid, self._entries[tid].spec.sizes))
    self._back()

  # pages

  def open_tile(self, tid: str) -> None:
    e = self._entries.get(tid)
    if e is None or e.tile.PAGE is None:
      return
    gui_app.pop_widgets_to(self._main, instant=True)  # never grow the nav stack
    self._stack = []
    if tid not in self._pages:
      try:
        page = e.tile.PAGE()
      except Exception:
        cloudlog.exception(f"tile {tid}: page failed to build")  # a broken page must not take the UI down
        return
      page.open = self._open
      if isinstance(page, DevicePage):
        page.main = self._main
      self._pages[tid] = page
    if e.tile.MICI:
      gui_app.push_widget(self._pages[tid])
      self._stack.append((tr(e.spec.title) if e.plugin is None else e.spec.title, None))  # shown by mici in FALLBACK_VIEW
    else:
      self._open(tr(e.spec.title) if e.plugin is None else e.spec.title, self._pages[tid])

  def _open(self, title, page) -> None:
    """Push a detail page; ListPage.open(title, sub) of the open page lands here too. page None: back one level."""
    if page is not None:
      self._stack.append((title, page))
    elif self._stack:
      self._stack.pop()

  def _onboarding_done(self) -> None:
    gui_app.pop_widgets_to(self._main, instant=True)  # what mici's OnboardingWindow callback does

  def back(self) -> None:
    """Close the open detail page (pages.base.back)."""
    self._back()

  def _back(self) -> None:
    if self._mode == "mici":
      gui_app.pop_widgets_to(self._main, instant=True)
      if self._stack and self._stack[-1][1] is not None:
        return  # a mici dialog opened by a native page (Device): back to that page
    if self._stack:
      self._stack.pop()

  @staticmethod
  def _is_modal(widget) -> bool:
    """tici dialogs (system/ui/widgets: ConfirmDialog, alert_dialog, Keyboard) pushed by the native pages."""
    from openpilot.tesla.ui.setting_keyboard import SettingKeyboard
    return isinstance(widget, SettingKeyboard) or type(widget).__module__.startswith("openpilot.system.ui.widgets")

  def _hook_modal(self) -> None:
    top = gui_app.get_active_widget()
    if top is not self._main and self._is_modal(top):
      top.set_visible(lambda: self._host_pass)  # hidden in the mici texture pass, drawn by _draw_offroad

  def _onroad(self) -> bool:
    """Started and not parked shows the drive layout; P shows the offroad screen (owner 2026-10-26). A long press switches
    between them while started; the choice lasts until the gear changes or openpilot engages."""
    if not ui_state.started:
      self._screen_override = None
      return False
    sm = ui_state.sm
    gear = sm['carState'].gearShifter
    engaged = ui_state.engaged
    if gear != self._last_gear or (engaged and not self._last_engaged):
      self._screen_override = None
    self._last_gear, self._last_engaged = gear, engaged
    if self._screen_override is not None:
      return self._screen_override == "onroad"
    return str(gear) != "park"

  def viewport(self) -> rl.Rectangle | None:
    onroad = self._mode == "onroad"
    started = self._onroad()
    if onroad != started:
      self._gesture = tiles.Gesture()
      self._view_drag = False
    if started:
      self._mode = "onroad"
      self._hud_drag, self._hud_selected, self._bar_drag = None, None, False
      self._edit, self._drag, self._selected, self._layout = False, None, None, "park"
      events = [e._replace(pos=MousePos(*offroad.to_screen(None, e.pos.x, e.pos.y))) for e in gui_app.mouse_events]
      self._touch(events, self._place("drive"))
      self._view = self._drive_view()
      self._zones = _zones(self._view)
    else:
      onboarding = self._main is None or gui_app.widget_in_stack(self._main._onboarding_window)
      top = gui_app.get_active_widget()
      self._mode, self._view = offroad.view_mode(onboarding, top is self._main, self._is_modal(top))
    if onroad != started:
      self._theme()
    self._refresh_faces()
    return rl.Rectangle(*self._view) if self._view is not None else None

  def _refresh_faces(self) -> None:
    """Redraw changed tile textures here, before gui_app.render begins the screen frame. Switching to a tile texture
    in the middle of the screen frame makes the tiled GPU resolve and reload the screen, suspected of the onroad tile flicker."""
    self._now, self._faced = time.monotonic(), {}
    if self._mode == "onroad":
      placed = self._place("drive")
    elif self._mode == "native" and not self._stack:
      placed = self._place(self._layout)
    else:
      return
    if self._sm is not None:
      self._sm.update(0)
    for tid, p in placed.items():
      self._faced[tid] = self._face(self._entries[tid], p[2], self._now)
    if self._mode == "onroad" and any(item[0] == "lean_navigation" for item in self._visible("drive")):
      self._faced["lean_navigation"] = self._face(self._entries["lean_navigation"], "small", self._now)

  def to_logical(self, x: float, y: float) -> tuple[float, float]:
    return offroad.to_logical(None if self._mode == "onroad" else self._view, x, y)

  def _drive_view(self) -> tuple:
    x, y, w, h = tiles.view(self._view_position)
    if self._view_drag and self._gesture.start is not None:
      dx, dy = (p - s for p, s in zip(self._gesture.pos, self._gesture.start[:2], strict=True))
      col, row = self._view_position
      x += min(max(dx, -col * tiles.PITCH), (tiles.COLS - tiles.DRIVE_CELLS[2] - col) * tiles.PITCH)
      y += min(max(dy, -row * tiles.PITCH), (tiles.ROWS - tiles.DRIVE_CELLS[3] - row) * tiles.PITCH)
    return x, y, w, h

  def _hud_defaults(self) -> dict:
    """Enabled HUD boxes in logical mici pixels."""
    defaults = tiles.builtin_hud_boxes((0, 0, *tiles.MICI))

    def box(widget):
      r = widget.default_rect(rl.Rectangle(0, 0, *tiles.MICI))
      defaults[widget.plugin_id] = (r.x, r.y, r.width, r.height)

    self._hud_overlays.each(box)
    return defaults

  def _hud_boxes(self) -> dict:
    """Enabled HUD boxes in screen pixels, including hidden ones so they can be shown again."""
    view = self._drive_view()
    vx, vy, vw, vh = view
    boxes = {}
    for name, (x, y, w, h) in self._hud_defaults().items():
      default = (vx + x * vw / tiles.MICI[0], vy + y * vh / tiles.MICI[1], w * vw / tiles.MICI[0], h * vh / tiles.MICI[1])
      rect = tiles.hud_rect(self._hud.get(name), default, view)
      if self._hud_drag == name and self._gesture.start is not None:
        dx, dy = (p - s for p, s in zip(self._gesture.pos, self._gesture.start[:2], strict=True))
        entry = [(rect[0] + dx - vx) / vw, (rect[1] + dy - vy) / vh, True, tiles.hud_scale(self._hud.get(name))]
        rect = tiles.hud_rect(entry, default, view)
      boxes[name] = rect
    return dict(sorted(boxes.items(), key=lambda item: -item[1][2] * item[1][3]))

  # drawing

  def render(self) -> None:
    if self._mode == "onroad":
      self._draw_tiles(self._place("drive"), self._now)
      self._draw_overlays()
      with scaled(1):
        self._draw_onroad_panels()
    elif self._mode == "onboarding":
      events = [e._replace(pos=MousePos(*offroad.to_screen(None, e.pos.x, e.pos.y))) for e in gui_app.mouse_events]
      self._show(self._onboarding if self._main is not None else None)
      if self._main is not None:
        with self._native(events):
          self._onboarding.render(rl.Rectangle(0, 0, *SCREEN))
    else:
      self._draw_offroad()

  def _face(self, e: _Entry, size: str, now: float) -> bool:
    """Bring the tile's cached texture up to date; True if there is one to draw."""
    w, h = tiles.SIZES[size]
    w, h = w * tiles.PITCH - tiles.GAP, h * tiles.PITCH - tiles.GAP
    if e.texture is None or e.texture.texture.width != w or e.texture.texture.height != h:
      if e.texture is not None:
        rl.unload_render_texture(e.texture)
      e.texture = rl.load_render_texture(w, h)
      e.poll.invalidate()
    try:
      if e.poll.due(now):
        e.state = e.tile.state(self._sm if self._sm is not None and e.spec.services else ui_state.sm)
      if e.poll.changed(e.state, size):
        rl.begin_texture_mode(e.texture)
        rl.clear_background(rl.BLANK)
        try:
          with scaled(1):
            e.tile.draw(rl.Rectangle(0, 0, w, h), size, e.state)
        finally:
          rl.end_texture_mode()
      e.failures = 0
    except Exception:
      e.failures += 1  # a broken tile must never take the UI (and engagement) down
      e.poll.invalidate()
      cloudlog.exception(f"tile {e.spec.id} failed")
      return False
    return True

  def _draw_tiles(self, placed, now: float) -> None:
    for tid, p in placed.items():
      e = self._entries[tid]
      if self._view_drag and rl.check_collision_recs(rl.Rectangle(*tiles.tile_rect(p)), rl.Rectangle(*self._drive_view())):
        continue
      if tid == self._drag or not (self._faced.pop(tid) if tid in self._faced else self._face(e, p[2], now)):  # edits re-place
        continue
      x, y, w, h = tiles.tile_rect(p)
      grow = 0.03 * w if self._edit and tid == self._selected else 0.0
      self._blit(e, rl.Rectangle(x - grow, y - grow * h / w, w + 2 * grow, h + 2 * grow * h / w))
      if self._edit and tid != self._selected:
        rl.draw_rectangle(x, y, w, h, SHADE)
      elif (self._mode != "onroad" and not self._edit and self._gesture.start is not None
            and tiles.hit({tid: p}, *self._gesture.start[:2]) and not self._gesture.long):
        rl.draw_rectangle(x, y, w, h, PRESSED)

  @staticmethod
  def _blit(e: _Entry, dest: rl.Rectangle) -> None:
    t = e.texture.texture
    rl.draw_texture_pro(t, rl.Rectangle(0, 0, t.width, -t.height), dest, rl.Vector2(0, 0), 0.0, rl.WHITE)

  def _draw_overlays(self) -> None:
    self._overlays.each(lambda o: (o.update(ui_state.sm), o.draw(self._zones)))

  def _draw_onroad_panels(self) -> None:
    x, y, w, h = tiles.alert_column(self._drive_view())
    rl.draw_rectangle(int(x), 0, int(w), SCREEN[1], rl.BLACK)
    if self.onroad_panels is not None:
      alert, navigation, show_navigation = self.onroad_panels
      alert.render(rl.Rectangle(x, y, w, h))
      if show_navigation:
        navigation.render(rl.Rectangle(x, h, w, SCREEN[1] - h - tiles.CELL))
    e = self._entries.get("lean_navigation")
    if e is not None and any(item[0] == "lean_navigation" for item in self._visible("drive")):
      if self._faced.get("lean_navigation"):
        self._blit(e, rl.Rectangle(x, SCREEN[1] - tiles.CELL, w, tiles.CELL))

  @contextmanager
  def _native(self, events):
    """Run tici widgets in screen pixels: screen-space touches, full-screen gui_app size, unscaled scissor."""
    saved = gui_app._mouse_events, gui_app._width, gui_app._height
    gui_app._mouse_events, (gui_app._width, gui_app._height) = events, SCREEN
    self._host_pass = True
    try:
      with scaled(1):
        yield
    finally:
      self._host_pass = False
      gui_app._mouse_events, gui_app._width, gui_app._height = saved

  def _draw_offroad(self) -> None:
    events = [e._replace(pos=MousePos(*offroad.to_screen(self._view, e.pos.x, e.pos.y))) for e in gui_app.mouse_events]
    top = gui_app.get_active_widget()
    if top is self._main and self._stack and self._stack[-1][1] is None:
      self._stack.pop()  # left the Models page with the mici back gesture
    modal = self._mode == "modal"
    if modal:
      self._hook_modal()
      with self._native(events):
        top.render(rl.Rectangle(0, 0, *SCREEN))
      return
    if not self._stack and self._mode != "mici":
      self._show(None)
      self._draw_start(events)
    else:
      self._draw_detail(top, events)
    self._hook_modal()

  def _show(self, page) -> None:
    if page is not self._shown:
      if self._shown is not None:
        self._shown.hide_event()
      if page is not None:
        page.show_event()
      else:
        self._theme()
      self._shown = page

  # tile screen and edit mode

  def _touch(self, events, placed) -> None:
    for ev in events:
      if ev.slot != 0:
        continue
      x, y = ev.pos.x, ev.pos.y
      if ev.left_pressed:
        self._gesture.press(x, y, ev.t)
      elif ev.left_released:
        self._on_gesture(self._gesture.start, self._gesture.release(x, y, ev.t), x, y, placed)
      elif ev.left_down:
        self._on_gesture(self._gesture.start, self._gesture.move(x, y, ev.t), x, y, placed)
    if self._gesture.start is not None:
      self._on_gesture(self._gesture.start, self._gesture.tick(time.monotonic()), *self._gesture.pos, placed)

  def _draw_start(self, events) -> None:
    now = self._now
    self._touch(events, self._place(self._layout))
    placed = self._place(self._layout)
    drive = self._layout == "drive"
    if drive:
      vx, vy, vw, vh = self._drive_view()
      rl.draw_rectangle_rec(rl.Rectangle(vx, vy, vw, vh), rl.Color(40, 42, 50, 255))
    self._draw_tiles(placed, now)
    if not self._edit:
      return
    if drive:
      boxes = self._hud_boxes()
      defaults = self._hud_defaults()
      getattr(rl, "_orig_begin_scissor_mode", rl.begin_scissor_mode)(int(vx), int(vy), int(vw), int(vh))
      try:
        for name, rect in boxes.items():
          if name.startswith("builtin:"):
            plugin_ui.draw_in(rl.Rectangle(*defaults[name]), rl.Rectangle(*rect),
                              lambda name=name: hud_preview.draw(name.removeprefix("builtin:"), rl.Rectangle(0, 0, *tiles.MICI)))

        def preview(widget):
          name = widget.plugin_id
          if name not in boxes:
            return
          default, target = rl.Rectangle(*defaults[name]), rl.Rectangle(*boxes[name])
          plugin_ui.draw_in(default, target, lambda: widget.render(default))
          text(FontWeight.MEDIUM, name, target.x, target.y, 32)

        self._hud_overlays.each(preview)
      finally:
        rl.end_scissor_mode()
      for name, rect in boxes.items():
        target = rl.Rectangle(*rect)
        rl.draw_rectangle_lines_ex(target, 2, rl.Color(255, 255, 255, 60))
        if not tiles.hud_visible(self._hud, name):
          rl.draw_rectangle_rec(target, rl.Color(0, 0, 0, 160))
          rl.draw_rectangle_lines_ex(target, 2, DIM)
        if name == self._hud_selected:
          rl.draw_rectangle_lines_ex(target, 3, rl.WHITE)
      if self._hud_selected in boxes and self._hud_drag is None:
        name = self._hud_selected
        required = name in tiles.HUD_REQUIRED
        scales = tuple(s for s in tiles.HUD_SCALES if not required or s >= 1.0)
        current = tiles.hud_scale(self._hud.get(name))
        labels = {"toggle": tr("Hide") if tiles.hud_visible(self._hud, name) else tr("Show"),
                  "smaller": tr("-"), "bigger": tr("+")}
        for action, (x, y, w, h) in tiles.hud_controls(boxes[name], required).items():
          disabled = (action == "smaller" and current == scales[0]) or (action == "bigger" and current == scales[-1])
          rl.draw_rectangle_rec(rl.Rectangle(x, y, w, h), DIM if disabled else rl.WHITE)
          label = labels[action]
          text(FontWeight.SEMI_BOLD, label, x + (w - text_w(FontWeight.SEMI_BOLD, label, 40)) / 2, y + (h - 40) / 2,
               40, rl.Color(20, 20, 24, 255))
    cell = tiles.add_cell(placed, drive, self._view_position)
    if cell is not None:
      x, y, w, h = tiles.cell_rect(*cell)
      rl.draw_rectangle_lines_ex(rl.Rectangle(x, y, w, h), 3, DIM)
      rl.draw_line_ex(rl.Vector2(x + w / 2 - 40, y + h / 2), rl.Vector2(x + w / 2 + 40, y + h / 2), 6, DIM)
      rl.draw_line_ex(rl.Vector2(x + w / 2, y + h / 2 - 40), rl.Vector2(x + w / 2, y + h / 2 + 40), 6, DIM)
    if self._selected in placed and self._drag is None:
      self._draw_corner_buttons(tiles.tile_rect(placed[self._selected]))
    if self._drag is not None and self._drag in placed and self._gesture.start is not None:
      e = self._entries[self._drag]
      x, y, w, h = tiles.tile_rect(placed[self._drag])
      dx, dy = self._gesture.pos[0] - self._gesture.start[0], self._gesture.pos[1] - self._gesture.start[1]
      if self._face(e, placed[self._drag][2], now):
        self._blit(e, rl.Rectangle(x + dx, y + dy, w, h))
    if self._bar_drag or (self._drag is None and not self._view_drag and self._hud_drag is None):
      self._draw_bar(placed)  # hidden while dragging, so a drop under it is visible

  def _bar_buttons(self, placed) -> dict:
    at = self._bar_at
    if self._bar_drag and self._gesture.start is not None:
      at = (at[0] + self._gesture.pos[0] - self._gesture.start[0], at[1] + self._gesture.pos[1] - self._gesture.start[1])
    return tiles.bar_buttons(placed, self._layout == "drive", self._view_position, at=at)

  def _draw_bar(self, placed) -> None:
    medium = gui_app.font(FontWeight.MEDIUM)
    for name, (x, y, w, h) in self._bar_buttons(placed).items():
      active = name == self._layout
      rl.draw_rectangle_rec(rl.Rectangle(x, y, w, h), rl.Color(*Tile.COLOR, 255) if active else rl.Color(72, 75, 88, 255))
      label = {"park": tr("Park"), "drive": tr("Drive"), "done": tr("Done")}[name]
      width = measure_text_cached(medium, label, 56).x
      rl.draw_text_ex(medium, label, rl.Vector2(x + (w - width) / 2, y + (h - 56) / 2), 56, 0, rl.WHITE)

  @staticmethod
  def _draw_corner_buttons(rect) -> None:
    buttons = tiles.corner_buttons(rect)
    for name, (cx, cy, r) in buttons.items():
      rl.draw_circle(int(cx), int(cy), r, rl.WHITE)
      d = r * 0.45
      dark = rl.Color(20, 20, 24, 255)
      if name == "unpin":  # x
        rl.draw_line_ex(rl.Vector2(cx - d, cy - d), rl.Vector2(cx + d, cy + d), 5, dark)
        rl.draw_line_ex(rl.Vector2(cx - d, cy + d), rl.Vector2(cx + d, cy - d), 5, dark)
      else:  # diagonal double arrow
        rl.draw_line_ex(rl.Vector2(cx - d, cy + d), rl.Vector2(cx + d, cy - d), 5, dark)
        for sx, sy in ((cx + d, cy - d), (cx - d, cy + d)):
          k = 1 if sx > cx else -1
          rl.draw_line_ex(rl.Vector2(sx, sy), rl.Vector2(sx - k * d * 0.9, sy), 5, dark)
          rl.draw_line_ex(rl.Vector2(sx, sy), rl.Vector2(sx, sy + k * d * 0.9), 5, dark)

  def _on_gesture(self, start, event, x, y, placed) -> None:
    if event is None or start is None:
      return
    sx, sy = start[:2]
    bar = self._bar_buttons(placed)
    on_bar = any(x0 <= sx < x0 + w and y0 <= sy < y0 + h for x0, y0, w, h in bar.values())
    if self._edit and (self._bar_drag or on_bar):
      if event == "tap" and not self._bar_drag:
        self._bar_tap(sx, sy, bar)
      elif event == "drag" and not self._bar_drag:
        self._bar_at, self._bar_drag = bar["park"][:2], True
      elif event == "drop" and self._bar_drag:
        moved = tiles.bar_buttons(placed, self._layout == "drive", self._view_position,
                                  at=(self._bar_at[0] + x - sx, self._bar_at[1] + y - sy))
        self._bar_at, self._bar_drag = moved["park"][:2], False
      return
    if self._mode != "onroad" and self._edit and self._layout == "drive":
      boxes = self._hud_boxes()
      if self._hud_selected in boxes and self._hud_drag is None:
        selected = self._hud_selected
        required = selected in tiles.HUD_REQUIRED
        controls = tiles.hud_controls(boxes[selected], required)
        action = next((name for name, (bx, by, bw, bh) in controls.items() if bx <= sx < bx + bw and by <= sy < by + bh), None)
        if action is not None:
          if event == "tap":
            vx, vy, vw, vh = self._drive_view()
            hx, hy, _, _ = boxes[selected]
            visible, scale = tiles.hud_visible(self._hud, selected), tiles.hud_scale(self._hud.get(selected))
            scales = tuple(s for s in tiles.HUD_SCALES if not required or s >= 1.0)
            if action == "toggle":
              visible = not visible
            elif action == "smaller" and scale != scales[0]:
              scale = scales[scales.index(scale) - 1]
            elif action == "bigger" and scale != scales[-1]:
              scale = scales[scales.index(scale) + 1]
            else:
              return
            self._hud[selected] = [(hx - vx) / vw, (hy - vy) / vh, visible, scale]
            self._save()
          return
      name = self._hud_drag or next((name for name, (bx, by, bw, bh) in reversed(list(boxes.items()))
                                    if bx <= sx < bx + bw and by <= sy < by + bh), None)
      if name is not None:
        bx, by, bw, bh = boxes[name]
        vx, vy, vw, vh = self._drive_view()
        visible, scale = tiles.hud_visible(self._hud, name), tiles.hud_scale(self._hud.get(name))
        self._selected = None
        if event == "drag":
          self._hud_drag, self._hud_selected = name, name
        elif event == "drop":
          lx, ly, lw, lh = self._hud_defaults()[name]
          default = (vx + lx * vw / tiles.MICI[0], vy + ly * vh / tiles.MICI[1], lw * vw / tiles.MICI[0], lh * vh / tiles.MICI[1])
          entry = [(bx - vx + x - sx) / vw, (by - vy + y - sy) / vh, visible, scale]
          bx, by, _, _ = tiles.hud_rect(entry, default, (vx, vy, vw, vh))
          self._hud[name] = [(bx - vx) / vw, (by - vy) / vh, visible, scale]
          self._hud_drag = None
          self._save()
        elif event == "tap":
          self._hud_selected = None if name == self._hud_selected else name
        return
    if self._mode == "onroad" or (self._edit and self._layout == "drive"):
      vx, vy, vw, vh = tiles.view(self._view_position)
      if vx <= sx < vx + vw and vy <= sy < vy + vh:
        if event == "tap":
          self._hud_selected = None
        if event == "drag":
          self._view_drag = True
        elif event == "drop":
          col, row = self._view_position
          self._view_position = (min(max(col + round((x - sx) / tiles.PITCH), 0), tiles.COLS - tiles.DRIVE_CELLS[2]),
                                 min(max(row + round((y - sy) / tiles.PITCH), 0), tiles.ROWS - tiles.DRIVE_CELLS[3]))
          self._view_drag = False
          self._save()
        return
      if self._mode == "onroad":
        if event == "long":
          self._screen_override = "offroad"
        return
    tid = tiles.hit(placed, sx, sy)
    if event == "long" and ui_state.started:
      self._screen_override = "onroad"  # tiles are edited only when not started
    elif event == "long" and not self._edit:
      self._hud_selected = None
      self._edit, self._selected = True, tid
    elif event == "drag" and self._edit and tid is not None:
      self._hud_selected = None
      self._drag = self._selected = tid
    elif event == "drop" and self._drag is not None:
      cell = tiles.cell_at(x, y)
      if cell is not None:
        self._save(tiles.move(self._layouts[self._layout][0], placed, self._drag, cell, self._layout == "drive", self._view_position))
      self._drag = None
    elif event == "tap" and not self._edit:
      if tid is not None:
        self.open_tile(tid)
    elif event == "tap":
      self._edit_tap(sx, sy, tid, placed)

  def _bar_tap(self, x, y, bar) -> None:
    for name, (x0, y0, w, h) in bar.items():
      if x0 <= x < x0 + w and y0 <= y < y0 + h:
        self._selected, self._hud_drag, self._hud_selected = None, None, None
        if name == "done":
          self._edit, self._layout = False, "park"
        else:
          self._layout = name

  def _edit_tap(self, x, y, tid, placed) -> None:
    self._hud_selected = None
    if self._selected in placed:
      for name, (cx, cy, r) in tiles.corner_buttons(tiles.tile_rect(placed[self._selected])).items():
        if (x - cx) ** 2 + (y - cy) ** 2 <= (r + 12) ** 2:
          if name == "unpin":
            self._save(*tiles.unpin(*self._layouts[self._layout], self._selected))
            self._selected = None
          else:
            self._save(tiles.resize(self._layouts[self._layout][0], self._selected, self._entries[self._selected].spec.sizes))
          return
    if tid is not None:
      self._selected = None if tid == self._selected else tid
      return
    drive = self._layout == "drive"
    if tiles.cell_at(x, y) is not None and tiles.cell_at(x, y) == tiles.add_cell(placed, drive, self._view_position):
      ids = tiles.addable(self._visible(self._layout), self._layouts[self._layout][1], placed)
      self._open(tr("add tiles"), AddTilesPage([self._entries[i] for i in ids], self._pin))
      return
    self._selected = None

  # detail pages

  def _draw_detail(self, top, events) -> None:
    title, page = self._stack[-1] if self._stack else ("", None)
    parent = self._stack[-2][0] if len(self._stack) > 1 else "xpilot"
    if self._mode == "mici" and page is not None:
      parent, title = title, type(top).__name__
    self._draw_title(parent, title, events)
    if self._mode == "mici":
      self._show(None)
      return  # the mici page is drawn in FALLBACK_VIEW
    self._show(page)
    if page is not None:
      with self._native(events):
        page.render(rl.Rectangle(*CONTENT))

  def _draw_title(self, parent: str, title: str, events) -> None:
    back = rl.Rectangle(*BACK)
    for ev in events:
      inside = rl.check_collision_point_rec(ev.pos, back)
      if ev.left_pressed and inside:
        self._back_pressed = True
      if ev.left_released and self._back_pressed:
        self._back_pressed = False
        if inside:
          self._back()
    cx, cy, r = 72, 84, 38
    color = DIM if self._back_pressed else rl.WHITE
    rl.draw_ring(rl.Vector2(cx, cy), r - 3, r, 0, 360, 48, color)
    rl.draw_line_ex(rl.Vector2(cx - 16, cy), rl.Vector2(cx + 16, cy), 4, color)
    rl.draw_line_ex(rl.Vector2(cx - 16, cy), rl.Vector2(cx - 2, cy - 14), 4, color)
    rl.draw_line_ex(rl.Vector2(cx - 16, cy), rl.Vector2(cx - 2, cy + 14), 4, color)
    rl.draw_text_ex(gui_app.font(FontWeight.SEMI_BOLD), parent.upper(), rl.Vector2(CONTENT[0], 50), 34, 2, DIM)
    rl.draw_text_ex(gui_app.font(FontWeight.NORMAL), title.lower(), rl.Vector2(CONTENT[0] - 6, 88), 110, 0, rl.WHITE)
    rl.draw_line_ex(rl.Vector2(CONTENT[0], TITLE_H - 2), rl.Vector2(CONTENT[0] + CONTENT[2], TITLE_H - 2), 2, rl.Color(255, 255, 255, 30))
