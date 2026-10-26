"""Plugins page (native): the plugin market. Left: one button per manifest category (plus "Failed" when a plugin failed to
load) and the store Refresh button. Right: the selected category's rows: a switch per plugin (version, author and
description below the title; tapping the row opens its page with settings, info and Update/Remove for downloaded plugins),
then the store entries not loaded this boot with Install & enable. The index is fetched once on first show (offroad.StoreJobs);
install and remove apply after a restart. Switching a plugin on switches off its conflicts (offroad.switch_plugin)."""
import pyray as rl

from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import gui_app
from openpilot.system.ui.lib.multilang import tr
from openpilot.system.ui.widgets import Widget
from openpilot.system.ui.widgets.button import Button, ButtonStyle
from openpilot.system.ui.widgets.list_view import ITEM_PADDING, ButtonAction, ListItem, TextAction, text_item, toggle_item
from openpilot.system.ui.widgets.scroller_tici import Scroller
from openpilot.tesla.manifest import CATEGORIES
from openpilot.tesla.plugins import FAULTS_PARAM, LOAD_ERRORS, is_enabled, resolve
from openpilot.tesla.ui.offroad import StoreJobs, market_sections, plugin_status, switch_plugin
from openpilot.tesla.ui.pages.base import ListPage, SettingRow, params

LEFT_W = 440
CAT_H = 96
GAP = 10


class InfoRow(ListItem):
  """ListItem with its description always shown; a tap outside the action calls on_tap (opens a plugin page)."""

  def __init__(self, title, description, action_item=None, on_tap=None, callback=None):
    super().__init__(title, description=description, action_item=action_item, callback=callback)
    self.description_visible = True
    self.on_tap = on_tap

  def show_event(self):
    Widget.show_event(self)  # ListItem.show_event collapses the description

  def set_parent_rect(self, parent_rect: rl.Rectangle) -> None:
    super().set_parent_rect(parent_rect)
    self._rect.height = self.get_item_height(self._font, int(self._rect.width - ITEM_PADDING * 2))

  def _handle_mouse_release(self, mouse_pos):
    if self.on_tap is not None and not rl.check_collision_point_rec(mouse_pos, self.get_right_item_rect(self._rect)):
      self.on_tap()


class Switches:
  """Plugin switch rows: [status] after the title, offroad-only, conflicts switched off."""

  def __init__(self, jobs):
    self.jobs = jobs
    self.items = []
    self.errors = {}

  def make(self, plugin, description: str, on_tap=None) -> InfoRow:
    status = {"": ""}
    action = toggle_item("", initial_state=is_enabled(plugin, ui_state.params), callback=lambda v: self.switch(plugin, v),
                         enabled=lambda: ui_state.is_offroad() and not self.jobs.busy and plugin.ENABLE_PARAM is not None).action_item
    item = InfoRow(lambda: f"{plugin.TITLE}  [{status['']}]" if status[""] else plugin.TITLE, description, action, on_tap)
    self.items.append((plugin, item, status))
    return item

  def switch(self, plugin, value: bool) -> None:
    self.errors = {plugin.NAME: switch_plugin(plugin, value, params(), jobs=self.jobs) if ui_state.is_offroad() else tr("park first")}
    self.refresh()

  def refresh(self) -> None:
    faults = ui_state.params.get(FAULTS_PARAM) or {}
    _, conflicts = resolve(ui_state.params)
    for p, item, status in self.items:
      item.action_item.set_state(is_enabled(p, ui_state.params))
      status[""] = self.errors.get(p.NAME) or plugin_status(p, faults, conflicts)


class PluginPage(ListPage):
  """One plugin: switch, settings, info, and Update/Remove when it was downloaded from the store."""

  def __init__(self, plugin, jobs: StoreJobs):
    super().__init__()
    m = plugin.MANIFEST
    self.switches = Switches(jobs)
    self.add(self.switches.make(plugin, plugin.DESCRIPTION))
    self.rows = [SettingRow(s) for s in plugin.SETTINGS]
    self.add(*[r.item for r in self.rows])
    uses = {"onroad": tr("onroad"), "offroad": tr("offroad"), "both": tr("onroad + offroad")}
    self.add(text_item(tr("Source"), tr("store (removable)") if plugin.NAME in jobs.installed else tr("built-in (switch off only)")),
             text_item(tr("Version"), f"v{m.version}"), text_item(tr("Author"), m.author or tr("unknown")),
             text_item(tr("Category"), tr(CATEGORIES.get(plugin.CATEGORY, plugin.CATEGORY))),
             text_item(tr("Use"), uses[m.use]),
             text_item(tr("Claims"), ", ".join(plugin.CLAIMS) or tr("none")), text_item(tr("Priority"), str(plugin.PRIORITY)))
    pid = plugin.NAME
    if pid in jobs.installed:
      self.add(ListItem(lambda: tr("Store: {}").format(tr("working...") if jobs.busy else jobs.status),
                        action_item=ButtonAction(lambda: jobs.action(jobs.current(pid), pid), enabled=lambda: jobs.can_act(pid)),
                        callback=lambda: jobs.run(lambda: jobs.act(pid))))

  def refresh(self) -> None:
    self.switches.refresh()
    for row in self.rows:
      row.refresh()

  def show_event(self):
    super().show_event()

  def _update_state(self):
    if gui_app.frame % gui_app.target_fps == 0:
      self.switches.refresh()


