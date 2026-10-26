"""Terms page (native): the text and QR code of mici TermsPage (selfdrive/ui/mici/layouts/onboarding.py). Without callbacks it is
review only (Device); onboarding passes on_accept and on_decline and gets the accept and decline buttons."""
from __future__ import annotations

from collections.abc import Callable

import pyray as rl

from openpilot.selfdrive.ui.mici.layouts.onboarding import QRCodeWidget
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.widgets import Widget
from openpilot.system.ui.widgets.button import Button, ButtonStyle
from openpilot.system.ui.lib.multilang import tr
from openpilot.tesla.ui.pages.base import PAD, body, draw_card

URL = "https://sunnypilot.ai/terms"
QR_SIZE = 500


class TermsPage(Widget):
  def __init__(self, on_accept: Callable[[], None] | None = None, on_decline: Callable[[], None] | None = None):
    super().__init__()
    review = on_accept is None
    self._title = "" if review else tr("terms of service")
    self._text = body(tr("Scan the QR code, or go to {}").format(URL) +
                      ("" if review else tr("\n\nYou must accept the Terms of Service to use sunnypilot.")))
    self._qr = QRCodeWidget(URL, QR_SIZE)
    self._buttons = []
    if not review:
      self._buttons = [Button(tr("decline & uninstall"), on_decline, button_style=ButtonStyle.DANGER),
                       Button(tr("accept terms"), on_accept, button_style=ButtonStyle.PRIMARY)]
      for b in self._buttons:
        b.set_enabled(ui_state.is_offroad)

  def show_event(self):
    super().show_event()

  def _render(self, rect: rl.Rectangle):
    rl.draw_rectangle_rec(rect, rl.BLACK)
    text = rl.Rectangle(rect.x, rect.y, rect.width - QR_SIZE - PAD, rect.height)
    draw_card(text, self._title, self._text, [])
    self._qr.render(rl.Rectangle(rect.x + rect.width - QR_SIZE - PAD, rect.y + PAD, QR_SIZE, QR_SIZE))
    draw_card(rect, "", None, self._buttons)
