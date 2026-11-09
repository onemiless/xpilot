"""Zero-timeout updates must never enter the native poll sleep; timed updates keep poll semantics."""

import ast
import sys
from pathlib import Path
from types import SimpleNamespace

path = (
    Path(sys.argv[1])
    if len(sys.argv) > 1
    else Path(__file__).resolve().parents[2] / "openpilot/cereal/messaging/__init__.py"
)
t = ast.parse(path.read_text())
cls = next(n for n in t.body if isinstance(n, ast.ClassDef) and n.name == "SubMaster")
f = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "update")
f.returns = None
for a in f.args.args:
    a.annotation = None
code = compile(
    ast.fix_missing_locations(ast.Module(body=[f], type_ignores=[])), str(path), "exec"
)
for timeout in [0, 25]:
    calls = []

    def poll(ms, calls=calls):
        calls.append(ms)
        assert ms > 0, "zero-timeout update must not enter sleeping native poll"
        return ["a"]

    obj = SimpleNamespace(
        sock={"a": "a", "b": "b"},
        poller=SimpleNamespace(poll=poll),
        non_polled_services=["b"],
        update_msgs=lambda now, msgs, calls=calls: calls.append(msgs),
    )
    env = {
        "recv_one_or_none": lambda sock: sock,
        "time": SimpleNamespace(monotonic=lambda: 1),
    }
    exec(code, env)  # Execute the production method with isolated socket substitutes.
    env["update"](obj, timeout)
    assert calls == ([["a", "b"]] if timeout == 0 else [25, ["a", "b"]]), calls
print("PASS nonblocking and timed update semantics")
