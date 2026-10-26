#!/usr/bin/env python3
"""Plugin store check: index, download, install, discovery and installed-plugin params, over a real local HTTP server.

Failure cases listed before implementation:
- S1 an archive whose sha256 differs from the index is installed
- S2 an archive with ../, absolute paths or symlinks writes outside the plugin directory
- S3 a failed install (bad manifest, hash, api) removes or damages the installed version
- S4 an installed plugin with the id of a built-in plugin is installed or discovered
- S5 an installed plugin declaring a param key that already exists (core or another plugin) is installed
- S6 an installed plugin is not discovered, or cannot use relative imports inside its package
- S7 uninstall leaves the plugin discoverable
- S8 installed plugin params: wrong type round trip, no default, or built-in keys routed to the store
- S9 is_enabled of an installed plugin raises (its ENABLE_PARAM is unknown to the compiled Params)
- S10 an index entry for another api version is offered as installable
- S11 an unreachable index or a malformed entry crashes instead of returning an error
- S12 an update keeps no copy until the new version is fully in place (half-written plugin directory)
- S13 a plugin built for another platform (machine / Python ABI) is offered as installable or installed
- S14 an installed plugin whose platform no longer matches (e.g. after a Python upgrade) is loaded instead of skipped
- S15 a plugin for this platform is refused
- S16 a plugin that breaks the code layout rules (manifest.layout_problems, e.g. one file over MAX_LINES) is installed
- S17 the UI looks for an installed plugin's ui.py under openpilot.tesla.plugins instead of xpilot_ext
- S18 can.tx and runtime rules disagree or a valid rule plugin is refused
- S19 index-flagged defaults are not installed, unflagged/invalid flags install, or an unreachable/unflagged index sets the marker
- S20 the second replace fails and the old version is lost, changed or no longer discoverable
- S21 the second replace and restore fail, and the old backup is deleted or its path is missing from the error
- S22 install or uninstall onroad does not return "park first", changes the installed files, or shows UI success
- S23-S28/S30 retired profile/param_bit cases; S40-S41 cover the replacement rule model.
- S29 two enabled plugins owning the same rule address both win its exclusive can.tx claim
- S31 invalid rules on disk are discovered instead of skipped with a reason
- S32 uninstall removes the only installed claimant of a required role or changes its files on refusal
- S33 uninstall refuses a replacement when a built-in or another installed claimant remains
- S34 the host Params stub accepts a key absent from params_keys.h and plugin_params.inc
- S35 install or uninstall succeeds while IsOffroad is False
- S36 missing/invalid use installs, damages an old version, or loads without a reason
- S37 tile/use mismatch installs instead of preserving the old version
- S38 incompatible requires installs or replaces a working version
- S39 incompatible requires loads instead of being skipped with its reason
- S40 legacy safety.profile installs instead of being rejected with a clear reason
- S41 invalid runtime rules install or replace a working plugin
"""
import gzip
import hashlib
import http.server
import io
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import threading
import types
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xp-store-"))
os.environ["XPILOT_PLUGINS_DIR"] = str(TMP / "installed")
sys.path[:0] = [str(ROOT), str(ROOT / "opendbc_repo")]


class CoreParams:  # stands in for the compiled Params: knows only core and built-in keys
  def __init__(self):
    self.values = {"IsOffroad": True}

  def get_bool(self, key, block=False):
    return bool(self.get(key, block))

  def get(self, key, block=False, return_default=False):
    if key not in mf.compiled_param_keys():
      raise KeyError(key)
    return self.values.get(key)

  def put(self, key, value, block=False):
    if key not in mf.compiled_param_keys():
      raise KeyError(key)
    self.values[key] = value

  def put_bool(self, key, value, block=False):
    self.put(key, bool(value), block)

  def remove(self, key):
    self.values.pop(key, None)


try:
  import openpilot.common.params  # noqa: F401
except Exception:
  sys.modules["openpilot.common.params"] = types.SimpleNamespace(Params=CoreParams, UnknownKeyName=KeyError)
sys.modules["openpilot.common.params"].Params = CoreParams
from openpilot.common.swaglog import cloudlog  # noqa: E402
for _level in ("error", "exception", "warning", "info"):
  setattr(cloudlog, _level, lambda *a, **k: None)

