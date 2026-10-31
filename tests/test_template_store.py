"""离线 ``.apkg`` 必须使用 Anki 里**真实**的笔记类型模板。

背景（用户反馈）：genanki 自建的笔记类型只有 ``{{pic_kong}}``，没有模板里内联的
解密 / 绘制脚本，导入 Anki 后表现为"图片在、挖空矩形没了"，还会在 Anki 里留下
``图片挖空模板sle1_0+`` 这种垃圾类型。

这里验证：Anki 在线时抓快照 → 离线导出用快照（模型 id / 字段 / 模板 / CSS 全对齐）。
"""

from __future__ import annotations

import json
import sqlite3
import zipfile
from pathlib import Path

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QImage

from ankisnip import apkg_export, template_store
from ankisnip.base import annotations as ann
from ankisnip.saver import AnkiSaver
from ankisnip.settings import Settings

REAL_MODEL_ID = 1782473519709          # 与 Anki 中「图片挖空模板sle1_0」一致
REAL_QFMT = '<div id="origin">{{pic_kong}}</div><script>renderKong();</script>'
REAL_AFMT = "{{FrontSide}}<hr id=answer>"
REAL_CSS = ".card { font-size: 20px; }"


class TemplateFakeConnect:
    """带完整笔记类型定义的 AnkiConnect 替身。"""

    def __init__(self, available=True):
        self.available = available
        self.models = ["图片挖空模板sle1_0"]
        self.fields = ["pic_kong", "question", "relation"]
        self.media: dict[str, bytes] = {}
        self.notes: list[dict] = []

    def is_available(self):
        return self.available

    def version(self):
        return 6

    def deck_names(self):
        return ["图片挖空模板sle1_0"]

    def model_names(self):
        return list(self.models)

    def model_field_names(self, model):
        return list(self.fields)

    def find_model_id(self, model):
        return REAL_MODEL_ID

    def model_templates(self, model):
        return {model: {"Front": REAL_QFMT, "Back": REAL_AFMT}}

    def model_styling(self, model):
        return REAL_CSS

    def create_deck(self, deck):
        pass

    def store_media_file(self, filename, data):
        self.media[filename] = data
        return filename

    def delete_media_file(self, filename):
        self.media.pop(filename, None)

    def add_note(self, deck, model, fields, tags=None, allow_duplicate=True):
        self.notes.append({"deck": deck, "model": model, "fields": fields})
        return 2000 + len(self.notes)


@pytest.fixture()
def tmp_store(tmp_path, monkeypatch):
    """把快照目录指到临时目录。"""
    store = tmp_path / "snapshots"
    store.mkdir()
    monkeypatch.setattr(template_store, "store_dir", lambda: store)
    return store


@pytest.fixture()
def settings(tmp_path):
    s = Settings()
    s.offline_dir = str(tmp_path / "offline")
    s.temp_dir = str(tmp_path / "tmp")
    s.deck_name = s.model_name = "图片挖空模板sle1_0"
    s.image_field = s.kong_field = "pic_kong"
    s.question_field = "question"
    s.relation_field = "relation"
    s.field_order = ["pic_kong", "question", "relation"]
    return s


@pytest.fixture()
def scene():
    img = QImage(400, 300, QImage.Format_RGB32)
    img.fill(Qt.white)
    style = ann.Style(QColor("#057fff"), 3, 24)
    mask = ann.MaskAnnotation.from_norm(style, 0.25, 0.25, 0.2, 0.2, 400, 300)
    arrow = ann.ArrowAnnotation(style, QPointF(10, 10))
    arrow.update_to(QPointF(300, 200))
    return img, [mask, arrow]


