"""Settings rows (openpilot.tesla.plugin.Setting) for the upstream Tesla options, plus label helpers shared with the
Plugins page. Plugin settings live in each plugin's SETTINGS, not here."""
from openpilot.tesla.plugin import Setting
from openpilot.system.ui.lib.multilang import tr, tr_noop

VEHICLE_SETTINGS = (
  Setting('TeslaCoopSteering', tr_noop('Cooperative Steering')),
  Setting('TeslaMadsScreenButton', tr_noop('MADS Screen Activation'),
          choices=((tr_noop('Off'), 0), (tr_noop('3-Finger'), 1), (tr_noop('4-Finger'), 2), (tr_noop('5-Finger'), 3))),  # upstream MadsScreenButtonType
)


# sunnypilot features that stay in the host (not role plugins), as on the SP settings pages. Keys, ranges and choice values follow
# selfdrive/ui/sunnypilot/layouts/settings; the SP code that reads them is unchanged. MADS lives in sp_engagement and the live delay and
# lane turn rows in sp_models (their plugin.json settings); OSM downloads, ICBM and Quickboot (it also writes a prebuilt file) are not here.
SP_SETTINGS = (
  (tr_noop("Lane change"), tr_noop("Blinker lane change timing and blocks."), (
    Setting('AutoLaneChangeTimer', tr_noop('Auto lane change by blinker'),
            choices=((tr_noop('Off'), -1), (tr_noop('Nudge'), 0), (tr_noop('Nudgeless'), 1), ('0.5 s', 2), ('1 s', 3), ('2 s', 4), ('3 s', 5))),
    Setting('AutoLaneChangeBsmDelay', tr_noop('Auto lane change: delay with blind spot')),
    Setting('RoadEdgeLaneChangeEnabled', tr_noop('Block lane change: road edge detection')),
  )),
  (tr_noop("Steering"), tr_noop("Lateral control around the turn signal."), (
    Setting('BlinkerPauseLateralControl', tr_noop('Pause lateral control with blinker')),
    Setting('BlinkerMinLateralControlSpeed', tr_noop('Minimum speed to pause lateral control'), (0, 255, 5, 'km/h|mph', 1)),
    Setting('BlinkerLateralReengageDelay', tr_noop('Post-blinker delay'), (0, 10, 1, 's', 1)),
  )),
  (tr_noop("Cruise"), tr_noop("Smart cruise control and set speed steps."), (
    Setting('SmartCruiseControlVision', tr_noop('Smart cruise control: vision')),
    Setting('SmartCruiseControlMap', tr_noop('Smart cruise control: map')),
    Setting('CustomAccIncrementsEnabled', tr_noop('Custom ACC speed increments')),
    Setting('CustomAccShortPressIncrement', tr_noop('Short press increment'), (1, 10, 1, 'km/h|mph', 1)),
    Setting('CustomAccLongPressIncrement', tr_noop('Long press increment'), choices=(('1', 1), ('5', 5), ('10', 10))),
  )),
  (tr_noop("Speed limit"), tr_noop("Speed limit source, warning and offset."), (
    Setting('SpeedLimitMode', tr_noop('Speed limit'),
            choices=((tr_noop('Off'), 0), (tr_noop('Info'), 1), (tr_noop('Warning'), 2), (tr_noop('Assist'), 3))),
    Setting('SpeedLimitPolicy', tr_noop('Speed limit source'),
            choices=((tr_noop('Car only'), 0), (tr_noop('Map only'), 1), (tr_noop('Car first'), 2), (tr_noop('Map first'), 3), (tr_noop('Combined'), 4))),
    Setting('SpeedLimitOffsetType', tr_noop('Speed limit offset'), choices=((tr_noop('None'), 0), (tr_noop('Fixed'), 1), ('%', 2))),
    Setting('SpeedLimitValueOffset', tr_noop('Offset value (speed or %)'), (-30, 30, 1, '', 1)),
  )),
  (tr_noop("Visuals"), tr_noop("Driving screen overlays and alerts."), (
    Setting('BlindSpot', tr_noop('Show blind spot warnings')),
    Setting('ShowTurnSignals', tr_noop('Display turn signals')),
    Setting('TorqueBar', tr_noop('Steering arc')),
    Setting('RocketFuel', tr_noop('Real-time acceleration bar')),
    Setting('StandstillTimer', tr_noop('Standstill timer')),
    Setting('RoadNameToggle', tr_noop('Display road name')),
    Setting('TrueVEgoUI', tr_noop('Speedometer: always display true speed')),
    Setting('HideVEgoUI', tr_noop('Speedometer: hide from onroad screen')),
    Setting('RainbowMode', tr_noop('Rainbow path')),
    Setting('GreenLightAlert', tr_noop('Green traffic light alert (beta)')),
    Setting('LeadDepartAlert', tr_noop('Lead departure alert (beta)')),
    Setting('ChevronInfo', tr_noop('Metrics below chevron'),
            choices=((tr_noop('Off'), 0), (tr_noop('Distance'), 1), (tr_noop('Speed'), 2), (tr_noop('Time'), 3), (tr_noop('All'), 4))),
    Setting('DevUIInfo', tr_noop('Developer UI'),
            choices=((tr_noop('Off'), 0), (tr_noop('Bottom'), 1), (tr_noop('Right'), 2), (tr_noop('Right & bottom'), 3))),
  )),
  (tr_noop("Display"), tr_noop("Screen brightness and timeouts."), (
    Setting('Brightness', tr_noop('Screen brightness'), choices=((tr_noop('Auto'), 0), *((f'{v} %', v) for v in range(10, 101, 10)))),
    Setting('OnroadScreenOffBrightness', tr_noop('Onroad brightness'),
            choices=((tr_noop('Auto'), 0), (tr_noop('Auto (dark)'), 1), (tr_noop('Screen off'), 2), *((f'{(v - 2) * 5} %', v) for v in range(3, 23)))),
    Setting('OnroadScreenOffTimer', tr_noop('Onroad brightness delay'),
            choices=tuple((f'{v} s' if v < 60 else f'{v // 60} min', v) for v in (3, 5, 7, 10, 15, 30, *range(60, 601, 60)))),
    Setting('InteractivityTimeout', tr_noop('Interactivity timeout (0 = default)'), (0, 120, 10, 's', 1)),
  )),
  (tr_noop("Device"), tr_noop("Power, sounds, updates and engagement."), (
    Setting('MaxTimeOffroad', tr_noop('Max time offroad'),
            choices=((tr_noop('Always on'), 0), *((f'{v} min' if v < 60 else f'{v // 60} h', v) for v in (5, 10, 15, 30, 60, 120, 180, 300, 600, 1440, 1800)))),
    Setting('DeviceBootMode', tr_noop('Wake up behavior'), choices=((tr_noop('Default'), 0), (tr_noop('Offroad'), 1))),
    Setting('QuietMode', tr_noop('Quiet mode')),
    Setting('DisengageOnAccelerator', tr_noop('Disengage on accelerator pedal')),
    Setting('DisableUpdates', tr_noop('Disable updates')),
  )),
)


