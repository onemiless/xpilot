#!/usr/bin/env python3
"""Tesla sleep shutdown. Host power path, not a plugin.

Failure cases:
- S1 TeslaSleepShutdown is missing, not a persistent bool, or its default is not off
- S2 hardwared requests DoShutdown outside the existing shutdown gate, or ignores the sleep policy
- S3 the device page has no toggle, or its copy is not English/ASCII or disagrees with the delays
- S4 the key or the policy lives under a plugin directory
- S5 the toggle off, ignition on, onroad/started, unplugged harness, or a dead panda shuts down
- S6 CAN silence at boot, before the car has been awake, shuts down
- S7 ignition off with the bus still moving shuts down before the bounded delay
- S8 the bus dying together with ignition (no traffic after park) takes the fast silence path
- S9 silence shorter than the window, or a delay shorter than the bound, shuts down
- S10 ignition returning, a drive, disabling the toggle, or losing the panda fails to clear the timers
"""
import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

PARAM_LINE = '{"TeslaSleepShutdown", {PERSISTENT | BACKUP, BOOL, "0"}}'
POLICY = ROOT / "openpilot/system/hardware/tesla_sleep_shutdown.py"
HARDWARED = ROOT / "openpilot/system/hardware/hardwared.py"
DEVICE = ROOT / "openpilot/tesla/ui/pages/device.py"
PLUGINS = ROOT / "openpilot/tesla/plugins"


def source_contracts() -> None:
  params = (ROOT / "openpilot/common/params_keys.h").read_text()
  assert PARAM_LINE in params, "S1 TeslaSleepShutdown must default off"

  hardware = HARDWARED.read_text()
  assert "TeslaSleepShutdown()" in hardware, "S2 hardwared does not own the sleep policy"
  assert 'params.get_bool("TeslaSleepShutdown")' in hardware, "S2 toggle is not read"
  assert "started=started_ts is not None" in hardware, "S2 onroad state is not passed"
  assert "ignition=bool(onroad_conditions[\"ignition\"])" in hardware, "S2 ignition is not passed"
  tree = ast.parse(hardware)
  gated = False
  for node in ast.walk(tree):
    if not isinstance(node, ast.If):
      continue
    for call in ast.walk(node):
      if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute) or call.func.attr != "put_bool":
        continue
      if not call.args or not isinstance(call.args[0], ast.Constant) or call.args[0].value != "DoShutdown":
        continue
      test = ast.dump(node.test)
      assert "sleep_reason" in test and "should_shutdown" in test, "S2 DoShutdown is not gated"
      gated = True
  assert gated, "S2 DoShutdown write missing"
  assert hardware.count('put_bool("DoShutdown"') == 1, "S2 a second shutdown write was added"

  page = DEVICE.read_text()
  assert '"TeslaSleepShutdown"' in page, "S3 device page has no toggle"
  assert "Shut down when Tesla sleeps" in page, "S3 toggle title missing"
  assert "Off by default" in page, "S3 default-off copy missing"
  assert page.isascii(), "S3 device page copy is not ASCII"

  for path in PLUGINS.rglob("*"):
    if path.is_file() and path.suffix in {".py", ".json"}:
      assert "TeslaSleepShutdown" not in path.read_text(errors="replace"), f"S4 plugin file {path}"
  assert not (ROOT / "plugins").exists() or "TeslaSleepShutdown" not in "\n".join(
    p.read_text(errors="replace") for p in (ROOT / "plugins").rglob("*") if p.is_file()
  ), "S4 downloadable plugin tree"


