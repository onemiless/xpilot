"""Tesla page (native): car model, driver monitoring and OSM map toggles and the VEHICLE_SETTINGS rows of mici TeslaSettingsMici."""
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.sunnypilot.hardware.driver_monitoring import driver_monitoring_configured
from openpilot.sunnypilot.hardware.profile import has_driver_camera
from openpilot.system.ui.widgets.list_view import ButtonAction, ListItem, button_item, toggle_item
from openpilot.system.ui.lib.multilang import multilang, tr, tr_noop
from openpilot.tesla.ui.offroad import MODEL_PARAM, tesla_models
from openpilot.tesla.ui.pages.base import Header, ListPage, SettingRow, back
from openpilot.tesla.ui.settings_meta import VEHICLE_SETTINGS


MODEL_DESC = tr_noop("Auto identifies the car when it starts. Pick a model only if it is not recognized. Applies on the next start.")


def _model() -> str:
  return (ui_state.params.get(MODEL_PARAM) or {}).get("name") or tr("Auto")


class ModelPage(ListPage):
  """Auto, then one row per Tesla in the car list; picking one goes back."""

  def __init__(self):
    super().__init__()
    self.add(button_item(tr("Auto"), lambda: tr("SELECTED") if _model() == tr("Auto") else tr("SELECT"), tr(MODEL_DESC),
                         callback=lambda: self._pick(None), enabled=ui_state.is_offroad))
    for name, bundle in tesla_models():
      self.add(button_item(name, lambda name=name: tr("SELECTED") if _model() == name else tr("SELECT"),
                           callback=lambda bundle=bundle: self._pick(bundle), enabled=ui_state.is_offroad))

  def _pick(self, bundle) -> None:
    if ui_state.is_offroad():
      if bundle is None:
        ui_state.params.remove(MODEL_PARAM)
      else:
        ui_state.params.put(MODEL_PARAM, bundle, block=True)
      back()


class TeslaPage(ListPage):
  def __init__(self):
    super().__init__()
    self.models = ModelPage()
    self.model = ListItem(tr("Car model"), description=tr(MODEL_DESC), action_item=ButtonAction(_model, width=900, enabled=ui_state.is_offroad),
                          callback=lambda: self.open(tr("car model"), self.models))
    self.dm = toggle_item(tr("Driver Monitoring"), tr("When off, the DM processes do not start. Change only while parked; applies on the next drive."),
                          driver_monitoring_configured(ui_state.params), self._set_dm,
                          enabled=lambda: ui_state.is_offroad() and has_driver_camera())
    self.map = toggle_item(tr("OSM Map Data"), initial_state=self._osm(), callback=self._set_map, enabled=ui_state.is_offroad)
    self.rows = [SettingRow(s, translate=True) for s in VEHICLE_SETTINGS]
    self.add(Header(tr("Vehicle")), self.model, *(r.item for r in self.rows), Header(tr("Device")), self.dm, self.map)
    if len(multilang.languages) > 1:
      self.language_page = LanguagePage()
      self.language = ListItem(tr("Language"), description=tr("Language changes apply after the UI restarts."),
                               action_item=ButtonAction(lambda: multilang.codes[multilang.language], width=900),
                               callback=lambda: self.open(tr("language"), self.language_page))
      self.add(self.language)

  @staticmethod
  def _osm() -> bool:
    return ui_state.params.get("MapProvider", return_default=True) == "osm"

  def _set_dm(self, value: bool) -> None:
    if ui_state.is_offroad() and has_driver_camera():
      ui_state.params.put_bool("DriverMonitoringEnabled", value, block=True)
    self.refresh()

  def _set_map(self, value: bool) -> None:
    if ui_state.is_offroad():
      ui_state.params.put("MapProvider", "osm" if value else "none", block=True)
    self.refresh()

  def refresh(self) -> None:
    self.dm.action_item.set_state(driver_monitoring_configured(ui_state.params))
    self.map.action_item.set_state(self._osm())
    for row in self.rows:
      row.refresh()

  def show_event(self):
    super().show_event()


class LanguagePage(ListPage):
  def __init__(self):
    super().__init__()
    for name, code in multilang.languages.items():
      self.add(button_item(name, lambda code=code: tr("SELECTED") if multilang.language == code else tr("SELECT"),
                           callback=lambda code=code: self._pick(code), enabled=ui_state.is_offroad))

  def _pick(self, code: str) -> None:
    if ui_state.is_offroad():
      multilang.change_language(code)
      back()
