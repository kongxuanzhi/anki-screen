"""从 .apkg 提取笔记类型模板。"""

from __future__ import annotations

import json
import sqlite3
import zipfile

import pytest

from ankisnip import apkg_template


def _make_apkg(tmp_path):
    db = tmp_path / "collection.anki2"
    connection = sqlite3.connect(db)
    connection.execute("create table col (models text)")
    models = {
        "123": {
            "id": 123,
            "name": "图片挖空模板sle1_0",
            "flds": [{"name": "pic_kong"}, {"name": "question"}, {"name": "relation"}],
            "tmpls": [{
                "name": "Card 1",
                "qfmt": "{{pic_kong}} {{question}} {{relation}}",
                "afmt": "{{FrontSide}}",
            }],
            "css": "body {}",
        }
    }
    connection.execute("insert into col values (?)", (json.dumps(models),))
    connection.commit()
    connection.close()

    apkg = tmp_path / "template.apkg"
    with zipfile.ZipFile(apkg, "w") as archive:
        archive.write(db, "collection.anki2")
    return apkg


def test_read_models_and_auto_mapping(tmp_path):
    model = apkg_template.read_models(_make_apkg(tmp_path))[0]

    assert model.name == "图片挖空模板sle1_0"
    assert model.model_id == 123
    assert model.fields == ["pic_kong", "question", "relation"]
    assert "{{pic_kong}}" in model.templates["Card 1"]["Front"]
    assert model.css == "body {}"

    mapping = apkg_template.field_mapping(model)
    assert mapping["image_field"] == "pic_kong"
    assert mapping["kong_field"] == "pic_kong"
    assert mapping["question_field"] == "question"
    assert mapping["relation_field"] == "relation"
    assert mapping["field_order"] == ["pic_kong", "question", "relation"]


def test_missing_collection_raises(tmp_path):
    apkg = tmp_path / "new-format.apkg"
    with zipfile.ZipFile(apkg, "w") as archive:
        archive.writestr("meta", "{}")
        archive.writestr("collection.anki21b", b"not-sqlite")

    with pytest.raises(apkg_template.ApkgTemplateError):
        apkg_template.read_models(apkg)
