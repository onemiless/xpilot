#!/usr/bin/env python3
"""Real GPU E2E for the UI v4 tiles; saves screenshots and result.json.

Failure modes (listed before implementation):
1. Offroad tile screen has a viewport, draws mici, or omits a settings tile.
2. A mapped tap at a settings tile's center opens the wrong title/PAGE; Models
   or its Small models sub-page is not native, or BACK loses the parent page.
3. A native detail page draws mici or grows the application nav stack beyond 1.
4. BACK does not return to tiles; Plugins -> Store does not push a second detail
   level, or BACK from Store does not return to the same Plugins page.
5. Holding Toggles for LONG_PRESS+0.2 s does not select it in edit mode; its
   resize corner does not change size; + does not open "add tiles"; tapping the
   selected tile or an empty non-add cell does not deselect while staying in
   edit mode; Done does not leave edit mode and return to the park layout.
6. Editing permanently changes XpilotTileLayout bytes (including absence),
   or either of the deep-copied park/drive layouts; restore even on failure.
7. More than 2 tile faces redraw over 60 idle, touch-free tile-screen frames.
8. Synthetic onroad has the wrong tiles.VIEW, a tile overlaps VIEW/blocked
   cells, settings remain placed, a required data tile is missing, or the
   forced blind-spot overlay is absent, never draws, or raises.
9. Drive in the edit bar does not show only data tiles outside blocked cells;
   the default layout does not alternate left/right; a 20-move drag to (7,3)
   does not save that fixed cell (clamped for size) or persist into onroad.
10. Device rows open the wrong native sub-page/title or BACK loses Device;
    training leaves its first step, or driver camera BACK leaves driver view on.
11. A separate, untouched native OnboardingPage cannot render full screen;
    the real onboarding window is pushed, or any offroad frame draws mici.
12. IsDriverViewEnabled is not restored to its original value on exit.
13. Real NetworkUI in NEEDS_AUTH under the host clears three typed keys over later modal frames,
    or the modal renders the background page/tile screen.
14. Onroad tiles use the chosen accent, or park/onroad transitions reuse a stale colored texture.
15. Onroad/drive-edit view drags do not snap, clamp, persist or re-place covered fixed tiles;
    other onroad touches change state or reach mici widgets.
16. Engaged camera shader output is desaturated, differs from disengaged color, or never renders the production shader.
17. Drive edit omits enabled HUD boxes; dragging/toggling does not persist, restart loses it, the onroad renderer ignores
    saved rect/visibility, dragging escapes the view, disabled overlays show, or moving the view loses HUD fractions.
18. Drive/park add list includes the wrong show; adding/dropping/fixing a tile enters the Driving view; onroad info cannot
    render outside the view as a Tile or in a ScreenOverlay side zone.
19. Black is absent from Toggles, its park/onroad tiles have a border or are not black with white text,
    or switching themes leaves stale black/bordered textures or changes other themes onroad.

Run with comma service stopped and native libraries built. No CAN transmission,
settings actions, store refresh/install, or reboot/reset/uninstall confirmation.
20. Edit bar buttons are smaller than twice their old height, retain small text, or cover tiles/the Driving view.
21. Built-in HUD boxes cannot move/hide/persist, or production HUD drawing ignores their layout.
22. Saved hidden/invalid entries hide steering/model/blindspot safety indications, or alerts enter the editor/are suppressed.
23. Plugins page omits the declared use next to a plugin.
24. A hideable HUD box lacks a high-contrast Hide/Show button with >=40 px text, a required box has a button,
    or hidden/required boxes lack Hidden/Move only labels.
25. A HUD button tap does not toggle/save/reload visibility, or a box-body tap changes visibility/saved layout.
26. A drag starting on a HUD button moves/toggles/saves the box or view, or a body drag stops moving the box.
27. HUD buttons overlap each other or leave the screen, including coincident boxes and view-edge positions.
28. Either settings page omits text/secret rows, fails to prefill text/clear secret, or cannot save through ParamsView.
29. Non-printable/non-ASCII/overlong touch or hardware input changes the keyboard/param instead of preserving it with an error.
30. Secret content or its stored length appears in row/keyboard draw calls or logs, including immediately after typing.
31. Onroad row entry or confirmation after an offroad->onroad transition writes a setting.
32. Empty secret lacks Not set, Cancel changes storage, or existing BOOL/range/choices controls change.
"""
import argparse
from copy import deepcopy
import json
import subprocess
import tempfile
import time
import traceback
from pathlib import Path
import sys


