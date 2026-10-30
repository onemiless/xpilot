"""xpilot plugin interface. Rules for writing a plugin: XPILOT.md at the repo root.

One feature = one package openpilot/tesla/plugins/<id>/, found automatically:
  plugin.json  manifest: id, category, priority, claims, enable switch, settings, params (openpilot/tesla/manifest.py)
  plugin.py    the Plugin subclass named by the manifest's "entry"
  ui.py        optional: Tile, HudOverlay (onroad HUD) and ScreenOverlay subclasses, found by the UI
SP stays the main stack: SP files only call these hooks. Every hook has a do-nothing default.
The host (plugins/__init__.py) builds only enabled plugins that win their claims, refuses unclaimed use,
and drops a plugin at its first exception.
"""
from dataclasses import dataclass

from openpilot.tesla.manifest import Setting  # noqa: F401  (re-exported for settings code)


@dataclass(frozen=True)
class PluginAlert:
  """One alert from alert_source(). severity: "info" (no sound), "warning" (prompt) or "critical" (warning sound).
  Plugin alerts never disengage and rank below SP's own critical alerts; text is cut to tesla/alerts.py limits."""
  severity: str
  title: str
  body: str = ""


class Plugin:
  # Filled from plugin.json by the host; do not set them in code.
  NAME = ""
  TITLE = ""
  DESCRIPTION = ""
  CATEGORY = "ui"
  PRIORITY = 100
  ENABLE_PARAM: str | None = None
  CLAIMS: tuple[str, ...] = ()
  SETTINGS: tuple = ()
  MANIFEST = None
  SERVICES: tuple[str, ...] = ()  # extra services plannerd, selfdrived and controlsd subscribe for this plugin's hooks

  @classmethod
  def network_vpn(cls):
    """Bounded host VPN client for an enabled role:network.vpn owner."""
    if 'role:network.vpn' not in cls.CLAIMS:
      raise PermissionError('network_vpn requires role:network.vpn')
    from openpilot.tesla.network import NetworkVPN
    return NetworkVPN(cls.NAME)

  def __init__(self, CP, params):
    """card: once per car, after fingerprinting. Read Params here or in service_params()."""
    self.CP = CP

  # ---- card ----
  def setup(self, CP_SP) -> None:
    """Car init, before CarParamsSP is published: set CP_SP flags or safetyParam bits for a panda plugin."""

  def observe_can(self, can_list, now_ns: int) -> list:
    """Every CAN batch, read-only. Returns CanData to send at once (rare; prefer control_sends)."""
    return []

  def publish_state(self, CS_SP) -> None:
    """Every carState cycle: fill this plugin's fields of the outgoing carStateSP (capnp builder)."""

  def control_sends(self, CS, CC, now_ns: int) -> list:
    """Every control cycle: extra CanData to send. Each address needs a panda safety plugin."""
    return []

  def service_params(self, params) -> None:
    """~10 Hz in card's params thread: reload live settings."""

  # ---- manager ----
  @classmethod
  def processes(cls) -> list:
    """Extra manager processes (ManagerProcess instances). Import manager code inside, not at module level."""
    return []

  @classmethod
  def role_processes(cls, role, procs) -> list:
    """manager: select all, some or none of SP's procs (name -> ManagerProcess) for a claimed role. Return only
    supplied objects; they run with their original conditions only while this plugin owns role. Extra processes
    still come from processes(). Raising or returning a foreign object records a fault and rejects this selection."""
    return []

  # ---- selfdrived (claim "alert") ----
  @classmethod
  def alert_source(cls, CP, params):
    """None, or an object with update(sm) -> list[PluginAlert], called every selfdrived cycle (100 Hz)."""
    return None

  # ---- modeld (claim "role:lane_change.policy", exclusive) ----
  @classmethod
  def lane_change_policy(cls, params):
    """None, or an object with allow(carstate, direction, default) -> bool: replaces only SP's "start without
    steering nudge" decision (default) during preLaneChange. Blinker, minimum speed, blind spot and road edge stay
    hard requirements in DesireHelper."""
    return None

  # ---- controlsd (claim "lat.correction") ----
  @classmethod
  def lat_correction(cls, CP, params):
    """None, or an object with update(sm, v_ego, curvature) -> curvature or None, called every control cycle while
    lateral is active. Corrections chain in plugin order; the host caps the total change at LAT_CORRECTION_ACCEL
    (m/s^2 equivalent) and SP's clip_curvature (ISO jerk/accel) applies afterwards."""
    return None

  # ---- controlsd (claim "role:lateral.controller", exclusive) ----
  @classmethod
  def lat_controller(cls, CP, CP_SP, CI, params):
    """None, or a LatControl-compatible object: update(active, CS, VM, params, steer_limited_by_safety,
    desired_curvature, calibrated_pose, curvature_limited, lat_delay) -> (steer, steering_angle_deg, lac_log) and
    reset(). Its output is not limited against SP's; the car port's angle rate limits and panda still apply. SP's
    controller keeps running alongside and takes over for good if this one raises or returns a non-finite output."""
    return None

  @classmethod
  def long_owner(cls, CP, CP_SP, params):
    """None, or an object with desired(ctx, CS) -> None | "stock" | "sp". Exclusive role:long.owner;
    proposes ownership only. Host vetoes and Panda health remain authoritative; a fault disables this hook for the drive."""
    return None

  # ---- plannerd (claim "role:long.planner", exclusive) ----
  @classmethod
  def long_planner(cls, CP, CP_SP, params):
    """None, or an object with update(sm, sp) -> plan or None, called every plan cycle after SP's planner. sp has
    SP's speeds/accels/jerks (CONTROL_N points), a_target and should_stop. A plan (same fields) replaces SP's,
    including lead following; a_target is clipped to ACCEL_MIN/MAX. SP's MPC still runs and still decides FCW.
    None, wrong lengths or non-finite values keep SP's plan; raising drops the planner."""
    return None

  # ---- plannerd ----
  @classmethod
  def long_target(cls, CP, params):
    """None, or an object with update(sm, v_ego, v_cruise) -> target speed m/s or None, called every plan cycle.
    Lower than v_cruise: tightens, like SP's own limiters. Higher: replaces only the driver set speed, and only
    while engaged without gas/brake/override, capped at V_CRUISE_MAX. MPC (leads), model and SP limiters still apply."""
    return None

  @classmethod
  def plan_filter(cls, CP, params):
    """None, or an object with publisher(pm, sm) returning a pm-like sink that may only tighten the plan."""
    return None

  # ---- selfdrived (claims "role:engagement.state" / "role:safety.fallback", exclusive; openpilot/tesla/core_host.py) ----
  @classmethod
  def engagement(cls, selfdrive):
    """None, or an object with state_machine (SP StateMachine interface: update(events) -> (enabled, active), state,
    current_alert_types, soft_disable_timer) and mads (ModularAssistiveDrivingSystem interface: update(CS), enabled,
    active, enabled_toggle, state_machine.state, read_params(); None disables MADS). Plugins may retain/read
    selfdrive, but change engagement only through these outputs, which the host clamps. Replaces SP's whole
    engagement. The host clamps the outputs: floor disables win, an engage needs SP's driver ENABLE event (MADS: the
    LKAS button), active implies enabled, soft disable ends within 4 s, ET.PERMANENT always shows. Raising or an
    invalid output latches both axes off for the rest of the drive."""
    return None

  @classmethod
  def safety_fallback(cls, ctx):
    """Return an object with update(ctx, CS, names, names_sp) -> (names, names_sp), after SP's update_events.
    ctx is built once: SimpleNamespace(sm, CP, CP_SP, params=view(params)); it exposes no selfdrive or Events.
    Return lists of known event names to keep/add. The host rebuilds the same Events objects, preserving counters,
    then restores every floor (pedals, cancel, FCW, stock AEB, panda/CAN faults, core-data liveness, DM).
    Missing owner/output, raising or invalid lists latch both axes off for the rest of the drive; no fallback."""
    return None

  @classmethod
  def radar_interface(cls, CP, CP_SP, params):
    """None, or a RadarInterfaceBase (update(can_list) -> RadarData or None) replacing the port's radar in card; needs
    the source:radar claim. The port's radar keeps running alongside and takes over for good if this one raises.
    A radar that sends CAN also needs a panda safety plugin."""
    return None
