#!/usr/bin/env python3
"""End to end: a checkout of the plugin repository -> its index and archives served locally -> store install ->
discovery -> enable via the params view -> the plugin's hook runs in the host. Usage:
  python3 tools/sp_tesla_e2e/store_e2e.py --repo ~/xpilot-plugins

Failure cases listed before running:
- E1 a platform-compatible index entry is not installable on this host, or fails to install
- E2 an installed repository plugin is not discovered, or reports a load error
- E3 a plugin that is off by default is active before it is switched on
- E4 after switching on, its alert hook is not collected or gives no alert when it should (overspeed_warning)
- E5 the alert fires below the margin or without a known limit
- E4b the alert hook reads a service selfdrived does not subscribe (selfdrived has no carState unless a plugin
      declares it in SERVICES; overspeed_warning 1.0.0 raised KeyError every frame on the car)
- E6 a setting written through the params view does not reach the plugin on the next start
- E8 an installed plugin tile without explicit show is not reported by the shared M13 source check
- E7 an installed model plugin (op_models) owns role:perception.model while off, does not take it from sp_models when
     switched on, or does not start its own op_modeld process
- E9 repository source/archive lacks valid use or a tile conflicts with use
- E10 a mismatched native-platform plugin is not refused with the platform reason and no writes, then skipped
- E11 a mismatched native-platform plugin is accidentally offered as installable or accepted
- E12 a matching native-platform plugin skips the full install/discovery/activation checks (simulate aarch64 too)
"""
import argparse
import http.server
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import types

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xp-e2e-"))
os.environ["XPILOT_PLUGINS_DIR"] = str(TMP / "installed")
sys.path[:0] = [str(ROOT), str(ROOT / "opendbc_repo")]

from openpilot.tesla import manifest as mf  # noqa: E402


class CoreParams:
  def get_bool(self, key, block=False):
    return bool(self.get(key, block))

  def get(self, key, block=False, return_default=False):
    if key not in mf.compiled_param_keys():
      raise KeyError(key)
    return True if key == "IsOffroad" else None


try:
  import openpilot.common.params  # noqa: F401
except Exception:
  sys.modules["openpilot.common.params"] = types.SimpleNamespace(Params=CoreParams, UnknownKeyName=KeyError)
from openpilot.common.swaglog import cloudlog  # noqa: E402
for _level in ("error", "exception", "warning", "info"):
  setattr(cloudlog, _level, lambda *a, **k: None)
from openpilot.tesla import store  # noqa: E402


class CP:
  brand = "tesla"


