"""保存链路测试（不依赖真实的 Anki）。"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QImage

from ankisnip.anki_connect import AnkiConnectError
from ankisnip.base import annotations as ann
from ankisnip.saver import AnkiSaver
from ankisnip.settings import Settings


class FakeConnect:
    """最小 AnkiConnect 替身。"""

    def __init__(self, available=True, decks=None, models=None, fields=None,
                 fail_on_add=False):
        self.available = available
        self.decks = decks if decks is not None else ["图片挖空模板sle1_0"]
        self.models = models if models is not None else ["图片挖空模板sle1_0"]
        self.fields = fields if fields is not None else ["pic_kong", "question", "relation"]
        self.fail_on_add = fail_on_add
        self.media: dict[str, bytes] = {}
        self.notes: list[dict] = []

    # -- 探测 / 查询 -------------------------------------------------------
    def is_available(self):
        return self.available

    def version(self):
        return 6

    def deck_names(self):
        return list(self.decks)

    def model_names(self):
        return list(self.models)

    def model_field_names(self, model):
        if model not in self.models:
            raise AnkiConnectError(f"no such model {model}")
        return list(self.fields)

    def create_deck(self, deck):
        if deck not in self.decks:
            self.decks.append(deck)

    # -- 媒体 / 卡片 -------------------------------------------------------
    def store_media_file(self, filename, data):
        self.media[filename] = data
        return filename

    def delete_media_file(self, filename):
        self.media.pop(filename, None)

    def add_note(self, deck, model, fields, tags=None, allow_duplicate=True):
        if self.fail_on_add:
            raise AnkiConnectError("addNote 失败（模拟）")
        self.notes.append({"deck": deck, "model": model, "fields": fields})
        return 1000 + len(self.notes)


@pytest.fixture()
def settings(tmp_path):
    """每次都把字段映射显式写成确定值，避免用例之间互相污染。"""
    s = Settings()
    s.offline_dir = str(tmp_path / "offline")
    s.temp_dir = str(tmp_path / "tmp")
    s.deck_name = "图片挖空模板sle1_0"
    s.model_name = "图片挖空模板sle1_0"
    s.image_field = "pic_kong"
    s.kong_field = "pic_kong"
    s.question_field = "question"
    s.relation_field = "relation"
    s.field_order = ["pic_kong", "question", "relation"]
    return s


@pytest.fixture()
def scene():
    img = QImage(400, 300, QImage.Format_RGB32)
    img.fill(Qt.white)
    style = ann.Style(QColor("#FF0000"), 3, 24)
    mask = ann.MaskAnnotation.from_norm(style, 0.25, 0.25, 0.2, 0.2, 400, 300)
    arrow = ann.ArrowAnnotation(style, QPointF(10, 10))
    arrow.update_to(QPointF(300, 200))
    text = ann.TextAnnotation(style, QPointF(20, 250), "重点")
    return img, [mask, arrow, text]


def test_prepare_splits_and_encodes(settings, scene):
    img, items = scene
    saver = AnkiSaver(settings, FakeConnect())
    prepared = saver.prepare(img, items, question="题面", relation="关联")

    assert prepared.mask_count == 1
    assert prepared.baked_count == 2
    assert prepared.baked_image.size() == img.size()   # 原图分辨率
    assert prepared.png_bytes[:8] == b"\x89PNG\r\n\x1a\n"
    assert prepared.kong.count(";") == 1 and prepared.kong.endswith(";")
    html = prepared.fields["pic_kong"]
    assert html.startswith('<img src="ankisnip_')
    assert 'kong="' in html
    assert prepared.fields["question"] == "题面"
    assert prepared.fields["relation"] == "关联"


def test_save_via_ankiconnect(settings, scene):
    img, items = scene
    connect = FakeConnect()
    saver = AnkiSaver(settings, connect)
    prepared = saver.prepare(img, items)
    result = saver.save(prepared, settings.temp_dir)
    assert result.ok and result.mode == "ankiconnect"
    assert len(connect.notes) == 1
    assert len(connect.media) == 1
    assert "1 个挖空" in result.message


def test_save_creates_missing_deck(settings, scene):
    img, items = scene
    settings.deck_name = "不存在的牌组"
    connect = FakeConnect(decks=["别的牌组"])
    saver = AnkiSaver(settings, connect)
    prepared = saver.prepare(img, items)
    result = saver.save(prepared, settings.temp_dir)
    assert result.ok
    assert "不存在的牌组" in connect.decks


def test_add_note_failure_cleans_media(settings, scene):
    img, items = scene
    connect = FakeConnect(fail_on_add=True)
    saver = AnkiSaver(settings, connect)
    prepared = saver.prepare(img, items)
    result = saver.save(prepared, settings.temp_dir)
    assert not result.ok
    assert connect.media == {}      # 孤儿媒体已清理
    assert connect.notes == []


def test_missing_model_gives_actionable_error(settings, scene):
    img, items = scene
    settings.model_name = "没有这个类型"
    saver = AnkiSaver(settings, FakeConnect())
    prepared = saver.prepare(img, items)
    result = saver.save(prepared, settings.temp_dir)
    assert not result.ok
    assert "不存在笔记类型" in result.message


def test_missing_field_gives_actionable_error(settings, scene):
    img, items = scene
    settings.image_field = "pic_kong"
    settings.question_field = "没有的字段"
    saver = AnkiSaver(settings, FakeConnect())
    prepared = saver.prepare(img, items)
    result = saver.save(prepared, settings.temp_dir)
    assert not result.ok
    assert "缺少字段" in result.message


def test_offline_apkg_fallback(settings, scene, tmp_path):
    img, items = scene
    saver = AnkiSaver(settings, FakeConnect(available=False))
    prepared = saver.prepare(img, items)
    result = saver.save(prepared, settings.temp_dir)
    assert result.ok and result.mode == "apkg"
    apkg = Path(result.apkg_path)
    assert apkg.is_file()
    with zipfile.ZipFile(apkg) as zf:
        names = zf.namelist()
        assert "collection.anki2" in names
        # 图片必须作为媒体打进包里（0/1/2… 是 genanki 重命名后的媒体）
        assert any(n.isdigit() for n in names)
    # 临时图片应已删除
    assert not (Path(settings.temp_dir) / prepared.media_name).exists()


def test_offline_saves_accumulate_into_one_pack(settings, scene):
    """连着保存两张：必须落在**同一个**牌包里，而不是两个文件。"""
    import sqlite3

    saver = AnkiSaver(settings, FakeConnect(available=False))
    paths = []
    for i in (1, 2):
        img, items = scene
        prepared = saver.prepare(img, items, question=f"第{i}题")
        result = saver.save(prepared, settings.temp_dir)
        assert result.ok, result.message
        paths.append(Path(result.apkg_path))
        if i == 2:
            assert "累计 2 张" in result.message

    assert paths[0] == paths[1], "两次离线保存应当写同一个牌包文件"
    assert len(list(Path(settings.offline_dir).glob("*.apkg"))) == 1

    with zipfile.ZipFile(paths[1]) as zf:
        blob = zf.read("collection.anki2")
    tmp_db = Path(settings.temp_dir) / "check.anki2"
    (Path(settings.temp_dir)).mkdir(parents=True, exist_ok=True)
    tmp_db.write_bytes(blob)
    con = sqlite3.connect(str(tmp_db))
    count = con.execute("select count(*) from notes").fetchone()[0]
    con.close()
    assert count == 2, "牌包里应该同时有两张卡片"
