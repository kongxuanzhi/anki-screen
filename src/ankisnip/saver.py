"""保存链路编排（与 UI 解耦，可单测）。

流程（任务书 §5-C）::

    annotations ──► 分离 mask / 其它
                    ├─► 其它（箭头 / 文字 …）──► 按原图分辨率烧录 ──► 图片字节
                    └─► mask（归一化）────────► kong 字符串
                                        └─► 字段 HTML <img src=... kong=...>
                                                 ├─ AnkiConnect 可用 → addNote
                                                 └─ 不可用            → genanki .apkg

取消 / 失败时负责清理已经写进 Anki 的媒体文件，不留孤儿。
"""

from __future__ import annotations

import datetime as _dt
import logging
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from PySide6.QtGui import QImage

from .anki_connect import AnkiConnect, AnkiConnectError
from .base import annotations as ann
from .kong.serialize import build_img_html, build_kong
from .rasterize import bake, split_annotations, to_png_bytes

LOG = logging.getLogger("ankisnip.saver")


@dataclass
class PreparedCard:
    """保存前的中间产物。"""

    baked_image: QImage
    media_name: str
    png_bytes: bytes
    fields: Dict[str, str]
    kong: str
    mask_count: int
    baked_count: int


@dataclass
class SaveResult:
    ok: bool
    message: str = ""
    note_id: Optional[int] = None
    apkg_path: str = ""
    media_written: List[str] = field(default_factory=list)
    mode: str = ""  # "ankiconnect" | "apkg"

    def __bool__(self) -> bool:  # pragma: no cover - 便利
        return self.ok


