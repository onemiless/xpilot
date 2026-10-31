"""Fork-only process changes, applied once to the official process list.

Keeps openpilot/system/manager/process_config.py official except for one call:
- driver monitoring follows the DM switch (C3XL has no cabin camera)
- micd/soundd only run on hardware with a microphone/amplifier (C3XL has neither)
- C3XL routes alerts to its GPIO buzzer
- xpilot feature plugins add their own processes (openpilot/tesla/plugins); an active source:map plugin replaces OSM
- SP's model stack is selected by the owner of role:perception.model
"""
from openpilot.common.hardware import PC
from openpilot.sunnypilot.hardware.driver_monitoring import driver_monitoring_enabled
from openpilot.sunnypilot.hardware.profile import HardwareProfile, get_hardware_profile, has_audio_output, has_microphone
from openpilot.system.manager.process import PythonProcess
from openpilot.tesla import plugins


def _gate(process, condition) -> None:
  official = process.should_run
  process.should_run = lambda started, params, CP: condition(params) and official(started, params, CP)


def apply_fork_processes(procs: list, only_onroad) -> list:
  by_name = {p.name: p for p in procs}
  for name in ("dmonitoringmodeld", "dmonitoringd"):
    _gate(by_name[name], driver_monitoring_enabled)
  _gate(by_name["micd"], lambda params: has_microphone())
  _gate(by_name["soundd"], lambda params: has_audio_output())
  for name in ("mapd", "mapd_manager", "map_osm"):
    _gate(by_name[name], lambda params: not plugins.holds(params, "source:map"))
  # mapd_manager is the sole liveMapDataSP publisher, including onroad.
  _gate(by_name["map_osm"], lambda params: False)
  role_procs = {n: by_name[n] for names in plugins.ROLE_PROCESSES.values() for n in names}
  procs = [p for p in procs if p.name not in role_procs] + [
    PythonProcess("alert_output", "openpilot.sunnypilot.system.alert_output",
                  lambda started, params, CP: not PC and get_hardware_profile() == HardwareProfile.C3XL),
  ]
  return procs + plugins.processes({p.name for p in procs}, role_procs)
