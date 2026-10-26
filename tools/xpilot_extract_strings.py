#!/usr/bin/env python3
"""Append Tesla UI tr()/trn()/tr_noop() strings to the English PO template."""
import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "openpilot/tesla/ui"
PO = ROOT / "openpilot/selfdrive/ui/translations/app_en.po"


def existing_msgids(text: str) -> set[str]:
  lines = text.splitlines()
  out = set()
  for i, line in enumerate(lines):
    if not line.startswith("msgid "):
      continue
    value = ast.literal_eval(line[6:])
    i += 1
    while i < len(lines) and lines[i].startswith('"'):
      value += ast.literal_eval(lines[i])
      i += 1
    if value:
      out.add(value)
  return out


def strings() -> dict[str, tuple[str | None, set[str]]]:
  out: dict[str, tuple[str | None, set[str]]] = {}
  for path in sorted(UI.rglob("*.py")):
    for node in ast.walk(ast.parse(path.read_text())):
      if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or node.func.id not in ("tr", "trn", "tr_noop"):
        continue
      if not node.args or not isinstance(node.args[0], ast.Constant) or not isinstance(node.args[0].value, str):
        continue
      singular = node.args[0].value
      plural = node.args[1].value if node.func.id == "trn" and len(node.args) > 1 and isinstance(node.args[1], ast.Constant) else None
      old_plural, refs = out.setdefault(singular, (plural, set()))
      if old_plural != plural:
        raise ValueError(f"inconsistent plural for {singular!r}")
      refs.add(f"{path.relative_to(ROOT)}:{node.lineno}")
  return out


def main() -> None:
  text = PO.read_text()
  known = existing_msgids(text)
  blocks = []
  for singular, (plural, refs) in sorted(strings().items()):
    if singular in known:
      continue
    block = [f"#: {' '.join(sorted(refs))}", f"msgid {json.dumps(singular, ensure_ascii=False)}"]
    if plural is None:
      block.append(f"msgstr {json.dumps(singular, ensure_ascii=False)}")
    else:
      block += [f"msgid_plural {json.dumps(plural, ensure_ascii=False)}",
                f"msgstr[0] {json.dumps(singular, ensure_ascii=False)}",
                f"msgstr[1] {json.dumps(plural, ensure_ascii=False)}"]
    blocks.append("\n".join(block))
  PO.write_text(text.rstrip() + ("\n\n" + "\n\n".join(blocks) if blocks else "") + "\n")


if __name__ == "__main__":
  main()
