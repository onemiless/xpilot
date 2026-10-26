"""Plugin manifests (plugin.json), categories, claims and conflict resolution. Stdlib only: the UI, gen_params.py and
the checks read manifests without importing plugin code. Format and rules: XPILOT.md "插件清单".

plugin.json:
  id           directory name                     name, description, author, version   shown on the Plugins page
  api          API_VERSION this plugin targets    category  one of CATEGORIES
  use          onroad | offroad | both (required; metadata, not hook gating)
  entry        "module:Class" inside the plugin package (e.g. "plugin:Example")
  priority     lower wins an exclusive claim (then id); also orders plugins in a category. Default 100
  enable_param BOOL param that switches the plugin; null = always on
  claims       resources the plugin uses (see CLAIM_PATTERNS); the host refuses anything unclaimed
  settings     [{"key", "label", "range": [min, max, step, unit, divisor]} | {"key", "label", "choices": [[label, value]]}
                | {"key", "label"} (toggle)]
  params       {"Key": {"type", "default", "flags", "note"}} rendered into common/plugin_params.inc by gen_params.py
  requires     optional hooks/msg names, rule_abi and native platform required from HOST_CAPS
  rules        list of template_clone.v1 rules (docs/design/xpilot-panda-rules.md)
  platform     null (pure Python) or {"machine": "aarch64", "python": "cpython-312"} for plugins with prebuilt binaries;
               such a plugin installs and loads only where host_platform() matches
"""
import ast
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import platform as _platform
import re
import subprocess
import sys

API_VERSION = 1
PLUGINS_DIR = Path(__file__).resolve().parent / "plugins"  # built-in plugins (in git)
INSTALL_DIR = Path(os.environ.get("XPILOT_PLUGINS_DIR", "/data/xpilot_plugins"))  # downloaded plugins (store.py)
_COMMON = Path(__file__).resolve().parents[1] / "common"

# id -> title, in pipeline order. The first 12 sit on the control path.
CATEGORIES = {
  "perception_model": "Perception Models",
  "object_fusion": "Lead & Object Fusion",
  "vehicle_params": "Calibration & Vehicle Params",
  "engagement": "Engagement",
  "driver_monitoring": "Driver Monitoring",
  "lateral_planning": "Lateral Planning",
  "longitudinal_planning": "Longitudinal Planning",
  "lane_change": "Lane Change",
  "navigation": "Navigate on xpilot",
  "controller": "Controllers",
  "actuation": "Actuation & Car Messages",
  "safety_fallback": "Safety Fallback & Autopilot Handoff",
  "data_source": "Data Sources & External Compute",
  "alerts": "Alerts",
  "body_comfort": "Body & Comfort",
  "ui": "Tiles",
  "overlays": "Driving Overlays",
  "tools": "Tools & Diagnostics",
}

# Exclusive unless shared() (SHARED_CLAIMS and every msg:<kind>): only one active plugin may hold it.
CLAIM_PATTERNS = (
  r"can\.tx:0x[0-9A-Fa-f]{1,8}:[0-7]",  # send this CAN address on this bus (needs a runtime panda rule)
  r"process:[a-z][a-z0-9_]*",           # run this manager process
  r"source:[a-z][a-z0-9_.]*",           # be the provider of this data, e.g. source:radar, source:lanes
  r"role:[a-z][a-z0-9_.]*",             # own a replaceable stage, e.g. role:lane_change.policy
  r"long\.target",                      # long_target hook (shared, merged by the host)
  r"plan\.filter",                      # plan_filter hook (shared, chained in priority order)
  r"event\.filter",                     # event_filter hook (shared, allowlisted deletions only)
  r"alert",                             # alert_source hook (shared, first alert per severity in priority order)
  r"lat\.correction",                   # lat_correction hook (shared, chained, total change capped by the host)
  r"power",                            # reboot/shutdown through host_api (shared, parked only)
  r"net\.wifi",                         # Wi-Fi changes through host_api (exclusive, parked only)
  r"sound",                            # custom sound through host_api (shared, parked only)
  r"display",                          # display ownership (exclusive)
  r"camera:(road|wide|driver)",         # declarative camera use (shared)
  r"msg:[a-z][a-z0-9_.]*",              # publish pluginMsg of this kind (shared; source tells publishers apart)
)
SHARED_CLAIMS = {"long.target", "plan.filter", "event.filter", "alert", "lat.correction", "power", "sound",
                 "camera:road", "camera:wide", "camera:driver"}


