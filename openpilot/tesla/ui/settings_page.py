"""Tesla settings page (mici): upstream Tesla options and device toggles (DM, map provider).
SettingsList renders any Setting rows; the Plugins page (plugins_page.py) reuses it."""
from openpilot.selfdrive.ui.mici.widgets.button import BigButton, BigToggle
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import gui_app
from openpilot.system.ui.lib.multilang import multilang, tr
from openpilot.system.ui.widgets.scroller import NavScroller
from openpilot.tesla.plugins import view
from openpilot.tesla.ui.settings_meta import SP_SETTINGS, VEHICLE_SETTINGS, setting_choices, setting_label, stored_value, to_param
from openpilot.tesla.ui.setting_keyboard import edit_text_setting
from openpilot.tesla.ui.offroad import step_value
from openpilot.sunnypilot.hardware.driver_monitoring import driver_monitoring_configured
from openpilot.sunnypilot.hardware.profile import has_driver_camera


def _page() -> NavScroller:
  page = NavScroller()
  back = BigButton(tr('Back'))
  back.set_click_callback(gui_app.pop_widget)
  page._scroller.add_widget(back)
  return page


def _params():
  """ui_state.params with downloaded plugins' keys routed to their store."""
  return view(ui_state.params)


def _set_param(key, value) -> None:
  _params().put(key, value, block=True)


def _get(setting):
  return stored_value(setting, _params().get(setting.key, return_default=True))


def _set(setting, value) -> None:
  _set_param(setting.key, to_param(setting, _params().get(setting.key, return_default=True), value))


class SettingsList(NavScroller):
  """Back button, then one row per Setting: toggles, or buttons that open a value picker."""

  def __init__(self, settings=()):
    super().__init__()
    self.toggles = []
    self.choice_buttons = []
    back = BigButton(tr('Back'))
    back.set_click_callback(gui_app.pop_widget)
    self._scroller.add_widget(back)
    self.add_settings(settings)

  def add_settings(self, settings, translate=False) -> None:
    for setting in settings:
      label = tr(setting.label)
      if setting.type is None and setting.range is None and setting.choices is None:
        toggle = BigToggle(label, initial_state=bool(_get(setting)),
                           toggle_callback=lambda value, s=setting: (_set(s, value), self.show_event()))
        self.toggles.append((setting, toggle))
        self._scroller.add_widget(toggle)
      else:
        button = BigButton(label)
        button.set_click_callback(lambda s=setting, t=translate: self._choose(s, t))
        self.choice_buttons.append((setting, button, translate))
        self._scroller.add_widget(button)

  def _choose(self, setting, translate=False) -> None:
    if setting.type in ("text", "secret"):
      edit_text_setting(setting, _params(), self.show_event)
      return
    page = _page()
    if setting.range is not None:  # numeric: -/+ around the current value
      steps = [(BigButton(text), delta) for text, delta in (('+', 1), ('-', -1))]
      def show():
        value = setting_label(setting, _get(setting), ui_state.is_metric)
        for b, _ in steps:
          b.set_value(tr(value) if translate else value)
      for b, delta in steps:
        b.set_click_callback(lambda d=delta: (_set(setting, step_value(setting, _get(setting), d)), show(), self.show_event()))
        page._scroller.add_widget(b)
      show()
      gui_app.push_widget(page)
      return
    for label, value in setting_choices(setting, ui_state.is_metric):
      label = tr(label) if translate else label
      button = BigButton(label)
      button.set_click_callback(lambda v=value: (_set(setting, v), self.show_event(), gui_app.pop_widget()))
      page._scroller.add_widget(button)
    gui_app.push_widget(page)

  def show_event(self):
    super().show_event()
    for setting, toggle in self.toggles:
      toggle.set_checked(bool(_get(setting)))
    for setting, button, translate in self.choice_buttons:
      value = setting_label(setting, _get(setting), ui_state.is_metric)
      button.set_value(tr(value) if translate else value)


class TeslaSettingsMici(SettingsList):
  def __init__(self):
    super().__init__()
    self.dm_toggle = BigToggle(tr('Driver Monitoring'), initial_state=driver_monitoring_configured(ui_state.params),
                               toggle_callback=self._set_driver_monitoring,
                               description=tr('When off, the DM processes do not start. Change only while parked; applies on the next drive.'))
    self.dm_toggle.set_enabled(lambda: ui_state.is_offroad() and has_driver_camera())
    self._scroller.add_widget(self.dm_toggle)
    self.map_toggle = BigToggle(tr('OSM Map Data'), initial_state=ui_state.params.get('MapProvider', return_default=True) == 'osm',
                                toggle_callback=self._set_map_provider)
    self.map_toggle.set_enabled(ui_state.is_offroad)
    self._scroller.add_widget(self.map_toggle)
    if len(multilang.languages) > 1:
      self.language = BigButton(tr('Language'), description=tr('Language changes apply after the UI restarts.'))
      self.language.set_value(multilang.codes[multilang.language])
      self.language.set_click_callback(self._choose_language)
      self._scroller.add_widget(self.language)
    self.add_settings(VEHICLE_SETTINGS, translate=True)
    for title, description, group in SP_SETTINGS:
      button = BigButton(tr(title), description=tr(description))
      button.set_click_callback(lambda g=group: self._open_group(g))
      self._scroller.add_widget(button)
    self.show_event()

  @staticmethod
  def _open_group(group):
    page = SettingsList()
    page.add_settings(group, translate=True)
    page.show_event()
    gui_app.push_widget(page)

  def _set_driver_monitoring(self, value):
    if ui_state.is_offroad() and has_driver_camera():
      ui_state.params.put_bool('DriverMonitoringEnabled', value, block=True)
    self.dm_toggle.set_checked(driver_monitoring_configured(ui_state.params))

  def _set_map_provider(self, value):
    if ui_state.is_offroad():
      ui_state.params.put('MapProvider', 'osm' if value else 'none', block=True)
    self.map_toggle.set_checked(ui_state.params.get('MapProvider', return_default=True) == 'osm')

  def _choose_language(self):
    page = _page()
    for name, code in multilang.languages.items():
      button = BigButton(name)
      button.set_click_callback(lambda code=code: (multilang.change_language(code), gui_app.pop_widget()))
      page._scroller.add_widget(button)
    gui_app.push_widget(page)

  def show_event(self):
    super().show_event()
    if hasattr(self, 'map_toggle'):  # NavScroller may call show_event before these toggles exist
      self.dm_toggle.set_checked(driver_monitoring_configured(ui_state.params))
      self.map_toggle.set_checked(ui_state.params.get('MapProvider', return_default=True) == 'osm')