from openpilot.tesla import manifest as mf, store  # noqa: E402
from tools.sp_tesla_e2e.panda_rules_check import sample_rule
from openpilot.tesla.plugin_params import ParamsView  # noqa: E402


def manifest(pid, **over):
  m = {"id": pid, "name": pid, "version": "1.0.0", "api": mf.API_VERSION, "category": "tools", "entry": "plugin:P",
       "use": "both", "enable_param": f"Ext{pid}On", "claims": [],
       "params": {f"Ext{pid}On": {"type": "BOOL", "default": "1"}, f"Ext{pid}Level": {"type": "INT", "default": "7"},
                  f"Ext{pid}Cfg": {"type": "JSON"}}}
  m.update(over)
  return m


def archive(pid, files: dict, raw: list | None = None) -> bytes:
  buf = io.BytesIO()
  with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz, tarfile.open(fileobj=gz, mode="w") as tar:  # same bytes on republish
    for name, data in files.items():
      info = tarfile.TarInfo(f"{pid}/{name}")
      data = data.encode() if isinstance(data, str) else data
      info.size = len(data)
      tar.addfile(info, io.BytesIO(data))
    for info in raw or []:
      tar.addfile(info, io.BytesIO(b"x") if info.isfile() else None)
  return buf.getvalue()


PLUGIN_PY = "from .helper import VALUE\nfrom openpilot.tesla.plugin import Plugin\n\nclass P(Plugin):\n  X = VALUE\n"


def good_files(pid, version="1.0.0", **over):
  return {"__init__.py": "", "helper.py": f"VALUE = {version!r}\n", "plugin.py": PLUGIN_PY, "ui.py": "from .helper import VALUE\n",
          "plugin.json": json.dumps(manifest(pid, version=version, **over))}


