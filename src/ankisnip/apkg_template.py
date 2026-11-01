"""从 .apkg 里提取笔记类型模板（名称 / 字段 / 模板 / CSS）。

.apkg 本质是 zip；其中的 ``collection.anki2`` 是 SQLite，``col.models`` 是 JSON，
包含笔记类型、字段和正面 / 背面模板。这里只做"读模板"，不导入包里的卡片。

新版只有 ``collection.anki21b``（zstd/protobuf）的包读不了，遇到时给出明确提示：
先在 Anki 里导入一次，再用「从 Anki 刷新」。
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List


class ApkgTemplateError(RuntimeError):
    """.apkg 无法解析 / 没有笔记类型。"""


@dataclass
class ApkgModel:
    """一个笔记类型（只保留模板需要的信息）。"""

    model_id: int
    name: str
    fields: List[str]
    templates: Dict[str, Dict[str, str]]
    css: str


def read_models(path: str | Path) -> List[ApkgModel]:
    """读取 .apkg 内的所有笔记类型。"""
    path = Path(path)
    if not path.is_file():
        raise ApkgTemplateError(f"找不到文件：{path}")

    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            entry = next(
                (name for name in ("collection.anki2", "collection.anki21")
                 if name in names), None)
            if entry is None:
                raise ApkgTemplateError(
                    "这个 .apkg 是较新的压缩格式（只有 collection.anki21b），"
                    "暂时无法直接读取。\n"
                    "请先在 Anki 里导入它一次，再用「从 Anki 刷新」抓取模板。")
            data = archive.read(entry)
    except zipfile.BadZipFile as exc:
        raise ApkgTemplateError("不是有效的 .apkg 文件（zip 解析失败）。") from exc

    fd, temp_path = tempfile.mkstemp(suffix=".anki2")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        try:
            connection = sqlite3.connect(temp_path)
            try:
                row = connection.execute("select models from col").fetchone()
            finally:
                connection.close()
        except sqlite3.DatabaseError as exc:
            raise ApkgTemplateError("collection.anki2 不是可读的 Anki 数据库。") from exc
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass

    if not row or not row[0]:
        raise ApkgTemplateError("这个 .apkg 里没有笔记类型模板。")
    try:
        raw_models = json.loads(row[0])
    except (TypeError, json.JSONDecodeError) as exc:
        raise ApkgTemplateError("笔记类型数据损坏，无法解析。") from exc

    models: List[ApkgModel] = []
    for raw in raw_models.values():
        name = str(raw.get("name") or "").strip()
        fields = [str(f.get("name") or "").strip()
                  for f in (raw.get("flds") or []) if f.get("name")]
        templates: Dict[str, Dict[str, str]] = {}
        for template in raw.get("tmpls") or []:
            template_name = str(template.get("name") or f"模板 {template.get('ord', 0)}")
            templates[template_name] = {
                "Front": str(template.get("qfmt") or ""),
                "Back": str(template.get("afmt") or ""),
            }
        if not name or not fields or not templates:
            continue
        models.append(ApkgModel(
            model_id=int(raw.get("id") or 0),
            name=name,
            fields=fields,
            templates=templates,
            css=str(raw.get("css") or ""),
        ))
    if not models:
        raise ApkgTemplateError("这个 .apkg 里没有可用的笔记类型。")
    return models


def _pick_field(fields: List[str], patterns: tuple, used: set) -> str:
    for field in fields:
        if field in used:
            continue
        low = field.lower()
        if any(pattern in low for pattern in patterns):
            return field
    return ""


def field_mapping(model: ApkgModel) -> Dict[str, object]:
    """根据模板自动猜字段映射，返回 Settings 需要的键。

    默认模板会得到：
    ``image_field=kong_field=pic_kong, question_field=question,
    relation_field=relation, field_order=[pic_kong, question, relation]``。
    """
    fields = list(model.fields)
    front = "\n".join(t.get("Front", "") for t in model.templates.values())
    tokens = [re.split(r"[|:]", token.strip())[0].strip()
              for token in re.findall(r"\{\{([^{}]+)\}\}", front)]
    tokens = [token for token in tokens if token in fields]

    image = _pick_field(
        fields, ("pic_kong", "pic", "kong", "image", "img", "图片", "挖空", "遮罩"), set())
    if not image and tokens:
        image = tokens[0]
    if not image and fields:
        image = fields[0]

    question = _pick_field(
        fields, ("question", "题面", "问题", "正面", "front", "prompt"), {image})
    relation = _pick_field(
        fields, ("relation", "关联", "答案", "背面", "back", "answer", "extra"),
        {image, question})
    if not question:
        rest = [field for field in fields if field != image]
        question = rest[0] if rest else ""
    if not relation:
        rest = [field for field in fields if field not in (image, question)]
        relation = rest[0] if rest else ""

    return {
        "model_name": model.name,
        "image_field": image,
        "kong_field": image,
        "question_field": question,
        "relation_field": relation,
        "field_order": fields,
    }
