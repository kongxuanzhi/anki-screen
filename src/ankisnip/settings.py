"""应用配置（``QSettings`` 持久化）。

继承自基座 ``base.config.Config``，因此热键 / 画笔颜色 / 线宽 / 保存目录等
沿用基座实现，这里只**新增** Anki 相关的设置项。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import List

from PySide6.QtCore import QSettings, QStandardPaths

from .base.config import Config

ORG = "ankisnip"
APP = "anki-screen"


#: 导出图片尺寸策略
#: ``screen`` —— 1 图片像素 = 1 屏幕点，导出图和框选范围看起来一样大（**默认**）。
#: ``native`` —— 保留抓取到的原生像素：Retina（dpr=2）上长宽各 2 倍、像素数 4 倍。
EXPORT_SCALE_SCREEN = "screen"
EXPORT_SCALE_NATIVE = "native"
EXPORT_SCALES = (EXPORT_SCALE_SCREEN, EXPORT_SCALE_NATIVE)


def _default_offline_dir() -> str:
    docs = QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation)
    base = Path(docs) if docs else Path.home()
    return str(base / "AnkiSnip")


#: 测试专用：把 QSettings 重定向到指定 ini 文件，避免单测读/写用户真实配置。
#: 只允许 conftest 设置；生产代码保持 ``None``。
_scope_override: object = None


def set_test_scope(path: str) -> None:
    """把后续所有 :class:`Settings` 的持久化目标切到 ``path``（仅测试用）。"""
    global _scope_override
    _scope_override = path


def _make_settings(org: str, app: str):
    if _scope_override:
        return QSettings(str(_scope_override), QSettings.IniFormat)
    return QSettings(org, app)


def _default_temp_dir() -> str:
    tmp = QStandardPaths.writableLocation(QStandardPaths.TempLocation)
    return str((Path(tmp) if tmp else Path.home() / ".cache") / "ankisnip")


class Settings(Config):
    """AnkiSnip 的全部设置项。"""

    def __init__(self) -> None:
        # 覆盖基座的 QSettings 作用域（组织名与应用名），避免与基座原程序串味。
        self._s = _make_settings(ORG, APP)

    # -- 画笔 / 文字 ------------------------------------------------------- #
    @property
    def font_size(self) -> int:
        return int(self._s.value("font_size", 24))

    @font_size.setter
    def font_size(self, value: int) -> None:
        self._s.setValue("font_size", int(value))

    # -- Anki -------------------------------------------------------------- #
    @property
    def deck_name(self) -> str:
        return str(self._s.value("deck_name", "图片挖空模板sle1_0"))

    @deck_name.setter
    def deck_name(self, value: str) -> None:
        self._s.setValue("deck_name", value)

    @property
    def model_name(self) -> str:
        return str(self._s.value("model_name", "图片挖空模板sle1_0"))

    @model_name.setter
    def model_name(self, value: str) -> None:
        self._s.setValue("model_name", value)

    @property
    def image_field(self) -> str:
        """放 ``<img src=...>`` 的字段（现有模板里同时承载 kong 属性）。"""
        return str(self._s.value("image_field", "pic_kong"))

    @image_field.setter
    def image_field(self, value: str) -> None:
        self._s.setValue("image_field", value)

    @property
    def kong_field(self) -> str:
        return str(self._s.value("kong_field", "pic_kong"))

    @kong_field.setter
    def kong_field(self, value: str) -> None:
        self._s.setValue("kong_field", value)

    @property
    def question_field(self) -> str:
        return str(self._s.value("question_field", "question"))

    @question_field.setter
    def question_field(self, value: str) -> None:
        self._s.setValue("question_field", value)

    @property
    def relation_field(self) -> str:
        return str(self._s.value("relation_field", "relation"))

    @relation_field.setter
    def relation_field(self, value: str) -> None:
        self._s.setValue("relation_field", value)

    @property
    def field_order(self) -> List[str]:
        raw = self._s.value("field_order", "pic_kong,question,relation")
        return [f.strip() for f in str(raw).split(",") if f.strip()]

    @field_order.setter
    def field_order(self, value: List[str]) -> None:
        self._s.setValue("field_order", ",".join(value))

    def field_names(self) -> List[str]:
        """笔记类型字段顺序（离线导出 apkg 时需要与模板一致）。"""
        return self.field_order

    @property
    def anki_url(self) -> str:
        return str(self._s.value("anki_url", "http://127.0.0.1:8765"))

    @anki_url.setter
    def anki_url(self, value: str) -> None:
        self._s.setValue("anki_url", value)

    @property
    def anki_timeout(self) -> float:
        return float(self._s.value("anki_timeout", 3.0))

    @anki_timeout.setter
    def anki_timeout(self, value: float) -> None:
        self._s.setValue("anki_timeout", float(value))

    @property
    def offline_dir(self) -> str:
        return str(self._s.value("offline_dir", _default_offline_dir()))

    @offline_dir.setter
    def offline_dir(self, value: str) -> None:
        self._s.setValue("offline_dir", value)

    @property
    def temp_dir(self) -> str:
        return str(self._s.value("temp_dir", _default_temp_dir()))

    @temp_dir.setter
    def temp_dir(self, value: str) -> None:
        self._s.setValue("temp_dir", value)

    @property
    def batch_mode(self) -> str:
        """离线牌包的归并方式，取值见 :mod:`ankisnip.card_batch`。

        ``daily``（默认）一天一个文件；``single`` 永远同一个文件；
        ``per-note`` 每张卡片一个文件（旧行为）。
        """
        from .card_batch import MODES

        value = str(self._s.value("batch_mode", "daily"))
        return value if value in MODES else "daily"

    @batch_mode.setter
    def batch_mode(self, value: str) -> None:
        self._s.setValue("batch_mode", value)

    @property
    def export_scale(self) -> str:
        """导出图片的像素口径，见 :data:`EXPORT_SCALE_SCREEN` / ``_NATIVE``。

        Retina 屏上 ``QScreen.grabWindow`` 交回的是**原生像素**（1280×800 的屏，
        pixmap 是 2560×1600），所以按原样导出的话，一张看起来 400×300 的选区会
        变成 800×600 的文件 —— 这就是"保存的图比截图大"的来由。

        默认**保留原生像素**，不缩回屏幕点：屏幕点口径等于把真实采样丢掉一半，
        而这张图之后在 Anki、预览、微信里都是按 Retina 放大回来看的，结果就是
        "糊"。嫌文件太大可在设置里换回"与屏幕一致"。
        """
        self._migrate_export_scale()
        value = str(self._s.value("export_scale", EXPORT_SCALE_NATIVE))
        return value if value in EXPORT_SCALES else EXPORT_SCALE_NATIVE

    def _migrate_export_scale(self) -> None:
        """把早期版本落地的 ``screen`` 默认值纠正成 ``native``（只做一次）。

        曾经默认过"与屏幕一致"，实测下来偏糊；可已存在的配置会压过默认值，
        光改默认值救不回来。这里用一次性标记把那批配置升级，之后用户自己
        选的任何值都照旧保留。
        """
        if self._s.value("_export_scale_migrated") is not None:
            return
        stored = self._s.value("export_scale")
        if stored is None or str(stored) == EXPORT_SCALE_SCREEN:
            self._s.setValue("export_scale", EXPORT_SCALE_NATIVE)
        self._s.setValue("_export_scale_migrated", 1)

    @export_scale.setter
    def export_scale(self, value: str) -> None:
        self._s.setValue("export_scale", value if value in EXPORT_SCALES
                         else EXPORT_SCALE_NATIVE)

    @property
    def tags(self) -> List[str]:
        raw = self._s.value("tags", "ankisnip")
        return [t.strip() for t in str(raw).split(",") if t.strip()]

    @tags.setter
    def tags(self, value: List[str]) -> None:
        self._s.setValue("tags", ",".join(value))

    @property
    def first_run_done(self) -> bool:
        return bool(int(self._s.value("first_run_done", 0)))

    def mark_first_run_done(self) -> None:
        self._s.setValue("first_run_done", 1)

    def ensure_dirs(self) -> None:
        Path(self.offline_dir).mkdir(parents=True, exist_ok=True)
        Path(self.temp_dir).mkdir(parents=True, exist_ok=True)

    def reset(self) -> None:
        self._s.clear()

    # -- 覆盖基座：默认热键用 AnkiSnip 自己的记法 -------------------------- #
    @property
    def hotkey(self) -> str:
        return str(self._s.value("hotkey", DEFAULT_HOTKEY))

    @hotkey.setter
    def hotkey(self, value: str) -> None:
        self._s.setValue("hotkey", value)


#: 跨平台默认热键（pynput 记法，见 :mod:`ankisnip.hotkey`）
from .hotkey import default_hotkey as _default_hotkey  # noqa: E402

DEFAULT_HOTKEY = _default_hotkey()
