#!/usr/bin/env python3
"""Plugin manifest, claims and conflict check (runs without a device).

Failure cases listed before implementation:
- M1 an invalid manifest (id != directory, unknown category, wrong api, bad claim, bad JSON) crashes discovery
     or loads anyway, instead of being skipped with an error while the other plugins load
- M2 discovery order is not deterministic (category order, then priority, then id)
- M3 two enabled plugins with the same exclusive claim both become active (two senders on one CAN address)
- M4 a conflict is won by load order instead of priority (then id)
- M5 a plugin uses something it did not claim and keeps running: sends an unclaimed CAN address/bus,
     returns a long target or plan filter without claiming it, or starts an unclaimed process
- M6 shared claims (long.target, plan.filter, msg:<kind>) are treated as exclusive, or msg:<kind> is refused
- M7 switching a plugin on leaves its conflicting plugins switched on
- M8 a built-in plugin's manifest is invalid, or its claims do not cover the hooks it overrides
- M9 an unknown claim kind is accepted
- M10 a disabled plugin still takes an exclusive claim from an enabled one
- M11 a plugin's code is not split by role (XPILOT.md 5.3): a file over MAX_LINES, a fat entry module, or pyray imported
     outside ui.py passes; or a built-in plugin breaks the rules
- M12 a new plugin directory appears under openpilot/tesla/plugins/ (only the SP core plugins stay built in;
      everything else goes to the plugin repository, github.com/onemiless/xpilot-plugins)
- M13 a built-in or store TileSpec omits an explicit valid show; the repository scan misses it (store_e2e included)
- M14 a missing/invalid use is accepted or discovery loses the rejection reason
- M15 a plugin tile show conflicts with its declared use
- M16 requires accepts malformed fields, unknown hooks/messages/native ABI, or an unavailable rule ABI
- M17 HOST_CAPS hooks drift from Plugin methods, or optional requires breaks API 1 plugins
"""
import json
from pathlib import Path
import sys
import tempfile
import types

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "opendbc_repo")]
try:
  import openpilot.common.params  # noqa: F401
except Exception:
  sys.modules["openpilot.common.params"] = types.SimpleNamespace(Params=object, UnknownKeyName=KeyError)

from openpilot.tesla import manifest as mf  # noqa: E402
from openpilot.common.swaglog import cloudlog  # noqa: E402
for _level in ("error", "exception", "warning", "info"):  # the check only needs results, not logs (host has no log sink)
  setattr(cloudlog, _level, lambda *a, **k: None)
from openpilot.tesla.plugin import Plugin  # noqa: E402

CORE_PLUGINS = {"sp_engagement", "sp_models", "sp_safety_fallback"}


def write(root: Path, d: str, **over) -> None:
  m = {"id": d, "name": d, "version": "1.0.0", "api": mf.API_VERSION, "category": "body_comfort", "entry": "plugin:X",
       "use": "both", "enable_param": f"{d}On", "claims": [], "priority": 100}
  m.update(over)
  (root / d).mkdir()
  (root / d / "plugin.json").write_text(json.dumps(m))


class FakeParams:
  def __init__(self, values=None):
    self.values = dict(values or {})

  def get_bool(self, key):
    return bool(self.values.get(key, False))


def tile_show_problems(path: Path) -> list[str]:
  """Shared source check, including plugins not importable on this host."""
  return ["M13 " + p for p in mf.tile_problems(path)]


def capability_checks() -> list[str]:
  from dataclasses import replace
  errors = []
  if not hasattr(mf, "compatible"):
    return ["M16 missing compatible", "M17 missing HOST_CAPS"]
  hooks = {name for name, value in vars(Plugin).items() if callable(value) or isinstance(value, classmethod)}
  if set(mf.HOST_CAPS["hooks"]) != hooks or mf.RULE_ABI is not None or mf.HOST_CAPS["rule_abi"] is not None:
    errors.append("M17 hook inventory or premature rule ABI")
  m = mf.load_all()[0][0]
  for requires, ok, reason in (({}, True, ""), ({"hooks": ["setup"]}, True, ""),
      ({"hooks": ["teleport"]}, False, "teleport"), ({"msg": ["demo.hint"]}, True, ""),
      ({"rule_abi": 1}, False, "rule_abi"), ({"native": mf.host_platform()}, True, ""),
      ({"native": {"machine": "sparc", "python": "cpython-27"}}, False, "native"),
      ([], False, "requires"), (None, False, "requires"), ({"hooks": "setup"}, False, "hooks"),
      ({"hooks": [1]}, False, "hooks"), ({"msg": ["bad kind"]}, False, "msg"),
      ({"msg": "demo.hint"}, False, "msg"), ({"rule_abi": True}, False, "rule_abi"),
      ({"rule_abi": "1"}, False, "rule_abi"), ({"native": {}}, False, "native"),
      ({"native": []}, False, "native"), ({"unknown": 1}, False, "requires")):
    result, why = mf.compatible(replace(m, requires=requires), mf.HOST_CAPS)
    if result != ok or reason not in why:
      errors.append(f"M16 requires={requires!r}: {result}, {why!r}")
  caps = {**mf.HOST_CAPS, "msg": ["known.hint"], "rule_abi": 1}
  if mf.compatible(replace(m, requires={"msg": ["demo.hint"]}), caps)[0] or not mf.compatible(replace(m, requires={"rule_abi": 1}), caps)[0]:
    errors.append("M16 advertised message/rule capabilities ignored")
  if m.api != 1 or not mf.compatible(m, mf.HOST_CAPS)[0]:
    errors.append("M17 legacy manifest without requires refused")
  return errors


