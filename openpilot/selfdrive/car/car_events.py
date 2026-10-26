from openpilot.cereal import log
from opendbc.car.structs import car
from opendbc.car import DT_CTRL, structs
from opendbc.car.car_helpers import interfaces
from opendbc.car.interfaces import MAX_CTRL_SPEED

from openpilot.selfdrive.selfdrived.events import Events
from openpilot.selfdrive.controls.lib.long_owner import sp_longitudinal

ButtonType = structs.CarState.ButtonEvent.Type
GearShifter = structs.CarState.GearShifter
EventName = log.OnroadEvent.EventName


class CarEvents:
  def __init__(self, CP: structs.CarParams):
    self.CP = CP

    self.steering_unpressed = 0
    self.no_steer_warning = False
    self.silent_steer_warning = True

  def update(self, CS: car.CarState, CS_prev: car.CarState, CC: car.CarControl, CC_SP):
    if self.CP.brand == 'mock':
      return Events()

    events = self.create_common_events(CS, CS_prev,
                                       sp_longitudinal(self.CP, CC_SP))

    return events

  def create_common_events(self, CS: structs.CarState, CS_prev: car.CarState, op_long):
    events = Events()

    CI = interfaces[self.CP.carFingerprint]
    pcm_enable = self.CP.pcmCruise or (self.CP.brand == 'tesla' and not op_long)
    allow_button_cancel = True

    if CS.doorOpen:
      events.add(EventName.doorOpen)
    if CS.seatbeltUnlatched:
      events.add(EventName.seatbeltNotLatched)
    if CS.gearShifter != GearShifter.drive and CS.gearShifter not in CI.DRIVABLE_GEARS:
      events.add(EventName.wrongGear)
    if CS.gearShifter == GearShifter.reverse:
      events.add(EventName.reverseGear)
    if not CS.cruiseState.available:
      events.add(EventName.wrongCarMode)
    if CS.espDisabled:
      events.add(EventName.espDisabled)
    if CS.espActive:
      events.add(EventName.espActive)
    if CS.stockFcw:
      events.add(EventName.stockFcw)
    if CS.stockAeb:
      events.add(EventName.stockAeb)
    if CS.stockLkas:
      events.add(EventName.stockLkas)
    if CS.vEgo > MAX_CTRL_SPEED:
      events.add(EventName.speedTooHigh)
    if CS.cruiseState.nonAdaptive:
      events.add(EventName.wrongCruiseMode)
    if CS.brakeHoldActive and op_long:
      events.add(EventName.brakeHold)
    if CS.parkingBrake:
      events.add(EventName.parkBrake)
    if CS.accFaulted:
      events.add(EventName.accFaulted)
    if CS.carNotReady:
      events.add(EventName.carNotReady)
    if CS.steeringPressed:
      events.add(EventName.steerOverride)
    if CS.steeringDisengage and not CS_prev.steeringDisengage:
      events.add(EventName.steerDisengage)
    if CS.brakePressed and CS.standstill:
      events.add(EventName.preEnableStandstill)
    if CS.gasPressed:
      events.add(EventName.gasPressedOverride)
    if CS.vehicleSensorsInvalid:
      events.add(EventName.vehicleSensorsInvalid)
    if CS.invalidLkasSetting:
      events.add(EventName.invalidLkasSetting)
    if CS.lowSpeedAlert:
      events.add(EventName.belowSteerSpeed)
    if CS.buttonEnable:
      events.add(EventName.buttonEnable)

    # Handle cancel button presses
    for b in CS.buttonEvents:
      # Disable on rising and falling edge of cancel for both stock and OP long
      # TODO: only check the cancel button with openpilot longitudinal on all brands to match panda safety
      if b.type == ButtonType.cancel and (allow_button_cancel or not self.CP.pcmCruise):
        events.add(EventName.buttonCancel)

    # Handle permanent and temporary steering faults
    self.steering_unpressed = 0 if CS.steeringPressed else self.steering_unpressed + 1
    if CS.steerFaultTemporary:
      if CS.steeringPressed and (not CS_prev.steerFaultTemporary or self.no_steer_warning):
        self.no_steer_warning = True
      else:
        self.no_steer_warning = False

        # if the user overrode recently, show a less harsh alert
        if self.silent_steer_warning or CS.standstill or self.steering_unpressed < int(1.5 / DT_CTRL):
          self.silent_steer_warning = True
          events.add(EventName.steerTempUnavailableSilent)
        else:
          events.add(EventName.steerTempUnavailable)
    else:
      self.no_steer_warning = False
      self.silent_steer_warning = False
    if CS.steerFaultPermanent:
      events.add(EventName.steerUnavailable)

    # we engage when pcm is active (rising edge)
    # enabling can optionally be blocked by the car interface
    if pcm_enable:
      if CS.cruiseState.enabled and not CS_prev.cruiseState.enabled and not CS.blockPcmEnable:
        events.add(EventName.pcmEnable)
      elif not CS.cruiseState.enabled:
        events.add(EventName.pcmDisable)

    return events
