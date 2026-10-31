"""冒烟测试：所有模块可导入，覆盖层可就地装配标注（不启动真实托盘 / 热键）。"""

from __future__ import annotations

import importlib

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

MODULES = [
    "ankisnip",
    "ankisnip.app",
    "ankisnip.coords",
    "ankisnip.editor_canvas",
    "ankisnip.editor_toolbar",
    "ankisnip.overlay_ext",
    "ankisnip.rasterize",
    "ankisnip.saver",
    "ankisnip.settings",
    "ankisnip.settings_dialog",
    "ankisnip.logging_setup",
    "ankisnip.anki_connect",
    "ankisnip.apkg_export",
    "ankisnip.kong",
    "ankisnip.kong.crypto",
    "ankisnip.kong.serialize",
    "ankisnip.base.annotations",
    "ankisnip.base.app",
    "ankisnip.base.capture",
    "ankisnip.base.overlay",
    "ankisnip.base.toolbar",
    "ankisnip.base.pin",
    "ankisnip.base.permissions",
]


@pytest.mark.parametrize("name", MODULES)
def test_module_imports(name):
    assert importlib.import_module(name) is not None


def test_overlay_assembles_and_reaches_anki_save():
    """覆盖层就地装配标注画布，工具条上的「保存到 Anki」能走到完成动作。"""
    from PySide6.QtCore import QRect, QRectF
    from PySide6.QtGui import QPixmap

    from ankisnip.base.config import Config
    from ankisnip.overlay_ext import AnkiCaptureOverlay, Phase

    class _FakeShot:
        def __init__(self, pixmap: QPixmap, geo: QRect):
            self.screen = None
            self.pixmap = pixmap
            self._geo = geo

        @property
        def geometry(self):
            return self._geo

    geo = QRect(0, 0, 320, 240)
    pix = QPixmap(320, 240)
    pix.fill(Qt.white)
    overlay = AnkiCaptureOverlay(_FakeShot(pix, geo), Config())

    assert overlay.phase is Phase.SELECT
    overlay._selection = QRectF(20, 20, 160, 120)
    overlay._ensure_toolbar()
    assert overlay._toolbar is not None

    received = []
    overlay._finish = lambda action: received.append(action)  # type: ignore[method-assign]
    overlay._toolbar.saveToAnkiRequested.emit()
    assert received == ["anki"]
    overlay.close()


def test_mask_tool_is_registered_in_base():
    from ankisnip.base import annotations as ann

    assert ann.ToolType.MASK.value == "mask"
    created = ann.create_annotation(ann.ToolType.MASK, ann.Style(), __import__(
        "PySide6.QtCore", fromlist=["QPointF"]).QPointF(1, 2))
    assert isinstance(created, ann.MaskAnnotation)