def shared(claim: str) -> bool:
  return claim in SHARED_CLAIMS or claim.startswith("msg:")


_CLAIM_RE = re.compile("|".join(f"(?:{p})" for p in CLAIM_PATTERNS))


@dataclass(frozen=True)
class Setting:
  """One settings row: text/secret, range=(min, max, step, unit, divisor), choices, or a toggle."""
  key: str
  label: str
  range: tuple[int, int, int, str, int] | None = None
  choices: tuple[tuple[str, int], ...] | None = None
  type: str | None = None
  max_len: int = 64

  def input_error(self, value: str) -> str:
    if any(not 32 <= ord(c) <= 126 for c in value):
      return "Printable ASCII only"
    if len(value) > self.max_len:
      return f"Maximum {self.max_len} characters"
    return ""


@dataclass(frozen=True)
class Manifest:
  id: str
  name: str
  version: str
  api: int
  category: str
  use: str
  entry: str
  description: str = ""
  author: str = ""
  priority: int = 100
  enable_param: str | None = None
  claims: tuple[str, ...] = ()
  settings: tuple[Setting, ...] = ()
  params: dict = field(default_factory=dict)
  platform: dict | None = None
  rules: list = field(default_factory=list)
  requires: dict = field(default_factory=dict)


def _setting(d: dict, params: dict) -> Setting:
  kind = d.get("type")
  if "type" in d and kind not in ("text", "secret"):
    raise ValueError("bad setting type")
  length = d.get("max_len", 64)
  if type(length) is not int or not 1 <= length <= 256 or ("max_len" in d and kind is None):
    raise ValueError("bad setting max_len")
  if kind is not None:
    if "range" in d or "choices" in d:
      raise ValueError("text/secret cannot have range/choices")
    spec = params.get(d["key"])
    if not isinstance(spec, dict) or spec.get("type") != "STRING":
      raise ValueError("text/secret requires a STRING param")
  rng = d.get("range")
  return Setting(d["key"], d["label"], tuple(rng) if rng else None,
                 tuple((str(label), int(v)) for label, v in d["choices"]) if d.get("choices") else None, kind, length)


def parse(path: Path) -> Manifest:
  """Read and validate one plugin.json; raises ValueError with the reason."""
  try:
    d = json.loads(path.read_text())
  except (OSError, ValueError) as e:
    raise ValueError(f"unreadable: {e}") from None
  if not isinstance(d, dict):
    raise ValueError("not an object")
  if d.get("id") != path.parent.name:
    raise ValueError(f"id {d.get('id')!r} != directory {path.parent.name!r}")
  if d.get("api") != API_VERSION:
    raise ValueError(f"api {d.get('api')!r}, host supports {API_VERSION}")
  if d.get("category") not in CATEGORIES:
    raise ValueError(f"unknown category {d.get('category')!r}")
  if not isinstance(d.get("entry"), str) or not re.fullmatch(r"[a-z_][a-z0-9_]*:[A-Za-z_]\w*", d["entry"]):
    raise ValueError(f"bad entry {d.get('entry')!r}")
  if d.get("use") not in ("onroad", "offroad", "both"):
    raise ValueError(f"bad use {d.get('use')!r}: expected onroad/offroad/both")
  plat = d.get("platform")
  if plat is not None and not (isinstance(plat, dict) and isinstance(plat.get("machine"), str) and isinstance(plat.get("python"), str)):
    raise ValueError(f"bad platform {plat!r}")
  claims = d.get("claims", [])
  if not isinstance(claims, list) or not all(isinstance(c, str) and _CLAIM_RE.fullmatch(c) for c in claims):
    raise ValueError(f"bad claims {claims!r}")
  requires = d.get("requires", {})
  if "role:long.owner" in claims and (not isinstance(requires, dict) or not isinstance(requires.get("hooks"), list)
                                     or "long_owner" not in requires["hooks"]):
    raise ValueError("role:long.owner requires hooks: [long_owner]")
  if "safety" in d:
    raise ValueError("safety is no longer supported; publish a new plugin version with rules")
  from openpilot.tesla.panda_rules import encode_rules
  rules = d.get("rules", [])
  encode_rules(rules)
  if can_tx(claims) != {(r["tx_address"], r["tx_bus"]) for r in rules}:
    raise ValueError("can.tx must match rule TX addresses")

  try:
    return Manifest(id=d["id"], name=str(d.get("name") or d["id"]), version=str(d.get("version", "0")), api=d["api"],
                    category=d["category"], use=d["use"], entry=d["entry"], description=str(d.get("description", "")),
                    author=str(d.get("author", "")), priority=int(d.get("priority", 100)), enable_param=d.get("enable_param"),
                    claims=tuple(claims), settings=tuple(_setting(s, d.get("params", {})) for s in d.get("settings", [])), params=dict(d.get("params", {})),
                    platform=plat, rules=rules, requires=d.get("requires", {}))
  except (KeyError, TypeError, ValueError) as e:
    raise ValueError(f"bad field: {e!r}") from None


