"""Build host subscriptions without weakening required base-message checks.

Called once at process startup. Only inputs added solely for active plugins are
optional; a plugin also declaring a base input cannot exempt it from validation.
Existing caller ignores, poll service and frequency retain their original meaning.
"""


def submaster(base_services: list[str], **kwargs):
  from openpilot.cereal import messaging
  from openpilot.tesla import plugins

  services = list(dict.fromkeys(base_services))
  extra = [s for s in plugins.services() if s not in services]
  for key in ('ignore_alive', 'ignore_avg_freq', 'ignore_valid'):
    kwargs[key] = list(dict.fromkeys([*(kwargs.get(key) or []), *extra]))
  return messaging.SubMaster(services + extra, **kwargs)