def sm(v_ego: float, limit: float) -> dict:
  """What selfdrived's SubMaster holds: longitudinalPlanSP always, carState only via plugins.services()."""
  from openpilot.tesla import plugins
  plan = types.SimpleNamespace(speedLimit=types.SimpleNamespace(resolver=types.SimpleNamespace(speedLimit=limit)))
  out = {"carState": types.SimpleNamespace(vEgo=v_ego), "longitudinalPlanSP": plan}
  return {k: v for k, v in out.items() if k != "carState" or k in plugins.services()}


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("--repo", type=Path, required=True)
  repo = ap.parse_args().repo.expanduser().resolve()
  errors = []

  for path in sorted((repo / "plugins").glob("*/plugin.json")):
    try:
      m = mf.parse(path)
      if getattr(m, "use", None) is None:
        errors.append(f"E9 {m.id}: use not validated by manifest.parse")
      elif (ui := path.with_name("ui.py")).exists():
        errors += [f"E9 {m.id}: {p}" for p in mf.tile_problems(ui, m.use)]
    except ValueError as e:
      errors.append(f"E9 {path.parent.name}: {e}")

  http.server.SimpleHTTPRequestHandler.log_message = lambda *a: None
  server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), lambda *a, **k: http.server.SimpleHTTPRequestHandler(*a, directory=str(repo), **k))
  threading.Thread(target=server.serve_forever, daemon=True).start()
  base = f"http://127.0.0.1:{server.server_port}"
  index = json.loads((repo / "index.json").read_text())
  for e in index["plugins"]:  # serve the repository's own archives instead of GitHub
    e["url"] = base + "/" + e["url"].split("/main/", 1)[1]
  (TMP / "index.json").write_text(json.dumps(index))
  entries, err = store.fetch_index((TMP / "index.json").as_uri())
  if err or not entries:
    errors.append(f"E1 index: {err}")
  skipped = {}
  for e in entries:
    if not mf.platform_ok(e.platform):
      before = {p: p.read_bytes() if p.is_file() else None for p in TMP.rglob("*")}
      r = store.install(e)
      after = {p: p.read_bytes() if p.is_file() else None for p in TMP.rglob("*")}
      if e.installable or r is None:
        errors.append(f"E11 {e.id}: mismatched platform accepted, installable={e.installable}, result={r}")
      if r != f"not installable (api {e.api} vs {mf.API_VERSION}, platform {e.platform}, or a built-in id)" or after != before:
        errors.append(f"E10 {e.id}: platform refusal={r}, unchanged={after == before}")
      skipped[e.id] = r
      continue
    if not e.installable or (r := store.install(e)) is not None:
      errors.append(f"E1 {e.id}: installable={e.installable} {r if e.installable else ''}")

  from tools.sp_tesla_e2e.manifest_check import tile_show_problems
  for path in sorted(mf.INSTALL_DIR.glob("*/ui.py")):
    errors += ["E8 " + problem for problem in tile_show_problems(path)]

  from openpilot.tesla import plugins
  from openpilot.tesla.plugin_params import ParamsView
  ids = {e.id for e in entries} - skipped.keys()
  found = {c.NAME for c in plugins.PLUGINS} & ids
  if found != ids or ids & set(plugins.LOAD_ERRORS):
    errors.append(f"E2 discovered {found}, load errors {plugins.LOAD_ERRORS}")

  if "overspeed_warning" in found:
    core = CoreParams()
    if plugins.alert_sources(CP, core):
      errors.append("E3 active while switched off")
    view = ParamsView(core, plugins.INSTALLED_SPECS)
    view.put_bool("ExtOverspeedWarning", True)
    view.put("ExtOverspeedMargin", 8)
    sources = plugins.alert_sources(CP, core)
    if len(sources) != 1:
      errors.append(f"E4 sources {sources}")
    else:
      src = sources[0]
      limit = 50 / 3.6
      try:
        over = src.update(sm(limit + 10 / 3.6, limit))
        if [a.severity for a in over] != ["warning"] or "10 km/h" not in over[0].title:
          errors.append(f"E4/E6 alert at +10 km/h with margin 8: {over}")
        if src.update(sm(limit + 6 / 3.6, limit)) or src.update(sm(30.0, 0.0)):
          errors.append("E5 alert below margin or without a limit")
      except KeyError as e:
        errors.append(f"E4b hook read {e} that selfdrived does not subscribe; add it to the plugin's SERVICES")

  if "op_models" in found:
    core = CoreParams()
    role = "role:perception.model"
    view = ParamsView(core, plugins.INSTALLED_SPECS)
    off = plugins.owner(core, role)
    view.put_bool("ExtOpModels", True)
    on = plugins.owner(core, role)
    procs = [p for p in plugins.processes() if p.name == "op_modeld"]
    runs = bool(procs) and procs[0].should_run(True, core, None) and not procs[0].should_run(False, core, None)
    if off != "sp_models" or on != "op_models" or not runs:
      errors.append(f"E7 owner off={off} on={on}, op_modeld onroad-only={runs}")

  server.shutdown()
  print(json.dumps({"installed": sorted(found), "skipped": skipped, "errors": errors}, indent=1))
  return 1 if errors else 0


if __name__ == "__main__":
  sys.exit(main())
