"""Tesla runtime ownership: Panda health commits; host policy only requests."""
import math

# Set on this car for whole drives (bus-2 interrupt rate, X-20261028-75d): pausing on it disabled every owner switch.
BENIGN_PANDA_FAULTS = {'interruptRateCan2'}


def desired_owner(ctx, CS):
  if ctx.long_owner_policy is None:
    return None
  desired = ctx.long_owner_policy(ctx.long_owner, CS)
  if ctx.long_owner_policy.faulted:
    raise ValueError('long owner policy fault')
  return desired


def sp_longitudinal(CP, CC_SP):
  return str(CC_SP.longOwner) == 'sp' if CP.brand == 'tesla' else CP.openpilotLongitudinalControl


class LongOwner:
  def __init__(self, CP):
    self.owner = 'sp' if CP.openpilotLongitudinalControl else 'stock'
    self.available = False
    self.seen_abi = False
    self.locked = False
    self.reason = ''
    self.refusals = 0
    self.desired = None
    self.confirm_since = 0.
    self.cooldown_until = 0.
    self.request = 255
    self.request_since = 0.
    self.request_refusal = 0
    self.request_seen = False
    self.panda_fault = False

  def refuse(self, reason, now):
    self.reason = reason
    self.refusals += 1
    self.locked |= self.refusals >= 2
    self.request = 255
    self.desired = None
    self.cooldown_until = now + 2.

  def update(self, ctx, CS, CC_SP, now):
    sm = ctx.sm
    enabled = sm['selfdriveState'].enabled
    self.sm, self.enabled, self.sp_accel = sm, enabled, ctx.LoC.last_output_accel
    if not enabled:
      self.locked, self.refusals, self.request, self.desired = False, 0, 255, None
    pandas = [p for p in sm['pandaStates'] if p.safetyModel == 'tesla']
    self.available = ctx.CP.brand == 'tesla' and len(pandas) == 1 and pandas[0].longOwnerAvailable
    if self.available:
      p = pandas[0]
      self.seen_abi = True
      self.owner = 'sp' if p.longOwner == 1 else 'stock'
      if self.request != 255:
        self.request_seen |= p.longOwnerPending == self.request or p.longOwnerRefusal == 0
        if p.longOwner == self.request:
          self.request, self.refusals, self.desired = 255, 0, None
          self.cooldown_until = now + 2.
        elif (sm.logMonoTime['pandaStates'] / 1e9 > self.request_since and p.longOwnerPending == 255 and
              p.longOwnerRefusal and (self.request_seen or p.longOwnerRefusal != self.request_refusal or
                                      now - self.request_since >= .25)):
          self.refuse(f'panda:{p.longOwnerRefusal}', now)
        elif now - self.request_since > .5:
          self.locked, self.reason, self.request = True, 'ackMismatch', 255
      # Another temporary panda fault only pauses new requests; interruptRateCan2 alone does not (owner 2026-10-28).
      # Stale pandaStates, a permanent fault or an ACC fault lock switching until disengage.
      faults = {str(f) for f in p.faults}
      self.panda_fault = bool(faults - BENIGN_PANDA_FAULTS) or (p.faultStatus != 'none' and not faults)
      if enabled and (not sm.all_checks(['pandaStates']) or p.faultStatus == 'faultPerm' or CS.accFaulted):
        self.locked, self.reason = True, 'fault'
    elif self.seen_abi:
      # Losing/resetting the owner ABI must never re-enable the legacy SP path.
      self.owner, self.locked, self.reason = 'stock', enabled, 'ownerUnavailable'

    try:
      desired = desired_owner(ctx, CS)
      if desired is not None and desired not in ('stock', 'sp'):
        raise ValueError('invalid long owner')
    except Exception:
      self.locked, self.reason, desired = enabled, 'policyFault', None
    if self.available and enabled and not self.locked:
      if desired != self.desired:
        self.desired, self.confirm_since = desired, now
      if (desired is not None and desired != self.owner and self.request == 255 and not self.panda_fault and
          now - self.confirm_since >= 1. and now >= self.cooldown_until and not self.locked):
        oem = sm['carStateSP'].teslaLongControl
        accel = ctx.LoC.last_output_accel if self.owner == 'sp' else CS.aEgo
        leads = (sm['radarState'].leadOne, sm['radarState'].leadTwo)
        reason = ''
        if not ctx.CP.openpilotLongitudinalControl:
          reason = 'notCapable'
        elif not CS.cruiseState.enabled:
          reason = 'cruiseInactive'
        elif CS.gasPressed:
          reason = 'gas'
        elif CS.brakePressed:
          reason = 'brake'
        elif CS.standstill:
          reason = 'standstill'
        elif CS.stockAeb:
          reason = 'stockAeb'
        elif not math.isfinite(CS.aEgo) or abs(CS.aEgo) >= .35:
          reason = 'acceleration'
        elif not sm.all_checks(['carState', 'carStateSP', 'radarState', 'longitudinalPlan']):
          reason = 'inputs'
        elif any(l.present and l.vRel < 0 and l.dRel / -l.vRel < 3. for l in leads):
          reason = 'leadTtc'
        elif oem.monoTime == 0 or not 0 <= now - oem.monoTime / 1e9 <= .06:
          reason = 'oemStale'
        elif oem.accState not in (3, 4):
          reason = 'oemInactive'
        elif (not all(math.isfinite(a) for a in (accel, oem.accelMin, oem.accelMax)) or
              oem.accelMin > oem.accelMax or oem.accelMax >= 5.44 or
              abs(oem.accelMin - accel) > .200001 or abs(oem.accelMax - max(accel, 0.)) > .200001):
          reason = 'accelGap'
        if reason:
          self.refuse(reason, now)
        else:
          self.request, self.request_since = int(desired == 'sp'), now
          self.request_refusal, self.request_seen = p.longOwnerRefusal, False
    if self.locked or not enabled:
      self.request = 255
    CC_SP.longOwner = self.owner
    CC_SP.longOwnerAvailable = self.available or self.seen_abi
    CC_SP.longOwnerRequest = self.request
    CC_SP.longOwnerLocked = self.locked
    CC_SP.longOwnerRefusal = self.reason
