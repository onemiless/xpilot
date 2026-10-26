"""Plugins page (mici): the one place to manage plugins, grouped by manifest category.
Category page: one switch per plugin (long-press for description, author, version and claims) and its settings.
Switching a plugin on switches off every plugin it conflicts with (shared exclusive claim). Row values:
"fault" = dropped this boot (Params XpilotPluginFaults), "conflict" = switched on but lost a claim.
Plugins whose manifest or code failed to load are listed at the top. Changes apply on the next drive; UI follows in 1 s."""
from openpilot.selfdrive.ui.mici.widgets.button import BigButton, BigToggle
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import gui_app
from openpilot.system.ui.lib.multilang import tr
from openpilot.tesla.manifest import CATEGORIES
from openpilot.tesla.plugins import FAULTS_PARAM, LOAD_ERRORS, PLUGINS, is_enabled, resolve, view
from openpilot.tesla.ui.offroad import CATEGORY_LABELS, plugin_status, switch_plugin
from openpilot.tesla.ui.settings_page import SettingsList
from openpilot.tesla.ui.store_page import StorePage


def _details(plugin) -> str:
  m = plugin.MANIFEST
  claims = ', '.join(plugin.CLAIMS) or tr('none')
  return f'{plugin.DESCRIPTION}\n\n{m.author or tr("unknown")} - v{m.version} - {tr("priority")} {plugin.PRIORITY}\n{tr("claims")}: {claims}'


class CategoryPage(SettingsList):
  def __init__(self, plugins, jobs):
    super().__init__()
    self.jobs = jobs
    self.rows = []
    self.errors = {}
    for plugin in plugins:
      toggle = BigToggle(plugin.TITLE, initial_state=is_enabled(plugin, ui_state.params),
                         toggle_callback=lambda value, p=plugin: self._switch(p, value), description=_details(plugin))
      toggle.set_enabled(lambda p=plugin: ui_state.is_offroad() and not self.jobs.busy and p.ENABLE_PARAM is not None)
      self._scroller.add_widget(toggle)
      self.rows.append((plugin, toggle))
      if plugin.SETTINGS:
        page = SettingsList(plugin.SETTINGS)
        button = BigButton(tr('{} Settings').format(plugin.TITLE))
        button.set_click_callback(lambda pg=page: (pg.show_event(), gui_app.push_widget(pg)))
        self._scroller.add_widget(button)
    self.show_event()

  def _switch(self, plugin, value) -> None:
    self.errors = {plugin.NAME: switch_plugin(plugin, value, view(ui_state.params), jobs=self.jobs) if ui_state.is_offroad() else tr("park first")}
    self.show_event()

  def show_event(self):
    super().show_event()
    faults = ui_state.params.get(FAULTS_PARAM) or {}
    _, conflicts = resolve(ui_state.params)
    for plugin, toggle in getattr(self, 'rows', ()):
      toggle.set_checked(is_enabled(plugin, ui_state.params))
      toggle.set_value(self.errors.get(plugin.NAME) or plugin_status(plugin, faults, conflicts))


class PluginsPage(SettingsList):
  def __init__(self):
    super().__init__()
    store_page = StorePage()
    store_btn = BigButton(tr('Plugin Store'), description=tr('Install third-party plugins (reviewed, not signed). Restart to apply.'))
    store_btn.set_click_callback(lambda: gui_app.push_widget(store_page))
    self._scroller.add_widget(store_btn)
    for name, error in LOAD_ERRORS.items():
      broken = BigButton(tr('Failed: {}').format(name), description=error)
      broken.set_enabled(False)
      self._scroller.add_widget(broken)
    self.categories = []
    for category in CATEGORIES:
      plugins = [p for p in PLUGINS if p.CATEGORY == category]
      if not plugins:
        continue
      page = CategoryPage(plugins, store_page._jobs)
      button = BigButton(tr(CATEGORY_LABELS[category]))
      button.set_click_callback(lambda pg=page: (pg.show_event(), gui_app.push_widget(pg)))
      self._scroller.add_widget(button)
      self.categories.append((plugins, button))
    self.show_event()

  def show_event(self):
    super().show_event()
    params = view(ui_state.params)
    for plugins, button in getattr(self, 'categories', ()):
      on = sum(is_enabled(p, params) for p in plugins)
      button.set_value(tr('{}/{} on').format(on, len(plugins)))
