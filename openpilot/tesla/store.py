"""Plugin store client: read the plugin repository index, install, update and remove downloaded plugins.

Repository (https://github.com/onemiless/xpilot-plugins): plugins/<id>/ sources, dist/<id>-<version>.tar.gz built
reproducibly by its tools/build_index.py, and index.json:
  {"api": 1, "plugins": [{"id", "name", "version", "api", "category", "author", "description", "url", "sha256"}]}
Third parties send pull requests; the maintainer reviews and merges. Archives are not signed (yet): the sha256 only
guards against corrupt or swapped downloads, trust comes from the reviewed repository.

Install: download (size-capped), check sha256, extract regular files only into a staging directory next to the
target, validate the manifest (id, api, entry module, no built-in id, no clashing or unsupported param keys, can.tx
and runtime rules supported by the firmware ABI) and the code
layout (manifest.layout_problems), then
swap it into INSTALL_DIR/<id> with renames. Any failure leaves the installed version as it was. Changes apply after
a restart (plugins are discovered at process start). Call only while offroad.
"""
from dataclasses import dataclass
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request

from openpilot.common.utils import atomic_write
from openpilot.tesla import host_api, manifest as mf
from openpilot.tesla.plugin_params import ParamsView, STORE_TYPES

INDEX_URL = "https://raw.githubusercontent.com/onemiless/xpilot-plugins/main/index.json"
# First boot: index defaults installed once; a removed default stays removed.
DEFAULTS_DONE = ".defaults_done"
MAX_ARCHIVE = 20 * 1024 * 1024
TIMEOUT_S = 15


@dataclass(frozen=True)
class IndexEntry:
  id: str
  name: str
  version: str
  api: int
  category: str
  author: str
  description: str
  url: str
  sha256: str
  platform: dict | None = None  # None: pure Python; else must equal manifest.host_platform()
  installable: bool = False  # api and platform match this host and the id is not a built-in plugin
  use: str | None = None
  default: bool = False


def _get(url: str, limit: int) -> bytes:
  with urllib.request.urlopen(url, timeout=TIMEOUT_S) as r:
    data = r.read(limit + 1)
  if len(data) > limit:
    raise ValueError(f"download over {limit} bytes")
  return data


def builtin_ids() -> set[str]:
  return {p.parent.name for p in mf.PLUGINS_DIR.glob("*/plugin.json")}


def fetch_index(url: str = INDEX_URL) -> tuple[list[IndexEntry], str | None]:
  """(entries, error). Malformed entries are skipped; an unreachable or broken index returns ([], reason)."""
  try:
    doc = json.loads(_get(url, 2 * 1024 * 1024))
  except Exception as e:
    return [], f"index: {e}"
  entries, builtin = [], builtin_ids()
  for raw in doc.get("plugins", []) if isinstance(doc, dict) else []:
    try:
      e = IndexEntry(**{f: raw[f] for f in ("id", "name", "version", "api", "category", "author", "description", "url", "sha256")},
                     platform=raw.get("platform"), use=raw.get("use"), default=raw.get("default") is True)
      # Same main category as a loaded plugin: from claims when the index carries them, else the old field mapped.
      claims = raw.get("claims")
      category = mf.category_of(claims) if isinstance(claims, list) else mf.LEGACY_CATEGORIES.get(e.category, "data")
      e = IndexEntry(**{**e.__dict__, "category": category})
    except (KeyError, TypeError):
      continue
    ok = e.api == mf.API_VERSION and e.id not in builtin and mf.platform_ok(e.platform)
    entries.append(IndexEntry(**{**e.__dict__, "installable": ok}))
  return entries, None


def installed_versions() -> dict[str, str]:
  return {m.id: m.version for m in mf.load_all(mf.INSTALL_DIR)[0]}


def _extract(data: bytes, pid: str, dest: Path) -> None:
  """Regular files and directories under <pid>/ only; anything else (links, devices, ../, absolute) is refused."""
  with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
    for member in tar.getmembers():
      path = PurePosixPath(member.name)
      if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != pid:
        raise ValueError(f"unsafe path {member.name!r}")
      if not (member.isfile() or member.isdir()):
        raise ValueError(f"not a regular file: {member.name!r}")
      target = dest.joinpath(*path.parts[1:])
      if member.isdir():
        target.mkdir(parents=True, exist_ok=True)
        continue
      target.parent.mkdir(parents=True, exist_ok=True)
      src = tar.extractfile(member)
      target.write_bytes(src.read() if src else b"")