def load_all(root: Path = PLUGINS_DIR) -> tuple[list[Manifest], dict[str, str]]:
  """Valid manifests in (category, priority, id) order, and {directory: error} for the rejected ones."""
  found, errors = [], {}
  for path in sorted(root.glob("*/plugin.json")):
    try:
      found.append(parse(path))
    except ValueError as e:
      errors[path.parent.name] = str(e)
  order = list(CATEGORIES)
  found.sort(key=lambda m: (order.index(m.category), m.priority, m.id))
  return found, errors


def resolve(items, enabled: set[str]) -> tuple[set[str], dict[str, str]]:
  """items: (id, priority, claims). Returns (active ids, {losing id: winning id}) among the enabled ones.
  Each exclusive claim goes to the lowest (priority, id); a plugin that loses any claim is not active."""
  active, conflicts, owner = set(), {}, {}
  for name, _, claims in sorted((i for i in items if i[0] in enabled), key=lambda i: (i[1], i[0])):
    taken = [owner[c] for c in claims if not shared(c) and c in owner]
    if taken:
      conflicts[name] = taken[0]
      continue
    active.add(name)
    owner.update({c: name for c in claims if not shared(c)})
  return active, conflicts


def conflicting(name: str, items) -> list[str]:
  """Ids sharing an exclusive claim with name: switch these off when name is switched on."""
  mine = {c for i, _, claims in items if i == name for c in claims if not shared(c)}
  return [i for i, _, claims in items if i != name and mine & set(claims)]


def can_tx(claims) -> set[tuple[int, int]]:
  """(address, bus) pairs from can.tx claims."""
  return {(int(a, 16), int(b)) for c in claims if c.startswith("can.tx:") for a, b in [c[7:].split(":")]}


def compiled_param_keys() -> set[str]:
  """Keys the compiled Params knows: core params_keys.h plus built-in plugins (plugin_params.inc)."""
  text = "".join((_COMMON / f).read_text() for f in ("params_keys.h", "plugin_params.inc") if (_COMMON / f).exists())
  return set(re.findall(r'\{"(\w+)", \{', text))


def host_platform() -> dict[str, str]:
  """What prebuilt binaries must match: CPU architecture and Python ABI tag."""
  return {"machine": _platform.machine(), "python": sys.implementation.cache_tag}


def platform_ok(plat: dict | None) -> bool:
  host = host_platform()
  return plat is None or all(plat.get(k) == v for k, v in host.items())


RULE_ABI = 1
HOST_CAPS = {
  "hooks": ("__init__", "setup", "observe_can", "publish_state", "control_sends", "service_params", "processes",
            "role_processes", "alert_source", "lane_change_policy", "lat_correction", "lat_controller", "long_owner", "long_planner",
            "long_target", "plan_filter", "event_filter", "engagement", "safety_fallback", "radar_interface"),
  "msg": ("*",),  # plugin_msgd authenticates/limits publishers; every declared kind, no payload schema.
  "host_api": ("parked", "reboot", "shutdown", "wifi_connect", "wifi_forget", "hotspot", "play_sound", "data_dir"),
  "rule_abi": RULE_ABI,
  "native": host_platform(),
}
UPDATE_COMPAT_PARAM = "XpilotUpdateIncompatiblePlugins"
UPDATE_SPECS = {UPDATE_COMPAT_PARAM: {"type": "JSON"}}