def main() -> int:
  errors = []
  core = CoreParams()
  for key in ("IsOnroad", "NotARegisteredParam"):
    for method, args in (("get", ()), ("get_bool", ()), ("put", (True,)), ("put_bool", (True,))):
      try:
        getattr(core, method)(key, *args)
      except KeyError:
        continue
      errors.append(f"S34 Params.{method} accepted {key}")
  site = TMP / "site"
  site.mkdir()
  entries = []

  def publish(pid, data: bytes, version="1.0.0", sha=None, api=mf.API_VERSION, platform=None):
    (site / f"{pid}-{version}.tar.gz").write_bytes(data)
    entries.append({"id": pid, "name": pid, "version": version, "api": api, "category": "tools", "author": "t",
                    "description": "d", "url": f"{base}/{pid}-{version}.tar.gz", "sha256": sha or hashlib.sha256(data).hexdigest(),
                    "platform": platform})

  handler = lambda *a, **k: http.server.SimpleHTTPRequestHandler(*a, directory=str(site), **k)  # noqa: E731
  http.server.SimpleHTTPRequestHandler.log_message = lambda *a: None
  server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
  threading.Thread(target=server.serve_forever, daemon=True).start()
  base = f"http://127.0.0.1:{server.server_port}"

  publish("ext_ok", archive("ext_ok", good_files("ext_ok")))
  publish("ext_hash", archive("ext_hash", good_files("ext_hash")), sha="0" * 64)
  evil = tarfile.TarInfo("../evil.txt")
  evil.size = 1
  link = tarfile.TarInfo("ext_link/link")
  link.type, link.linkname = tarfile.SYMTYPE, "/etc/passwd"
  publish("ext_trav", archive("ext_trav", good_files("ext_trav"), raw=[evil]))
  publish("ext_link", archive("ext_link", good_files("ext_link"), raw=[link]))
  publish("sp_models", archive("sp_models", good_files("sp_models")))
  publish("ext_dup", archive("ext_dup", good_files("ext_dup", params={"IsMetric": {"type": "BOOL"}})))
  safe = [sample_rule()]
  tx = ['can.tx:0x600:1']
  for pid in ('auto_speed', 'ambient_light', 'ext_tx'):
    publish(pid, archive(pid, good_files(pid, claims=tx, rules=safe, priority=10 if pid == 'ext_tx' else 100)))
  bad_safety = {
    'old_profile': ('S40', dict(safety={'profile': 'auto_speed'}, claims=tx)),
    'old_legacy': ('S40', dict(safety={}, claims=tx)),
    'wrong_tx': ('S41', dict(rules=safe, claims=['can.tx:0x601:1'])),
    'owned': ('S41', dict(rules=[{**safe[0], 'tx_address': 0x488}], claims=['can.tx:0x488:1'])),
    'bus': ('S41', dict(rules=[{**safe[0], 'tx_bus': 0}], claims=tx)),
    'no_rules': ('S41', dict(claims=tx)),
    'too_many': ('S41', dict(rules=[{**safe[0], 'tx_address': 0x600+i} for i in range(9)])),
  }
  for pid, (_, over) in bad_safety.items():
    publish(pid, archive(pid, good_files(pid, **over)))
  publish("speed_card", archive("speed_card", good_files("speed_card")))
  for pid, flag in (("ext_default", True), ("ext_optional", False), ("ext_invalid", "true")):
    publish(pid, archive(pid, good_files(pid)))
    entries[-1]["default"] = flag
  publish("ext_fat", archive("ext_fat", {**good_files("ext_fat"), "helper.py": "VALUE = 1\n" * (mf.MAX_LINES + 1)}))
  publish("ext_api", archive("ext_api", good_files("ext_api")), api=mf.API_VERSION + 1)
  here, other = mf.host_platform(), {"machine": "sparc", "python": "cpython-27"}
  publish("ext_here", archive("ext_here", good_files("ext_here", platform=here)), platform=here)
  publish("ext_other", archive("ext_other", good_files("ext_other", platform=other)), platform=other)
  (site / "index.json").write_text(json.dumps({"api": mf.API_VERSION, "plugins": entries + [{"id": "broken"}]}))

  try:
    index, err = store.fetch_index(f"{base}/index.json")
  except Exception as e:
    errors.append(f"S11 fetch_index raised {e!r}")
    index, err = [], str(e)
  by_id = {e.id: e for e in index}
  if "broken" in by_id or "ext_ok" not in by_id:
    errors.append(f"S11 index {sorted(by_id)} err={err}")
  if by_id.get("ext_api") and by_id["ext_api"].installable:
    errors.append("S10 other api offered as installable")
  try:
    bad_index, bad_err = store.fetch_index(f"{base}/missing.json")
    if bad_index or not bad_err:
      errors.append("S11 missing index gave no error")
  except Exception as e:
    errors.append(f"S11 missing index raised {e!r}")

  def install(pid):
    try:
      return store.install(by_id[pid])
    except Exception as e:
      return f"raised {e!r}"

  if by_id.get("ext_other") and by_id["ext_other"].installable:
    errors.append("S13 other platform offered as installable")
  if not by_id.get("ext_here") or not by_id["ext_here"].installable or install("ext_here") is not None:
    errors.append("S15 this platform refused")
  if (r := install("ext_ok")) is not None:
    errors.append(f"S6 install ext_ok failed: {r}")
  for pid, code in (("ext_hash", "S1"), ("ext_trav", "S2"), ("ext_link", "S2"), ("sp_models", "S4"), ("ext_dup", "S5"), ("ext_fat", "S16")):
    if install(pid) is None:
      errors.append(f"{code} {pid} installed")
  if (TMP / "evil.txt").exists() or (TMP / "installed" / "evil.txt").exists():
    errors.append("S2 file written outside the plugin directory")
  for pid, code in (("auto_speed", "S18"), ("ambient_light", "S18"), ("ext_tx", "S18")):
    if (r := install(pid)) is not None:
      errors.append(f"{code} declared safety plugin {pid} refused: {r}")
  for pid, (code, _) in bad_safety.items():
    if not install(pid) or (mf.INSTALL_DIR / pid).exists():
      errors.append(f"{code} {pid} installed or left on disk")
  if any((TMP / "installed" / p).exists() for p in ("ext_hash", "ext_trav", "ext_link", "ext_dup", "ext_fat")):
    errors.append("S1/S2/S5/S16 rejected plugin left on disk")

  # update with a broken version keeps 1.0.0 (S3); a good update replaces it (S12)
  bad_v2 = good_files("ext_ok", version="2.0.0")
  bad_v2["plugin.json"] = "{broken"
  publish("ext_ok", archive("ext_ok", bad_v2), version="2.0.0")
  if store.install(store.IndexEntry(**{**entries[-1], "installable": True})) is None:
    errors.append("S3 broken update installed")
  if json.loads((TMP / "installed" / "ext_ok" / "plugin.json").read_text())["version"] != "1.0.0":
    errors.append("S3 broken update damaged the installed version")

  use_target = mf.INSTALL_DIR / "ext_ok"
  before_use = {p.name: p.read_bytes() for p in use_target.iterdir() if p.is_file()}
  for code, use, show in [("S36", None, None), ("S36", "never", None), ("S36", [], None),
                           ("S37", "onroad", "offroad"), ("S37", "offroad", "onroad"), ("S37", "onroad", "always")]:
    files = good_files("ext_ok", use=use)
    if use is None:
      data = json.loads(files["plugin.json"])
      del data["use"]
      files["plugin.json"] = json.dumps(data)
    if show:
      files["ui.py"] = f'from openpilot.tesla.ui.tiles import TileSpec\nSPEC = TileSpec("x", "X", show={show!r})\n'
    publish("ext_ok", archive("ext_ok", files), version="3.0.0")
    error = store.install(store.IndexEntry(**{**entries[-1], "installable": True}))
    after = {p.name: p.read_bytes() for p in use_target.iterdir() if p.is_file()}
    if not error or after != before_use:
      errors.append(f"{code} use={use!r} show={show!r}: error={error!r}, preserved={after == before_use}")
  incompatible = {"need_hook": {"hooks": ["teleport"]}, "need_rules": {"rule_abi": 2},
                  "need_native": {"native": {"machine": "sparc", "python": "cpython-27"}}, "bad_requires": []}
  for pid, requires in incompatible.items():
    publish("ext_ok", archive("ext_ok", good_files("ext_ok", requires=requires)), version="4.0.0")
    error = store.install(store.IndexEntry(**{**entries[-1], "installable": True}))
    after = {p.name: p.read_bytes() for p in use_target.iterdir() if p.is_file()}
    if not error or after != before_use:
      errors.append(f"S38 {pid} installed or damaged previous version: {error}")
    dest = mf.INSTALL_DIR / pid
    dest.mkdir()
    for name, content in good_files(pid, requires=requires).items():
      (dest / name).write_text(content)
  publish("ext_ok", archive("ext_ok", good_files("ext_ok")), version="1.0.0")
  store.install(store.IndexEntry(**{**entries[-1], "installable": True}))
  bad_use = mf.INSTALL_DIR / "bad_use"
  bad_use.mkdir()
  for name, content in good_files("bad_use", use="never").items():
    (bad_use / name).write_text(content)

  # S14: the installed ext_here manifest now claims another platform (as after a Python upgrade)
  here_json = TMP / "installed" / "ext_here" / "plugin.json"
  here_json.write_text(json.dumps({**json.loads(here_json.read_text()), "platform": other}))
  for pid, (_, over) in bad_safety.items():
    dest = mf.INSTALL_DIR / pid
    dest.mkdir(exist_ok=True)
    for name, content in good_files(pid, **over).items():
      (dest / name).write_text(content)
  import importlib
  from openpilot.tesla import plugins
  plugins = importlib.reload(plugins)
  if any(c.NAME == "ext_here" for c in plugins.PLUGINS) or "ext_here" not in plugins.LOAD_ERRORS:
    errors.append("S14 installed plugin for another platform loaded")
  names = [c.NAME for c in plugins.PLUGINS]
  for pid in incompatible:
    if pid in names or not plugins.LOAD_ERRORS.get(pid):
      errors.append(f"S39 {pid} loaded or reason missing")
  if "bad_use" in names or "use" not in plugins.LOAD_ERRORS.get("bad_use", ""):
    errors.append("S36 invalid installed use loaded or lost its reason")
  for pid in bad_safety:
    if pid in names or not plugins.LOAD_ERRORS.get(pid):
      errors.append(f"S31 {pid} not skipped with a reason")
  for pid, code in (("ext_tx", "S18"), ("auto_speed", "S18"), ("ambient_light", "S18")):
    cls = next((c for c in plugins.PLUGINS if c.NAME == pid), None)
    if cls is None or cls.MANIFEST.rules != safe:
      errors.append(f"{code} {pid} not discovered with runtime rules")
  ext = next((c for c in plugins.PLUGINS if c.NAME == "ext_ok"), None)
  if ext is None:
    errors.append(f"S6 installed plugin not discovered: {names} {plugins.LOAD_ERRORS}")
  elif ext.X != "1.0.0":
    errors.append("S6 relative import inside the installed package failed")
  elif plugins.ui_module(ext) != "xpilot_ext.ext_ok.ui" or importlib.import_module(plugins.ui_module(ext)).VALUE != "1.0.0" or \
      plugins.ui_module(next(c for c in plugins.PLUGINS if c.NAME == "sp_models")) != "openpilot.tesla.plugins.sp_models.ui":
    errors.append(f"S17 wrong ui module: {plugins.ui_module(ext)}")
  if names.count("sp_models") != 1:
    errors.append("S4 built-in id duplicated")

  core = CoreParams()
  view = ParamsView(core)
  active, conflicts = plugins.resolve(core)
  if active & {"ext_tx", "auto_speed", "ambient_light"} != {"ext_tx"} or \
      any(conflicts.get(pid) != "ext_tx" for pid in ("auto_speed", "ambient_light")):
    errors.append(f"S29 profile claim arbitration: {active}, {conflicts}")
  host = plugins.CarPlugins(types.SimpleNamespace(brand="tesla"), core)
  cp_sp = types.SimpleNamespace(safetyParam=0)
  host.setup(cp_sp)
  if cp_sp.safetyParam != 0:
    errors.append(f"S18 setup changed base safetyParam {cp_sp.safetyParam:#x}")
  if {p.NAME for p in host.plugins} & {"ext_tx", "auto_speed", "ambient_light"} != {"ext_tx"}:
    errors.append("S29 card built multiple profile claimants")
  try:
    if ext is not None and not plugins.is_enabled(ext, core):
      errors.append("S9 installed plugin default-on not enabled")
  except Exception as e:
    errors.append(f"S9 is_enabled raised {e!r}")
  try:
    if view.get("Extext_okLevel", return_default=True) != 7 or view.get("Extext_okLevel") is not None:
      errors.append("S8 default handling")
    view.put("Extext_okLevel", 12)
    view.put_bool("Extext_okOn", False)
    view.put("Extext_okCfg", {"a": [1, 2]})
    again = ParamsView(CoreParams())
    if again.get("Extext_okLevel") != 12 or again.get_bool("Extext_okOn") or again.get("Extext_okCfg") != {"a": [1, 2]}:
      errors.append("S8 round trip")
    view.put("IsMetric", True)
    if core.values.get("IsMetric") is not True:
      errors.append("S8 built-in key not routed to compiled Params")
  except Exception as e:
    errors.append(f"S8 raised {e!r}")

  publish("ext_ok", archive("ext_ok", good_files("ext_ok", version="2.0.0")), version="2.0.0")
  update = store.IndexEntry(**{**entries[-1], "installable": True})
  target = mf.INSTALL_DIR / "ext_ok"

  def files(path):
    return {str(p.relative_to(path)): p.read_bytes() for p in path.rglob("*") if p.is_file()}

  for code, failures in (("S20", (2,)), ("S21", (2, 3))):
    before = files(target)
    calls = []
    replace = store.os.replace

    def fail_replace(src, dst):
      if Path(src) == target or Path(dst) == target:
        calls.append((Path(src), Path(dst)))
        if len(calls) in failures:
          raise OSError(f"injected replace failure {len(calls)}")
      replace(src, dst)

    with patch.object(store.os, "replace", fail_replace):
      error = store.install(update)
    if error is None or len(calls) != 3:
      errors.append(f"{code} error={error!r}, replace calls={len(calls)}")
    if code == "S20":
      plugins = importlib.reload(plugins)
      if files(target) != before or store.installed_versions().get("ext_ok") != "1.0.0" or \
          not any(c.NAME == "ext_ok" and c.MANIFEST.version == "1.0.0" and c.X == "1.0.0" for c in plugins.PLUGINS):
        errors.append("S20 failed update lost, changed or hid the old version")
    else:
      backup = calls[0][1]
      if not backup.is_dir() or files(backup) != before or str(backup) not in (error or ""):
        errors.append(f"S21 old backup missing, changed or not named in error: {backup}, {error!r}")
    if (r := install("ext_ok")) is not None:
      errors.append(f"{code} fixture reinstall failed: {r}")

  from openpilot.tesla.ui.offroad import StoreJobs
  onroad = CoreParams()
  onroad.put_bool("IsOffroad", False)
  before = files(target)
  with patch.object(sys.modules["openpilot.common.params"], "Params", lambda: onroad):
    for action in (lambda: store.install(update), lambda: store.uninstall("ext_ok")):
      error = action()
      if error != "park first" or files(target) != before or store.installed_versions().get("ext_ok") != "1.0.0":
        errors.append(f"S22/S35 onroad action error={error!r}, installed={store.installed_versions()}")
    if (r := install("ext_ok")) != "park first":
      errors.append(f"S22 onroad reinstall error={r!r}")
    jobs = StoreJobs(lambda: True)
    jobs.installed = {"ext_ok": "1.0.0"}
    jobs.act("ext_ok")
    if jobs.status != "ext_ok: park first":
      errors.append(f"S22 UI reported {jobs.status!r}")

  store.uninstall("ext_ok")
  plugins = importlib.reload(plugins)
  if any(c.NAME == "ext_ok" for c in plugins.PLUGINS):
    errors.append("S7 uninstalled plugin still discovered")

  for role in ("role:perception.model", "role:engagement.state", "role:safety.fallback"):
    pid = "ext_required"
    publish(pid, archive(pid, good_files(pid, claims=[role])))
    entry = store.IndexEntry(**{**entries[-1], "installable": True})
    if error := store.install(entry):
      errors.append(f"S32 fixture install: {error}")
      continue
    target = mf.INSTALL_DIR / pid
    before = files(target)
    with patch.object(mf, "PLUGINS_DIR", TMP / "no-builtins"):
      error = store.uninstall(pid)
    if error != f"required role {role}" or files(target) != before:
      errors.append(f"S32 only claimant {role}: error={error!r}, files preserved={files(target) == before}")
    if error := store.uninstall(pid):
      errors.append(f"S33 built-in fallback {role}: {error}")
    if target.exists():
      errors.append(f"S33 replacement {role} still installed")
    store.install(entry)
    other = "ext_fallback"
    publish(other, archive(other, good_files(other, claims=[role], enable_param=None)))
    store.install(store.IndexEntry(**{**entries[-1], "installable": True}))
    with patch.object(mf, "PLUGINS_DIR", TMP / "no-builtins"):
      if (error := store.uninstall(pid)) or target.exists():
        errors.append(f"S33 installed fallback {role}: {error!r}")
    store.uninstall(other)

  # S19: an unreachable index sets no marker; a reachable one installs the missing defaults once
  store.INDEX_URL = f"{base}/missing.json"
  if store.install_defaults() or store.defaults_done():
    errors.append("S19 marker set on an unreachable index")
  (site / "unflagged.json").write_text(json.dumps({"plugins": [{k: v for k, v in e.items() if k != "default"} for e in entries]}))
  store.INDEX_URL = f"{base}/unflagged.json"
  if store.install_defaults() or store.defaults_done():
    errors.append("S19 old/unflagged index prematurely completes defaults")
  store.INDEX_URL = f"{base}/index.json"
  if not store.install_defaults() or "ext_default" not in store.installed_versions() or not store.defaults_done():
    errors.append(f"S19 defaults not installed: {store.installed_versions()}")
  if {"speed_card", "ext_optional", "ext_invalid"} & store.installed_versions().keys():
    errors.append("S19 unflagged or invalid default installed")
  if store.install_defaults():
    errors.append("S19 installed default installed again")

  server.shutdown()
  print(json.dumps({"artifacts": str(TMP), "errors": errors}, indent=1))
  return 1 if errors else 0


if __name__ == "__main__":
  sys.exit(main())
