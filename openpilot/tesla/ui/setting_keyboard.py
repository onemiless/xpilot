"""Plugin text/secret editing with the existing Wi-Fi keyboard; values stay out of messages."""
import pyray as rl

from openpilot.system.ui.lib.application import gui_app, MousePos
from openpilot.system.ui.widgets import DialogResult
from openpilot.system.ui.widgets.inputbox import InputBox
from openpilot.system.ui.widgets.keyboard import Keyboard
from openpilot.tesla.ui.offroad import editable


class SettingInput(InputBox):
  def __init__(self, setting, keyboard):
    super().__init__(setting.max_len, password_mode=setting.type == "secret")
    self.setting, self.keyboard = setting, keyboard

  def add_char_at_cursor(self, char):
    value = self.text[:self._cursor_position] + char + self.text[self._cursor_position:]
    error = self.setting.input_error(value)
    self.keyboard.set_title(self.setting.label, error)
    if error:
      return False
    return super().add_char_at_cursor(char)

  def _get_display_text(self):
    # Wi-Fi echoes the last character briefly; secrets must never echo it.
    return "*" * len(self.text) if self.setting.type == "secret" else self.text


class SettingKeyboard(Keyboard):
  def _render(self, rect):
    if rect.height >= 1080:
      return super()._render(rect)
    # The Wi-Fi keyboard uses tici pixel sizes; fit the same keys on mici.
    scale = rect.height / 1080
    events, scissor = gui_app._mouse_events, rl.begin_scissor_mode
    gui_app._mouse_events = [e._replace(pos=MousePos(e.pos.x / scale, e.pos.y / scale)) for e in events]
    rl.begin_scissor_mode = lambda x, y, w, h: scissor(int(x * scale), int(y * scale), int(w * scale), int(h * scale))
    rl.rl_push_matrix()
    rl.rl_scalef(scale, scale, 1)
    try:
      super()._render(rl.Rectangle(rect.x / scale, rect.y / scale, rect.width / scale, 1080))
    finally:
      rl.rl_pop_matrix()
      gui_app._mouse_events, rl.begin_scissor_mode = events, scissor


def edit_text_setting(setting, params, refresh) -> None:
  if not editable():
    return
  keyboard = SettingKeyboard()
  keyboard._input_box = SettingInput(setting, keyboard)
  keyboard.set_title(setting.label)
  if setting.type == "text":
    keyboard.set_text(params.get(setting.key, return_default=True) or "")

  def finished(result):
    if result == DialogResult.CONFIRM and editable():
      error = setting.input_error(keyboard.text)
      if error:
        keyboard.set_title(setting.label, error)
        gui_app.push_widget(keyboard)
        return
      params.put(setting.key, keyboard.text, block=True)
    keyboard.clear()
    refresh()

  keyboard.set_callback(finished)
  gui_app.push_widget(keyboard)