def host_check(output):
  """Host GL composition: real host, NetworkUI and keyboard; fake services, fixture tiles and camera, no device IO."""
  import logging
  import os
  import types
  root = Path(__file__).resolve().parents[2]
  sys.path[:0] = [str(root), str(root / 'opendbc_repo')]
  output.mkdir(parents=True, exist_ok=True)
  sandbox = tempfile.TemporaryDirectory()
  os.environ.update(XPILOT_PLUGINS_DIR=sandbox.name, SCALE='1', SUNNYPILOT_UI='0')
  cereal = types.ModuleType('openpilot.cereal')
  cereal.__path__ = [str(root / 'openpilot/cereal')]
  import capnp
  cereal.log = capnp.load(str(root / 'openpilot/cereal/log.capnp'), imports=[str(root / 'opendbc_repo/opendbc/car')])
  sys.modules['openpilot.cereal'] = cereal
  sys.modules['openpilot.cereal.messaging'] = types.SimpleNamespace()
  sys.modules['openpilot.common.swaglog'] = types.SimpleNamespace(cloudlog=logging.getLogger('host-e2e'))
  sys.modules['openpilot.common.hardware'] = types.SimpleNamespace(PC=True, HARDWARE=types.SimpleNamespace(get_device_type=lambda: 'pc'), COMMA_HARDWARE=False)
  from tools.sp_tesla_e2e.offroad_pages_check import FakeParams
  sys.modules['openpilot.common.params'] = types.SimpleNamespace(Params=FakeParams)
  params = FakeParams()
  state = types.SimpleNamespace(started=False, engaged=False, status=0, sm={}, params=params, add_offroad_transition_callback=lambda cb: None)
  sys.modules['openpilot.selfdrive.ui.ui_state'] = types.SimpleNamespace(
    ui_state=state, UIStatus=types.SimpleNamespace(DISENGAGED=0, ENGAGED=1, LAT_ONLY=2, LONG_ONLY=3),
    ChestnutState=types.SimpleNamespace(LOADING=1, UNCOMPILED=2, FAILED=3, ACTIVE=4), device=types.SimpleNamespace(awake=True))
  sys.modules['openpilot.sunnypilot.hardware.profile'] = types.SimpleNamespace(HardwareProfile=types.SimpleNamespace(C3XL='c3xl'), get_hardware_profile=lambda: 'pc', has_driver_camera=lambda: True)
  import pyray as rl
  from openpilot.system.ui.lib.application import gui_app, FontWeight, MouseEvent, MousePos
  from openpilot.system.ui.widgets import Widget
  from openpilot.system.ui.widgets.button import Button
  from openpilot.system.ui.widgets.network import NetworkUI, UIState, Network, SecurityType
  from openpilot.tesla.ui import tiles
  from openpilot.tesla.ui.tile import Tile

  class Page(Widget):
    def __init__(self, *args):
      super().__init__()

    def _render(self, rect):
      pass

  for module, name in (('device', 'DevicePage'), ('onboarding', 'OnboardingPage')):
    sys.modules['openpilot.tesla.ui.pages.' + module] = types.SimpleNamespace(**{name: Page})
  from openpilot.tesla.ui import screen, plugin_ui
  screen.load = lambda base: [(None, type('DataTile', (Tile,), {'SPEC': tiles.TileSpec(str(i), title, sizes=('small',), show='always'),
                            'draw': lambda self, rect, size, value: self.draw_value(rect, str(42 + int(self.SPEC.id)), 'km/h')})())
                            for i, title in enumerate(('Speed', 'Lead', 'Battery', 'Range', 'Temperature', 'Clock', 'Limit', 'Power'))]
  class Hud(plugin_ui.HudOverlay):
    def default_rect(self, view):
      return rl.Rectangle(view.x + 12, view.y + 24, 120, 48)

    def _render(self, rect):
      hud_draws.append([rect.x, rect.y, rect.width, rect.height])
      rl.draw_rectangle_rec(rect, rl.Color(0, 100, 210, 255))
      rl.draw_text_ex(gui_app.font(FontWeight.MEDIUM), 'Plugin HUD', rl.Vector2(rect.x + 8, rect.y + 8), 20, 0, rl.WHITE)

  class Side(plugin_ui.ScreenOverlay):
    def draw(self, zones):
      r = zones['right']
      side_draws.append([r.x, r.y, r.width, r.height])
      rl.draw_rectangle(int(r.x), int(r.y), int(r.width), 12, rl.GREEN)

  hud_draws, side_draws = [], []
  plugin_ui.load = lambda base: [('example', Hud())] if base is plugin_ui.HudOverlay else [('example', Side())]
  plugin_ui.EnabledPlugins.get = lambda self: {'example'}
  screen.messaging.SubMaster = lambda services: types.SimpleNamespace(update=lambda timeout: None)
  park = type('ParkTile', (Tile,), {'SPEC': tiles.TileSpec('park', 'Park only', show='offroad')})()
  original_load = screen.load
  screen.load = lambda base: original_load(base) + [(None, park)]

  rl.set_trace_log_level(rl.TraceLogLevel.LOG_WARNING)
  rl.init_window(*tiles.SCREEN, 'xpilot host E2E')
  font = rl.load_font_ex('/System/Library/Fonts/Supplemental/Arial.ttf', 100, None, 0)
  gui_app._fonts = {weight: font for weight in FontWeight}
  gui_app._patch_text_functions()
  rl._orig_begin_scissor_mode = rl.begin_scissor_mode
  image = rl.gen_image_color(32, 32, rl.WHITE)
  icon = rl.load_texture_from_image(image)
  rl.unload_image(image)
  gui_app.texture = lambda *args, **kwargs: icon
  host = screen.BigScreenHost()
  host._layout_params.put(tiles.THEME_PARAM, 'Crimson')
  host._theme()
  pinned, hidden = host._layouts['drive']
  host._layouts['drive'] = ([(tid, size, (0, 3) if tid == '0' else fixed) for tid, size, fixed in pinned], hidden)
  main = Page()
  main._onboarding_window = object()
  gui_app._nav_stack = [main]
  host.bind_settings(main, None)
  # Production camera shader and HUD with deterministic services, NV12 input and fixture icons.
  import numpy as np
  stream = types.SimpleNamespace(VISION_STREAM_CABIN=2, VISION_STREAM_NARROW_ROAD=0)
  client = types.SimpleNamespace(width=96, stride=96, height=64, is_connected=lambda: True)
  sys.modules['openpilot.cereal.visionipc'] = types.SimpleNamespace(VisionStreamType=stream)
  sys.modules['msgq.visionipc'] = types.SimpleNamespace(VisionIpcClient=lambda *a, **kw: client, VisionBuf=object)
  from openpilot.selfdrive.ui.mici.onroad.cameraview import CameraView
  y = np.zeros((64, 96), dtype=np.uint8)
  uv = np.zeros((32, 96), dtype=np.uint8)
  for i, (red, green, blue) in enumerate(((.9, .1, .1), (.1, .9, .1), (.1, .1, .9))):
    y[:, i * 32:(i + 1) * 32] = round(255 * (.299 * red + .587 * green + .114 * blue))
    uv[:, i * 32:(i + 1) * 32:2] = round(255 * (-.168736 * red - .331264 * green + .5 * blue + .5))
    uv[:, i * 32 + 1:(i + 1) * 32:2] = round(255 * (.5 * red - .418688 * green - .081312 * blue + .5))
  camera_frame = types.SimpleNamespace(width=96, height=64, uv_offset=y.size, data=np.concatenate((y.ravel(), uv.ravel())))
  client.recv = lambda **kw: camera_frame
  camera = CameraView('fixture', stream.VISION_STREAM_NARROW_ROAD)
  camera._initialize_textures()
  logical = rl.load_render_texture(*tiles.MICI)
  gui_app._host = host
  gui_app.sunnypilot_ui = lambda: True
  state.is_offroad = lambda: not state.started
  state.started_frame, state.chestnut_state, state.is_metric, state.blindspot = 0, 1, True, True
  class SM(dict):
    recv_frame = {'carState': 1}
  state.sm = SM(carState=types.SimpleNamespace(vCruiseCluster=70, vEgoCluster=18, vEgo=18, steeringAngleDeg=0,
                                               leftBlindspot=True, rightBlindspot=True),
                controlsState=types.SimpleNamespace(deprecated=types.SimpleNamespace(vCruise=70),
                  lateralControlState=types.SimpleNamespace(which=lambda: 'torqueState')),
                selfdriveState=types.SimpleNamespace(enabled=True),
                carOutput=types.SimpleNamespace(actuatorsOutput=types.SimpleNamespace(torque=.4)), onroadEvents=[])

  from openpilot.selfdrive.ui.sunnypilot.mici.onroad.hud_renderer import HudRendererSP
  renderer = HudRendererSP()
  builtin_draws = {}
  for name, obj, method in (('torque_bar', renderer._torque_bar, 'render'), ('set_speed', renderer, '_draw_set_speed'),
                             ('model_source', renderer, '_draw_model_source'), ('steering_wheel', renderer, '_draw_steering_wheel'),
                             ('blind_spot_indicators', renderer.blind_spot_indicators, 'render')):
    original = getattr(obj, method)
    def record(rect, name=name, original=original):
      builtin_draws.setdefault(name, []).append([rect.x, rect.y, rect.width, rect.height])
      return original(rect)
    setattr(obj, method, record)

  target = rl.load_render_texture(*tiles.SCREEN)
  errors, cases, clicks = [], [], []
  mici = Button('mici', lambda: clicks.append(True))

  def check(ok, message):
    if not ok:
      errors.append(message)

  def frame(events=(), name=None, hud=True):
    gui_app._mouse_events = list(events)
    mici.render(rl.Rectangle(0, 0, *tiles.MICI))
    view = host.viewport()
    if view is not None:
      rl.begin_texture_mode(logical)
      rl.clear_background(rl.BLACK)
      camera.render(rl.Rectangle(0, 0, *tiles.MICI))
      if hud:
        renderer.render(rl.Rectangle(0, 0, *tiles.MICI))
      rl.end_texture_mode()
    rl.begin_texture_mode(target)
    rl.clear_background(rl.BLACK)
    if view is not None:
      rl.draw_texture_pro(logical.texture, rl.Rectangle(0, 0, tiles.MICI[0], -tiles.MICI[1]), view, rl.Vector2(0, 0), 0, rl.WHITE)
    host.render()
    rl.end_texture_mode()
    if name:
      image = rl.load_image_from_texture(target.texture)
      rl.image_flip_vertical(image)
      check(rl.export_image(image, str(output / (name + '.png'))), 'export ' + name)
      rl.unload_image(image)

  def event(point, phase):
    return MouseEvent(MousePos(*host.to_logical(*point)), 0, phase == 0, phase == 1, phase != 1, time.monotonic())

  def touch(point):
    frame([event(point, 0)])
    frame([event(point, 1)])

  def drag(start, end, follow=False):
    before = host._view
    frame([event(start, 0)])
    for i in range(1, 21):
      frame([event((start[0] + (end[0] - start[0]) * i / 20, start[1] + (end[1] - start[1]) * i / 20), 2)])
      if i == 10 and follow:
        check(host._view != before, '15 view did not follow the drag before release')
    frame([event(end, 1)])
    frame()

  def center(rect):
    x, y, w, h = rect
    return x + w / 2, y + h / 2

  def hud_body(name):
    boxes = host._hud_boxes()
    buttons = tiles.hud_buttons(boxes, host._drive_view())
    x, y, w, h = boxes[name]
    points = [center(boxes[name])] + [(x + dx, y + dy) for dy in range(12, int(h), 32) for dx in range(12, int(w), 32)]
    for px, py in points:
      if any(bx <= px < bx + bw and by <= py < by + bh for bx, by, bw, bh in buttons.values()):
        continue
      hit = next(n for n, (bx, by, bw, bh) in reversed(list(boxes.items())) if bx <= px < bx + bw and by <= py < by + bh)
      if hit == name:
        return px, py
    raise AssertionError('HUD body is covered: ' + name)

  def color():
    e = host._entries[next(iter(host._placed))]
    image = rl.load_image_from_texture(e.texture.texture)
    pixel = rl.get_image_color(image, 3, 3)
    rgb = (pixel.r, pixel.g, pixel.b)
    rl.unload_image(image)
    return rgb

  try:
    wifi = types.SimpleNamespace(add_callbacks=lambda **kw: None, process_callbacks=lambda: None, set_active=lambda active: None, ipv4_address='')
    network = NetworkUI(wifi)
    panel = network._wifi_panel
    panel._networks = [Network('Host fixture', 100, SecurityType.WPA2, False)]
    panel._state_network, panel.state = panel._networks[0], UIState.NEEDS_AUTH
    host._open('network', network)
    frame()
    keyboard = panel.keyboard
    check(gui_app.get_active_widget() is keyboard, '13 NetworkUI did not push keyboard')
    frame()
    for key in 'abc':
      touch(center((keyboard._all_keys[key].rect.x, keyboard._all_keys[key].rect.y,
                    keyboard._all_keys[key].rect.width, keyboard._all_keys[key].rect.height)))
    for _ in range(12):
      frame()
    check(keyboard.text == 'abc', f'13 modal keyboard cleared typed text: {keyboard.text!r}')
    check(len(gui_app._nav_stack) == 2, '13 modal was pushed repeatedly')
    frame(name='wifi-keyboard')
    cases.append({'id': 13, 'text': keyboard.text, 'later_frames': 12})
    gui_app.pop_widgets_to(main, instant=True)
    host._stack = []
    frame()
    try:
      import contextlib
      import io
      from openpilot.tesla import manifest as mf
      from openpilot.tesla.plugin_params import ParamsView
      from openpilot.tesla.ui.pages.base import ListPage, SettingRow
      from openpilot.tesla.ui.settings_page import SettingsList
      from openpilot.system.ui.widgets.keyboard import Keyboard, ENTER_KEY
      from openpilot.tesla.ui.settings_meta import setting_label
      fixture = Path(sandbox.name) / 'g6_fixture'
      fixture.mkdir()
      keys = ('G6Text', 'G6Secret')
      (fixture / 'plugin.json').write_text(json.dumps({'id': 'g6_fixture', 'api': 1, 'category': 'tools',
        'use': 'both', 'entry': 'plugin:P', 'params': {key: {'type': 'STRING'} for key in keys},
        'settings': [{'key': keys[0], 'label': 'Server name', 'type': 'text', 'max_len': 8},
                     {'key': keys[1], 'label': 'Access token', 'type': 'secret', 'max_len': 8}]}))
      manifest = mf.parse(fixture / 'plugin.json')
      settings = manifest.settings
      from openpilot.tesla import plugins
      plugins.INSTALLED_SPECS.update(manifest.params)
      if not all(getattr(s, 'type', None) in ('text', 'secret') for s in settings):
        raise AssertionError('28 text/secret manifest metadata missing')
      values = ParamsView(params)
      values.put(keys[0], 'host')
      sentinel = 'G6s3cr!'
      values.put(keys[1], sentinel)
      captured, drawn = io.StringIO(), []
      original_draw = rl.draw_text_ex
      def capture_draw(font, text, *args):
        drawn.append(text)
        return original_draw(font, text, *args)
      rl.draw_text_ex = capture_draw
      try:
        with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
          for variant in ('native', 'mici'):
            if variant == 'native':
              page = ListPage()
              rows = [SettingRow(s) for s in settings]
              page.add(*(row.item for row in rows))
              open_row = lambda i: rows[i].item.callback()
            else:
              page = SettingsList(settings)
              open_row = lambda i: page._choose(settings[i])
            host._open('Plugin settings', page)
            frame(name='g6-' + variant + '-text-secret-rows')
            check('******' in drawn and sentinel not in drawn, '30 secret row content visible')
            open_row(0)
            editor = gui_app.get_active_widget()
            check(isinstance(editor, Keyboard) and editor.text == 'host', '28 text keyboard not prefilled')
            frame()
            rl.begin_texture_mode(target)
            with host._native([]):
              editor.render(rl.Rectangle(0, 0, 536, 240))
            rl.end_texture_mode()
            key_rect = editor._all_keys['a'].rect
            check(key_rect.height > 0 and (key_rect.y + key_rect.height) * 240 / 1080 <= 240,
                  '28 keyboard keys do not fit mici')
            frame()
            box = editor._input_box
            for bad in ('\n', '\x7f', 'é', '12345'):
              before = editor.text
              box.add_char_at_cursor(bad)
              check(editor.text == before and bool(editor._sub_title._text), '29 invalid input changed text/no error')
            editor.handle_key_press('a')
            editor._key_callback(ENTER_KEY)
            check(values.get(keys[0]) == 'hosta', '28 text edit not stored through ParamsView')
            open_row(1)
            editor = gui_app.get_active_widget()
            check(isinstance(editor, Keyboard) and editor.text == '', '28 secret keyboard prefilled')
            frame()
            for key in sentinel:
              editor.handle_key_press(key)
              check(key not in editor._input_box._get_display_text(), '30 secret typed character echoed')
            frame(name='g6-' + variant + '-secret-keyboard')
            check(sentinel not in drawn, '30 secret visible in draw calls')
            editor._key_callback(ENTER_KEY)
            check(values.get(keys[1]) == sentinel, '28 secret edit not stored')
            open_row(1)
            gui_app.get_active_widget()._cancel_button_callback()
            check(values.get(keys[1]) == sentinel, '32 Cancel changed secret')
            open_row(0)
            editor = gui_app.get_active_widget()
            editor.set_text('changed')
            state.started = True
            editor._key_callback(ENTER_KEY)
            check(values.get(keys[0]) == 'hosta', '31 onroad confirmation wrote a value')
            before_stack = list(gui_app._nav_stack)
            open_row(1)
            check(gui_app._nav_stack == before_stack, '31 onroad row opened editor')
            state.started = False
            values.put(keys[1], '')
            page.show_event()
            drawn.clear()
            frame(name='g6-' + variant + '-empty-secret')
            check(setting_label(settings[1], '') == 'Not set' and 'Not set' in drawn, '32 empty secret label wrong')
            values.put(keys[0], 'host')
            values.put(keys[1], sentinel)
            host._stack = []
            frame()
          check(sentinel not in captured.getvalue(), '30 secret in stdout/stderr')
      finally:
        rl.draw_text_ex = original_draw
      for case in range(28, 33):
        cases.append({'id': case, 'pages': ['native', 'mici'], 'storage': 'temporary ParamsView STRING files',
                      'secret_in_draw_or_logs': False})
    except Exception:
      errors.append('28-32 text/secret settings: ' + traceback.format_exc())
    finally:
      state.started = False
      gui_app.pop_widgets_to(main, instant=True)
      host._stack = []
      frame()
    accent = color()
    check(accent == tiles.THEMES['Crimson'], f'14 park lost chosen theme: {accent}')
    state.started = True
    frame(name='onroad-before')
    neutral = color()
    check(neutral[0] == neutral[1] == neutral[2] and max(neutral) <= 64, f'14 colored onroad tile: {neutral}')
    state.started = False
    frame()
    check(color() == accent, f'14 stale park texture: {color()} != {accent}')
    state.started = True
    frame()
    check(color() == neutral, '14 stale onroad texture')
    cases.append({'id': 14, 'park_rgb': accent, 'onroad_rgb': neutral})
    before = tuple(host._view)
    saved = host._layout_params.get(tiles.LAYOUT_PARAM)
    outside = center(tiles.tile_rect(next(iter(host._placed.values()))))
    pixel = []
    for events in ([], [event(outside, 0)]):
      frame(events)
      image = rl.load_image_from_texture(target.texture)
      rl.image_flip_vertical(image)
      x, y, _, _ = tiles.tile_rect(next(iter(host._placed.values())))
      c = rl.get_image_color(image, x + 3, y + 3)
      pixel.append((c.r, c.g, c.b))
      rl.unload_image(image)
    frame([event(outside, 1)])
    check(pixel[0] == pixel[1], f'T22/15 outside touch changed tile appearance: {pixel}')
    touch(outside)
    drag(outside, center(before))
    touch(center(before))
    check(host._layout_params.get(tiles.LAYOUT_PARAM) == saved and tuple(host._view) == before and not host._stack and not host._edit,
          'T22/15 outside touch or view tap changed state')
    check(not clicks, 'T22/15 onroad touch reached mici')
    start = center(before)
    drag(start, (start[0] - tiles.PITCH + 47, start[1] + tiles.PITCH + 31), follow=True)
    wanted = (before[0] - tiles.PITCH, before[1] + tiles.PITCH, *before[2:])
    check(tuple(host._view) == wanted, f'T20/15 view did not snap: {host._view} != {wanted}')
    frame(name='onroad-after')
    raw = host._layout_params.get(tiles.LAYOUT_PARAM)
    check(raw is not None and raw['drive'].get('view') == [0, 1], f'T19/15 position not saved: {raw}')
    restarted = screen.BigScreenHost()
    check(tuple(restarted.viewport().__getattribute__(n) for n in ('x', 'y', 'width', 'height')) == wanted, 'T19/15 restart lost view')
    for p in host._place('drive').values():
      x, y, w, h = tiles.tile_rect(p)
      check(not (x < wanted[0] + wanted[2] and wanted[0] < x + w and y < wanted[1] + wanted[3] and wanted[1] < y + h), 'T21/15 tile overlaps view')
    zones = host._zones
    check((zones['view'].x, zones['view'].y, zones['view'].width, zones['view'].height) == wanted, 'T20/15 stale overlay zones')
    check(host._place('drive')['0'][:2] == (0, 0), 'T21/15 covered fixed tile did not flow')
    drag(center(host._view), (10000, -10000))
    expected = (before[0] + tiles.PITCH, before[1], *before[2:])
    check(tuple(host._view) == expected, f'T20/15 off-screen drop {host._view}')
    state.started = False
    host._edit, host._layout = True, 'drive'
    frame()
    start = (expected[0] + 800, expected[1] + 30)
    drag(start, (start[0] - 2 * tiles.PITCH, start[1] + tiles.PITCH))
    saved_edit = host._layout_params.get(tiles.LAYOUT_PARAM) or {}
    check(saved_edit.get('drive', {}).get('view') == [0, 1], '15 drive-edit placeholder did not move')
    frame(name='edit-drive-moved')
    cases.append({'id': 15, 'before': before, 'after': wanted, 'saved': raw, 'mici_clicks': clicks})
    state.started = True
    state.engaged, state.status = False, 0
    frame(hud=False)
    def camera_pixels():
      image = rl.load_image_from_texture(logical.texture)
      pixels = []
      for x in (150, 268, 385):
        c = rl.get_image_color(image, x, 120)
        pixels.append([c.r, c.g, c.b])
      rl.unload_image(image)
      return pixels
    disengaged = camera_pixels()
    state.engaged, state.status = True, 1
    frame(name='onroad-engaged-camera-color', hud=False)
    engaged = camera_pixels()
    check(engaged == disengaged and all(max(c) - min(c) > 120 for c in engaged), f'16 engaged camera desaturated: {disengaged} -> {engaged}')
    cases.append({'id': 16, 'input': 'NV12 RGB bars', 'disengaged_rgb': disengaged, 'engaged_rgb': engaged,
                  'shader': 'production CameraView PC shader; device EGL shader not executed'})
    state.started, state.engaged, state.status = False, False, 0
    host._edit, host._layout = True, 'drive'
    frame()
    try:
      boxes = host._hud_boxes()
      check(set(boxes) == {'example', 'builtin:torque_bar', 'builtin:set_speed', 'builtin:steering_wheel',
                           'builtin:model_source', 'builtin:blind_spot_indicators'}, f'17 missing enabled HUD boxes: {boxes}')
      frame(name='drive-edit-hud-boxes')
      buttons = tiles.hud_buttons(boxes, host._drive_view())
      check(set(buttons) == set(boxes) - tiles.HUD_REQUIRED, '24 missing hideable/extra required button')
      from tools.sp_tesla_e2e.tile_layout_check import overlap, inside
      check(all(inside(r, (0, 0, *tiles.SCREEN)) for r in buttons.values()) and
            all(not overlap(a, b) for i, a in enumerate(buttons.values()) for b in list(buttons.values())[i + 1:]),
            '27 HUD buttons overlap/leave screen')
      drawn_text, drawn_lines, fills = [], [], []
      original_text, original_lines, original_fill = screen.text, screen.lines_in, rl.draw_rectangle_rec
      def record_text(weight, label, x, y, size, color=rl.WHITE):
        drawn_text.append((label, x, y, size, (color.r, color.g, color.b)))
        original_text(weight, label, x, y, size, color)
      def record_lines(rect, lines, *args, **kwargs):
        drawn_lines.extend(lines)
        original_lines(rect, lines, *args, **kwargs)
      def record_fill(rect, color):
        c = rl.Color(*color) if isinstance(color, tuple) else color
        fills.append(((rect.x, rect.y, rect.width, rect.height), (c.r, c.g, c.b)))
        original_fill(rect, color)
      screen.text, screen.lines_in, rl.draw_rectangle_rec = record_text, record_lines, record_fill
      try:
        frame(name='drive-edit-hud-buttons')
        for name, r in buttons.items():
          check(any(label == 'Hide' and size >= 40 and inside((x, y, 1, size), r) and max(rgb) <= 32
                    for label, x, y, size, rgb in drawn_text) and
                any(np.allclose(r, rect, atol=.001) and rgb == (255, 255, 255) for rect, rgb in fills),
                '24 missing/high-contrast/small Hide label: ' + name)
        check(drawn_lines.count('Move only') == len(tiles.HUD_REQUIRED), '24 required box missing Move only')
        for name in buttons:
          before = deepcopy(host._hud)
          saved_before = host._layout_params.get(tiles.LAYOUT_PARAM)
          touch(hud_body(name))
          check(host._hud == before and host._layout_params.get(tiles.LAYOUT_PARAM) == saved_before,
                '25 body tap toggled/saved: ' + name)
          r = tiles.hud_buttons(host._hud_boxes(), host._drive_view())[name]
          view_before = host._view_position
          drag(center(r), (center(r)[0] + 260, center(r)[1] + 100))
          check(host._hud == before and host._view_position == view_before and host._hud_drag is None and
                host._layout_params.get(tiles.LAYOUT_PARAM) == saved_before, '26 button drag moved/toggled/saved: ' + name)
          touch(center(r))
          check(host._hud.get(name, [0, 0, True])[2] is False, '25 Hide failed: ' + name)
          saved_hidden = host._layout_params.get(tiles.LAYOUT_PARAM)
          check(saved_hidden['drive']['hud'][name] == host._hud[name] and screen.BigScreenHost()._hud == host._hud,
                '25 Hide not saved/reloaded: ' + name)
          drawn_text.clear()
          drawn_lines.clear()
          fills.clear()
          frame(name='drive-edit-one-hidden' if name == 'example' else None)
          check('Hidden' in drawn_lines and any(label == 'Show' and size >= 40 for label, _, _, size, _ in drawn_text),
                '24 hidden box missing Hidden/Show: ' + name)
          check(any(np.allclose(host._hud_boxes()[name], rect, atol=.001) and rgb == (60, 62, 70) for rect, rgb in fills),
                '24 hidden box not dimmed: ' + name)
          touch(center(r))
          check(host._hud[name][2] is True and screen.BigScreenHost()._hud == host._hud, '25 Show not saved/reloaded: ' + name)
        cases.extend([{'id': 24, 'buttons': buttons, 'font_px': 40, 'required': sorted(tiles.HUD_REQUIRED)},
                      {'id': 25, 'toggled_and_reloaded': list(buttons), 'body_taps_unchanged': True},
                      {'id': 26, 'button_drags_unchanged': list(buttons), 'body_drag': 'case 17'},
                      {'id': 27, 'buttons': buttons, 'geometry_cases': 'tile_layout_check T32'}])
      finally:
        screen.text, screen.lines_in, rl.draw_rectangle_rec = original_text, original_lines, original_fill
      original = boxes['example']
      drag(center(original), (center(original)[0] + 260, center(original)[1] + 90))
      raw = host._layout_params.get(tiles.LAYOUT_PARAM)
      entry = raw['drive']['hud']['example']
      check(entry[0] > 0 and entry[1] > 0 and entry[2] is True, f'17 HUD move not saved: {raw}')
      restarted = screen.BigScreenHost()
      check(restarted._hud == host._hud, '17 restart lost HUD fractions')
      state.started, state.engaged, state.status = True, True, 1
      hud_draws.clear()
      frame(name='onroad-moved-hud')
      check(len(hud_draws) == 1 and abs(hud_draws[0][0] / tiles.MICI[0] - entry[0]) < 1e-5
            and abs(hud_draws[0][1] / tiles.MICI[1] - entry[1]) < 1e-5, f'17 saved HUD rect not applied: {hud_draws}')
      before = hud_draws[-1]
      drag(center(host._view), (center(host._view)[0] + tiles.PITCH, center(host._view)[1] - tiles.PITCH))
      check(hud_draws[-1] == before and host._layout_params.get(tiles.LAYOUT_PARAM)['drive']['hud']['example'] == entry,
            '17 moving view lost HUD fractions')
      state.started, state.engaged, state.status = False, False, 0
      host._edit, host._layout = True, 'drive'
      frame()
      touch(center(tiles.hud_buttons(host._hud_boxes(), host._drive_view())['example']))
      check(host._hud['example'][2] is False, '17 HUD button did not hide')
      frame(name='drive-edit-hidden-hud')
      state.started = True
      hud_draws.clear()
      frame()
      check(not hud_draws, '17 hidden HUD still drawn')
      state.started = False
      host._edit, host._layout = True, 'drive'
      frame()
      touch(center(tiles.hud_buttons(host._hud_boxes(), host._drive_view())['example']))
      drag(center(host._hud_boxes()['example']), (-10000, 10000))
      box = host._hud_boxes()['example']
      vx, vy, vw, vh = tiles.view(host._view_position)
      check(vx <= box[0] <= box[0] + box[2] <= vx + vw + .001 and vy <= box[1] <= box[1] + box[3] <= vy + vh + .001,
            f'17 HUD dragged outside view: {box}')
      disabled = host._hud_overlays._enabled.shows
      host._hud_overlays._enabled.shows = lambda name: False
      check('example' not in host._hud_boxes(), '17 disabled plugin appears in editor')
      host._hud_overlays._enabled.shows = disabled
      cases.append({'id': 17, 'saved': raw, 'onroad_rect': before, 'clamped_box': box, 'restart': True, 'hidden_draws': 0})
    except Exception:
      errors.append('17 HUD editor/render pipeline: ' + traceback.format_exc())
    for mode in ('drive', 'park'):
      host._layout, host._edit = mode, True
      frame()
      host._layouts[mode] = tiles.arrange([e.spec for e in host._entries.values()], {mode: {'hidden': list(host._entries)}}, mode)
      host._placed_key = None
      frame()
      cell = tiles.add_cell(host._place(mode), mode == 'drive', host._view_position)
      touch(center(tiles.cell_rect(*cell)))
      page = host._stack[-1][1]
      titles = [row.title for row in page._scroller._items]
      check(('Park only' in titles) == (mode == 'park'), f'18 wrong {mode} add list: {titles}')
      host._stack = []
      host._layouts[mode] = tiles.arrange([e.spec for e in host._entries.values()], None, mode)
    host._edit, host._layout = True, 'drive'
    frame()
    tile_id = next(iter(host._place('drive')))
    before = deepcopy(host._layouts['drive'])
    drag(center(tiles.tile_rect(host._placed[tile_id])), center(tiles.view(host._view_position)))
    check(host._layouts['drive'] == before, '18 tile dropped inside view')
    state.started = True
    side_draws.clear()
    frame()
    check(bool(side_draws) and all(e.spec.show != 'offroad' for tid, e in host._entries.items() if tid in host._placed),
          '18 onroad side zone/Tile unavailable')
    cases.append({'id': 18, 'side_zone_rects': side_draws, 'outside_tiles': list(host._placed)})
    state.started = False
    host._edit, host._layout = False, 'park'
    host._layout_params.put(tiles.THEME_PARAM, 'Outline')
    host._theme()
    samples = []
    for onroad in (False, True):
      state.started = onroad
      frame(name='onroad-black' if onroad else 'park-black')
      entry = host._entries[next(iter(host._placed))]
      image = rl.load_image_from_texture(entry.texture.texture)
      border = rl.get_image_color(image, 1, 1)
      interior = rl.get_image_color(image, 4, 4)
      pixels = np.frombuffer(rl.ffi.buffer(image.data, image.width * image.height * 4), dtype=np.uint8).reshape(-1, 4)
      white = bool(np.any(np.all(pixels[:, :3] == 255, axis=1)))
      sample = {'onroad': onroad, 'border': [border.r, border.g, border.b], 'interior': [interior.r, interior.g, interior.b], 'white_text': white}
      samples.append(sample)
      check(sample['border'] == [0, 0, 0] and sample['interior'] == [0, 0, 0] and white, f'19 border/incorrect Black tile: {sample}')
      rl.unload_image(image)
    state.started = False
    host._layout_params.put(tiles.THEME_PARAM, 'Crimson')
    host._theme()
    frame()
    check(color() == tiles.THEMES['Crimson'], '19 stale Outline texture after restoring theme')
    state.started = True
    frame()
    check(color() == (32, 32, 32), '19 other onroad theme no longer neutral')
    toggles = (root / 'openpilot/tesla/ui/pages/toggles.py').read_text()
    check('list(THEMES)' in toggles and 'Black' in tiles.THEMES and 'Outline' not in tiles.THEMES, '19 Toggles theme list omits Black')
    cases.append({'id': 19, 'samples': samples, 'restored_onroad_rgb': color()})
    state.started = False
    host._layout_params.put(tiles.THEME_PARAM, 'Black')
    host._theme()
    frame()
    check(color() == (0, 0, 0), '19 new Black theme is not black')
    for mode in ('park', 'drive'):
      host._layout, host._edit = mode, True
      frame(name='edit-bar-' + mode)
      bars = tiles.bar_buttons(host._placed, mode == 'drive', host._view_position)
      from tools.sp_tesla_e2e.tile_layout_check import overlap
      check(all(r[3] >= 152 for r in bars.values()), '20 edit bar height not doubled')
      check(all(not overlap(r, tiles.tile_rect(p)) for r in bars.values() for p in host._placed.values()), '20 edit bar covers tiles')
      check(mode == 'park' or all(not overlap(r, tiles.view(host._view_position)) for r in bars.values()), '20 edit bar covers view')
      cases.append({'id': 20, 'mode': mode, 'buttons': bars})
    screen_source = (root / 'openpilot/tesla/ui/screen.py').read_text()
    check('label, 56' in screen_source, '20 edit bar font not enlarged')
    augmented = (root / 'openpilot/selfdrive/ui/mici/onroad/augmented_road_view.py').read_text()
    check('if gui_app.host is None:  # big screens draw alerts' not in augmented and
          '    self._alert_renderer.render(self._content_rect)' in augmented, '22 big-screen alerts suppressed')
    cases.append({'id': 22, 'protected': sorted(getattr(tiles, 'HUD_REQUIRED', ())), 'alerts': 'SP call remains unconditional; no alert editor entry'})
    from openpilot.tesla import plugins
    from openpilot.tesla.ui.pages.plugins import PluginsPage, PluginPage
    page = PluginsPage()
    page._build()
    for plugin in plugins.PLUGINS:
      use = getattr(plugin.MANIFEST, 'use', None)
      check(use is not None, '23 plugin manifest has no use: ' + plugin.NAME)
      text = 'onroad + offroad' if use == 'both' else use or 'missing use'
      descriptions = [row.description for _, section in page.sections for row in section._scroller._items]
      check(any(text in d for d in descriptions), '23 plugin list omits use: ' + plugin.NAME)
      detail = PluginPage(plugin, page.jobs)
      check(any(row.title == 'Use' for row in detail._scroller._items), '23 plugin detail omits use: ' + plugin.NAME)
    cases.append({'id': 23, 'plugins': [p.NAME for p in plugins.PLUGINS]})
    host._view_position = (1, 0)
    host._hud = {}
    frame(name='drive-edit-builtin-hud-boxes')
    expected = {'builtin:torque_bar', 'builtin:set_speed', 'builtin:steering_wheel', 'builtin:model_source', 'builtin:blind_spot_indicators'}
    boxes = host._hud_boxes()
    check(expected <= set(boxes) and not any('alert' in name for name in boxes), '21/22 built-in boxes missing or alert editable')
    defaults = tiles.builtin_hud_boxes((0, 0, *tiles.MICI))
    point = hud_body('builtin:set_speed')
    drag(point, (point[0] + 320, point[1]))
    moved = list(host._hud['builtin:set_speed'])
    touch(center(tiles.hud_buttons(host._hud_boxes(), host._drive_view())['builtin:torque_bar']))
    check(not host._hud['builtin:torque_bar'][2], '21 torque bar button did not hide')
    for name in ('builtin:steering_wheel', 'builtin:model_source'):
      box = host._hud_boxes()[name]
      touch((box[0] + box[2] / 2, box[1] + box[3] / 2))
      check(host._hud.get(name, [0, 0, True])[2], '22 protected indicator hid: ' + name)
    restarted = screen.BigScreenHost()
    check(restarted._hud == host._hud, '21 restart lost built-in layout')
    state.started, state.status = True, 1
    builtin_draws.clear()
    frame(name='onroad-builtin-moved-hidden')
    check(not builtin_draws.get('torque_bar'), '21 hidden torque bar still drawn')
    actual = builtin_draws.get('set_speed', [[-999, -999]])[-1]
    check(abs(actual[0] + defaults['builtin:set_speed'][0] - moved[0] * tiles.MICI[0]) < .001, '21 saved built-in position not applied')
    raw = deepcopy(host._hud)
    for name in tiles.HUD_REQUIRED:
      host._hud[name] = [.1, .1, False]
    renderer.set_wheel_critical_icon(True)
    builtin_draws.clear()
    frame(name='onroad-protected-indicators')
    check(renderer._wheel_alpha_filter.x > 0, '22 critical steering icon faded away')
    check(all(builtin_draws.get(name.split(':')[1]) for name in tiles.HUD_REQUIRED), '22 persisted hide disabled protected indication')
    for name in tiles.HUD_REQUIRED:
      default = defaults[name]
      expected_rect = tiles.hud_rect([.1, .1, False], default, (0, 0, *tiles.MICI))
      actual = builtin_draws[name.split(':')[1]][-1]
      check(abs(actual[0] + default[0] - expected_rect[0]) < .001 and
            abs(actual[1] + default[1] - expected_rect[1]) < .001, '21 protected indicator did not move: ' + name)
    renderer.set_wheel_critical_icon(False)
    for entry in ([float('nan'), 0, False], [0, float('inf'), True], [0, 0, 0], 'garbage', None):
      host._layout_params.put(tiles.LAYOUT_PARAM, {'drive': {'hud': {'builtin:set_speed': entry}}})
      restored = screen.BigScreenHost()
      check('builtin:set_speed' not in restored._hud, '22 invalid entry survived restart')
    host._hud = raw
    host._save()
    before = builtin_draws.get('set_speed', [])[-1]
    drag(center(host._view), (center(host._view)[0] + tiles.PITCH, center(host._view)[1] + tiles.PITCH))
    check(host._hud == raw and builtin_draws.get('set_speed', [])[-1] == before, '21 moving view lost built-in fractions')
    cases.append({'id': 21, 'saved': raw, 'draws': builtin_draws, 'restart': True})




  except Exception:
    errors.append(traceback.format_exc())
  finally:
    result = {'passed': not errors, 'head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
              'implementation_ref': os.environ.get('XPILOT_E2E_SOURCE_REF', 'working tree'),
              'scope': host_check.__doc__, 'cases': cases, 'errors': errors}
    (output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    camera.close()
    rl.close_window()
    sandbox.cleanup()
  return 1 if errors else 0


if '--host' in sys.argv:
  host_parser = argparse.ArgumentParser()
  host_parser.add_argument('--host', action='store_true')
  host_parser.add_argument('--output', type=Path, required=True)
  host_args = host_parser.parse_args()
  sys.exit(host_check(host_args.output))

import pyray as rl
from openpilot.common.params import Params
from openpilot.system.ui.lib.application import gui_app, MouseEvent, MousePos
from openpilot.selfdrive.ui.mici.layouts.main import MiciMainLayout
from openpilot.selfdrive.ui.sunnypilot.model_info import bundles_for_source
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.tesla.ui import tiles
from openpilot.tesla.plugins import PLUGINS
from openpilot.tesla.ui.offroad import BACK, CONTENT
from openpilot.tesla.ui.pages.plugins import CAT_H, GAP, LEFT_W
from openpilot.tesla.ui.pages.onboarding import OnboardingPage, onboarding_needed
from openpilot.tesla.ui.screen import BigScreenHost

parser = argparse.ArgumentParser()
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=True)
assert Params().get_bool('IsOffroad') and not Params().get_bool('IsEngaged')
gui_app.init_window('xpilot big screen E2E', host=BigScreenHost)
assert not onboarding_needed(), 'complete onboarding before E2E; never push its real window during this test'
main = MiciMainLayout()
host = gui_app.host
driver_view_before = ui_state.params.get_bool('IsDriverViewEnabled')
SETTINGS = ('status', 'toggles', 'plugins', 'tesla', 'models', 'network', 'device', 'software', 'developer')
errors, cases = [], []
original_render = host.render
original_draw_texture_pro = rl.draw_texture_pro
original_begin_texture_mode = rl.begin_texture_mode
frame, next_step = 0, 1
mici_draws, tile_redraws = 0, 0
in_screenshot, completed = False, False
pending_events = []
layout_path = host._layout_params._path(tiles.LAYOUT_PARAM)
layout_backup = None


