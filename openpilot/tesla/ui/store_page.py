"""Store page (mici), opened from the Plugins page: lists the plugin repository index (store.py), installs, updates
and removes downloaded plugins while offroad. Network and disk work run in a background thread (offroad.StoreJobs,
shared with the big-screen Store page); row values show the state. Changes apply after a restart.
Downloaded plugins are reviewed but not signed."""
from openpilot.selfdrive.ui.mici.widgets.button import BigButton
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import gui_app
from openpilot.system.ui.lib.multilang import tr
from openpilot.system.ui.widgets.scroller import NavScroller
from openpilot.tesla.ui.offroad import StoreJobs


class StorePage(NavScroller):
  def __init__(self):
    super().__init__()
    self._jobs = StoreJobs(ui_state.is_offroad)
    self._rows: dict[str, BigButton] = {}
    back = BigButton(tr('Back'))
    back.set_click_callback(gui_app.pop_widget)
    self._scroller.add_widget(back)
    self.refresh = BigButton(tr('Refresh'), description=tr('Download the plugin list from github.com/onemiless/xpilot-plugins.'))
    self.refresh.set_click_callback(lambda: self._jobs.run(self._jobs.fetch))
    self.refresh.set_enabled(lambda: not self._jobs.busy)
    self._scroller.add_widget(self.refresh)

  def _update_state(self):
    super()._update_state()
    jobs = self._jobs
    self.refresh.set_value(tr('working...') if jobs.busy else jobs.status)
    if jobs.dirty:  # rebuild rows on the UI thread after a fetch
      jobs.dirty = False
      for entry in jobs.entries:
        if entry.id not in self._rows:
          row = BigButton(tr(entry.name), description=f'{tr(entry.description)}\n\n{entry.author} - {entry.category} - {entry.id}' +
                                                  '\n' + tr('Install & enable restarts while parked.'))
          row.set_click_callback(lambda pid=entry.id: jobs.run(lambda: jobs.act(pid)))
          row.set_enabled(lambda pid=entry.id: jobs.can_act(pid))
          self._scroller.add_widget(row)
          self._rows[entry.id] = row
    if not jobs.busy:
      for entry in jobs.entries:
        if entry.id in self._rows:
          self._rows[entry.id].set_value(f'{jobs.row_value(entry)} - {jobs.action(entry).lower()}')