def main() -> int:
  errors = capability_checks()
  from openpilot.tesla.ui.tiles import TileSpec
  try:
    TileSpec("missing", "Missing")
    errors.append("M13 runtime TileSpec accepted missing show")
  except TypeError:
    pass
  for path in sorted((ROOT / "openpilot/tesla/ui/slots").rglob("*.py")) + sorted(mf.PLUGINS_DIR.glob("*/ui.py")):
    errors += tile_show_problems(path)
  with tempfile.TemporaryDirectory() as tmp:
    path = Path(tmp) / "ui.py"
    for declaration, count in (('TileSpec("x", "X")', 1), ('TileSpec("x", "X", show="never")', 1),
                               ('TileSpec("x", "X", show="onroad")', 0)):
      path.write_text(declaration)
      if len(tile_show_problems(path)) != count:
        errors.append(f"M13 declaration check: {declaration}")
  with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    write(root, "good_b", priority=50)
    write(root, "good_a", priority=50)
    write(root, "early", category="engagement", priority=900)
    write(root, "wrong_id", id="other")
    write(root, "bad_cat", category="nope")
    write(root, "bad_api", api=mf.API_VERSION + 1)
    write(root, "bad_claim", claims=["can.tx:hello"])
    write(root, "unknown_kind", claims=["teleport:now"])
    (root / "bad_json").mkdir()
    (root / "bad_json" / "plugin.json").write_text("{nope")
    try:
      found, errs = mf.load_all(root)
    except Exception as e:
      errors.append(f"M1 load_all raised {e!r}")
      found, errs = [], {}
    ids = [m.id for m in found]
    for bad in ("wrong_id", "bad_cat", "bad_api", "bad_claim", "bad_json"):
      if bad in ids or bad not in errs:
        errors.append(f"M1 {bad} not rejected with an error")
    if "unknown_kind" in ids or "unknown_kind" not in errs:
      errors.append("M9 unknown claim kind accepted")
    if ids != ["early", "good_a", "good_b"]:
      errors.append(f"M2 order {ids}")

  with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    for i, use in enumerate((None, "", "never", [], {}, True, 1)):
      write(root, f"bad_use_{i}", use=use)
    write(root, "missing_use")
    path = root / "missing_use" / "plugin.json"
    data = json.loads(path.read_text())
    del data["use"]
    path.write_text(json.dumps(data))
    for use in ("onroad", "offroad", "both"):
      write(root, use, use=use)
    found, rejected = mf.load_all(root)
    if {m.id for m in found} != {"onroad", "offroad", "both"} or len(rejected) != 8 or any("use" not in e for e in rejected.values()):
      errors.append(f"M14 invalid use accepted/missing reason: {found}, {rejected}")
    path = root / "ui.py"
    for use in ("onroad", "offroad", "both"):
      for show in ("onroad", "offroad", "always"):
        path.write_text(f'TileSpec("x", "X", show={show!r})')
        expected = use != "both" and show != use
        if not hasattr(mf, "tile_problems") or bool(mf.tile_problems(path, use)) != expected:
          errors.append(f"M15 tile show={show} use={use}")

  X = "can.tx:0x3C2:1"
  items = [("b", 10, (X, "long.target")), ("a", 10, (X,)), ("c", 5, (X,)), ("d", 1, ("long.target", "plan.filter"))]
  active, conflicts = mf.resolve(items, {"a", "b", "c", "d"})
  if active != {"c", "d"}:
    errors.append(f"M3/M4/M6 active {active}")
  if conflicts.get("a") != "c" or conflicts.get("b") != "c":
    errors.append(f"M4 conflicts {conflicts}")
  active, _ = mf.resolve(items, {"a", "b"})
  if active != {"a"}:
    errors.append(f"M4 tie not broken by id: {active}")
  active, _ = mf.resolve(items, {"a", "d"})
  if active != {"a", "d"}:
    errors.append(f"M10 disabled plugin took a claim: {active}")
  if not mf._CLAIM_RE.fullmatch("msg:x.y") or mf.resolve([("e", 1, ("msg:x.y",)), ("f", 2, ("msg:x.y",))], {"e", "f"})[0] != {"e", "f"}:
    errors.append("M6 msg:<kind> refused or exclusive")
  if set(mf.conflicting("c", items)) != {"a", "b"} or mf.conflicting("d", items):
    errors.append(f"M7 conflicting('c') = {mf.conflicting('c', items)}")

  from openpilot.tesla import plugins
  faults = []
  plugins.record_fault = lambda name, where, exc: faults.append((name, where))

  class CP:
    brand = "tesla"

  def make(name, claims, send=None, target=False, proc=None):
    class P(Plugin):
      NAME, CLAIMS, ENABLE_PARAM, PRIORITY = name, tuple(claims), None, 100

      def control_sends(self, CS, CC, now_ns):
        return [send] if send else []

      @classmethod
      def long_target(cls, CP, params):
        return types.SimpleNamespace(update=lambda sm, v, c: 1.0) if target else None

      @classmethod
      def processes(cls):
        return [types.SimpleNamespace(name=proc, should_run=lambda s, p, c: True)] if proc else []
    return P

  plugins.PLUGINS = (make("ok", [X], send=(0x3C2, b"", 1)), make("wrong_bus", ["can.tx:0x3C3:1"], send=(0x3C3, b"", 0)),
                     make("unclaimed", [], send=(0x679, b"", 1)))
  host = plugins.CarPlugins(CP, FakeParams())
  try:
    sent = host.control_sends(None, None, 0)
  except Exception as e:
    errors.append(f"M5 host raised {e!r}")
    sent = []
  if [tuple(s) for s in sent] != [(0x3C2, b"", 1)]:
    errors.append(f"M5 sends {sent}")
  if {p.NAME for p in host.plugins} != {"ok"} or {n for n, _ in faults} != {"wrong_bus", "unclaimed"}:
    errors.append(f"M5 plugins {[p.NAME for p in host.plugins]} faults {faults}")
  plugins.PLUGINS = (make("t_no", [], target=True), make("t_yes", ["long.target"], target=True))
  if [t.plugin_name for t in plugins.long_targets(CP, FakeParams())] != ["t_yes"]:
    errors.append("M5 unclaimed long target used")
  plugins.PLUGINS = (make("p_no", [], proc="rogued"), make("p_yes", ["process:goodd"], proc="goodd"))
  if [p.name for p in plugins.processes()] != ["goodd"]:
    errors.append("M5 unclaimed process started")
  plugins.PLUGINS = (make("x1", [X], send=(0x3C2, b"", 1)), make("x2", [X], send=(0x3C2, b"", 1)))
  if [p.NAME for p in plugins.CarPlugins(CP, FakeParams()).plugins] != ["x1"]:
    errors.append("M3 both claimants built in card")

  import importlib
  plugins = importlib.reload(plugins)
  found, errs = mf.load_all()
  if errs:
    errors.append(f"M8 built-in manifest errors {errs}")
  for cls in plugins.PLUGINS:
    claims = set(cls.CLAIMS)
    if cls.control_sends is not Plugin.control_sends and not any(c.startswith("can.tx:") for c in claims):
      errors.append(f"M8 {cls.NAME} sends CAN without a can.tx claim")
    if cls.long_target.__func__ is not Plugin.long_target.__func__ and "long.target" not in claims:
      errors.append(f"M8 {cls.NAME} long_target unclaimed")
    if cls.plan_filter.__func__ is not Plugin.plan_filter.__func__ and "plan.filter" not in claims:
      errors.append(f"M8 {cls.NAME} plan_filter unclaimed")
    for proc in cls.processes():
      if f"process:{proc.name}" not in claims:
        errors.append(f"M8 {cls.NAME} process {proc.name} unclaimed")
  builtin = [c for c in plugins.PLUGINS if not c.__module__.startswith("xpilot_ext.")]  # store plugins are not in mf.load_all()
  if len(builtin) != len(found):
    errors.append(f"M8 {len(found)} manifests but {len(builtin)} built-in plugin classes")

  for m in found:
    if (path := mf.PLUGINS_DIR / m.id / "ui.py").exists() and hasattr(mf, "tile_problems"):
      errors += [f"M15 {m.id}: {p}" for p in mf.tile_problems(path, m.use)]
    errors += [f"M11 {m.id}/{p}" for p in mf.layout_problems(mf.PLUGINS_DIR / m.id, m.entry.split(":")[0])]
  extra = {d.name for d in mf.PLUGINS_DIR.iterdir() if d.is_dir() and d.name != "__pycache__"} - CORE_PLUGINS
  if extra:
    errors.append(f"M12 plugin directories outside the core set (move them to xpilot-plugins): {sorted(extra)}")
  with tempfile.TemporaryDirectory() as tmp:
    d = Path(tmp)
    (d / "plugin.py").write_text("x = 1\n" * (mf.MAX_ENTRY_LINES + 1))
    (d / "logic.py").write_text("import pyray as rl\n" + "x = 1\n" * mf.MAX_LINES)
    (d / "ui.py").write_text("import pyray as rl\n")
    (d / "tests").mkdir()
    (d / "tests" / "big.py").write_text("x = 1\n" * (mf.MAX_LINES + 1))
    got = sorted(p.split(":")[0] + ":" + p.split(": ")[1].split()[0] for p in mf.layout_problems(d))
    if got != ["logic.py:401", "logic.py:imports", "plugin.py:151"]:
      errors.append(f"M11 layout problems {got}")

  print(json.dumps({"plugins": [c.NAME for c in plugins.PLUGINS], "errors": errors}, indent=1))
  return 1 if errors else 0


if __name__ == "__main__":
  sys.exit(main())
