#!/usr/bin/env python3
"""S2: HTTP store -> transaction -> parked reboot request -> fresh manager discovery/registration.

Failure scenarios fixed before implementation:
- corrupt download, API/native ABI mismatch, unusable entry or missing/failed local preparation replaces the original;
- stale PLUGINS chooses the old enable key/claims, enables another model, or changes settings/core parameters;
- a conflicting claimant supplies another required role, or an always-on claimant defeats the selected plugin;
- going onroad during download/preparation/toggle writes or immediately before reboot leaves changes behind;
- toggle/reboot/rename writes fail (including after a write) and lose original bytes, absent keys or backup;
- import probing hot-loads into the running host, or simulated restart fails to register/gate the new process;
- missing/broken/stale/version-mismatched state is called ready, or a missing/broken plugin removes sp_models.

Run: python3 tools/sp_tesla_e2e/store_activation_e2e.py --output /tmp/s2-evidence
All Params and plugin storage are isolated; no device, real Params, model execution or actual reboot.
"""
import argparse
import hashlib
import http.server
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.sp_tesla_e2e.store_check import CoreParams, ROOT, TMP, archive, good_files, mf, store
from openpilot.tesla.plugin_params import ParamsView
from openpilot.tesla import plugins
from openpilot.tesla.ui.offroad import StoreJobs

ROLE = "role:perception.model"
PID = "s2_model"
NEW_KEY = "ExtS2New"
MODEL = '''from pathlib import Path
from openpilot.tesla.plugin import Plugin

class P(Plugin):
  @classmethod
  def prepare_install(cls):
    return Path(__file__).with_name('local.model').read_bytes() == b'validated local model'

  @classmethod
  def processes(cls):
    from openpilot.system.manager.process import PythonProcess
    return [PythonProcess('s2_modeld', cls.__module__, lambda started, params, CP: started)]
'''


def tree(path):
  return {str(p.relative_to(path)): p.read_bytes().hex() for p in sorted(path.rglob("*"))
          if p.is_file() and "__pycache__" not in p.parts}


def discover():
  # A separate interpreter stands for the new manager; registration and owner gates are the real host code.
  code = '''
import json
from openpilot.common.swaglog import cloudlog
cloudlog.exception = lambda *a, **kw: None
from openpilot.tesla import plugins
class Core:
  def get_bool(self, key, **kw): return key == 'IsOffroad'
  def get(self, key, **kw): return None
core = Core()
procs = plugins.processes()
print(json.dumps({'owner': plugins.owner(core, 'role:perception.model'), 'errors': plugins.LOAD_ERRORS,
                  'processes': {p.name: [bool(p.should_run(s, core, None)) for s in (False, True)] for p in procs},
                  'types': {p.name: type(p).__name__ for p in procs}}))
'''
  result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, check=True, capture_output=True, text=True,
                          env=os.environ, timeout=30)
  return json.loads(result.stdout)