def counting_draw_texture_pro(texture, *rest):
  global mici_draws
  if not in_screenshot and texture.id == gui_app._render_texture.texture.id:
    mici_draws += 1
  original_draw_texture_pro(texture, *rest)


def counting_begin_texture_mode(target):
  global tile_redraws
  if not in_screenshot and any(e.texture is not None and target.id == e.texture.id for e in host._entries.values()):
    tile_redraws += 1
  original_begin_texture_mode(target)


rl.draw_texture_pro = counting_draw_texture_pro
rl.begin_texture_mode = counting_begin_texture_mode


def check(ok, message):
  if not ok:
    errors.append(message)


def rect(r):
  return None if r is None else [r.x, r.y, r.width, r.height]


def center(r):
  x, y, w, h = r
  return x + w / 2, y + h / 2


def screenshot(name):
  global in_screenshot
  in_screenshot = True
  target = rl.load_render_texture(2160, 1080)
  events = gui_app._mouse_events
  try:
    rl.begin_texture_mode(target)
    try:
      rl.clear_background(rl.BLACK)
      gui_app._mouse_events = []
      if name == 'onboarding':
        with host._native([]):
          OnboardingPage(on_done=lambda: None).render(rl.Rectangle(0, 0, 2160, 1080))
      else:
        texture = gui_app._render_texture.texture
        view = host.viewport()
        if view is not None:
          rl.draw_texture_pro(texture, rl.Rectangle(0, 0, texture.width, -texture.height), view, rl.Vector2(0, 0), 0, rl.WHITE)
        original_render()  # tile textures were refreshed by this frame's on-screen render
    finally:
      rl.end_texture_mode()
    image = rl.load_image_from_texture(target.texture)
    try:
      rl.image_flip_vertical(image)
      path = args.output / (name + '.png')
      check(rl.export_image(image, str(path)), f'screenshot export failed: {name}')
    finally:
      rl.unload_image(image)
  finally:
    gui_app._mouse_events = events
    rl.unload_render_texture(target)
    in_screenshot = False