class AnkiSaver:
    """把编辑器内容保存成 Anki 卡片。"""

    def __init__(self, settings, connect: Optional[AnkiConnect] = None) -> None:
        self.settings = settings
        self.connect = connect or AnkiConnect(settings.anki_url, settings.anki_timeout)
        self._snapshot_ok = False

    # -- 步骤 1~3 --------------------------------------------------------- #
    def prepare(self, image: QImage, annotations: Sequence[ann.Annotation],
                question: str = "", relation: str = "") -> PreparedCard:
        masks, baked = split_annotations(annotations)

        baked_image = bake(image, baked)
        png_bytes = to_png_bytes(baked_image)

        stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        media_name = f"ankisnip_{stamp}_{random.randint(1000, 9999)}.png"

        norm_masks = [m for m in masks if isinstance(m, ann.MaskAnnotation)]
        rects = []
        for m in norm_masks:
            nx, ny, nw, nh = m.to_norm(image.width(), image.height())
            if nw <= 0 or nh <= 0:
                continue
            from .kong.serialize import MaskRect
            rects.append(MaskRect(nx=nx, ny=ny, nw=nw, nh=nh, color=m.style.color.name()))
        kong = build_kong(rects)

        fields = self._build_fields(media_name, kong, question, relation)
        LOG.info("prepare: 原图 %dx%d, 挖空 %d, 烧录 %d",
                 image.width(), image.height(), len(rects), len(baked))
        return PreparedCard(baked_image, media_name, png_bytes, fields, kong,
                            len(rects), len(baked))

    def _build_fields(self, media_name: str, kong: str, question: str,
                      relation: str) -> Dict[str, str]:
        s = self.settings
        html = build_img_html(media_name, kong)
        fields: Dict[str, str] = {s.image_field: html}
        if s.kong_field and s.kong_field != s.image_field:
            fields[s.kong_field] = kong
        if s.question_field:
            fields[s.question_field] = question
        if s.relation_field:
            fields[s.relation_field] = relation
        return fields

    # -- 步骤 4 ------------------------------------------------------------ #
    def save(self, prepared: PreparedCard, tmp_dir: Optional[str] = None) -> SaveResult:
        if self.connect.is_available():
            return self._save_via_ankiconnect(prepared)
        return self._save_via_apkg(prepared, tmp_dir)

    def _save_via_ankiconnect(self, prepared: PreparedCard) -> SaveResult:
        s = self.settings
        result = SaveResult(ok=False, mode="ankiconnect")
        try:
            self._validate_model()
            note_id = self._add_note_with_media(prepared)
        except AnkiConnectError as exc:
            self.cleanup(result)
            return SaveResult(ok=False, message=str(exc), mode="ankiconnect")
        extra = f"，{prepared.mask_count} 个挖空写入 {s.kong_field}" if prepared.mask_count else ""
        deck_note = "（牌组不存在，已自动创建）" if getattr(self, "_deck_created", False) else ""
        result.ok = True
        result.note_id = note_id
        result.message = (
            f"已添加到牌组「{s.deck_name}」{deck_note}（note {note_id}）{extra}。\n"
            f"图片：{prepared.media_name}（{prepared.baked_image.width()}×"
            f"{prepared.baked_image.height()}，已烧录 {prepared.baked_count} 个标注）"
        )
        return result

    def _validate_model(self) -> None:
        s = self.settings
        models = self.connect.model_names()
        if s.model_name not in models:
            raise AnkiConnectError(
                f"Anki 中不存在笔记类型「{s.model_name}」。\n"
                f"可选类型：{', '.join(models[:12]) or '（空）'}\n"
                "请先导入图片挖空模板，或在「设置」里改笔记类型名。"
            )
        available = self.connect.model_field_names(s.model_name)
        missing = [f for f in (s.image_field, s.kong_field, s.question_field, s.relation_field)
                   if f and f not in available]
        if missing:
            raise AnkiConnectError(
                f"笔记类型「{s.model_name}」缺少字段：{', '.join(missing)}。\n"
                f"该类型现有字段：{', '.join(available)}"
            )

    def _add_note_with_media(self, prepared: PreparedCard) -> int:
        s = self.settings
        decks = self.connect.deck_names()
        created = False
        if s.deck_name not in decks:
            self.connect.create_deck(s.deck_name)
            created = True
        self.connect.store_media_file(prepared.media_name, prepared.png_bytes)
        prepared_written = prepared.media_name
        try:
            note_id = self.connect.add_note(s.deck_name, s.model_name, prepared.fields,
                                            tags=list(s.tags))
        except AnkiConnectError:
            self.connect.delete_media_file(prepared_written)
            raise
        # 趁 Anki 还开着，把笔记类型的真实定义（含渲染挖空的模板脚本）存成快照，
        # 供以后离线导出 .apkg 使用。
        self.ensure_template_snapshot()
        self._last_media = [prepared_written]
        self._deck_created = created
        return note_id

    def refresh_template_snapshot(self) -> bool:
        """抓取并缓存笔记类型定义。失败只记日志，不影响保存主流程。"""
        from . import template_store

        snapshot = template_store.capture_from_anki(
            self.connect, self.settings.model_name)
        self._snapshot_ok = snapshot is not None
        return self._snapshot_ok

    def ensure_template_snapshot(self, force: bool = False) -> bool:
        """保证离线导出时有真实模板可用。

        ``force=False``（默认，保存后调用）只在快照缺失时才抓，避免每次保存
        都传一遍几十万字的模板；``force=True`` 用于启动时或用户手动点「检查连接」。
        """
        from . import template_store

        if not force and template_store.load_snapshot(self.settings.model_name):
            self._snapshot_ok = True
            return True
        return self.refresh_template_snapshot()

    def _save_via_apkg(self, prepared: PreparedCard, tmp_dir: Optional[str]) -> SaveResult:
        from . import apkg_export, card_batch, template_store

        s = self.settings
        tmp_path = Path(tmp_dir or s.temp_dir)
        tmp_path.mkdir(parents=True, exist_ok=True)

        # 离线导出目录：确保存在且可写，否则明确报错，而不是悄悄写到别处。
        out_dir = Path(s.offline_dir) if s.offline_dir else Path.home() / "AnkiSnip"
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return SaveResult(ok=False, mode="apkg",
                              message=f"离线导出目录不可用：{out_dir}\n{exc}")

        snapshot = template_store.load_snapshot(s.model_name)
        batch = card_batch.CardBatch(
            out_dir, deck_name=s.deck_name, model_name=s.model_name,
            field_names=s.field_names(), mode=s.batch_mode, tags=list(s.tags),
        )
        try:
            batch.add(prepared.media_name, prepared.png_bytes, prepared.fields,
                      tags=list(s.tags), deck_name=s.deck_name)
            out, total, used_snapshot = batch.export(snapshot)
        except Exception as exc:  # pragma: no cover - 打包路径
            LOG.exception("apkg 导出失败")
            return SaveResult(ok=False, message=f"导出 .apkg 失败：{exc}", mode="apkg")

        LOG.info("apkg 导出：目录=%s，文件=%s，包内卡片=%d，使用真实模板快照=%s",
                 out_dir, out, total, used_snapshot)
        return self._apkg_result(out, total, used_snapshot, batch)

    def _apkg_result(self, out: Path, total: int, used_snapshot: bool,
                     batch: "card_batch.CardBatch") -> SaveResult:
        """整理离线导出的用户反馈文案。"""
        from . import card_batch as _cb

        s = self.settings
        if used_snapshot:
            render_tip = "用的是「" + s.model_name + "」的真实模板，导入后挖空可正常显示。"
        else:
            render_tip = ("⚠️ 本机还没有「" + s.model_name + "」的模板快照，\n"
                          "这个牌包用的是最小模板，导入后**挖空不会显示**。\n"
                          "请在 Anki 打开时用一次「保存到 Anki」，之后再导出即可。")

        if batch.mode == _cb.MODE_PER_NOTE:
            head = "未检测到 Anki / AnkiConnect，已生成离线牌包："
            tail = "双击导入 Anki 即可；" + render_tip
        else:
            head = (f"未检测到 Anki / AnkiConnect，已收入牌组「{s.deck_name}」\n"
                    f"的离线牌包（累计 {total} 张）：")
            tail = "双击这个文件一次性导入即可。\n" + render_tip

        return SaveResult(
            ok=True,
            mode="apkg",
            apkg_path=str(out),
            message=f"{head}\n{out}\n{tail}",
        )

    # -- 清理 -------------------------------------------------------------- #
    def cleanup(self, result: SaveResult) -> None:
        """删除已经写入 Anki 的媒体文件，避免取消/失败留下孤儿媒体。"""
        for name in getattr(self, "_last_media", []) + list(result.media_written):
            if not name:
                continue
            try:
                self.connect.delete_media_file(name)
                LOG.info("已清理媒体文件 %s", name)
            except Exception:  # pragma: no cover - 清理失败不影响主流程
                LOG.warning("清理媒体文件失败：%s", name)
        self._last_media = []
        result.media_written = []

    def discard(self, prepared: Optional[PreparedCard] = None) -> None:
        """用户取消保存时的显式清理入口。"""
        if prepared is None:
            self.cleanup(SaveResult(ok=False))
            return
        try:
            if self.connect.is_available():
                self.connect.delete_media_file(prepared.media_name)
        except Exception:  # pragma: no cover
            LOG.warning("取消保存时清理失败：%s", prepared.media_name)
