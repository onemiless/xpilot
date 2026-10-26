"""Models page (native): what mici ModelsLayoutMici offers (active model and download state, small and big model lists,
cancel download, refresh list, clear cache), reusing selfdrive/ui/sunnypilot/model_info.py. Each model list is a native
sub-page: the default, then one row per bundle under its folder header (favorites first). Select, refresh and clear cache
are offroad-only (the model manager runs offroad only); clear cache asks in a tici ConfirmDialog."""
import time

from openpilot.cereal import custom
from openpilot.selfdrive.ui.sunnypilot.mici.layouts.models import ModelsLayoutMici, _model_info
from openpilot.selfdrive.ui.sunnypilot.model_info import (active_source, bundles_for_source, default_model_name, model_cache_size_mb,
                                                           queued_name, refresh_in_progress, refresh_model_list)
from openpilot.selfdrive.ui.ui_state import device, ui_state
from openpilot.sunnypilot.models.helpers import ACTIVE_BUNDLE_KEYS, get_selected_bundle
from openpilot.system.ui.lib.application import gui_app
from openpilot.system.ui.lib.multilang import tr, tr_noop
from openpilot.system.ui.widgets.confirm_dialog import alert_dialog
from openpilot.system.ui.widgets.list_view import ButtonAction, ListItem, button_item, text_item
from openpilot.tesla.ui.pages.base import Header, ListPage, confirm

DS = custom.ModelManagerSP.DownloadStatus
SOURCES = (("qcom", tr_noop("Small models")), ("chestnut", tr_noop("Big models")))


def _row(title, text, value, callback, enabled=True) -> ListItem:
  action = ButtonAction(text, enabled=enabled)
  action.set_value(value)
  return ListItem(title=title, action_item=action, callback=callback)


def _status(bundle) -> tuple[str, str, str, str, bool]:
  """(header, model, info header, info, verifying): the mici panel text, the download progress while downloading."""
  active, info_header, info = _model_info()
  if not (bundle and bundle.status == DS.downloading):
    if bundle and bundle.status == DS.failed:
      info_header, info = tr_noop("error"), tr_noop("download failed")
    elif bundle and bundle.status == DS.downloaded:
      info_header, info = tr_noop("downloaded"), tr_noop("downloaded")
    return tr_noop("active model"), active, info_header, info, False
  progress, verifying, canceling, speed = 0.0, False, False, 0.0
  for model in bundle.models:
    p = model.artifact.downloadProgress
    if p.status in (DS.downloading, DS.verifying):
      progress += p.progress
      verifying = verifying or p.status == DS.verifying
      canceling = canceling or p.canceling
      speed = max(speed, p.speed)  # one file downloads at a time
    elif p.status in (DS.downloaded, DS.cached):
      progress += 100.0
  name = bundle.internalName
  if queued := queued_name(bundle.ref):
    name += "  |  " + tr("{} queued").format(queued)
  info = f"{progress / max(len(bundle.models), 1):.2f}%"
  if not verifying and not canceling:
    info += f"  |  {speed / 1024 ** 2:.1f} MB/s" if speed > 0 else "  |  --.- MB/s"
  status = tr_noop("canceling...") if canceling else tr_noop("verifying") if verifying else tr_noop("downloading")
  return status, name, tr_noop("progress"), info, verifying


def _selected_name(source: str) -> str:
  bundle = get_selected_bundle(ui_state.params, source)
  name = bundle.internalName if bundle else default_model_name(source)
  return name + (tr(" (active)") if source == active_source() else "")