def main():
  ap = argparse.ArgumentParser()
  ap.add_argument("--output", type=Path, required=True)
  output = ap.parse_args().output.resolve()
  output.mkdir(parents=True, exist_ok=True)
  support = output / "host-support"
  support.mkdir(exist_ok=True)
  # Match the parent test's isolated Params boundary in both the readiness and restarted-manager interpreters.
  (support / "sitecustomize.py").write_text("import sys, types\nsys.modules['openpilot.common.params'] = " +
                                         "types.SimpleNamespace(Params=object, UnknownKeyName=KeyError)\n")
  os.environ["PYTHONPATH"] = os.pathsep.join((str(support), str(ROOT / "opendbc_repo")))
  site = output / "http"
  site.mkdir(exist_ok=True)
  http.server.SimpleHTTPRequestHandler.log_message = lambda *a: None
  server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), lambda *a, **kw: http.server.SimpleHTTPRequestHandler(*a, directory=str(site), **kw))
  threading.Thread(target=server.serve_forever, daemon=True).start()
  base = f"http://127.0.0.1:{server.server_port}"
  core = CoreParams()
  cases = []

  def publish(version="2.0.0", **over):
    spec = {NEW_KEY: {"type": "BOOL", "default": "0"}, "ExtS2Level": {"type": "INT", "default": "7"}}
    files = good_files(PID, version, enable_param=NEW_KEY, params=spec,
                       category="perception_model", claims=[ROLE, "process:s2_modeld"], **over)
    files.update({"plugin.py": MODEL, "local.model": b"validated local model"})
    return files

  def serve(files, **over):
    data = archive(PID, files)
    name = f"{PID}-{hashlib.sha256(data).hexdigest()[:12]}.tar.gz"
    (site / name).write_bytes(data)
    raw = {"id": PID, "name": "S2 model", "version": "2.0.0", "api": mf.API_VERSION, "category": "perception_model", "author": "E2E",
           "description": "Install & enable; parked restart", "url": base + "/" + name, "sha256": hashlib.sha256(data).hexdigest()}
    raw.update(over)
    (site / "index.json").write_text(json.dumps({"api": mf.API_VERSION, "plugins": [raw]}))
    entries, error = store.fetch_index(base + "/index.json")
    assert not error and len(entries) == 1, error
    return entries[0]

  def reset(existing=True, **rival):
    shutil.rmtree(mf.INSTALL_DIR, ignore_errors=True)
    mf.INSTALL_DIR.mkdir()
    core.values = {"IsOffroad": True, "IsMetric": False, "OpenpilotEnabledToggle": True, "ExperimentalMode": False,
                   "DisengageOnAccelerator": True, "LongitudinalPersonality": 1}
    for pid, overrides in ((PID, {}), ("s2_rival", rival), ("s2_other", {"claims": ["source:map"]})):
      if pid == PID and not existing:
        continue
      dest = mf.INSTALL_DIR / pid
      dest.mkdir()
      opts = {"claims": [ROLE], "priority": 50, **overrides}
      for name, data in good_files(pid, **opts).items():
        (dest / name).write_text(data)
    view = ParamsView(core)
    view.put_bool("Exts2_rivalOn", True)
    view.put_bool("Exts2_otherOn", True)
    if existing:
      view.put_bool(f"Ext{PID}On", False)
    view.put("ExtS2Level", 42) if "ExtS2Level" in view._specs else (mf.INSTALL_DIR / ".params" / "ExtS2Level").write_text("42")
    importlib.reload(plugins)  # freeze the old manager's discovery before replacing files
    return tree(mf.INSTALL_DIR), dict(core.values)

  def record(name, job):
    try:
      detail = job()
      cases.append({"name": name, "ok": True, "detail": detail})
    except Exception as e:
      cases.append({"name": name, "ok": False, "error": repr(e)})

  def refused(name, change=lambda files: files, inject=None, existing=True, **rival):
    before, params = reset(existing, **rival)
    entry = serve(change(publish()))
    with patch("openpilot.common.params.Params", lambda: core):
      context = inject() if inject else patch.dict(os.environ, {})
      with context:
        error = store.install(entry, activate=True, offroad=lambda: core.get_bool("IsOffroad"))
    after_params = {**core.values, "IsOffroad": params["IsOffroad"]}
    assert error, "accepted failed transaction"
    assert tree(mf.INSTALL_DIR) == before, "original files/toggles not restored"
    assert after_params == params, f"core parameters changed: {core.values}"
    return {"error": error, "before": before, "after": tree(mf.INSTALL_DIR), "params": after_params}

  record("corrupt download", lambda: refused("hash", inject=lambda: patch.object(store, "_get", lambda url, limit: b"corrupt")))
  for label, changes in (("API mismatch", {"api": 999}), ("ABI mismatch", {"requires": {"native": {"machine": "wrong", "python": "wrong"}}}),
                         ("protected toggle", {"enable_param": "OpenpilotEnabledToggle"}), ("losing priority", {"priority": 20000})):
    def bad_manifest(files, changes=changes):
      files["plugin.json"] = json.dumps({**json.loads(files["plugin.json"]), **changes})
      return files
    record(label, lambda change=bad_manifest, label=label: refused(label, change))
  for label, code in (("entry import failure", "raise ImportError('missing host ABI')\n"),
                      ("entry not Plugin", "class P: pass\n"),
                      ("local model unavailable", MODEL.replace("b'validated local model'", "b'unavailable'")),
                      ("no local preparation", MODEL.replace("prepare_install", "not_preparation"))):
    record(label, lambda code=code, label=label: refused(label, lambda files: {**files, "plugin.py": code}))
  partial_core = TMP / "partial-core"
  for pid in ("sp_models", "sp_safety_fallback"):
    shutil.copytree(mf.PLUGINS_DIR / pid, partial_core / pid)
  record("required-role conflict", lambda: refused("required", claims=[ROLE, "role:engagement.state"],
                                                   inject=lambda: patch.object(mf, "PLUGINS_DIR", partial_core)))
  record("new-install rollback", lambda: refused("new", lambda files: {**files, "plugin.py": "raise ImportError('broken')\n"}, existing=False))

  def file_write_failure():
    replace = os.replace
    def fail(src, dst):
      if Path(dst).name == NEW_KEY:
        raise OSError("injected parameter rename failure")
      replace(src, dst)
    return patch.object(os, "replace", fail)
  record("parameter file write failure", lambda: refused("file write", inject=file_write_failure))

  def dropped_role():
    reset()
    old = mf.INSTALL_DIR / PID / "plugin.json"
    old.write_text(json.dumps({**json.loads(old.read_text()), "claims": ["role:engagement.state"]}))
    ParamsView(core).put_bool(f"Ext{PID}On", True)
    before, params = tree(mf.INSTALL_DIR), dict(core.values)
    entry = serve(publish())
    with patch("openpilot.common.params.Params", lambda: core), patch.object(mf, "PLUGINS_DIR", partial_core):
      error = store.install(entry, activate=True, offroad=lambda: True)
    assert error and "required role" in error, error
    assert tree(mf.INSTALL_DIR) == before and core.values == params
    return error
  record("update drops its old required role", dropped_role)

  record("entry process factory import failure", lambda: refused("factory", lambda files: {
    **files, "plugin.py": MODEL.replace("return [PythonProcess", "raise ImportError('missing process ABI')\n    return [PythonProcess")}))

  for phase in ("download", "toggle", "restart", "reboot write", "reboot write after commit"):
    def failure(phase=phase):
      if phase == "download":
        get = store._get
        def flip(url, limit):
          data = get(url, limit)
          core.values["IsOffroad"] = False
          return data
        return patch.object(store, "_get", flip)
      if phase == "toggle":
        put = ParamsView.put_bool
        def broken(view, key, value, **kw):
          put(view, key, value, **kw)
          if key == NEW_KEY:
            raise OSError("injected toggle write failure after commit")
        return patch.object(ParamsView, "put_bool", broken)
      put = core.put_bool
      if phase == "restart":
        call = ParamsView.put_bool
        def flip(view, key, value, **kw):
          call(view, key, value, **kw)
          if key == NEW_KEY:
            core.values["IsOffroad"] = False
        return patch.object(ParamsView, "put_bool", flip)
      def broken(key, value, **kw):
        if key == "DoReboot":
          if phase.endswith("after commit"):
            put(key, value, **kw)
          raise OSError("injected reboot write failure")
        put(key, value, **kw)
      return patch.object(core, "put_bool", broken)
    record(phase, lambda phase=phase: refused(phase, inject=failure))

  def success(existing):
    reset(existing)
    entry = serve(publish())
    frozen = plugins.PLUGINS
    with patch("openpilot.common.params.Params", lambda: core):
      jobs = StoreJobs(lambda: True)
      jobs.entries = [entry]
      if existing:
        error = store.install(entry, activate=True, offroad=lambda: True)
        assert error is None, error
      else:
        assert jobs.action(entry) == "INSTALL & ENABLE"
        jobs.run(lambda: jobs.act(PID), wait=True)
        assert "restarting" in jobs.status, jobs.status
    assert core.values.get("DoReboot") is True
    assert plugins.PLUGINS is frozen, "hot-loaded into the old process"
    view = ParamsView(core)
    assert view.get_bool(NEW_KEY) and not view.get_bool("Exts2_rivalOn")
    assert view.get_bool("Exts2_otherOn") and view.get("ExtS2Level") == 42
    assert json.loads((mf.INSTALL_DIR / PID / "plugin.json").read_text())["params"][NEW_KEY]["default"] == "0"
    snap = discover()
    assert snap["owner"] == PID and snap["processes"]["s2_modeld"] == [False, True], snap
    assert snap["types"]["s2_modeld"] == "PythonProcess", snap
    assert next(p for p in plugins.PLUGINS if p.NAME == "sp_models").ENABLE_PARAM is None
    (output / "installed-snapshot.json").write_text(json.dumps(tree(mf.INSTALL_DIR), indent=2))
    return {"manager": snap, "params": core.values, "plugin_params": tree(mf.INSTALL_DIR / ".params")}
  record("update from stale manifest", lambda: success(True))
  record("StoreJobs install-enable-reboot-discover", lambda: success(False))

  def states():
    state = mf.INSTALL_DIR / ".data" / PID / "status.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    seen = []
    now = time.time()  # noqa: TID251 (exercise the persisted cross-boot timestamp contract)
    assert store.plugin_state(PID, "2.0.0") == "Installed"
    for label in ("Local ready", "Phone warming", "Phone active", "Local fallback"):
      temp = state.with_suffix(".tmp")
      temp.write_text(json.dumps({"state": label, "version": "2.0.0", "updated_at": now}))
      os.replace(temp, state)
      assert store.plugin_state(PID, "2.0.0") == label
      seen.append(label)
    for raw in ("{", "[]", json.dumps({"state": "Phone active", "version": "1.0.0", "updated_at": now}),
                json.dumps({"state": "Phone active", "version": "2.0.0", "updated_at": 0})):
      state.write_text(raw)
      assert store.plugin_state(PID, "2.0.0") == "Installed", raw
    return seen
  record("plugin-written atomic status", states)

  for label in ("absent", "import failure"):
    def fallback(label=label):
      if label == "absent":
        shutil.rmtree(mf.INSTALL_DIR / PID)
      else:
        (mf.INSTALL_DIR / PID).mkdir(exist_ok=True)
        for name, content in publish().items():
          (mf.INSTALL_DIR / PID / name).write_bytes(content if isinstance(content, bytes) else content.encode())
        (mf.INSTALL_DIR / PID / "plugin.py").write_text("raise ImportError('host ABI missing')\n")
      result = discover()
      assert result["owner"] == "sp_models", result
      return result
    record("sp_models fallback: " + label, fallback)

  server.shutdown()
  result = {"source_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            "command": " ".join(sys.argv), "storage": str(mf.INSTALL_DIR), "cases": cases,
            "errors": [c["name"] for c in cases if not c["ok"]]}
  (output / "result.json").write_text(json.dumps(result, indent=2))
  (output / "SHA256SUMS").write_text("".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(output)}\n"
                                             for p in sorted(output.rglob("*")) if p.is_file() and p.name != "SHA256SUMS"))
  print(json.dumps({"evidence": str(output), "cases": len(cases), "errors": result["errors"]}, indent=2))
  return bool(result["errors"])


if __name__ == "__main__":
  sys.exit(main())
