"""离线批次收集器测试。

这些用例专门盯住两件容易悄悄坏掉、而且坏了以后用户很难自己发现的事：

1. **多次保存只产出同一个牌包**，包里是**累计**的卡片，而不是每次一个文件；
2. **guid 稳定** —— 否则用户"先导入一次、攒多了再导入一次"会得到重复卡片，
   而这正是 genanki 默认行为（guid 按字段算，字段里含随机文件名）会踩的坑。
"""

from __future__ import annotations

import sqlite3
import tempfile
import zipfile
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

from ankisnip.card_batch import (
    MODE_DAILY,
    MODE_PER_NOTE,
    MODE_SINGLE,
    CardBatch,
    stable_guid,
)

FIELDS = ["pic_kong", "question", "relation"]


def _png_bytes(color: str = "#ff0000") -> bytes:
    img = QImage(20, 20, QImage.Format_RGB32)
    img.fill(Qt.white)
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * 32  # 只做占位，genanki 不解析内容


def _make_batch(root: Path, mode: str = MODE_DAILY, deck: str = "测试牌组") -> CardBatch:
    return CardBatch(root, deck_name=deck, model_name="图片挖空模板sle1_0",
                     field_names=FIELDS, mode=mode, tags=["ankisnip"])


def _add_card(batch: CardBatch, index: int) -> None:
    batch.add(
        media_name=f"ankisnip_000{index}.png",
        png_bytes=_png_bytes(),
        fields={"pic_kong": f'<img src="ankisnip_000{index}.png" kong="abc">',
                "question": f"第{index}题", "relation": ""},
    )


def _notes_in(apkg: Path):
    with zipfile.ZipFile(apkg) as zf:
        names = zf.namelist()
        blob = zf.read("collection.anki2")
    tmp = Path(tempfile.mkdtemp()) / "c.anki2"
    tmp.write_bytes(blob)
    con = sqlite3.connect(str(tmp))
    rows = con.execute("select guid, flds from notes").fetchall()
    con.close()
    return rows, names


# --- 基本行为 ------------------------------------------------------------ #
def test_batch_accumulates_into_one_file(tmp_path):
    batch = _make_batch(tmp_path)
    _add_card(batch, 1)
    _add_card(batch, 2)
    _add_card(batch, 3)

    out, total, _ = batch.export(None)
    assert total == 3
    assert out.name.startswith("AnkiSnip-")
    # 磁盘上只有一个 apkg
    apkgs = list(tmp_path.glob("*.apkg"))
    assert [p.name for p in apkgs] == [out.name]

    rows, names = _notes_in(out)
    assert len(rows) == 3, "包里应当一次装下所有 3 张卡片"
    assert len([n for n in names if n.isdigit()]) == 3, "3 张图片都应作为媒体进包"


def test_reopen_batch_keeps_history(tmp_path):
    """新进程 / 新实例读同一个目录，要能看到之前攒下的卡片。"""
    batch = _make_batch(tmp_path)
    _add_card(batch, 1)
    batch.export(None)

    reopened = _make_batch(tmp_path)  # 模拟程序重启
    assert reopened.count == 1
    _add_card(reopened, 2)
    out, total, _ = reopened.export(None)
    assert total == 2
    rows, _ = _notes_in(out)
    assert len(rows) == 2


def test_guid_is_stable_across_exports(tmp_path):
    """同一张卡片每次导出都必须是同一个 guid —— 否则重复导入会重复。"""
    batch = _make_batch(tmp_path)
    _add_card(batch, 1)
    out1, _, _ = batch.export(None)
    first_guids = {g for g, _ in _notes_in(out1)[0]}

    _add_card(batch, 2)
    out2, _, _ = batch.export(None)
    second_guids = {g for g, _ in _notes_in(out2)[0]}

    assert out1 == out2, "目标文件必须是同一个，不能又生一个"
    assert first_guids <= second_guids, "已有卡片的 guid 不能变"


def test_duplicate_key_overwrites(tmp_path):
    batch = _make_batch(tmp_path)
    _add_card(batch, 1)
    _add_card(batch, 1)  # 同一个媒体名重复收录
    out, total, _ = batch.export(None)
    assert total == 1
    assert len(_notes_in(out)[0]) == 1


def test_stable_guid_is_deterministic():
    assert stable_guid("a.png") == stable_guid("a.png")
    assert stable_guid("a.png") != stable_guid("b.png")
    assert len(stable_guid("a.png")) == 10


# --- 归档与模式 ---------------------------------------------------------- #
def test_archive_renames_and_starts_fresh(tmp_path):
    batch = _make_batch(tmp_path)
    _add_card(batch, 1)
    _add_card(batch, 2)
    target = batch.export(None)[0]

    archived = batch.archive()
    assert archived is not None and archived.is_file()
    assert archived.name != target.name
    assert not target.exists()
    assert batch.count == 0
    assert not (tmp_path / ".ankisnip-batch" / "media").exists()


def test_archive_nothing_returns_none(tmp_path):
    assert _make_batch(tmp_path).archive() is None


def test_single_mode_always_same_filename(tmp_path):
    for i in (1, 2):
        batch = _make_batch(tmp_path, mode=MODE_SINGLE)
        _add_card(batch, i)
        out, total, _ = batch.export(None)
        assert out.name == "AnkiSnip.apkg"
        assert total == i
    assert [p.name for p in tmp_path.glob("*.apkg")] == ["AnkiSnip.apkg"]


def test_per_note_mode_makes_one_file_each(tmp_path):
    batch = _make_batch(tmp_path, mode=MODE_PER_NOTE)
    _add_card(batch, 1)
    out1, total1, _ = batch.export(None)
    assert total1 == 1
    assert batch.count == 0, "每张一个文件的模式下导出即清账"

    batch2 = _make_batch(tmp_path, mode=MODE_PER_NOTE)
    _add_card(batch2, 2)
    out2, _, _ = batch2.export(None)
    assert len(list(tmp_path.glob("*.apkg"))) == 2
    assert out1 != out2


# --- 牌组 ---------------------------------------------------------------- #
def test_notes_keep_their_own_deck(tmp_path):
    """中途改了牌组，已收录的卡片仍进它当初那个牌组，不该被搬运。"""
    batch = _make_batch(tmp_path, deck="牌组A")
    _add_card(batch, 1)
    batch.add(media_name="card2.png", png_bytes=_png_bytes(),
              fields=dict.fromkeys(FIELDS, ""), deck_name="牌组B")

    out, _, _ = batch.export(None)
    with zipfile.ZipFile(out) as zf:
        blob = zf.read("collection.anki2")
    tmp = Path(tempfile.mkdtemp()) / "c.anki2"
    tmp.write_bytes(blob)
    con = sqlite3.connect(str(tmp))
    # notes.flds 之外的 deck 关系在 cards.did 上（apkg 里 deck 名在 col 的 decks 里）
    import json

    decks = json.loads(con.execute("select decks from col").fetchone()[0])
    con.close()
    assert {"牌组A", "牌组B"} <= {d["name"] for d in decks.values()}