def tap(x, y, phase):
  """Queue screen press (0), release (1) or move (2) through the real touch mapping."""
  global pending_events
  lx, ly = host.to_logical(x, y)
  check(not (0 <= lx <= 536 and 0 <= ly <= 240) or host.viewport() is not None, f'tap ({x},{y}) maps inside mici ({lx},{ly})')
  pending_events = [MouseEvent(MousePos(lx, ly), 0, phase == 0, phase == 1, phase != 1, time.monotonic())]


def touch(point):
  tap(*point, 0)
  yield 1
  tap(*point, 1)
  yield 4


def hold_toggles():
  point = center(tiles.tile_rect(host._placed['toggles']))
  start = time.monotonic()
  tap(*point, 0)
  while time.monotonic() - start < tiles.LONG_PRESS + 0.2:
    yield 1
  tap(*point, 1)
  yield 3
  assert host._edit and host._selected == 'toggles', f'long press: edit={host._edit}, selected={host._selected}'


def tile_screen():
  assert not host._stack and gui_app.get_active_widget() is main, 'BACK did not return to the tile screen'
  check(host.viewport() is None and host._shown is None, 'tile screen still shows a detail page or mici viewport')


def layout_bytes():
  return layout_path.read_bytes() if layout_path.exists() else None


def restore_layout():
  if layout_backup is None:
    return
  layouts, raw, position = layout_backup
  host._layouts, host._layout = deepcopy(layouts), 'park'
  host._view_position = position
  host._edit, host._selected, host._drag = False, None, None
  if layout_bytes() != raw:
    if raw is None:
      layout_path.unlink(missing_ok=True)
    else:
      with tempfile.NamedTemporaryFile(dir=layout_path.parent, delete=False) as saved:
        saved.write(raw)
      Path(saved.name).replace(layout_path)
  check(layout_bytes() == raw and host._layouts == layouts and host._view_position == position, 'saved park/drive layouts were not restored exactly')