def test_capture_snapshot_from_anki(tmp_store):
    snap = template_store.capture_from_anki(TemplateFakeConnect(), "图片挖空模板sle1_0")
    assert snap is not None
    assert snap["model_id"] == REAL_MODEL_ID
    assert snap["fields"] == ["pic_kong", "question", "relation"]
    assert snap["templates"]["图片挖空模板sle1_0"]["Front"] == REAL_QFMT
    assert snap["css"] == REAL_CSS
    # 落盘可被下次离线导出读到
    assert template_store.load_snapshot("图片挖空模板sle1_0")["model_id"] == REAL_MODEL_ID


def test_snapshot_is_written_on_successful_ankiconnect_save(tmp_store, settings, scene):
    img, items = scene
    saver = AnkiSaver(settings, TemplateFakeConnect())
    result = saver.save(saver.prepare(img, items), settings.temp_dir)
    assert result.ok and result.mode == "ankiconnect"
    assert template_store.load_snapshot(settings.model_name) is not None


def test_apkg_uses_real_model_definition(tmp_store, settings, scene):
    """离线导出的 apkg 里必须是真实的模型 id / 模板 / CSS。"""
    img, items = scene
    connect = TemplateFakeConnect()
    saver = AnkiSaver(settings, connect)
    # 先在线保存一次，拿到快照
    saver.save(saver.prepare(img, items), settings.temp_dir)

    offline = AnkiSaver(settings, TemplateFakeConnect(available=False))
    prepared = offline.prepare(img, items)
    result = offline.save(prepared, settings.temp_dir)
    assert result.ok and result.mode == "apkg"
    assert "真实模板" in result.message

    apkg = Path(result.apkg_path)
    with zipfile.ZipFile(apkg) as zf:
        tmpdir = apkg.parent / "_unz"
        zf.extractall(tmpdir)
    con = sqlite3.connect(tmpdir / "collection.anki2")
    (raw,) = con.execute("select models from col").fetchone()
    model = list(json.loads(raw).values())[0]

    assert int(list(json.loads(raw).keys())[0]) == REAL_MODEL_ID
    assert [f["name"] for f in model["flds"]] == ["pic_kong", "question", "relation"]
    assert model["css"] == REAL_CSS
    assert model["tmpls"][0]["qfmt"] == REAL_QFMT
    assert model["tmpls"][0]["afmt"] == REAL_AFMT


def test_apkg_without_snapshot_warns(tmp_store, settings, scene, tmp_path):
    """从没连过 Anki 时导出也要成功，但必须明确警告挖空不会显示。"""
    img, items = scene
    saver = AnkiSaver(settings, TemplateFakeConnect(available=False))
    result = saver.save(saver.prepare(img, items), settings.temp_dir)
    assert result.ok and result.mode == "apkg"
    assert "挖空不会显示" in result.message

    with zipfile.ZipFile(result.apkg_path) as zf:
        tmpdir = tmp_path / "_unz2"
        zf.extractall(tmpdir)
    con = sqlite3.connect(tmpdir / "collection.anki2")
    (raw,) = con.execute("select models from col").fetchone()
    assert int(list(json.loads(raw).keys())[0]) == apkg_export.FALLBACK_MODEL_ID


def test_offline_dir_is_honoured(tmp_store, settings, scene, tmp_path):
    """设置的离线目录必须生效（历史上被单测写脏过）。"""
    target = tmp_path / "我的导出目录"
    settings.offline_dir = str(target)
    img, items = scene
    saver = AnkiSaver(settings, TemplateFakeConnect(available=False))
    result = saver.save(saver.prepare(img, items), settings.temp_dir)
    assert result.ok
    assert Path(result.apkg_path).parent == target


def test_settings_do_not_leak_to_real_user_scope():
    """回归：单测里的 Settings 不能写进用户真实配置。"""
    s = Settings()
    s.offline_dir = "/绝对不该出现在用户配置里/xyz"
    assert Settings().offline_dir == "/绝对不该出现在用户配置里/xyz"
    from PySide6.QtCore import QSettings

    real = QSettings("ankisnip", "anki-screen")
    if real.contains("offline_dir"):
        assert "xyz" not in str(real.value("offline_dir"))
