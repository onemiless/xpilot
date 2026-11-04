"""UI side of plugins: finds Tile, HudOverlay, ScreenOverlay and RoadOverlay classes in openpilot/tesla/ui/slots/ and in each plugin's ui.py,
and hides a plugin's widgets while the plugin is switched off (Plugins page). A module that fails to import is
logged and skipped, so a broken plugin UI never takes the UI down."""
import importlib
import inspect
import pkgutil
import time

import pyray as rl

from openpilot.common.swaglog import cloudlog
from openpilot.system.ui.lib.application import gui_app
from openpilot.system.ui.widgets import Widget
from openpilot.tesla.plugin_params import ParamsView
from openpilot.tesla.ui import tiles
from openpilot.tesla.plugins import PLUGINS, resolve, ui_module

HUD_REFRESH_S = 1.0  # s between reads of the saved HUD layout
MAX_FAILURES = 3  # a widget that raises this many times in a row is not drawn again


def draw_in(default, target, draw) -> None:
  """Draw default-space content uniformly transformed into target."""
  rl.rl_push_matrix()
  try:
    rl.rl_translatef(target.x, target.y, 0.0)
    scale = target.width / default.width
    rl.rl_scalef(scale, scale, 1.0)
    rl.rl_translatef(-default.x, -default.y, 0.0)
    draw()
  finally:
    rl.rl_pop_matrix()


class HudOverlay(Widget):
  """Drawn over the mici onroad HUD. Subclass in a plugin's ui.py; must construct with no arguments."""

  def default_rect(self, view):
    """Default logical box; override for the overlay's footprint. Draw relative to render(rect)."""
    return rl.Rectangle(view.x, view.y, 120, 48)

  def update(self) -> None:
    """Every frame before render(); read ui_state.sm here."""


class ScreenOverlay:
  """Drawn by the big-screen host onroad, after its tiles, in screen px. Subclass in a plugin's ui.py; must construct with no arguments."""

  def update(self, sm) -> None:
    """Every frame before draw(); sm is ui_state.sm."""

  def draw(self, zones: dict) -> None:
    """zones: "left", "right", "top", "bottom" (strips around the view) and "view" (the mici view) as rl.Rectangle."""


class RoadOverlay:
  """Drawn on the road by the mici model renderer, after the path, while not disengaged. Subclass in a plugin's ui.py;
  must construct with no arguments. road: lane_points() gives the 4 lane lines as Nx3 car-space arrays (x forward, y left);
  polygon(points, half_width) maps such a line to a screen-px polygon for draw_polygon(road.rect, ...), empty when off screen."""

  def update(self, sm) -> None:
    """Every frame before draw(); sm is ui_state.sm."""

  def draw(self, road) -> None:
    pass


def _classes(module_name: str, base) -> list:
  try:
    mod = importlib.import_module(module_name)
  except ModuleNotFoundError as e:
    if e.name != module_name:
      cloudlog.exception(f"ui module {module_name} failed to load")
    return []
  except Exception:
    cloudlog.exception(f"ui module {module_name} failed to load")
    return []
  return [cls for _, cls in inspect.getmembers(mod, inspect.isclass)
          if issubclass(cls, base) and cls is not base and cls.__module__ == mod.__name__ and (base in (HudOverlay, ScreenOverlay, RoadOverlay) or hasattr(cls, "SPEC"))]


def load(base) -> list[tuple[str | None, object]]:
  """(plugin NAME or None for core tiles, widget instance) for every class deriving from base."""
  from openpilot.tesla.ui import slots as slots_pkg
  found: list[tuple[str | None, type]] = []
  for info in pkgutil.iter_modules(slots_pkg.__path__):
    found += [(None, cls) for cls in _classes(f"{slots_pkg.__name__}.{info.name}", base)]
  for plugin in PLUGINS:
    found += [(plugin.NAME, cls) for cls in _classes(ui_module(plugin), base)]
  out = []
  for name, cls in found:
    try:
      out.append((name, cls()))
    except Exception:
      cloudlog.exception(f"ui widget {cls.__name__} failed to construct")
  return out


class EnabledPlugins:
  """Names of active plugins (switched on and winning their claims), re-read from Params at most once a second."""

  def __init__(self, params):
    self._params, self._names, self._t = params, set(), -1.0

  def get(self) -> set[str]:
    now = time.monotonic()
    if now - self._t >= 1.0:
      self._t = now
      self._names = resolve(self._params)[0]
    return self._names

  def shows(self, name: str | None) -> bool:
    return name is None or name in self.get()


class Overlays:
  """Every widget deriving from base; each() skips a widget while its plugin is off, and for good once it raised
  MAX_FAILURES times in a row, so a broken plugin overlay never takes the UI down."""

  def __init__(self, base, params):
    self._entries = [[name, w, 0] for name, w in load(base)]
    self._enabled = EnabledPlugins(params)
    self._layout_params = ParamsView(params, tiles.LAYOUT_SPEC)
    self._hud, self._hud_t, self._hud_v = {}, -HUD_REFRESH_S, -1
    if base is HudOverlay:
      for name, widget, _ in self._entries:
        widget.plugin_id = name or type(widget).__name__

  def render_hud(self, rect) -> None:
    """Apply the persisted HUD rect before Widget.render; hidden overlays never render."""
    now = time.monotonic()
    if self._hud_v != tiles.layout_version or now - self._hud_t >= HUD_REFRESH_S:  # no Params read every frame
      self._hud, self._hud_t, self._hud_v = tiles.hud_layout(self._layout_params.get(tiles.LAYOUT_PARAM)), now, tiles.layout_version
    hud = self._hud

    def draw(widget):
      entry = hud.get(widget.plugin_id)
      if entry is not None and not entry[2]:
        return
      default = widget.default_rect(rect)
      target = rl.Rectangle(*tiles.hud_rect(entry, (default.x, default.y, default.width, default.height),
                                            (rect.x, rect.y, rect.width, rect.height)))
      draw_in(default, target, lambda: widget.render(default))

    rl.begin_scissor_mode(int(rect.x), int(rect.y), int(rect.width), int(rect.height))
    try:
      self.each(draw)
    finally:
      rl.end_scissor_mode()

  def each(self, fn) -> None:
    for entry in self._entries:
      name, widget, failures = entry
      if failures >= MAX_FAILURES or not self._enabled.shows(name):
        continue
      try:
        fn(widget)
        entry[2] = 0
      except Exception:
        entry[2] += 1
        cloudlog.exception(f"overlay {type(widget).__name__} failed")


def draw_builtin(name, rect, draw) -> None:
  """Transform original SP drawing into its saved box; keep safety indications visible in hand-edited files."""
  host = gui_app.host
  if host is None:
    draw(rect)
    return
  key = "builtin:" + name
  entry = host._hud.get(key)
  if not tiles.hud_visible(host._hud, key):
    return
  default = rl.Rectangle(*tiles.builtin_hud_boxes((rect.x, rect.y, rect.width, rect.height))[key])
  target = rl.Rectangle(*tiles.hud_rect(entry, (default.x, default.y, default.width, default.height),
                                        (rect.x, rect.y, rect.width, rect.height)))
  draw_in(default, target, lambda: draw(rect))
