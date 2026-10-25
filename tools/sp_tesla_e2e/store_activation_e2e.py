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
- FIX round 1: SIGKILL after package renames, each toggle or before DoReboot leaves partial state; startup discovers it;
- one failed rollback prevents other files/toggles/reboot restoration, or retry consumes its only backups;
- parameter names collide with old/candidate paths; orphan staging persists; completed installs get rolled back;
- busy store jobs allow either UI switch callback to mutate params, or store rows read runtime state each frame;
- a refusal is accepted for an unrelated error instead of reaching the claimed guard.

Run: python3 tools/sp_tesla_e2e/store_activation_e2e.py --output /tmp/s2-evidence
All Params and plugin storage are isolated; no device, real Params, model execution or actual reboot.
"""
import argparse
import ast
import hashlib
import http.server
import importlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import threading
import time
import types
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
  os.environ["PYTHONPATH"] = os.pathsep.join(filter(None, (str(support), str(ROOT / "opendbc_repo"), os.environ.get("PYTHONPATH"))))
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
    reasons = {"hash": "sha256 mismatch", "API mismatch": "api 999", "ABI mismatch": "requires native",
               "protected toggle": "own BOOL", "losing priority": "selected plugin loses an exclusive claim",
               "entry import failure": "missing host ABI", "entry not Plugin": "not a Plugin subclass",
               "local model unavailable": "local model not ready", "no local preparation": "prepare_install",
               "required": "required role role:engagement.state", "new": "broken", "file write": "injected parameter rename failure",
               "factory": "missing process ABI", "download": "park first", "toggle": "injected toggle write failure",
               "restart": "park first", "reboot write": "injected reboot write failure",
               "reboot write after commit": "injected reboot write failure"}
    assert reasons[name] in error, f"{name} did not reach its guard: {error}"
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

  def row_polling():
    jobs = StoreJobs(lambda: True)
    entry = serve(publish())
    jobs.installed = {PID: entry.version}
    with patch("openpilot.tesla.ui.offroad.time.monotonic", return_value=100) as clock, \
         patch.object(store, "plugin_state", return_value="Local ready") as read:
      for _ in range(60):
        assert "Local ready" in jobs.row_value(entry)
      assert read.call_count == 1, f"{read.call_count} reads in one second"
      clock.return_value = 101
      jobs.row_value(entry)
      assert read.call_count == 2
    return "60 row evaluations -> one status read; next second -> second read"
  record("store row polls at 1 Hz", row_polling)

  def busy_switches():
    reset()
    rival = next(p for p in plugins.PLUGINS if p.NAME == "s2_rival")
    from openpilot.tesla.ui import offroad
    jobs = StoreJobs(lambda: True)
    entered, release = threading.Event(), threading.Event()
    def hold():
      entered.set()
      assert release.wait(10)
    jobs.run(hold)
    assert entered.wait(10)
    before = tree(mf.INSTALL_DIR)
    try:
      for path, name, methods, callback in (("pages/plugins.py", "Switches", ("switch", "refresh"), "switch"),
                                           ("plugins_page.py", "CategoryPage", ("_switch", "show_event"), "_switch")):
        parsed = ast.parse((ROOT / "openpilot/tesla/ui" / path).read_text())
        cls = next(n for n in parsed.body if isinstance(n, ast.ClassDef) and n.name == name)
        cls.bases = [ast.Name(id="Base", ctx=ast.Load())]
        cls.body = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in methods]
        parsed.body = [cls]
        env = {"Base": type("Base", (), {"show_event": lambda self: None}),
               "ui_state": types.SimpleNamespace(is_offroad=lambda: True, params=core), "params": lambda: ParamsView(core),
               **{n: getattr(plugins, n) for n in ("view", "is_enabled", "resolve", "FAULTS_PARAM")},
               "switch_plugin": offroad.switch_plugin, "plugin_status": offroad.plugin_status}
        exec(compile(ast.fix_missing_locations(parsed), path, "exec"), env)
        ui = env[name]()
        ui.items, ui.rows, ui.errors, ui.jobs = [], [], {}, jobs
        getattr(ui, callback)(rival, False)
        assert ui.errors[rival.NAME] == "store busy", ui.errors
        assert tree(mf.INSTALL_DIR) == before
    finally:
      release.set()
      while jobs.busy:
        time.sleep(0.01)
    assert offroad.switch_plugin(rival, False, ParamsView(core), jobs=jobs) is None
    return "both real UI callbacks refused writes while worker busy; idle switch works"
  record("busy store blocks both UI switches", busy_switches)

  # Persistent, isolated core Params survive the killed installer and fresh startup interpreter.
  (support / "fixture_params.py").write_text('''import json, os
from pathlib import Path
class Params:
  def __init__(self): self.path = Path(os.environ['XP_TEST_CORE'])
  def get(self, key, **kw): return json.loads(self.path.read_text()).get(key)
  def get_bool(self, key, **kw): return bool(self.get(key))
  def put_bool(self, key, value, **kw):
    data = json.loads(self.path.read_text()); data[key] = value
    self.path.write_text(json.dumps(data))
  def remove(self, key):
    data = json.loads(self.path.read_text()); data.pop(key, None)
    self.path.write_text(json.dumps(data))
''')
  (support / "sitecustomize.py").write_text("import sys, types\nfrom fixture_params import Params\n" +
                                         "sys.modules['openpilot.common.params'] = types.SimpleNamespace(Params=Params, UnknownKeyName=KeyError)\n")
  kill_code = '''
import json, os, signal, sys
from pathlib import Path
from openpilot.common.params import Params
from openpilot.tesla import store, manifest as mf
from openpilot.tesla.plugin_params import ParamsView
entry = store.IndexEntry(**json.loads(Path(sys.argv[1]).read_text()))
point = sys.argv[2]
replace, put, reboot, sync = os.replace, ParamsView.put_bool, Params.put_bool, os.fsync
synced = set()
def kill(): os.kill(os.getpid(), signal.SIGKILL)
def fsync(fd):
  sync(fd)
  synced.add(os.fstat(fd).st_ino)
def swap(src, dst):
  if Path(src) == mf.INSTALL_DIR / entry.id:
    staging = Path(dst).parent
    for p in (staging / 'journal.json', staging, mf.INSTALL_DIR):
      assert p.stat().st_ino in synced, f'not durable before first mutation: {p}'
  replace(src, dst)
  if (point == 'journal published' and Path(dst).name == 'journal.json' or
      point == 'old rename' and Path(src) == mf.INSTALL_DIR / entry.id or
      point == 'new rename' and Path(dst) == mf.INSTALL_DIR / entry.id): kill()
def toggle(view, key, value, **kw):
  put(view, key, value, **kw)
  if point == 'first toggle' and key == 'Exts2_rivalOn': kill()
  if point == 'second toggle' and key == 'ExtS2New': kill()
def request(self, key, value, **kw):
  if point == 'before reboot' and key == 'DoReboot': kill()
  reboot(self, key, value, **kw)
  if point == 'after reboot' and key == 'DoReboot': kill()
os.replace, ParamsView.put_bool, Params.put_bool, os.fsync = swap, toggle, request, fsync
error = store.install(entry, activate=sys.argv[3] == 'True', offroad=lambda: True)
raise AssertionError(f'kill point not reached: {point}: {error}')
'''
  for point, existing, activate in (("journal published", True, True), ("old rename", True, True), ("new rename", True, True), ("first toggle", True, True),
                                     ("second toggle", True, True), ("before reboot", True, True),
                                     ("after reboot", True, True), ("new rename", False, True), ("new rename", True, False)):
    def crash(point=point, existing=existing, activate=activate):
      before, values = reset(existing)
      core_file = output / "core-params.json"
      core_file.write_text(json.dumps(values))
      entry = serve(publish())
      entry_file = output / "entry.json"
      entry_file.write_text(json.dumps(entry.__dict__))
      env = {**os.environ, "XP_TEST_CORE": str(core_file), "PYTHONDONTWRITEBYTECODE": "1"}
      killed = subprocess.run([sys.executable, "-c", kill_code, str(entry_file), point, str(activate)],
                              cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
      assert killed.returncode == -signal.SIGKILL, killed.stderr
      journals = list(mf.INSTALL_DIR.glob(".staging-*/journal.json"))
      assert len(journals) == 1, "missing durable rollback journal"
      interrupted = tree(mf.INSTALL_DIR)
      (mf.INSTALL_DIR / ".staging-orphan").mkdir()
      (mf.INSTALL_DIR / ".staging-orphan" / "partial").write_text("interrupted download")
      recovered = subprocess.run([sys.executable, "-c", "from openpilot.tesla.store import recover; recover(); " +
                                  "from openpilot.tesla import plugins; print([p.NAME for p in plugins.PLUGINS])"],
                                 cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
      assert recovered.returncode == 0, recovered.stderr
      assert tree(mf.INSTALL_DIR) == before, "startup did not restore byte-identical package/toggles or remove orphan staging"
      assert json.loads(core_file.read_text()) == values, "startup changed core Params"
      return {"checkpoint": point, "exit_code": killed.returncode, "before": before,
              "interrupted": interrupted, "after": tree(mf.INSTALL_DIR), "discovery": recovered.stdout.strip()}
    record(f"SIGKILL: {point}, existing={existing}, activate={activate}", crash)

  def independent_restore():
    before, values = reset()
    core.put_bool("DoReboot", False)
    values["DoReboot"] = False
    entry = serve(publish())
    replacing, put = os.replace, core.put_bool
    rolling_back = False
    attempts = []
    def fail(src, dst):
      if rolling_back:
        attempts.append(str(dst))
        if Path(src).name == "old" or Path(dst).name == "Exts2_rivalOn":
          raise OSError("restore blocked: " + Path(dst).name)
      replacing(src, dst)
    def request(key, value, **kw):
      nonlocal rolling_back
      put(key, value, **kw)
      if key == "DoReboot" and value:
        rolling_back = True
        raise OSError("request failed after write")
    with patch("openpilot.common.params.Params", lambda: core), patch.object(os, "replace", fail), patch.object(core, "put_bool", request):
      error = store.install(entry, activate=True)
    assert "restore blocked: " + PID in error and "restore blocked: Exts2_rivalOn" in error, error
    assert core.get("DoReboot") is False, "file failure skipped reboot restore"
    assert not (mf.INSTALL_DIR / ".params" / NEW_KEY).exists(), "earlier failure skipped another toggle"
    assert list(mf.INSTALL_DIR.glob(".staging-*/journal.json")), "failed rollback lost its retry journal"
    with patch("openpilot.common.params.Params", lambda: core):
      store.recover()
    assert tree(mf.INSTALL_DIR) == before and core.values == values, "retry lost or consumed an original backup"
    return {"error": error, "independent_restore_attempts": attempts}
  record("independent rollback and startup retry", independent_restore)

  for key in ("old", PID):
    def collision(key=key):
      reset()
      path = mf.INSTALL_DIR / ".params" / key
      path.write_bytes(b"0\n")
      before, values = tree(mf.INSTALL_DIR), dict(core.values)
      files = publish()
      m = json.loads(files["plugin.json"])
      m["enable_param"], m["params"][key] = key, m["params"].pop(NEW_KEY)
      files["plugin.json"] = json.dumps(m)
      entry = serve(files)
      def request(name, value, **kw):
        if name == "DoReboot":
          staging = next(mf.INSTALL_DIR.glob(".staging-*"))
          assert (staging / "params" / key).read_bytes() == b"0\n", "backup namespace collision"
          raise OSError("injected reboot failure")
      with patch("openpilot.common.params.Params", lambda: core), patch.object(core, "put_bool", request):
        error = store.install(entry, activate=True)
      assert "injected reboot failure" in error, error
      assert tree(mf.INSTALL_DIR) == before and core.values == values
      return "raw toggle bytes restored; backup under params/" + key
    record("backup name collision: " + key, collision)

  def startup_order():
    manager = ast.parse((ROOT / "openpilot/system/manager/manager.py").read_text())
    recovery = next(i for i, n in enumerate(manager.body) if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
                    and isinstance(n.value.func, ast.Name) and n.value.func.id == "recover")
    discovery = next(i for i, n in enumerate(manager.body) if isinstance(n, ast.ImportFrom)
                     and n.module == "openpilot.system.manager.process_config")
    assert recovery < discovery, "manager imports/discovers plugins before recovery"
    return "startup recovery precedes process_config -> fork_processes -> plugin discovery"
  record("manager recovers before plugin discovery", startup_order)

  def commit_cleanup():
    reset()
    core_file = output / "core-params.json"
    core_file.write_text(json.dumps(core.values))
    entry_file = output / "entry.json"
    entry_file.write_text(json.dumps(serve(publish()).__dict__))
    code = '''
import json, os, shutil, signal, sys
from pathlib import Path
from openpilot.tesla import store, manifest as mf
remove = shutil.rmtree
def cleanup(path, **kw):
  if Path(path).parent == mf.INSTALL_DIR and Path(path).name.startswith('.staging-') and not (Path(path) / 'journal.json').exists():
    os.kill(os.getpid(), signal.SIGKILL)
  remove(path, **kw)
shutil.rmtree = cleanup
error = store.install(store.IndexEntry(**json.loads(Path(sys.argv[1]).read_text())), activate=True)
raise AssertionError(f'commit cleanup not reached: {error}')
'''
    env = {**os.environ, "XP_TEST_CORE": str(core_file), "PYTHONDONTWRITEBYTECODE": "1"}
    killed = subprocess.run([sys.executable, "-c", code, str(entry_file)], cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
    assert killed.returncode == -signal.SIGKILL, killed.stderr
    staging = next(mf.INSTALL_DIR.glob(".staging-*"))
    assert (staging / "old").exists() and not (staging / "journal.json").exists()
    before = {k: v for k, v in tree(mf.INSTALL_DIR).items() if not k.startswith(staging.name + "/")}
    values = core_file.read_bytes()
    recovered = subprocess.run([sys.executable, "-c", "from openpilot.tesla.store import recover; recover()"],
                               cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
    assert recovered.returncode == 0, recovered.stderr
    assert tree(mf.INSTALL_DIR) == before and core_file.read_bytes() == values, "committed install rolled back"
    assert store.installed_versions()[PID] == "2.0.0"
    return "SIGKILL after durable journal deletion: startup removes only orphan backups, preserving committed package/toggles/reboot"
  record("committed install survives cleanup SIGKILL", commit_cleanup)

  reset()
  ParamsView(core).put_bool("Exts2_rivalOn", False)
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
