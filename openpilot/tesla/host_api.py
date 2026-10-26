"""Parked host operations for trusted plugins. Claims are a contract, not a sandbox."""
from pathlib import Path
import wave

from openpilot.common.swaglog import cloudlog
from openpilot.sunnypilot.hardware.profile import has_audio_output
from openpilot.tesla import manifest as mf


_WIFI_MANAGER = None


def _params():
  from openpilot.common.params import Params
  return Params()


def parked(params=None) -> bool:
  return (params if params is not None else _params()).get_bool("IsOffroad")


def _refuse(plugin, claim: str, params) -> str | None:
  from openpilot.tesla import plugins
  reason = None
  if not any(active.NAME == plugin.NAME for active in plugins.active(params)):
    reason = "plugin not active"
  elif claim not in plugin.CLAIMS:
    reason = f"missing claim {claim}"
  if reason:
    plugins.record_fault(plugin.NAME, "host_api", PermissionError(reason))
  return reason


def reboot(plugin) -> str | None:
  params = _params()
  if reason := _refuse(plugin, "power", params):
    return reason
  if not parked(params):
    return "park first"
  try:
    params.put_bool("DoReboot", True)
  except (OSError, RuntimeError):
    return "failed"
  cloudlog.warning(f"xpilot plugin {plugin.NAME} requested reboot")
  return None


def shutdown(plugin) -> str | None:
  params = _params()
  if reason := _refuse(plugin, "power", params):
    return reason
  if not parked(params):
    return "park first"
  try:
    params.put_bool("DoShutdown", True)
  except (OSError, RuntimeError):
    return "failed"
  cloudlog.warning(f"xpilot plugin {plugin.NAME} requested shutdown")
  return None


def _wifi_manager():
  global _WIFI_MANAGER
  if _WIFI_MANAGER is None:
    from openpilot.system.ui.lib.wifi_manager import WifiManager
    _WIFI_MANAGER = WifiManager()
  return _WIFI_MANAGER


def _wifi_action(plugin, action) -> str | None:
  params = _params()
  if reason := _refuse(plugin, "net.wifi", params):
    return reason
  if not parked(params):
    return "park first"
  try:
    manager = _wifi_manager()
    if manager._router_main is None:
      return "unsupported"
    action(manager)
  except ImportError:
    return "unsupported"
  except (OSError, RuntimeError):
    return "failed"
  return None


def wifi_connect(plugin, ssid, password) -> str | None:
  return _wifi_action(plugin, lambda manager: manager.connect_to_network(ssid, password))


def wifi_forget(plugin, ssid) -> str | None:
  return _wifi_action(plugin, lambda manager: manager.forget_connection(ssid))


def hotspot(plugin, on: bool) -> str | None:
  return _wifi_action(plugin, lambda manager: manager.set_tethering_active(on))


def data_dir(plugin) -> Path:
  path = mf.INSTALL_DIR / ".data" / plugin.NAME
  path.mkdir(parents=True, exist_ok=True)
  return path


def play_sound(plugin, path) -> str | None:
  params = _params()
  if reason := _refuse(plugin, "sound", params):
    return reason
  try:
    audio_output = has_audio_output()
  except (OSError, ValueError):
    return "unsupported"
  if not audio_output:
    return "unsupported"
  if not parked(params) or params.get_bool("IsDriverViewEnabled"):
    return "park first"

  try:
    sound = Path(path).resolve()
    roots = (data_dir(plugin).resolve(), (mf.INSTALL_DIR / plugin.NAME).resolve())
  except OSError:
    return "failed"
  if not any(sound.is_relative_to(root) for root in roots):
    return "path outside plugin directories"
  if sound.suffix.lower() != ".wav":
    return "WAV required"

  try:
    with wave.open(str(sound), "rb") as wav:
      channels, width, rate, frames = wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getnframes()
      if frames > rate * 10:
        return "sound longer than 10 s"
      # shortcut: 24-bit WAV is unsupported, add conversion when a plugin needs it.
      if width not in (1, 2, 4):
        return "unsupported"
      audio = wav.readframes(frames)
  except (EOFError, OSError, wave.Error):
    return "invalid WAV"

  try:
    import numpy as np
    import sounddevice as sd
  except ImportError:
    return "unsupported"
  try:
    samples = np.frombuffer(audio, dtype={1: np.uint8, 2: np.int16, 4: np.int32}[width]).reshape(-1, channels)
    sd.play(samples, rate, blocking=False)
  except (OSError, RuntimeError, ValueError, sd.PortAudioError):
    return "unsupported"
  return None