class BundlesPage(ListPage):
  """One source's models; the button shows CURRENT for the selected one and QUEUED for the pending download."""

  def __init__(self, source: str, bundles):
    super().__init__()
    self.source = source
    self._current = self._queued = None
    favs = ui_state.params.get("ModelManager_Favs")
    folders = ModelsLayoutMici._get_grouped_bundles(None, bundles, set(favs.split(";")) if favs else set())
    order = sorted(folders, key=lambda f: max((b.index for b in folders[f]), default=-1), reverse=True)
    order.sort(key=lambda f: f.lower() != "favorites")  # stable: favorites first, the rest newest first
    self.add(button_item(default_model_name(source), lambda: tr("CURRENT") if self._current is None else tr("SELECT"),
                         callback=lambda: self._select(None), enabled=ui_state.is_offroad))
    for folder in order:
      self.add(Header(folder or tr("models")))
      for b in sorted(folders[folder], key=lambda b: b.index, reverse=True):
        self.add(button_item(b.displayName, lambda r=b.ref: tr("CURRENT") if r == self._current else tr("QUEUED") if r == self._queued else tr("SELECT"),
                             callback=lambda b=b: self._select(b), enabled=ui_state.is_offroad))

  def _select(self, bundle) -> None:
    if not ui_state.is_offroad():
      return
    if bundle is None:
      ui_state.params.remove(ACTIVE_BUNDLE_KEYS[self.source])
    else:
      ui_state.params.put("ModelManager_DownloadRef", bundle.ref)

  def _update_state(self):
    super()._update_state()
    selected = get_selected_bundle(ui_state.params, self.source)
    self._current = selected.ref if selected else None
    self._queued = ui_state.params.get("ModelManager_DownloadRef")

  def show_event(self):
    super().show_event()


class ModelsPage(ListPage):
  def __init__(self):
    super().__init__()
    params = ui_state.params
    self._s = (tr_noop("active model"), "", "", "", False)
    self._downloading = self._was_downloading = self._clearing = False
    self._refresh_start: float | None = None
    self._cache, self._cache_t = "", 0.0
    offroad = ui_state.is_offroad
    cancel = button_item(lambda: tr("Cancel verification") if self._s[4] else tr("Cancel download"), tr("CANCEL"),
                         callback=lambda: params.remove("ModelManager_DownloadRef"))
    cancel.set_visible(lambda: self._downloading and params.get("ModelManager_DownloadRef") is not None)
    self.add(
      text_item(lambda: tr(self._s[0]).capitalize(), lambda: self._s[1]),
      text_item(lambda: tr(self._s[2]).capitalize(), lambda: tr(self._s[3])),
      *[_row(tr(label), tr("SELECT"), lambda s=source: _selected_name(s), lambda s=source: self._choose(s), offroad) for source, label in SOURCES],
      cancel,
      _row(tr("Refresh model list"), tr("REFRESH"), lambda: tr("fetching...") if self._refreshing() else "", self._refresh,
           lambda: offroad() and not self._downloading and not self._refreshing()),
      _row(tr("Clear model cache"), tr("CLEAR"), lambda: tr("clearing...") if self._clearing else self._cache, lambda: confirm(
        tr("Clear the model cache? Selected models download again."), "clear", lambda: params.put_bool("ModelManager_ClearCache", True)),
           lambda: offroad() and not self._downloading and not self._clearing),
    )

  def _refreshing(self) -> bool:
    return refresh_in_progress(self._refresh_start)

  def _refresh(self) -> None:
    if ui_state.is_offroad():
      refresh_model_list()
      self._refresh_start = time.monotonic()

  def _choose(self, source: str) -> None:
    bundles = bundles_for_source(source)
    if not bundles:
      gui_app.push_widget(alert_dialog(tr("No models are available for this hardware yet. Connect to the internet and refresh the model list.")))
      return
    self.open(tr(dict(SOURCES)[source]), BundlesPage(source, bundles))

  def _update_state(self):
    super()._update_state()
    bundle = ui_state.sm["modelManagerSP"].selectedBundle
    self._s = _status(bundle)
    self._downloading = bool(bundle and bundle.status == DS.downloading)
    if self._downloading:
      device.set_override_interactive_timeout(5)  # as mici: keep the screen on while downloading
    elif self._was_downloading:
      device.set_override_interactive_timeout(None)
    self._was_downloading = self._downloading
    self._clearing = ui_state.params.get_bool("ModelManager_ClearCache")
    if self._clearing:
      self._cache_t = 0.0  # re-read the size once clearing finishes
    elif (now := time.monotonic()) - self._cache_t > 0.5:
      self._cache, self._cache_t = f"{model_cache_size_mb():.1f} MB", now

  def show_event(self):
    super().show_event()
    self._cache_t = 0.0

  def hide_event(self):
    super().hide_event()
    if self._was_downloading:
      device.set_override_interactive_timeout(None)
      self._was_downloading = False
