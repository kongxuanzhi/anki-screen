"""覆盖层「选区 + 标注合一」的行为测试。

覆盖点（用户反馈驱动）：

* 默认处于选区态，选区仍可拖动（基座行为不变）；
* 选工具 / 双击 → 就地进入绘制态，**不弹新窗口**；
* 绘制态下 400% 挖空后缩回 100%，归一化坐标不变；
* 退回选区态后标注不丢；
* 导出图片里看不到挖空遮罩。
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QMouseEvent, QPixmap

from ankisnip.base import annotations as ann
from ankisnip.base.config import Config
from ankisnip.overlay_ext import AnkiCaptureOverlay, Phase


class _FakeShot:
    """离屏环境下替身 ``capture.ScreenShot``（不需要真实屏幕）。"""

    def __init__(self, pixmap: QPixmap, geo: QRect):
        self.screen = None
        self.pixmap = pixmap
        self._geo = geo

    @property
    def geometry(self):
        return self._geo


def _event(kind, x: float, y: float, button=Qt.LeftButton):
    return QMouseEvent(kind, QPointF(x, y), button, button, Qt.NoModifier)


def _drag(canvas, x0: float, y0: float, x1: float, y1: float) -> None:
    canvas.mousePressEvent(_event(QEvent.Type.MouseButtonPress, x0, y0))
    canvas.mouseMoveEvent(_event(QEvent.Type.MouseMove, x1, y1))
    canvas.mouseReleaseEvent(_event(QEvent.Type.MouseButtonRelease, x1, y1))


@pytest.fixture()
def overlay():
    geo = QRect(0, 0, 400, 300)
    pix = QPixmap(400, 300)
    pix.fill(Qt.white)
    ov = AnkiCaptureOverlay(_FakeShot(pix, geo), Config())
    ov._selection = QRectF(40, 30, 200, 150)
    ov._ensure_toolbar()
    yield ov
    ov.close()


def test_starts_in_select_phase(overlay):
    assert overlay.phase is Phase.SELECT
    assert overlay._canvas is None
    # 选区态：拖动选区内部仍然是"移动选区"（基座逻辑未被改动）
    overlay.mousePressEvent(_event(QEvent.Type.MouseButtonPress, 100.0, 100.0))
    assert overlay._mode.name == "MOVING_SEL"


def test_picking_a_tool_enters_draw_mode_in_place(overlay):
    overlay._on_tool_selected(ann.ToolType.MASK)
    assert overlay.phase is Phase.DRAW
    canvas = overlay._canvas
    assert canvas is not None
    # 画布就是选区矩形本身 —— 没有第二个窗口
    assert canvas.geometry() == QRect(40, 30, 200, 150)
    assert canvas.parent() is overlay
    assert canvas.image.size() == overlay.selection_image().size()


def test_double_click_enters_draw_mode(overlay):
    overlay.mouseDoubleClickEvent(
        _event(QEvent.Type.MouseButtonDblClick, 100.0, 100.0))
    assert overlay.phase is Phase.DRAW
    assert overlay._canvas.active_tool is ann.ToolType.MASK


def test_mask_geometry_is_stable_across_zoom(overlay):
    overlay._on_tool_selected(ann.ToolType.MASK)
    canvas = overlay._canvas
    canvas.set_absolute_zoom(4.0)
    a = canvas.transform.image_to_widget(50, 40)
    b = canvas.transform.image_to_widget(90, 70)
    _drag(canvas, a.x(), a.y(), b.x(), b.y())

    assert len(overlay.annotation_items()) == 1
    before = overlay._canvas.normalized_masks()[0]

    canvas.set_absolute_zoom(1.0)
    after = overlay._canvas.normalized_masks()[0]
    assert (after.nx, after.ny, after.nw, after.nh) == pytest.approx(
        (before.nx, before.ny, before.nw, before.nh), abs=1e-9)
    # 归一化坐标 = 原图像素 / 原图尺寸
    assert before.nx == pytest.approx(50 / 200, abs=1e-9)
    assert before.nw == pytest.approx(40 / 200, abs=1e-9)


def test_leaving_draw_mode_keeps_annotations(overlay):
    overlay._on_tool_selected(ann.ToolType.ARROW)
    canvas = overlay._canvas
    _drag(canvas, 10, 10, 60, 60)
    assert len(overlay.annotation_items()) == 1

    overlay.leave_draw_mode()
    assert overlay.phase is Phase.SELECT
    assert overlay._canvas is None
    assert len(overlay.annotation_items()) == 1
    assert isinstance(overlay.annotation_items()[0], ann.ArrowAnnotation)


class _OfflineConnect:
    """强制走离线 .apkg 路径的 AnkiConnect 替身。"""

    def is_available(self):
        return False


def test_anki_action_saves_end_to_end(overlay, tmp_path, monkeypatch):
    """覆盖层点「保存到 Anki」→ App 直接出卡片，中间不再有独立编辑器窗口。"""
    from PySide6.QtCore import QObject, QPoint
    from PySide6.QtGui import QPixmap

    import ankisnip.app as app_module
    from ankisnip.app import AnkiSnipApp
    from ankisnip.saver import AnkiSaver
    from ankisnip.settings import Settings

    monkeypatch.setattr(app_module.QDesktopServices, "openUrl", lambda _url: True)

    class _Harness(AnkiSnipApp):
        def __init__(self) -> None:
            QObject.__init__(self)
            s = Settings()
            s.offline_dir = str(tmp_path / "offline")
            s.temp_dir = str(tmp_path / "tmp")
            s.deck_name = s.model_name = "图片挖空模板sle1_0"
            s.image_field = s.kong_field = "pic_kong"
            s.question_field = "question"
            s.relation_field = "relation"
            s.field_order = ["pic_kong", "question", "relation"]
            self._config = s
            self._saver = AnkiSaver(s, connect=_OfflineConnect())
            self._overlays = []
            self.notified = []

        def _notify(self, message, icon=None, timeout=0):  # noqa: D102
            self.notified.append(message)

    overlay._on_tool_selected(ann.ToolType.MASK)
    _drag(overlay._canvas, 20, 20, 120, 100)

    harness = _Harness()
    harness._overlays.append(overlay)
    harness._on_completed("anki", QPixmap(), QPoint())

    assert harness.notified, "保存成功后应当有一次托盘提示"
    apkgs = list((tmp_path / "offline").glob("*.apkg"))
    assert apkgs, "未生成 .apkg"


def test_compose_includes_masks_for_copy_and_save(overlay):
    """复制 / 另存图片时遮挡必须画进图里（用户拿到什么就看到什么）。

    「保存到 Anki」则相反：挖空只以 kong 坐标另存，图片里不能有遮罩。
    """
    from PySide6.QtGui import QColor

    overlay._on_tool_selected(ann.ToolType.MASK)
    _drag(overlay._canvas, 20, 20, 120, 100)

    style = overlay.annotation_items()[0].style
    expected = QColor(style.color)

    # 默认（= 保存到 Anki）：挖空不进图
    anki_img = overlay.compose(include_masks=False).toImage()
    assert anki_img.pixelColor(70, 60).name() == "#ffffff"

    # 复制 / 另存：遮挡块必须可见
    copy_img = overlay.compose(include_masks=True).toImage()
    pixel = copy_img.pixelColor(70, 60)
    assert pixel != QColor(Qt.white)
    assert pixel.red() == expected.red() and pixel.green() == expected.green()


def test_finish_flags_mask_inclusion(overlay):
    """``_finish`` 按动作决定要不要把遮挡画进图（anki 除外，全部要）。"""
    overlay._on_tool_selected(ann.ToolType.MASK)
    _drag(overlay._canvas, 20, 20, 120, 100)

    for action, expect_masks in [("copy", True), ("save", True), ("pin", True),
                                 ("anki", False)]:
        overlay._finish(action)
        assert overlay._include_masks_in_export is expect_masks, action


def test_compose_excludes_masks(overlay):
    overlay._on_tool_selected(ann.ToolType.MASK)
    canvas = overlay._canvas
    _drag(canvas, 20, 20, 120, 100)

    composed = overlay.compose()
    assert composed is not None
    # 挖空区域在导出图里必须还是原图的白色（遮罩没有烧进去）
    img = composed.toImage()
    assert img.pixelColor(70, 60) == Qt.white or img.pixelColor(70, 60).name() == "#ffffff"

    # 而箭头 / 文字这类会烧录的标注必须进图
    overlay._on_tool_selected(ann.ToolType.ARROW)
    _drag(overlay._canvas, 10, 10, 150, 120)
    assert len(overlay.annotation_items()) == 2