def _validate(staged: Path, entry: IndexEntry) -> None:
  m = mf.parse(staged / "plugin.json")  # id == directory name, api, category, entry, claims
  if m.id != entry.id:
    raise ValueError(f"manifest id {m.id!r} != index id {entry.id!r}")
  if m.id in builtin_ids():
    raise ValueError("id used by a built-in plugin")
  ok, reason = mf.compatible(m, mf.HOST_CAPS)
  if not ok:
    raise ValueError(reason)
  module = m.entry.split(":")[0]
  for f in ("__init__.py", f"{module}.py"):
    if not (staged / f).is_file():
      raise ValueError(f"missing {f}")
  if problems := mf.layout_problems(staged, module):
    raise ValueError("layout: " + "; ".join(problems))
  if (ui := staged / "ui.py").exists() and (problems := mf.tile_problems(ui, m.use)):
    raise ValueError("tiles: " + "; ".join(problems))
  taken = mf.compiled_param_keys() | {k for o in mf.load_all(mf.INSTALL_DIR)[0] if o.id != m.id for k in o.params}
  for key, spec in m.params.items():
    if key in taken:
      raise ValueError(f"param {key} already exists")
    if not key.isidentifier() or spec.get("type") not in STORE_TYPES:
      raise ValueError(f"param {key}: unsupported type {spec.get('type')!r}")


def plugin_state(pid: str, version: str) -> str:
  """Only the plugin can report runtime readiness; package presence is merely Installed."""
  try:
    state = json.loads((mf.INSTALL_DIR / ".data" / pid / "status.json").read_text())
    age = time.time() - state["updated_at"]  # noqa: TID251 (persisted status must expire across boots)
    if state["version"] == version and 0 <= age <= 5 and \
        state["state"] in ("Local ready", "Phone warming", "Phone active", "Local fallback"):
      return state["state"]
  except (OSError, ValueError, KeyError, TypeError):
    pass
  return "Installed"


def _sync(path: Path) -> None:
  fd = os.open(path, os.O_RDONLY)
  try:
    os.fsync(fd)
  finally:
    os.close(fd)


def _write_journal(staging: Path, doc: dict) -> None:
  with atomic_write(str(staging / "journal.json"), overwrite=True) as f:
    json.dump(doc, f)
    f.flush()
    os.fsync(f.fileno())
  _sync(staging)
  _sync(mf.INSTALL_DIR)


def _finish(staging: Path) -> None:
  # Journal removal is the commit point; keep undo data until its deletion is durable.
  journal = staging / "journal.json"
  undo = json.loads(journal.read_text()) if journal.exists() else None
  journal.unlink(missing_ok=True)
  try:
    _sync(staging)
  except OSError:
    if undo is not None:
      _write_journal(staging, undo)
    raise
  shutil.rmtree(staging, ignore_errors=True)


def _rollback(staging: Path, params) -> list[str]:
  doc = json.loads((staging / "journal.json").read_text())
  pid = doc["id"]
  if not isinstance(pid, str) or not pid or pid.startswith(".") or Path(pid).name != pid or type(doc["old"]) is not bool or \
      any(not k.isidentifier() or k in mf.compiled_param_keys() or type(v) is not bool for k, v in doc["params"].items()) or \
      ("reboot" in doc and doc["reboot"] is not None and type(doc["reboot"]) is not bool):
    raise ValueError("invalid store journal")
  target, old = mf.INSTALL_DIR / pid, staging / "old"
  errors = []
  try:
    if old.exists() or not doc["old"]:
      if target.exists():
        shutil.rmtree(target)
      if old.exists():
        os.replace(old, target)
    _sync(mf.INSTALL_DIR)
    _sync(staging)
  except Exception as e:
    errors.append(f"files: {e}")
  for key, existed in doc["params"].items():
    try:
      path = mf.INSTALL_DIR / ".params" / key
      if existed:
        with atomic_write(str(path), "wb", overwrite=True) as f:
          f.write((staging / "params" / key).read_bytes())
          f.flush()
          os.fsync(f.fileno())
      else:
        path.unlink(missing_ok=True)
      if path.parent.exists():
        _sync(path.parent)
    except Exception as e:
      errors.append(f"{key}: {e}")
  if "reboot" in doc:
    try:
      if doc["reboot"] is None:
        params.remove("DoReboot")
      else:
        params.put_bool("DoReboot", doc["reboot"], block=True)
    except Exception as e:
      errors.append(f"DoReboot: {e}")
  return errors


