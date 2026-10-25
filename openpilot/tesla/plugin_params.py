"""Params for downloaded plugins. Their keys are not compiled into Params (params_keys.h), so a ParamsView keeps them
as files in <INSTALL_DIR>/.params/<key> and passes every other key to the real Params unchanged. The host wraps the
params it hands to plugins, settings pages and enable checks, so plugin code uses the usual get/get_bool/put API.
Supported types for downloaded plugins: STORE_TYPES (flags are ignored: values persist until the plugin changes them)."""
import json
import os
import tempfile

from openpilot.tesla import manifest as mf

STORE_TYPES = {"BOOL", "INT", "FLOAT", "STRING", "JSON"}


def installed_specs() -> dict[str, dict]:
  """key -> params spec of every installed plugin with a valid manifest."""
  specs: dict[str, dict] = {}
  for m in mf.load_all(mf.INSTALL_DIR)[0]:
    specs.update(m.params)
  return specs


def _decode(kind: str, raw: str):
  if kind == "BOOL":
    return raw.strip() in ("1", "true", "True")
  if kind == "INT":
    return int(raw)
  if kind == "FLOAT":
    return float(raw)
  if kind == "JSON":
    return json.loads(raw)
  return raw


def _encode(kind: str, value) -> str:
  if kind == "BOOL":
    return "1" if value else "0"
  if kind == "JSON":
    return json.dumps(value)
  return str(value)


class ParamsView:
  def __init__(self, params, specs: dict | None = None):
    self._p = params._p if isinstance(params, ParamsView) else params
    self._specs = installed_specs() if specs is None else specs

  def __getattr__(self, name):
    return getattr(self._p, name)

  @staticmethod
  def _kw(block: bool, **kw) -> dict:
    """Pass block only when set, so param-like objects without it still work."""
    return {**kw, "block": True} if block else kw

  def _path(self, key: str):
    return mf.INSTALL_DIR / ".params" / key

  def get(self, key, block=False, return_default=False):
    if key not in self._specs:
      return self._p.get(key, **self._kw(block, return_default=return_default))
    spec = self._specs[key]
    try:
      raw = self._path(key).read_text()
    except FileNotFoundError:
      if not return_default or "default" not in spec:
        return None
      raw = str(spec["default"])
    try:
      return _decode(spec["type"], raw)
    except ValueError:
      return None

  def get_bool(self, key, block=False):
    if key not in self._specs:
      return self._p.get_bool(key, **self._kw(block))
    return bool(self.get(key, return_default=True))

  def put(self, key, value, block=False):
    if key not in self._specs:
      return self._p.put(key, value, **self._kw(block))
    path = self._path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{key}.")
    try:
      with os.fdopen(fd, "w") as f:
        f.write(_encode(self._specs[key]["type"], value))
        f.flush()
        os.fsync(f.fileno())
      os.replace(tmp, path)
    finally:
      if os.path.exists(tmp):
        os.unlink(tmp)

  def put_bool(self, key, value, block=False):
    if key not in self._specs:
      return self._p.put_bool(key, value, **self._kw(block))
    self.put(key, bool(value))

  def remove(self, key):
    if key not in self._specs:
      return self._p.remove(key)
    self._path(key).unlink(missing_ok=True)