def steps():
  global layout_backup, completed
  yield 30
  tile_screen()
  check(mici_draws == 0, f'offroad tile screen drew mici {mici_draws} times')
  for tid in SETTINGS:
    check(tid in host._placed, f'offroad settings tile {tid} not placed')
  screenshot('start')
  cases.append({'name': 'offroad', 'viewport': rect(host.viewport()), 'placed': dict(host._placed), 'mici_draws': mici_draws})
  screenshot('onboarding')
  cases.append({'name': 'onboarding', 'standalone': True, 'size': [2160, 1080], 'touches': 0})
  before = tile_redraws
  yield 60
  check(tile_redraws - before <= 2, f'60 idle frames redrew {tile_redraws - before} tile faces')
  check(mici_draws == 0, 'idle tile screen drew mici')
  cases.append({'name': 'idle', 'frames': 60, 'tile_redraws': tile_redraws - before})

  for tid in SETTINGS:
    if tid not in host._placed:
      continue  # already reported; do not tap where an absent tile should have been
    tile = host._entries[tid].tile
    before = mici_draws
    yield from touch(center(tiles.tile_rect(host._placed[tid])))
    assert len(host._stack) == 1 and host._stack[-1][0] == tile.SPEC.title, f'tile {tid}: detail stack {host._stack}'
    page = host._stack[-1][1]
    active = gui_app.get_active_widget()
    assert not tile.MICI and type(page) is tile.PAGE, f'tile {tid}: page {type(page).__name__}, expected native {tile.PAGE.__name__}'
    check(host._shown is page and active is main and len(gui_app._nav_stack) == 1, f'tile {tid}: native page/application stack mismatch')
    check(host.viewport() is None and mici_draws == 0, f'tile {tid}: native page drew mici')
    screenshot('page-' + tid)
    cases.append({'name': tid, 'title': host._stack[-1][0], 'page': type(page).__name__, 'active': type(active).__name__,
                  'stack': len(host._stack), 'nav_stack': len(gui_app._nav_stack), 'mici_draws': mici_draws - before})
    if tid == 'plugins':
      if len(page.buttons) > 1:
        yield from touch(center((CONTENT[0], CONTENT[1] + GAP + CAT_H + GAP, LEFT_W, CAT_H)))
        check(page.selected == 1, 'Plugins: tapping the second category did not select it')
        screenshot('page-plugins-category')
      plugin = PLUGINS[0]
      page._open_plugin(plugin)
      yield 4
      check(len(host._stack) == 2 and host._stack[-1][0] == plugin.TITLE and type(host._stack[-1][1]).__name__ == 'PluginPage',
            'Plugins -> plugin did not push a second detail level')
      check(host._shown is host._stack[-1][1] and host.viewport() is None and mici_draws == before, 'plugin page did not show natively')
      check(len(gui_app._nav_stack) == 1, 'plugin page grew the application nav stack')
      screenshot('page-plugins-plugin')
      yield from touch(center(BACK))
      assert host._stack == [(tile.SPEC.title, page)] and host._shown is page, 'BACK from the plugin page did not return to Plugins'
      cases.append({'name': 'Plugins > plugin > BACK', 'page': type(page).__name__, 'stack': len(host._stack)})
    if tid == 'models':
      if bundles_for_source('qcom'):
        page._choose('qcom')
        yield 4
        check(len(host._stack) == 2 and host._stack[-1][0] == 'Small models' and type(host._stack[-1][1]).__name__ == 'BundlesPage', 'Models -> Small models did not push the native bundle list')
        check(host._shown is host._stack[-1][1] and host.viewport() is None and mici_draws == 0, 'Small models did not show natively')
        check(len(gui_app._nav_stack) == 1, 'Small models grew the application nav stack')
        screenshot('page-models-small')
        yield from touch(center(BACK))
        assert host._stack == [(tile.SPEC.title, page)] and host._shown is page, 'BACK from Small models did not return to Models'
        cases.append({'name': 'Models > Small models > BACK', 'page': 'BundlesPage', 'skipped': False})
      else:
        cases.append({'name': 'Models > Small models > BACK', 'skipped': True, 'reason': 'no qcom bundles available'})
    if tid == 'device':
      for title, row_title, expected in (
        ('training guide', 'Review training guide', 'TrainingGuidePage'),
        ('driver camera', 'Driver camera preview', 'DriverCameraPage'),
        ('terms', 'Terms & conditions', 'TermsPage'),
        ('regulatory', 'Regulatory info', 'RegulatoryPage'),
      ):
        row = next(row for row in page._scroller._items if row.title == row_title)
        if not row.is_visible:
          cases.append({'name': 'Device > ' + title, 'skipped': True, 'reason': 'row hidden on this hardware'})
          continue
        row.callback()
        yield 360 if title == 'driver camera' else 4  # camerad needs a few seconds after IsDriverViewEnabled
        assert len(host._stack) == 2 and host._stack[-1][0] == title, f'Device -> {title}: detail stack {host._stack}'
        sub = host._stack[-1][1]
        check(type(sub).__name__ == expected and host._shown is sub, f'Device -> {title}: page {type(sub).__name__}, expected {expected}')
        check(host.viewport() is None and mici_draws == 0 and len(gui_app._nav_stack) == 1, f'Device -> {title} did not stay native')
        if title == 'training guide':
          check(sub._step == 0, 'training guide left its first step')
        screenshot('page-device-' + title.replace(' ', '-'))
        yield from touch(center(BACK))
        assert host._stack == [(tile.SPEC.title, page)] and host._shown is page, f'BACK from {title} did not return to Device'
        case = {'name': 'Device > ' + title + ' > BACK', 'page': type(sub).__name__, 'stack': len(host._stack)}
        if title == 'driver camera':
          case['driver_view_after_back'] = ui_state.params.get_bool('IsDriverViewEnabled')
          check(not case['driver_view_after_back'], 'driver camera BACK left IsDriverViewEnabled on')
        cases.append(case)
    yield from touch(center(BACK))
    tile_screen()
    check(mici_draws == 0, f'BACK from {tid} drew mici')

  layout_backup = (deepcopy(host._layouts), layout_bytes(), host._view_position)
  yield from hold_toggles()
  screenshot('edit')
  old_size = host._placed['toggles'][2]
  yield from touch(tiles.corner_buttons(tiles.tile_rect(host._placed['toggles']))['resize'][:2])
  check('toggles' in host._placed and host._placed['toggles'][2] != old_size, 'resize corner did not change Toggles size')
  check(host._edit and host._selected == 'toggles', 'resize left edit mode or changed selection')
  screenshot('edit-resized')
  cell = tiles.add_cell(host._placed, False)
  assert cell is not None, 'no add cell after resize'
  yield from touch(center(tiles.cell_rect(*cell)))
  assert len(host._stack) == 1 and host._stack[-1][0] == 'add tiles', '+ did not open add tiles'
  check(type(host._shown).__name__ == 'AddTilesPage' and host._shown is host._stack[-1][1] and host.viewport() is None, 'wrong add tiles page')
  screenshot('add-tiles')
  yield from touch(center(BACK))
  tile_screen()
  assert host._edit and host._selected == 'toggles', 'BACK from add tiles lost edit mode'
  yield from touch(center(tiles.tile_rect(host._placed['toggles'])))
  check(host._edit and host._selected is None and not host._stack, 'selected tile tap did not deselect in edit mode')
  yield from touch(center(tiles.tile_rect(host._placed['toggles'])))
  check(host._edit and host._selected == 'toggles', 'tile tap did not reselect Toggles')
  add = tiles.add_cell(host._placed, False)
  empty = next(((c, r) for c in range(tiles.COLS) for r in range(tiles.ROWS)
                if (c, r) != add and tiles.hit(host._placed, *center(tiles.cell_rect(c, r))) is None), None)
  assert empty is not None, 'no empty non-add cell to test edit exit'
  yield from touch(center(tiles.cell_rect(*empty)))
  check(host._edit and host._selected is None and not host._stack, 'empty cell tap did not deselect in edit mode')
  cases.append({'name': 'edit', 'old_size': old_size, 'new_size': host._placed['toggles'][2], 'empty_cell': empty})

  host._layouts['drive'] = tiles.arrange([e.tile.SPEC for e in host._entries.values() if tiles.shows(e.tile.SPEC, True)], None, 'drive')
  host._view_position = tiles.DRIVE_CELLS[:2]
  yield from touch(center(tiles.bar_buttons(host._placed, host._layout == 'drive')['drive']))
  assert host._edit and host._layout == 'drive', 'Drive bar button did not select the drive layout'
  data = [tid for tid, _, _, _ in host._visible('drive')]  # enabled onroad tiles, built-in, plugin or store
  check(set(host._placed) == set(data), f'drive edit tiles: {list(host._placed)}, expected {data}')
  default_order = [tid for tid, _, _ in host._layouts['drive'][0] if tid in data]
  free = [cell for cell in tiles.scan(True) if cell not in tiles.blocked(True, host._view_position)]
  for tid, cell in zip(default_order, free):
    check(tid in host._placed and host._placed[tid][:2] == cell, f'default drive tile {tid}: {host._placed.get(tid)}, expected {cell}')
  for tid, (col, row, size) in host._placed.items():
    cols, rows = tiles.SIZES[size]
    check(not {(col + c, row + r) for c in range(cols) for r in range(rows)} & tiles.blocked(True, host._view_position), f'drive edit tile {tid} overlaps blocked cells')
  screenshot('edit-drive')
  cases.append({'name': 'edit drive default', 'order': default_order, 'placed': dict(host._placed)})

  moved = next((tid for tid in data if tid in host._placed), None)
  assert moved is not None, 'no data tile to drag'
  sx, sy = center(tiles.tile_rect(host._placed[moved]))
  ex, ey = center(tiles.cell_rect(7, 3))
  cols, rows = tiles.SIZES[host._placed[moved][2]]
  drop_cell = (min(7, tiles.COLS - cols), min(3, tiles.ROWS - rows))
  tap(sx, sy, 0)
  yield 1
  for step in range(1, 21):
    tap(sx + (ex - sx) * step / 20, sy + (ey - sy) * step / 20, 2)
    yield 1
  tap(ex, ey, 1)
  yield 4
  fixed = next(fixed for tid, _, fixed in host._layouts['drive'][0] if tid == moved)
  check(fixed == drop_cell, f'dragged {moved}: saved fixed cell {fixed}, expected {drop_cell}')
  check(moved in host._placed and host._placed[moved][:2] == drop_cell, f'dragged {moved} not placed at {drop_cell}')
  screenshot('edit-drive-moved')
  cases.append({'name': 'edit drive moved', 'tile': moved, 'fixed': fixed, 'expected': drop_cell, 'moves': 20})
  yield from touch(center(tiles.bar_buttons(host._placed, host._layout == 'drive', host._view_position)['done']))
  check(not host._edit and host._layout == 'park', 'Done did not leave edit mode and restore the park layout')
  tile_screen()

  gui_app.pop_widgets_to(main, instant=True)
  ui_state.started = True  # synthetic composition only; never write IsOffroad or send CAN
  view = tiles.view(host._view_position)
  check(rect(host.viewport()) == list(view), f'onroad viewport {rect(host.viewport())}')
  lx, ly = host.to_logical(*view[:2])
  check(not (0 <= lx <= 536 and 0 <= ly <= 240), f'onroad touch maps inside mici {lx},{ly}')
  forced, drawn = [], []
  for entry in host._overlays._entries:
    overlay = entry[1]
    if type(overlay).__name__ == 'BlindspotEdges':
      overlay.update = lambda sm, o=overlay: (setattr(o, 'left', True), setattr(o, 'right', True))

      def draw(zones, original=overlay.draw):
        original(zones)
        drawn.append(True)

      overlay.draw = draw
      forced.append(entry)
  check(bool(forced), 'blindspot_edges overlay not loaded (plugin off or import failed)')
  original_render()
  vx, vy, vw, vh = view
  for tid, placed in host._placed.items():
    x, y, w, h = tiles.tile_rect(placed)
    check(not (x < vx + vw and vx < x + w and y < vy + vh and vy < y + h), f'onroad tile {tid} overlaps VIEW')
    col, row, size = placed
    cols, rows = tiles.SIZES[size]
    cells = {(col + c, row + r) for c in range(cols) for r in range(rows)}
    check(not cells & tiles.blocked(True, host._view_position), f'onroad tile {tid} overlaps blocked cells')
    check(tid not in SETTINGS and host._entries[tid].tile.SPEC.show != 'offroad', f'onroad settings tile {tid} placed')
  for tid in data:
    check(tid in host._placed, f'onroad data tile {tid} not placed')
  check(moved in host._placed and host._placed[moved][:2] == drop_cell, f'onroad lost fixed position of {moved}: {host._placed.get(moved)}')
  check(len(drawn) == len(forced), 'blindspot overlay did not draw successfully')
  screenshot('onroad-blindspot')
  check(all(e[2] == 0 for e in forced), 'blindspot overlay raised')
  cases.append({'name': 'onroad', 'viewport': rect(host.viewport()), 'placed': dict(host._placed), 'blindspot_draws': len(drawn)})
  ui_state.started = False
  host.viewport()
  restore_layout()
  yield 4
  tile_screen()
  completed = True
  gui_app.request_close()
  yield 1


