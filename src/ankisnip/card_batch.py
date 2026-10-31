"""离线卡片批次：把多次截图攒进**同一个** ``.apkg``。

为什么需要它
------------
离线导出原本是「一张卡片 → 一个 ``ankisnip_<时间戳>.apkg``」。截十张图就在导出目录
里堆十个文件，而且每次导入 Anki 都是独立操作 —— 收集素材时非常难用。

这里改成**批次累积**::

    第 1 张 ┐
    第 2 张 ├─► 待导出批次（manifest.json + media/）──► 同一个 .apkg（整包重写）
    第 N 张 ┘

* 每次保存只做两件事：把图片写进 ``media/``、把字段写进 ``manifest.json``，
  然后**重新导出整个批次**到目标文件。磁盘上永远只有一个包。
* :meth:`CardBatch.archive` 把当前包封口改名（留档），并清空批次重新开始。

两个关键的导入语义
------------------
1. **稳定的 note guid**：由卡片的媒体名哈希而来。
   Anki 导入 ``.apkg`` 时按 guid 判定：已存在则**更新**（保留复习进度），
   不存在才新增。因此「先导入 3 张、之后累积到 10 张再导入同一个包」不会
   产生重复卡片；而 genanki 默认的 guid 是按字段算的（字段里含随机文件名），
   每次都不一样，重复导入必然重复。
2. **稳定的 deck id**：按牌组名哈希得到确定性整数。Anki 按**牌组名**合并导入，
   id 稳定只是让同一来源的导入行为可复现。

崩溃安全性
----------
``manifest.json`` 是唯一的事实来源，media 与 manifest 都先写临时再落盘；
进程中途被杀最多丢最后一张，已经收录的卡片不会丢。
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

LOG = logging.getLogger("ankisnip.card_batch")

#: 批次暂存目录名（放在用户设置的导出目录里，`.` 开头避免视觉干扰）
BATCH_DIR_NAME = ".ankisnip-batch"
MANIFEST_NAME = "manifest.json"
MEDIA_DIR_NAME = "media"
APKG_PREFIX = "AnkiSnip"

#: 归并模式 → 目标文件名策略
MODE_DAILY = "daily"        # AnkiSnip-2026-10-25.apkg（**默认**，一天一个，自动归档）
MODE_SINGLE = "single"      # AnkiSnip.apkg（永远只有这一个）
MODE_PER_NOTE = "per-note"  # AnkiSnip-20261025-153012.apkg（旧行为，一张一个）

MODES = (MODE_DAILY, MODE_SINGLE, MODE_PER_NOTE)

#: Anki 的 deck / note id 上限（都是有符号 64 位整数的一部分）
_ID_MOD = 10 ** 12


def _stable_id(seed: str) -> int:
    """把任意字符串映射成稳定正整数 id。"""
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()
    return int(digest[:12], 16) % _ID_MOD or 1


def stable_guid(key: str) -> str:
    """卡片的稳定 guid（10 位十六进制，Anki 允许任意短字符串）。"""
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]


@dataclass
class PendingNote:
    """批次里的一张待导出卡片。"""

    key: str                        # 唯一键 = 媒体名
    media_name: str                 # apkg 内的媒体文件名
    media_rel: str                  # 相对 media 目录的路径
    fields: Dict[str, str]
    tags: List[str] = field(default_factory=list)
    deck_name: str = ""
    added_at: str = ""

    @property
    def guid(self) -> str:
        return stable_guid(self.key)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "media_name": self.media_name,
            "media_rel": self.media_rel,
            "fields": dict(self.fields),
            "tags": list(self.tags),
            "deck_name": self.deck_name,
            "added_at": self.added_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PendingNote":
        return cls(
            key=str(data.get("key", "")),
            media_name=str(data.get("media_name", "")),
            media_rel=str(data.get("media_rel", data.get("media_name", ""))),
            fields=dict(data.get("fields") or {}),
            tags=list(data.get("tags") or []),
            deck_name=str(data.get("deck_name", "")),
            added_at=str(data.get("added_at", "")),
        )


class CardBatch:
    """离线卡片批次。

    :param root: 用户设置的离线导出目录。
    :param mode: 归并模式，见 :data:`MODE_DAILY` 等。
    """

    def __init__(self, root: str | Path, deck_name: str, model_name: str,
                 field_names: Sequence[str], mode: str = MODE_DAILY,
                 tags: Sequence[str] = ()) -> None:
        self.root = Path(root)
        self.batch_dir = self.root / BATCH_DIR_NAME
        self.media_dir = self.batch_dir / MEDIA_DIR_NAME
        self.manifest_path = self.batch_dir / MANIFEST_NAME
        self.deck_name = deck_name
        self.model_name = model_name
        self.field_names = [str(f) for f in field_names]
        self.mode = mode if mode in MODES else MODE_DAILY
        self.tags = [str(t) for t in tags]
        self._notes: Optional[List[PendingNote]] = None

    # -- 目标文件 ---------------------------------------------------------- #
    @property
    def apkg_name(self) -> str:
        if self.mode == MODE_SINGLE:
            return f"{APKG_PREFIX}.apkg"
        if self.mode == MODE_PER_NOTE:
            # 精确到毫秒：连续两次导出落在同一秒时不会互相覆盖。
            now = _dt.datetime.now()
            stamp = now.strftime("%Y%m%d-%H%M%S") + f"-{now.microsecond // 1000:03d}"
            return f"{APKG_PREFIX}-{stamp}.apkg"
        stamp = _dt.date.today().strftime("%Y-%m-%d")
        return f"{APKG_PREFIX}-{stamp}.apkg"

    @property
    def apkg_path(self) -> Path:
        return self.root / self.apkg_name

    # -- manifest ---------------------------------------------------------- #
    def notes(self) -> List[PendingNote]:
        if self._notes is None:
            self._notes = self._read_manifest()
        return self._notes

    @property
    def count(self) -> int:
        return len(self.notes())

    def _read_manifest(self) -> List[PendingNote]:
        if not self.manifest_path.is_file():
            return []
        try:
            data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            # manifest 坏了不能静默吞掉：那等于把用户已截的卡片弄丢。
            LOG.error("批次清单损坏，本次将从空批次开始：%s（%s）",
                      self.manifest_path, exc)
            return []
        return [PendingNote.from_dict(n) for n in (data.get("notes") or [])]

    def _write_manifest(self) -> None:
        payload = {
            "version": 1,
            "deck_name": self.deck_name,
            "model_name": self.model_name,
            "field_names": self.field_names,
            "created_at": _dt.datetime.now().isoformat(timespec="seconds"),
            "notes": [n.to_dict() for n in self.notes()],
        }
        self.batch_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.manifest_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.manifest_path)

    # -- 收卡 -------------------------------------------------------------- #
    def add(self, media_name: str, png_bytes: bytes, fields: Dict[str, str],
            tags: Sequence[str] = (), deck_name: str = "",
            key: Optional[str] = None) -> PendingNote:
        """收录一张卡片，返回它在批次里的记录。"""
        self.media_dir.mkdir(parents=True, exist_ok=True)
        note_key = key or media_name
        rel = media_name
        target = self.media_dir / rel
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_bytes(png_bytes)
        tmp.replace(target)

        note = PendingNote(
            key=note_key,
            media_name=media_name,
            media_rel=rel,
            fields=dict(fields),
            tags=list(tags) or list(self.tags),
            deck_name=deck_name or self.deck_name,
            added_at=_dt.datetime.now().isoformat(timespec="seconds"),
        )
        # 同一个 key 重复收录（例如重复保存同一张）视为覆盖，不产生重复卡片。
        existing = [n for n in self.notes() if n.key != note.key]
        existing.append(note)
        self._notes = existing
        self._write_manifest()
        LOG.info("批次新增第 %d 张：%s", len(self._notes), media_name)
        return note

    # -- 导出 -------------------------------------------------------------- #
    def export(self, snapshot: Optional[Dict]) -> Tuple[Path, int, bool]:
        """整包重写。

        返回 ``(文件路径, 包内卡片数, 是否用了真实模板快照)``。
        """
        from . import apkg_export

        notes = self.notes()
        if not notes:
            raise ValueError("批次为空，没有可导出的卡片")

        model, used_snapshot = apkg_export.build_model(
            snapshot, self.model_name, self.field_names)
        out_file = self.apkg_path
        self.root.mkdir(parents=True, exist_ok=True)
        apkg_export.write_apkg(
            notes=notes,
            model=model,
            default_deck=self.deck_name,
            media_dir=self.media_dir,
            out_file=out_file,
        )
        LOG.info("批次导出 %d 张到 %s（真实模板快照=%s）",
                 len(notes), out_file, used_snapshot)
        if self.mode == MODE_PER_NOTE:
            # 一张一个文件的模式下，导出即清账，下次从空批次开始。
            self.clear()
        return out_file, len(notes), used_snapshot

    # -- 生命周期 ---------------------------------------------------------- #
    def clear(self) -> None:
        """清空批次（media 与 manifest），已导出的文件不受影响。"""
        self._notes = []
        try:
            self.manifest_path.unlink(missing_ok=True)
        except OSError as exc:  # pragma: no cover - 极端 IO 错误
            LOG.warning("删除批次清单失败：%s", exc)
        if self.media_dir.is_dir():
            shutil.rmtree(self.media_dir, ignore_errors=True)

    def archive(self) -> Optional[Path]:
        """把当前目标包封口改名（留档），并重新开始一个空批次。

        返回归档后的文件路径；没有可归档的包时返回 ``None``。
        """
        target = self.apkg_path
        if not target.is_file():
            return None
        stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        archived = self.root / f"{target.stem}-{stamp}.apkg"
        index = 1
        while archived.exists():
            archived = self.root / f"{target.stem}-{stamp}-{index}.apkg"
            index += 1
        shutil.move(str(target), str(archived))
        self.clear()
        LOG.info("牌包已归档：%s", archived)
        return archived
