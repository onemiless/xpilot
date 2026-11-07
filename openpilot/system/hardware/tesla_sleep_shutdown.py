"""Opt-in shutdown after a parked Tesla goes to sleep.

Ignition here is the harness line or Tesla VCFRONT vehiclePowerState DRIVE.
Leaving DRIVE only means the car is parked. The body controller keeps talking
until the car actually sleeps, which is the vehicle bus going quiet.

The device's own 12V draw can hold that bus awake forever, so ignition-off
also has a bounded delay. Both paths stay closed while the toggle is off,
while ignition is on, while openpilot is onroad, while the harness is
unplugged, or while no panda is alive. A boot into an already quiet bus does
not shut down: the car has to have been seen awake first.

poweroff stops the CPU. Nothing in software can turn it back on. The next
drive powers the device only when sleep removes 12V and wake restores it.
If 12V stays up, the device stays off until the power button is pressed.
"""
CAN_SILENCE_S = 120.0
IGNITION_OFF_DELAY_S = 15 * 60.0


def panda_can_rx(panda_states) -> int | None:
  """Sum receive counters on every panda bus. None when no counter exists."""
  total = 0
  found = False
  for panda in panda_states:
    for bus in ("canState0", "canState1", "canState2"):
      state = getattr(panda, bus, None)
      count = None if state is None else getattr(state, "totalRxCnt", None)
      if count is None:
        continue
      total += int(count)
      found = True
  return total if found else None


class TeslaSleepShutdown:
  def __init__(self) -> None:
    self.seen_awake = False
    self._last_rx: int | None = None
    self._silent_since: float | None = None
    self._ignition_off_since: float | None = None
    self._traffic_since_off = False

  def update(self, *, enabled: bool, ignition: bool, started: bool, in_car: bool,
             panda_alive: bool, can_rx: int | None, now: float) -> str | None:
    """Return 'can-silence' or 'ignition-off-delay' when DoShutdown should be set."""
    rx_changed = False
    if panda_alive and can_rx is not None:
      if self._last_rx is not None and can_rx != self._last_rx:
        rx_changed = True
        self.seen_awake = True
      self._last_rx = can_rx
    else:
      self._last_rx = None

    # Onroad or DRIVE: never arm, and drop any park timer from the previous edge.
    if ignition or started:
      self.seen_awake = True
      self._clear_park()
      return None

    if not (enabled and in_car and panda_alive and self.seen_awake):
      self._clear_park()
      return None

    if self._ignition_off_since is None:
      self._ignition_off_since = now
    if rx_changed:
      self._traffic_since_off = True

    # Silence counts only after a frame arrived while already parked. A bus that
    # dies in the same moment as ignition must use the longer delay instead.
    if not self._traffic_since_off or can_rx is None or rx_changed:
      self._silent_since = None
    elif self._silent_since is None:
      self._silent_since = now
    elif now - self._silent_since >= CAN_SILENCE_S:
      return "can-silence"

    if now - self._ignition_off_since >= IGNITION_OFF_DELAY_S:
      return "ignition-off-delay"
    return None

  def _clear_park(self) -> None:
    self._ignition_off_since = None
    self._silent_since = None
    self._traffic_since_off = False