def stored_value(setting: Setting, raw):
  """Range rows step whole numbers; a FLOAT param holds the real number (LagdToggleDelay 0.2 s at divisor 100 steps as 20)."""
  if setting.range is not None and isinstance(raw, float):
    return round(raw * setting.range[4])
  return raw


def to_param(setting: Setting, raw, value):
  """value in the row's units -> what Params.put takes for the key's type (raw is the stored value): FLOAT back from range units,
  and an INT key keeps an int for a toggle (Params.put has no bool -> INT cast)."""
  if setting.range is not None and isinstance(raw, float):
    return value / setting.range[4]
  if isinstance(value, bool) and type(raw) is int:
    return int(value)
  return value


def setting_label(setting: Setting, value, metric: bool = True) -> str:
  """A unit "km/h|mph" is metric|imperial."""
  if setting.type == 'secret':
    return '******' if value else tr('Not set')
  if setting.type == 'text':
    return value or tr('Not set')
  if setting.range is not None:
    unit, divisor = setting.range[3].split('|')[0 if metric else -1], setting.range[4]
    number = f'{value / divisor:.{len(str(divisor)) - 1}f}' if divisor != 1 else str(value)
    return number + ('%' if unit == '%' else ' ' + unit)
  return next((label for label, choice in setting.choices or () if choice == value), str(value))


def setting_choices(setting: Setting, metric: bool = True) -> list[tuple[str, int]]:
  if setting.range is not None:
    minimum, maximum, step = setting.range[:3]
    return [(setting_label(setting, value, metric), value) for value in range(minimum, maximum + 1, step)]
  return list(setting.choices or ())
