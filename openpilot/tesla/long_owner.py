"""G8 proposals only: controlsd/Panda own all vetoes and acknowledgement. No CAN transmission."""
import time

from openpilot.cereal import messaging
from openpilot.common.swaglog import cloudlog
from openpilot.tesla import plugins


class LongOwnerPolicy:
  def __init__(self, policies, manual):
    self.policies, self.manual = policies, manual
    self.can = messaging.sub_sock("can")
    self.down = True  # require a non-four-finger sample before the first rising edge
    self.manual_target = None
    self.faulted = False
    self.shadow_time = 0

  def __call__(self, ctx, CS):
    """ctx: available (verified ABI), owner (stock/sp), enabled, sp_accel; plugins may also read ctx.sm.
    Manual input is a one-cycle proposal; the caller latches/debounces it before applying its normal vetoes.
    """
    self.manual_target = None
    self.faulted = False
    now = time.monotonic_ns()
    manual = None
    for event in messaging.drain_sock(self.can):
      if not 0 <= now - event.logMonoTime <= 100_000_000:
        continue
      for frame in event.can:
        if frame.address == 0x3DF and frame.src == 1 and len(frame.dat) == 8:
          # UI_status2.UI_activeTouchPoints: same vehicle-bus signal as Tesla CarStateExt.
          down = frame.dat[3] == 4
          if down and not self.down and self.manual and ctx.available and ctx.enabled:
            manual = 'sp' if ctx.owner == 'stock' else 'stock'
          self.down = down
        if (frame.address == 0x2B9 and frame.src == 2 and len(frame.dat) == 8 and ctx.available and ctx.owner == 'sp'
            and now - self.shadow_time >= 40_000_000):
          raw = int.from_bytes(frame.dat, 'little')
          cloudlog.event('xpilot.long_owner_shadow', canMonoTime=event.logMonoTime,
                         accState=(raw >> 12) & 15, accelMin=((raw >> 35) & 511) * .04 - 15,
                         accelMax=((raw >> 44) & 511) * .04 - 15, counter=(raw >> 53) & 7, spAccel=ctx.sp_accel)
          self.shadow_time = now
    if not ctx.available:
      return None
    desired = None
    if self.policies:
      obj = self.policies[0]
      ok, desired = plugins._call(self.policies, obj, 'desired', 'controlsd.long_owner', ctx, CS)
      if not ok:
        self.faulted = True
        return None
      if desired is not None and (type(desired) is not str or desired not in ('stock', 'sp')):
        self.faulted = True
        self.policies.clear()
        plugins.record_fault(obj.plugin_name, 'controlsd.long_owner.desired', ValueError(f'bad owner {desired!r}'))
        return None
    self.manual_target = manual
    return manual if manual is not None else desired
