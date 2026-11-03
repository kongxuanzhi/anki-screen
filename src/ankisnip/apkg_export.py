"""离线导出：Anki 没运行时用 genanki 生成 ``.apkg``，双击即可导入。

关键点：**笔记类型必须与 Anki 里已有的那个完全一致**（模型 id、字段、
正面/背面模板、CSS）。挖空矩形不是图片的一部分，而是靠笔记类型正面模板里
内联的脚本渲染出来的；自建空壳模板会导致导入后"图片在、挖空没了"，
还会在 Anki 里留下 ``图片挖空模板sle1_0+`` 这类垃圾类型。

因此这里优先使用 :mod:`ankisnip.template_store` 抓下来的真实快照；
只有在**从没连过 Anki**、没有快照时才退回最小模板，并在返回信息里明确告知。
"""

from __future__ import annotations

import datetime as _dt
import random
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import genanki

from .kong.serialize import MaskRect

# 没有真实快照时使用的兜底常量（有快照时一律以快照为准）
FALLBACK_MODEL_ID = 1607392319
DECK_ID = 2059400110

DEFAULT_MODEL_NAME = "图片挖空模板sle1_0"
DEFAULT_DECK_NAME = "图片挖空模板sle1_0"
DEFAULT_FIELDS = ["pic_kong", "question", "relation"]


def _fallback_model(model_name: str, fields: Sequence[str]) -> genanki.Model:
    """没有快照时的最小模板（**不会渲染挖空**，仅用于把卡片先存下来）。"""
    qfmt_lines = ["{{" + fields[0] + "}}"]
    afmt_lines = ["{{FrontSide}}", "<hr id=answer>"]
    if len(fields) > 1:
        afmt_lines.append("{{" + fields[1] + "}}")
    if len(fields) > 2:
        afmt_lines.append("{{" + fields[2] + "}}")
    return genanki.Model(
        FALLBACK_MODEL_ID,
        model_name,
        fields=[{"name": f} for f in fields],
        templates=[{"name": model_name, "qfmt": "\n".join(qfmt_lines),
                    "afmt": "\n".join(afmt_lines)}],
        css=".card { font-family: -apple-system, 'PingFang SC', sans-serif; }",
    )


def build_model(snapshot: Optional[Dict], model_name: str,
                fields: Sequence[str]) -> Tuple[genanki.Model, bool]:
    """按快照构造 :class:`genanki.Model`。

    返回 ``(model, used_snapshot)``。``used_snapshot=False`` 表示用的是兜底模板，
    导入后挖空**不会**显示，UI 必须显式提示用户。
    """
    if not snapshot:
        return _fallback_model(model_name, fields), False

    model_id = int(snapshot.get("model_id") or 0) or FALLBACK_MODEL_ID
    snap_fields = list(snapshot.get("fields") or [])
    # 快照里的字段顺序是权威的；配置字段顺序只用于兜底。
    field_names = snap_fields or list(fields) or list(DEFAULT_FIELDS)

    templates: List[Dict] = []
    for name, sides in (snapshot.get("templates") or {}).items():
        if not isinstance(sides, dict):
            continue
        templates.append({
            "name": name,
            "qfmt": sides.get("Front", "") or sides.get("qfmt", ""),
            "afmt": sides.get("Back", "") or sides.get("afmt", ""),
        })
    if not templates:
        return _fallback_model(model_name, fields), False

    return genanki.Model(
        model_id,
        snapshot.get("model_name") or model_name,
        fields=[{"name": f} for f in field_names],
        templates=templates,
        css=snapshot.get("css", "") or "",
    ), True


def write_apkg(notes: Sequence, model: genanki.Model, default_deck: str,
               media_dir: Path, out_file: Path) -> Path:
    """把**一批**卡片写成一个 ``.apkg``。

    :param notes: :class:`~ankisnip.card_batch.PendingNote` 序列（或用 duck typing
        提供 ``guid / fields / tags / deck_name / media_rel`` 的对象）。
    :param media_dir: 媒体文件所在目录，``media_rel`` 相对它。
    :param out_file: 目标文件；原子写入（先写临时文件再替换），失败不留半截包。

    每张卡片可以有自己的牌组，这里按牌组分桶生成多个 :class:`genanki.Deck`。
    guid 由调用方给定并保持稳定 —— 这是「重复导入同一个包不会重复」的关键。
    """
    import shutil as _shutil

    out_file = Path(out_file)
    out_file.parent.mkdir(parents=True, exist_ok=True)

    decks: Dict[str, genanki.Deck] = {}
    model_field_names = [f["name"] for f in model.fields]
    media_files: List[str] = []

    for note in notes:
        deck_name = str(getattr(note, "deck_name", "") or default_deck)
        deck = decks.get(deck_name)
        if deck is None:
            from .card_batch import _stable_id

            deck = genanki.Deck(_stable_id("deck:" + deck_name), deck_name)
            decks[deck_name] = deck
        deck.add_note(genanki.Note(
            model=model,
            fields=[note.fields.get(name, "") for name in model_field_names],
            tags=list(getattr(note, "tags", []) or []),
            guid=getattr(note, "guid", None),
        ))
        media_path = Path(media_dir) / getattr(note, "media_rel", "")
        if media_path.is_file():
            media_files.append(str(media_path))

    package = genanki.Package(list(decks.values()))
    package.media_files = media_files

    tmp_file = out_file.with_suffix(out_file.suffix + ".tmp")
    package.write_to_file(str(tmp_file))
    _shutil.move(str(tmp_file), str(out_file))
    return out_file


def export_apkg(image_path: str | Path, fields: dict, deck_name: str = DEFAULT_DECK_NAME,
            model_name: str = DEFAULT_MODEL_NAME, out_dir: str | Path = "",
            field_names: Sequence[str] = DEFAULT_FIELDS,
            tags: Sequence[str] = ("ankisnip",),
            snapshot: Optional[Dict] = None,
            guid: Optional[str] = None) -> Tuple[Path, bool]:
    """把合成后的图片 + 字段打包成 ``.apkg``。

    返回 ``(文件路径, 是否用了真实模板快照)``。
    """
    image_path = Path(image_path)
    if not image_path.is_file():
        raise FileNotFoundError(f"待打包的图片不存在：{image_path}")

    out_dir = Path(out_dir) if out_dir else Path.home() / "AnkiSnip"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_file = out_dir / f"ankisnip_{stamp}.apkg"

    model, used_snapshot = build_model(snapshot, model_name, field_names)

    class _Single:
        """把单张卡片的入参包装成 :func:`write_apkg` 认识的形状。"""

        def __init__(self) -> None:
            self.fields = dict(fields)
            self.tags = list(tags)
            self.deck_name = deck_name
            self.media_rel = image_path.name
            # 没显式给 guid 时按图片名算一个稳定的，重复导同一张图不会重复导入。
            from .card_batch import stable_guid

            self.guid = guid or stable_guid(image_path.name)

    write_apkg(
        notes=[_Single()],
        model=model,
        default_deck=deck_name,
        media_dir=image_path.parent,
        out_file=out_file,
    )
    return out_file, used_snapshot


def unused(_masks: List[MaskRect]) -> None:  # pragma: no cover - 占位，保持接口对称
    """挖空坐标已编码进字段 HTML，离线导出无需额外处理。"""