class PluginsPage(Widget):
  def __init__(self):
    super().__init__()
    self.open = lambda title, page: None
    self.jobs = jobs = StoreJobs(ui_state.is_offroad)
    self.pages: dict[str, PluginPage] = {}
    self.selected = 0
    self.fetched = False
    self.refresh_button = Button(lambda: tr("working...") if jobs.busy else tr("Refresh ({})").format(tr("failed") if jobs.failed else jobs.status),
                                 lambda: jobs.run(jobs.fetch), font_size=40)
    self.refresh_button.set_enabled(lambda: not jobs.busy)
    self._build()

  def _build(self) -> None:
    """Rows from PLUGINS and the latest index; rebuilt after each fetch."""
    jobs = self.jobs
    self.switches = Switches(jobs)
    self.sections: list[tuple[str, ListPage]] = []
    if LOAD_ERRORS:
      page = ListPage()
      page.add(*[ListItem(name, description=error, action_item=TextAction(tr("failed"))) for name, error in LOAD_ERRORS.items()])
      self.sections.append((tr("Failed"), page))
    for title, plugins, entries in market_sections(jobs.entries):
      page = ListPage()
      for p in plugins:
        use = {"onroad": tr("onroad"), "offroad": tr("offroad"), "both": tr("onroad + offroad")}[p.MANIFEST.use]
        summary = f"{use} - v{p.MANIFEST.version} - {p.MANIFEST.author or tr('unknown')} - {p.DESCRIPTION}"
        if p.SETTINGS:
          summary += "\n" + tr("Tap for settings ({})").format(len(p.SETTINGS))
        page.add(self.switches.make(p, summary, on_tap=lambda p=p: self._open_plugin(p)))
      for e in entries:
        use = {"onroad": tr("onroad"), "offroad": tr("offroad"), "both": tr("onroad + offroad")}.get(e.use, tr("use not declared"))
        page.add(InfoRow(lambda e=e: f"{e.name} - {jobs.row_value(jobs.current(e.id) or e)}",
                         f"{use} - v{e.version} - {e.author} - {e.description}\n{tr('Install & enable restarts while parked.')}",
                         ButtonAction(lambda e=e: jobs.action(jobs.current(e.id) or e), enabled=lambda e=e: jobs.can_act(e.id)),
                         callback=lambda e=e: jobs.run(lambda: jobs.act(e.id))))
      self.sections.append((tr(title), page))
    self.selected = max(0, min(self.selected, len(self.sections) - 1))
    self.buttons = [Button(title, lambda i=i: self._select(i), font_size=44) for i, (title, _) in enumerate(self.sections)]
    for b in self.buttons:
      b.set_rect(rl.Rectangle(0, 0, LEFT_W, CAT_H))
    self.categories = Scroller(self.buttons, spacing=GAP, pad_end=False)  # scrolls when the categories outgrow the column
    self._shown = None
    self._select(self.selected)

  def _select(self, i: int) -> None:
    self.selected = i
    for j, b in enumerate(self.buttons):
      b.set_button_style(ButtonStyle.PRIMARY if j == i else ButtonStyle.TRANSPARENT_WHITE_TEXT)

  def _open_plugin(self, plugin) -> None:
    if plugin.NAME not in self.pages:
      self.pages[plugin.NAME] = PluginPage(plugin, self.jobs)
    self.open(plugin.TITLE, self.pages[plugin.NAME])

  def show_event(self):
    super().show_event()
    if not self.fetched:
      self.fetched = True
      self.jobs.run(self.jobs.fetch)
    self.switches.refresh()
    self._shown = None  # the category page gets its show_event on the next render

  def hide_event(self):
    super().hide_event()
    if self._shown is not None:
      self._shown.hide_event()

  def _update_state(self):
    if gui_app.frame % gui_app.target_fps == 0:
      self.switches.refresh()
    if self.jobs.dirty:
      self.jobs.dirty = False
      self._build()

  def _render(self, rect: rl.Rectangle):
    self.categories.render(rl.Rectangle(rect.x, rect.y + GAP, LEFT_W, rect.height - CAT_H - 3 * GAP))
    self.refresh_button.render(rl.Rectangle(rect.x, rect.y + rect.height - CAT_H - GAP, LEFT_W, CAT_H))
    if not self.sections:
      return
    page = self.sections[self.selected][1]
    if page is not self._shown:
      if self._shown is not None:
        self._shown.hide_event()
      page.show_event()
      self._shown = page
    page.render(rl.Rectangle(rect.x + LEFT_W + 3 * GAP, rect.y, rect.width - LEFT_W - 3 * GAP, rect.height))