def compatible(manifest, caps) -> tuple[bool, str]:
  """One capability gate for install, enable, discovery and staged updates."""
  if manifest.api != API_VERSION:
    return False, f"api {manifest.api!r}, host supports {API_VERSION}"
  if manifest.platform is not None and not all(manifest.platform.get(k) == v for k, v in caps["native"].items()):
    return False, f"built for {manifest.platform}, this device is {caps['native']}"
  requires = manifest.requires
  if not isinstance(requires, dict) or set(requires) - {"hooks", "msg", "host_api", "rule_abi", "native"}:
    return False, "bad requires: expected hooks/msg/host_api/rule_abi/native object"
  for key in ("hooks", "msg", "host_api"):
    names = requires.get(key, [])
    if not isinstance(names, list) or not all(isinstance(n, str) and re.fullmatch(r"[a-z_][a-z0-9_.]*", n) for n in names):
      return False, f"bad requires.{key}: expected names"
    for name in names:
      if name not in caps[key] and not (key == "msg" and "*" in caps[key]):
        return False, f"requires {key} {name!r}"
  if "rule_abi" in requires:
    abi = requires["rule_abi"]
    if type(abi) is not int or abi < 1 or abi != caps["rule_abi"]:
      return False, f"requires rule_abi {abi!r}, host supports {caps['rule_abi']!r}"
  if "native" in requires and requires["native"] != caps["native"]:
    return False, f"requires native {requires['native']!r}, host supports {caps['native']}"
  return True, ""


def check_staged(root, params=None) -> None:
  """Warn using the staged host's parser/caps, without importing plugins or blocking activation."""
  from openpilot.tesla.plugin_params import ParamsView
  code = """
import json, runpy, sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]).parents[2]))
m = runpy.run_path(sys.argv[1])
found, errors = m['load_all'](Path(sys.argv[2]))
for manifest in found:
  ok, reason = m['compatible'](manifest, m['HOST_CAPS'])
  if not ok:
    errors[manifest.id] = reason
print(json.dumps(errors))
"""
  try:
    result = subprocess.run([sys.executable, "-c", code, str(Path(root) / "openpilot/tesla/manifest.py"), str(INSTALL_DIR)],
                            capture_output=True, text=True, timeout=15, check=True)
    errors = json.loads(result.stdout)
  except (OSError, subprocess.SubprocessError, ValueError) as e:
    errors = {"precheck": f"staged plugin precheck failed: {e}"}
  ParamsView(params, UPDATE_SPECS).put(UPDATE_COMPAT_PARAM, errors)


MAX_LINES, MAX_ENTRY_LINES = 400, 150  # per .py file / the entry module, which only wires hooks to the plugin's own modules


def layout_problems(d: Path, entry_module: str = "plugin") -> list[str]:
  """Code layout rules (XPILOT.md 5.3) broken by the plugin in d: a .py file over MAX_LINES (tests/ excluded), an entry
  module over MAX_ENTRY_LINES, pyray imported outside ui.py (card and process code must not load the UI)."""
  out = []
  for f in sorted(d.rglob("*.py")):
    rel = f.relative_to(d)
    if rel.parts[0] == "tests":
      continue
    src = f.read_text(errors="replace")
    lines, limit = len(src.splitlines()), MAX_ENTRY_LINES if rel.as_posix() == f"{entry_module}.py" else MAX_LINES
    if lines > limit:
      out.append(f"{rel}: {lines} lines > {limit}")
    if rel.name != "ui.py" and re.search(r"^\s*(import|from)\s+pyray\b", src, re.M):
      out.append(f"{rel}: imports pyray outside ui.py")
  return out


def tile_problems(path: Path, use: str | None = None) -> list[str]:
  """Check tile declarations without importing device-only plugin UI code."""
  errors = []
  for node in ast.walk(ast.parse(path.read_text())):
    if not isinstance(node, ast.Call) or not (isinstance(node.func, ast.Name) and node.func.id == "TileSpec"
                                            or isinstance(node.func, ast.Attribute) and node.func.attr == "TileSpec"):
      continue
    show = next((kw.value for kw in node.keywords if kw.arg == "show"), None)
    if not isinstance(show, ast.Constant) or show.value not in ("onroad", "offroad", "always"):
      errors.append(f"{path.name}:{node.lineno}: explicit show required (onroad/offroad/always)")
    elif use is not None and use != "both" and show.value != use:
      errors.append(f"{path.name}:{node.lineno}: tile show {show.value!r} conflicts with use {use!r}")
  return errors
