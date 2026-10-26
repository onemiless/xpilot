"""First-boot onboarding (native, full screen 2160x1080): the flow of mici OnboardingWindow (selfdrive/ui/mici/layouts/onboarding.py).
Terms: accept writes HasAcceptedTerms and HasAcceptedTermsSP, decline asks once more and then writes DoUninstall. Then the training
guide, which ends by writing CompletedTrainingVersion. on_done is called once, where mici pops the onboarding window.
hardwared does not go onroad before both are done, so this always runs offroad."""
from __future__ import annotations

from collections.abc import Callable

import pyray as rl

from openpilot.common.version import terms_version, terms_version_sp, training_version
from openpilot.selfdrive.ui.ui_state import device, ui_state
from openpilot.system.ui.widgets import Widget
from openpilot.system.ui.widgets.button import Button, ButtonStyle
from openpilot.system.ui.lib.multilang import tr
from openpilot.tesla.ui.pages.base import body, draw_card
from openpilot.tesla.ui.pages.terms import TermsPage
from openpilot.tesla.ui.pages.training import TrainingGuidePage

TERMS, DECLINE, GUIDE = range(3)


def _accepted_terms() -> bool:
  return ui_state.params.get("HasAcceptedTerms") == terms_version and ui_state.params.get("HasAcceptedTermsSP") == terms_version_sp


def _training_done() -> bool:
  return ui_state.params.get("CompletedTrainingVersion") == training_version


def onboarding_needed() -> bool:
  """not OnboardingWindow.completed"""
  return not (_accepted_terms() and _training_done())


class OnboardingPage(Widget):
  def __init__(self, on_done: Callable[[], None]):
    super().__init__()
    self._on_done = on_done
    self._done = False
    self._view = TERMS
    self._terms = TermsPage(on_accept=self._on_terms_accepted, on_decline=lambda: self._set_view(DECLINE))
    self._guide = TrainingGuidePage(on_done=self._on_completed_training)
    self._decline_text = body(tr("You must accept the Terms of Service to use sunnypilot.\n\n" +
                                 "Decline the terms and uninstall sunnypilot from this device?"))
    self._decline_buttons = [Button(tr("back"), lambda: self._set_view(TERMS)),
                             Button(tr("decline & uninstall"), self._on_uninstall, button_style=ButtonStyle.DANGER)]

  def _set_view(self, view: int) -> None:
    if view == GUIDE and self._view != GUIDE:
      self._guide.show_event()
    self._view = view

  def _on_uninstall(self) -> None:
    if ui_state.is_offroad():
      ui_state.params.put_bool("DoUninstall", True, block=True)

  def _on_terms_accepted(self) -> None:
    if not ui_state.is_offroad():
      return
    ui_state.params.put("HasAcceptedTerms", terms_version, block=True)
    ui_state.params.put("HasAcceptedTermsSP", terms_version_sp, block=True)
    if not _training_done():
      self._set_view(GUIDE)
    else:
      self._close()

  def _on_completed_training(self) -> None:
    ui_state.params.put("CompletedTrainingVersion", training_version, block=True)
    self._close()

  def _close(self) -> None:
    if self._done:
      return
    self._done = True
    self.hide_event()
    ui_state.params.put_bool("IsDriverViewEnabled", False)
    self._on_done()

  def show_event(self):
    super().show_event()
    self._done = False
    device.set_override_interactive_timeout(300)
    device.set_offroad_brightness(100)
    self._view = TERMS
    if _accepted_terms() and not _training_done():
      self._set_view(GUIDE)

  def hide_event(self):
    super().hide_event()
    device.set_override_interactive_timeout(None)
    device.set_offroad_brightness(None)
    if self._view == GUIDE:
      self._guide.hide_event()
    self._view = TERMS

  def _render(self, rect: rl.Rectangle):
    rl.draw_rectangle_rec(rect, rl.BLACK)
    if self._view == GUIDE:
      self._guide.render(rect)
    elif self._view == DECLINE:
      draw_card(rect, tr("decline terms"), self._decline_text, self._decline_buttons)
    else:
      self._terms.render(rect)