scenario = steps()


def render():
  global frame, next_step, pending_events
  frame += 1
  gui_app._mouse_events, pending_events = pending_events, []
  original_render()
  assert mici_draws == 0, f'offroad frame {frame} drew the mici texture'
  assert not host._is_modal(gui_app.get_active_widget()), 'unexpected dialog; no confirmation was sent'
  if frame >= next_step:
    next_step = frame + next(scenario)


host.render = render
try:
  for should_render, *_ in gui_app.render():
    ui_state.update()
    assert not ui_state.started and not ui_state.engaged, 'device must remain offroad and unengaged'
  check(completed, 'E2E closed before all steps completed')
except Exception:
  errors.append(traceback.format_exc())
finally:
  ui_state.started = False
  try:
    restore_layout()
    if layout_backup is not None:
      cases.append({'name': 'layout restored', 'file_matches': layout_bytes() == layout_backup[1],
                    'layouts_match': host._layouts == layout_backup[0]})
  except Exception:
    errors.append('layout restore failed:\n' + traceback.format_exc())
  try:
    try:
      host._show(None)
    finally:
      if ui_state.params.get_bool('IsDriverViewEnabled') != driver_view_before:
        ui_state.params.put_bool('IsDriverViewEnabled', driver_view_before, block=True)
    driver_view_after = ui_state.params.get_bool('IsDriverViewEnabled')
    check(driver_view_after == driver_view_before, 'IsDriverViewEnabled was not restored')
    cases.append({'name': 'driver view restored', 'before': driver_view_before, 'after': driver_view_after})
  except Exception:
    errors.append('driver view restore failed:\n' + traceback.format_exc())
  rl.draw_texture_pro = original_draw_texture_pro
  rl.begin_texture_mode = original_begin_texture_mode
  host.render = original_render
  result = {'passed': not errors, 'head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
            'scope': 'real GPU, production widgets and mapped touch events; layout restored; synthetic onroad; no CAN transmission',
            'cases': cases, 'errors': errors}
  (args.output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
  print(json.dumps(result, indent=2))
assert result['passed']
