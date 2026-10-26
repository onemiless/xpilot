"""Language-pack integration failures: discovery, PO loading, fallback for missing strings, and disabled packs."""
from pathlib import Path

from openpilot.system.ui.lib import multilang as ml
from openpilot.tesla import manifest, plugins


class FakeParams:
  values = {}

  def get(self, key):
    return self.values.get(key)

  def get_bool(self, key):
    return bool(self.values.get(key))

  def put(self, key, value, block=False):
    self.values[key] = value


class Chinese:
  NAME = "chinese"
  CLAIMS = ("lang:zh-CHS",)


def test_store_language_pack(monkeypatch, tmp_path: Path):
  po = tmp_path / Chinese.NAME / "translations" / "app_zh-CHS.po"
  po.parent.mkdir(parents=True)
  po.write_text('msgid "Hello"\nmsgstr "你好"\n\nmsgid "Language"\nmsgstr "语言"\n')
  FakeParams.values = {}
  monkeypatch.setattr(ml, "Params", FakeParams)
  monkeypatch.setattr(manifest, "INSTALL_DIR", tmp_path)
  monkeypatch.setattr(plugins, "active", lambda params: [Chinese])
  assert manifest.resolve([("preferred", 10, Chinese.CLAIMS), ("other", 20, Chinese.CLAIMS)], {"preferred", "other"}) == \
         ({"preferred"}, {"other": "preferred"})

  language = ml.Multilang()
  assert language.languages["中文（简体）"] == "zh-CHS"
  language.change_language("zh-CHS")
  assert language.tr("Hello") == "你好"
  assert language.tr("Missing") == "Missing"

  monkeypatch.setattr(plugins, "active", lambda params: [])
  disabled = ml.Multilang()
  assert disabled.language == "en"
  assert "zh-CHS" not in disabled.codes
