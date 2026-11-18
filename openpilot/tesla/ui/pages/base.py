"""Base of the native offroad pages (UI v3): a vertical list of tici-style rows (system/ui/widgets/list_view.py) in a
Scroller, drawn by the host in screen pixels inside offroad.content_rect(). Edits are offroad-only, as on the mici pages."""
import pyray as rl

from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import FontWeight, gui_app
from openpilot.system.ui.lib.multilang import tr
from openpilot.system.ui.widgets import DialogResult, Widget
from openpilot.system.ui.widgets.button import Button
from openpilot.system.ui.widgets.confirm_dialog import ConfirmDialog, alert_dialog
from openpilot.system.ui.widgets.label import UnifiedLabel, gui_label
from openpilot.system.ui.widgets.list_view import ListItem, MultipleButtonAction, ButtonAction, toggle_item
from openpilot.system.ui.widgets.scroller_tici import Scroller
from openpilot.tesla.plugins import view
from openpilot.tesla.ui.offroad import editable, next_choice, setting_kind, step_value
from openpilot.tesla.ui.settings_meta import setting_choices, setting_label
from openpilot.tesla.ui.setting_keyboard import edit_text_setting

HEADER_COLOR = rl.Color(140, 144, 156, 255)
HEADER_H = 110
PAD = 60
TITLE_SIZE = 80
BODY_SIZE = 52
BUTTON_H = 150
BUTTON_W = 700


class Header(Widget):
  """Section header: gray caps."""

  def __init__(self, text: str):
    super().__init__()
    self._text = text.upper()
    self._rect = rl.Rectangle(0, 0, 0, HEADER_H)

  def set_parent_rect(self, parent_rect: rl.Rectangle) -> None:
    super().set_parent_rect(parent_rect)
    self._rect.width = parent_rect.width

  def _render(self, rect: rl.Rectangle):
    rl.draw_text_ex(gui_app.font(FontWeight.MEDIUM), self._text, rl.Vector2(rect.x + 20, rect.y + rect.height - 56), 36, 2, HEADER_COLOR)


class ListPage(Widget):
  """Rows in a vertical scroller. refresh() reloads row state from params; called on show_event.
  open(title, page) is set by the host: shows a native sub-page with a breadcrumb and Back."""

  def __init__(self):
    super().__init__()
    self._scroller = Scroller([], spacing=0, line_separator=True)
    self.open = lambda title, page: None

  def add(self, *items: Widget) -> None:
    for item in items:
      self._scroller.add_widget(item)

  def refresh(self) -> None:
    pass

  def show_event(self):
    super().show_event()
    self.refresh()
    self._scroller.show_event()

  def hide_event(self):
    super().hide_event()
    self._scroller.hide_event()

  def _render(self, rect: rl.Rectangle):
    self._scroller.render(rect)


def confirm(text: str, action: str, callback) -> None:
  """tici ConfirmDialog for destructive actions; blocked while engaged, like the mici EngagedConfirmation buttons."""
  if ui_state.engaged:
    gui_app.push_widget(alert_dialog(tr("Disengage to {}").format(tr(action))))
    return
  gui_app.push_widget(ConfirmDialog(text, tr(action).capitalize(),
                                    callback=lambda result: result == DialogResult.CONFIRM and not ui_state.engaged and callback()))


def back() -> None:
  """Close the open native page, like the host's Back button."""
  if gui_app.host is not None:
    gui_app.host.back()


def body(text: str) -> UnifiedLabel:
  return UnifiedLabel(text, BODY_SIZE)