def behavior() -> None:
  from openpilot.system.hardware.tesla_sleep_shutdown import (
    CAN_SILENCE_S,
    IGNITION_OFF_DELAY_S,
    TeslaSleepShutdown,
    panda_can_rx,
  )

  assert CAN_SILENCE_S == 120.0 and IGNITION_OFF_DELAY_S == 900.0
  page = DEVICE.read_text()
  assert f"{int(CAN_SILENCE_S // 60)} minutes" in page, "S3 silence window disagrees with the toggle"
  assert f"{int(IGNITION_OFF_DELAY_S // 60)} minutes" in page, "S3 ignition-off bound disagrees with the toggle"

  parked = dict(enabled=True, ignition=False, started=False, in_car=True, panda_alive=True)

  def fresh():
    policy = TeslaSleepShutdown()
    assert policy.update(enabled=True, ignition=True, started=True, in_car=True, panda_alive=True, can_rx=10, now=0.0) is None
    return policy

  # S5: off, driving, unplugged, or no panda never shuts down, even after both deadlines.
  later = IGNITION_OFF_DELAY_S + CAN_SILENCE_S + 5
  off = TeslaSleepShutdown()
  assert off.update(enabled=False, ignition=True, started=False, in_car=True, panda_alive=True, can_rx=1, now=0.0) is None
  assert off.update(enabled=False, ignition=False, started=False, in_car=True, panda_alive=True, can_rx=1, now=later) is None
  driving = fresh()
  assert driving.update(enabled=True, ignition=True, started=True, in_car=True, panda_alive=True, can_rx=10, now=later) is None
  started = fresh()
  assert started.update(enabled=True, ignition=False, started=True, in_car=True, panda_alive=True, can_rx=10, now=later) is None
  desk = fresh()
  assert desk.update(enabled=True, ignition=False, started=False, in_car=False, panda_alive=True, can_rx=10, now=later) is None
  dead = fresh()
  assert dead.update(enabled=True, ignition=False, started=False, in_car=True, panda_alive=False, can_rx=None, now=later) is None

  # S6: already silent at boot, car never seen awake.
  boot = TeslaSleepShutdown()
  assert boot.update(**parked, can_rx=0, now=0.0) is None
  assert boot.update(**parked, can_rx=0, now=later) is None

  # S7: bus still moving. No shutdown until the bounded delay, then shutdown anyway.
  moving = fresh()
  assert moving.update(**parked, can_rx=11, now=1.0) is None
  assert moving.update(**parked, can_rx=50, now=1.0 + CAN_SILENCE_S) is None
  assert moving.update(**parked, can_rx=80, now=1.0 + IGNITION_OFF_DELAY_S - 0.1) is None
  assert moving.update(**parked, can_rx=81, now=1.0 + IGNITION_OFF_DELAY_S) == "ignition-off-delay"

  # S8: CAN dies as ignition drops, with no frame after park. Wait for the bound, not the silence window.
  died = fresh()
  assert died.update(**parked, can_rx=10, now=1.0) is None
  assert died.update(**parked, can_rx=10, now=1.0 + CAN_SILENCE_S) is None
  assert died.update(**parked, can_rx=10, now=1.0 + IGNITION_OFF_DELAY_S) == "ignition-off-delay"

  # S9: traffic after park, then silence. Fast path only once the window elapses.
  quiet = fresh()
  assert quiet.update(**parked, can_rx=12, now=1.0) is None  # one more frame after ignition off
  assert quiet.update(**parked, can_rx=12, now=2.0) is None  # silence clock starts
  assert quiet.update(**parked, can_rx=12, now=2.0 + CAN_SILENCE_S - 0.1) is None
  assert quiet.update(**parked, can_rx=12, now=2.0 + CAN_SILENCE_S) == "can-silence"

  # A single new frame restarts the silence window and does not fire early.
  restart = fresh()
  assert restart.update(**parked, can_rx=12, now=1.0) is None
  assert restart.update(**parked, can_rx=12, now=2.0) is None
  assert restart.update(**parked, can_rx=13, now=2.0 + CAN_SILENCE_S - 1.0) is None
  assert restart.update(**parked, can_rx=13, now=2.0 + CAN_SILENCE_S) is None
  assert restart.update(**parked, can_rx=13, now=2.0 + CAN_SILENCE_S + CAN_SILENCE_S) == "can-silence"

  # S10: ignition, an onroad sample, disabling, or panda loss clears a pending window.
  cleared = fresh()
  assert cleared.update(**parked, can_rx=20, now=1.0) is None
  assert cleared.update(**parked, can_rx=20, now=2.0) is None
  assert cleared.update(enabled=True, ignition=True, started=False, in_car=True, panda_alive=True, can_rx=20, now=3.0) is None
  assert cleared.update(**parked, can_rx=20, now=4.0) is None
  assert cleared.update(**parked, can_rx=20, now=4.0 + CAN_SILENCE_S - 0.1) is None
  assert cleared.update(enabled=True, ignition=False, started=True, in_car=True, panda_alive=True, can_rx=20, now=100.0) is None
  assert cleared.update(**parked, can_rx=21, now=101.0) is None
  assert cleared.update(enabled=False, ignition=False, started=False, in_car=True, panda_alive=True, can_rx=21, now=102.0) is None
  assert cleared.update(**parked, can_rx=21, now=102.0 + IGNITION_OFF_DELAY_S - 0.1) is None
  assert cleared.update(enabled=True, ignition=False, started=False, in_car=True, panda_alive=False, can_rx=None, now=200.0) is None
  assert cleared.update(**parked, can_rx=21, now=200.0 + IGNITION_OFF_DELAY_S - 0.1) is None

  class Bus:
    def __init__(self, rx):
      self.totalRxCnt = rx

  class Panda:
    def __init__(self, a, b, c):
      self.canState0, self.canState1, self.canState2 = Bus(a), Bus(b), Bus(c)

  assert panda_can_rx([Panda(1, 2, 3), Panda(4, 0, 0)]) == 10
  assert panda_can_rx([]) is None
  assert panda_can_rx([Panda(0, 0, 0)]) == 0


def main() -> None:
  source_contracts()
  behavior()
  print(json.dumps({"passed": True, "cases": ["S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8", "S9", "S10"]}))


if __name__ == "__main__":
  main()