def recover() -> None:
  """Manager startup, before plugin discovery. Incomplete installs roll back; completed/orphan staging is garbage."""
  errors = []
  for staging in sorted(mf.INSTALL_DIR.glob(".staging-*")):
    try:
      if (staging / "journal.json").exists():
        from openpilot.common.params import Params
        if failed := _rollback(staging, Params()):
          raise OSError("; ".join(failed))
      _finish(staging)
    except Exception as e:
      errors.append(f"{staging}: {e}")
  if errors:
    raise RuntimeError("store recovery failed: " + "; ".join(errors))


def install(entry: IndexEntry, *, activate: bool = False, offroad=None) -> str | None:
  """None on success, else the reason. The previous version stays in place on any failure.
  If restoration also fails, retain the backup and return its path."""
  from openpilot.common.params import Params
  params = Params()
  def parked():
    return host_api.parked(params) and (offroad is None or offroad())
  if not parked():
    return "park first"
  if not entry.installable:
    return f"not installable (api {entry.api} vs {mf.API_VERSION}, platform {entry.platform}, or a built-in id)"
  mf.INSTALL_DIR.mkdir(parents=True, exist_ok=True)
  staging = Path(tempfile.mkdtemp(prefix=f".staging-{entry.id}-", dir=mf.INSTALL_DIR))
  keep_backup = False
  target, old = mf.INSTALL_DIR / entry.id, staging / "old"
  try:
    data = _get(entry.url, MAX_ARCHIVE)
    if hashlib.sha256(data).hexdigest() != entry.sha256.lower():
      raise ValueError("sha256 mismatch")
    staged = staging / "new" / entry.id
    staged.mkdir(parents=True)
    _extract(data, entry.id, staged)
    _validate(staged, entry)
    journal = {"id": entry.id, "old": target.exists(), "params": {}}
    if activate:
      before = mf.load_all(mf.PLUGINS_DIR)[0] + mf.load_all(mf.INSTALL_DIR)[0]
      view = ParamsView(params)
      enabled = {o.id for o in before if o.enable_param is None or view.get_bool(o.enable_param)}
      active, _ = mf.resolve([(o.id, o.priority, o.claims) for o in before], enabled)
      before_claims = {c for o in before if o.id in active for c in o.claims}
      m = mf.parse(staged / "plugin.json")
      if m.enable_param is not None and m.params.get(m.enable_param, {}).get("type") != "BOOL":
        raise ValueError("enable_param must name the package's own BOOL")
      manifests = [o for o in before if o.id != m.id] + [m]
      conflicts = mf.conflicting(m.id, [(o.id, o.priority, o.claims) for o in manifests])
      (staging / "params").mkdir()
      for o in manifests:
        if o.enable_param is None or o.id not in {m.id, *conflicts}:
          continue
        key = o.enable_param
        if key in mf.compiled_param_keys() or o.params.get(key, {}).get("type") != "BOOL":
          raise ValueError(f"activation toggle {key!r} must be a plugin BOOL")
        path = mf.INSTALL_DIR / ".params" / key
        journal["params"][key] = path.exists()
        if path.exists():
          shutil.copy2(path, staging / "params" / key)
      journal["reboot"] = params.get("DoReboot")
    for path in sorted(staging.rglob("*"), reverse=True):
      _sync(path)
    _write_journal(staging, journal)
    if not parked():
      raise ValueError("park first")
    if target.exists():
      os.replace(target, old)
      _sync(mf.INSTALL_DIR)
      _sync(staging)
    os.replace(staged, target)
    _sync(staged.parent)
    _sync(mf.INSTALL_DIR)
    if activate:
      from openpilot.tesla.ui.offroad import REQUIRED_ROLES, switch_changes
      m = mf.parse(target / "plugin.json")
      # Probe in a fresh interpreter: importing a new package must not replace the running host's modules.
      code = """
import sys
from openpilot.tesla import plugins
name = sys.argv[1]
if name in plugins.LOAD_ERRORS:
  raise ValueError(plugins.LOAD_ERRORS[name])
cls = next(p for p in plugins.PLUGINS if p.NAME == name)
if 'role:perception.model' in cls.CLAIMS and cls.prepare_install() is not True:
  raise ValueError('local model not ready')
reserved = {name for names in plugins.ROLE_PROCESSES.values() for name in names}
for proc in cls.processes():
  if f'process:{proc.name}' not in cls.CLAIMS or proc.name in reserved:
    raise ValueError(f'unusable process {proc.name}')
"""
      result = subprocess.run([sys.executable, "-c", code, m.id], cwd=Path(__file__).resolve().parents[2],
                              env={**os.environ, "XPILOT_PLUGINS_DIR": str(mf.INSTALL_DIR), "PYTHONDONTWRITEBYTECODE": "1"},
                              capture_output=True, text=True, timeout=30)
      if result.returncode:
        raise ValueError("entry/local preparation: " + (result.stderr.strip() or f"probe exited {result.returncode}").splitlines()[-1])
      manifests = mf.load_all(mf.PLUGINS_DIR)[0] + mf.load_all(mf.INSTALL_DIR)[0]
      view = ParamsView(params, {key: spec for o in manifests for key, spec in o.params.items()})
      enabled = {o.id for o in manifests if o.enable_param is None or view.get_bool(o.enable_param)}
      items = [(o.id, o.priority, o.claims, o.enable_param) for o in manifests]
      changes, error = switch_changes(m.id, True, items, enabled)
      if error:
        raise ValueError(error)
      remaining = {o.id for o in manifests if o.enable_param is None or changes.get(o.enable_param, o.id in enabled)}
      active, _ = mf.resolve([(o.id, o.priority, o.claims) for o in manifests], remaining)
      if m.id not in active:
        raise ValueError("selected plugin loses an exclusive claim")
      after_claims = {c for o in manifests if o.id in active for c in o.claims}
      for role in REQUIRED_ROLES:
        if role in before_claims and role not in after_claims:
          raise ValueError(f"required role {role}")
      for key, value in changes.items():
        view.put_bool(key, value, block=True)
      if not parked():
        raise ValueError("park first")
      params.put_bool("DoReboot", True, block=True)
      if (mf.INSTALL_DIR / ".params").exists():
        _sync(mf.INSTALL_DIR / ".params")
    for path in sorted(target.rglob("*"), reverse=True):
      _sync(path)
    _sync(target)
    _sync(mf.INSTALL_DIR)
    _sync(staging)
    _finish(staging)
    return None
  except Exception as e:
    try:
      if (staging / "journal.json").exists():
        if errors := _rollback(staging, params):
          raise OSError("; ".join(errors))
        _finish(staging)
    except Exception as restore_error:
      keep_backup = True
      return f"{e}; restore failed: {restore_error}; backup: {old}; transaction: {staging}"
    return str(e) or repr(e)
  finally:
    if not keep_backup:
      shutil.rmtree(staging, ignore_errors=True)