def draw_card(rect: rl.Rectangle, title: str, text: UnifiedLabel | None, buttons: list[Button]) -> rl.Rectangle:
  """Big-screen card: bold title, wrapped body text and a row of buttons along the bottom right. Returns the body rect."""
  if title:
    gui_label(rl.Rectangle(rect.x + PAD, rect.y + PAD, rect.width - 2 * PAD, TITLE_SIZE), title, TITLE_SIZE, font_weight=FontWeight.BOLD)
  top = rect.y + (2 * PAD + TITLE_SIZE if title else PAD)
  area = rl.Rectangle(rect.x + PAD, top, rect.width - 2 * PAD, rect.y + rect.height - top - (BUTTON_H + 2 * PAD if buttons else PAD))
  if text is not None:
    text.render(area)
  w = min(BUTTON_W, (rect.width - 2 * PAD - PAD * (len(buttons) - 1)) / max(len(buttons), 1))
  x = rect.x + rect.width - PAD - len(buttons) * (w + PAD) + PAD
  for b in buttons:
    b.render(rl.Rectangle(x, rect.y + rect.height - PAD - BUTTON_H, w, BUTTON_H))
    x += w + PAD
  return area


def params():
  """ui_state.params with downloaded plugins' keys routed to their store."""
  return view(ui_state.params)


def set_param(key: str, value) -> None:
  if editable():
    params().put(key, value, block=True)


class SettingRow:
  """A plugin/vehicle Setting row: text/secret editor, toggle, -/+ stepper, buttons, or next-value button. refresh() reloads it."""

  def __init__(self, setting, title: str | None = None, translate: bool = False):
    self.setting = setting
    self.translate = translate
    self.kind = setting_kind(setting)
    title = title or setting.label
    title = tr(title) if translate else title
    if self.kind in ("text", "secret"):
      action = ButtonAction(tr("Edit"), enabled=editable)
      action.set_value(lambda: tr(setting_label(setting, self._value())) if translate else setting_label(setting, self._value()))
      self.item = ListItem(title=title, action_item=action, callback=lambda: edit_text_setting(setting, params(), self.refresh))
    elif self.kind == "toggle":
      self.item = toggle_item(title, initial_state=bool(self._value()), callback=self._set, enabled=editable)
    elif self.kind == "stepper":
      def label():
        value = setting_label(setting, self._value(), ui_state.is_metric)
        return tr(value) if translate else value
      def step(i):
        if i != 1:
          self._set(step_value(setting, self._value(), 1 if i == 2 else -1))
        self.item.action_item.set_selected_button(1)
      self.item = ListItem(title=title, action_item=MultipleButtonAction(["-", label, "+"], 220, 1, callback=step))
      self.item.action_item.set_enabled(editable)
    elif self.kind == "buttons":
      choices = setting_choices(setting, ui_state.is_metric)
      self.choices = [(tr(label), value) for label, value in choices] if translate else choices
      self.item = ListItem(title=title, action_item=MultipleButtonAction([label for label, _ in self.choices], 250,
                                                                         callback=lambda i: self._set(self.choices[i][1])))
      self.item.action_item.set_enabled(editable)
    else:
      choices = setting_choices(setting, ui_state.is_metric)
      self.choices = [(tr(label), value) for label, value in choices] if translate else choices
      def display_value():
        value = setting_label(setting, self._value(), ui_state.is_metric)
        return tr(value) if translate else value
      action = ButtonAction(display_value, enabled=editable)
      self.item = ListItem(title=title, action_item=action, callback=lambda: self._set(next_choice(self.choices, self._value())))
    self.refresh()

  def _value(self):
    return params().get(self.setting.key, return_default=True)

  def _set(self, value) -> None:
    set_param(self.setting.key, value)
    self.refresh()

  def refresh(self) -> None:
    value = self._value()
    if self.kind == "toggle":
      self.item.action_item.set_state(bool(value))
    elif self.kind == "buttons":
      values = [v for _, v in self.choices]
      if value in values:
        self.item.action_item.set_selected_button(values.index(value))


class ParamToggle:
  """toggle_item bound to a bool param (like mici BigParamControl); after(value) runs once the param is written."""

  def __init__(self, title: str, key: str, description: str = "", enabled=True, after=None):
    self.key, self.after = key, after
    self.item = toggle_item(title, description, ui_state.params.get_bool(key), self._set, enabled=enabled)

  def _set(self, value: bool) -> None:
    ui_state.params.put_bool(self.key, value, block=True)
    if self.after is not None:
      self.after(value)

  def refresh(self) -> None:
    self.item.action_item.set_state(ui_state.params.get_bool(self.key))
