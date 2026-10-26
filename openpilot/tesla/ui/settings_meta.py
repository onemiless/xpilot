"""Settings rows (openpilot.tesla.plugin.Setting) for the upstream Tesla options, plus label helpers shared with the
Plugins page. Plugin settings live in each plugin's SETTINGS, not here."""
from openpilot.tesla.plugin import Setting

VEHICLE_SETTINGS = (
  Setting('TeslaCoopSteering', 'Cooperative Steering'),
  Setting('TeslaMadsScreenButton', 'MADS Screen Activation',
          choices=(('Off', 0), ('3-Finger', 1), ('4-Finger', 2), ('5-Finger', 3))),  # upstream MadsScreenButtonType
)


def setting_label(setting: Setting, value, metric: bool = True) -> str:
  """A unit "km/h|mph" is metric|imperial."""
  if setting.type == 'secret':
    return '******' if value else 'Not set'
  if setting.type == 'text':
    return value or 'Not set'
  if setting.range is not None:
    unit, divisor = setting.range[3].split('|')[0 if metric else -1], setting.range[4]
    number = f'{value / divisor:.1f}' if divisor != 1 else str(value)
    return number + ('%' if unit == '%' else ' ' + unit)
  return next((label for label, choice in setting.choices or () if choice == value), str(value))


def setting_choices(setting: Setting, metric: bool = True) -> list[tuple[str, int]]:
  if setting.range is not None:
    minimum, maximum, step = setting.range[:3]
    return [(setting_label(setting, value, metric), value) for value in range(minimum, maximum + 1, step)]
  return list(setting.choices or ())