def uninstall(pid: str) -> str | None:
  """Remove an installed plugin (its stored param values stay, so a reinstall keeps its settings)."""
  from openpilot.common.params import Params
  if not Params().get_bool("IsOffroad"):
    return "park first"
  target = mf.INSTALL_DIR / pid
  if pid and "/" not in pid and target.is_dir():
    from openpilot.tesla.plugins import REQUIRED_ROLES
    manifests = mf.load_all(mf.PLUGINS_DIR)[0] + mf.load_all(mf.INSTALL_DIR)[0]
    for role in REQUIRED_ROLES:
      if any(m.id == pid and role in m.claims for m in manifests) and not any(m.id != pid and role in m.claims for m in manifests):
        return f"required role {role}"
    trash = Path(tempfile.mkdtemp(prefix=f".removing-{pid}-", dir=mf.INSTALL_DIR))
    os.replace(target, trash / pid)
    shutil.rmtree(trash, ignore_errors=True)


def defaults_done() -> bool:
  return (mf.INSTALL_DIR / DEFAULTS_DONE).exists()


def install_defaults() -> bool:
  """Install the missing index defaults and set the marker; True if any got installed. Unreachable index: no marker, retry."""
  entries, error = fetch_index(INDEX_URL)
  if error or not any(e.default for e in entries):
    return False
  have = installed_versions()
  done = [e.id for e in entries if e.default and e.id not in have and install(e) is None]
  mf.INSTALL_DIR.mkdir(parents=True, exist_ok=True)
  (mf.INSTALL_DIR / DEFAULTS_DONE).touch()
  return bool(done)
