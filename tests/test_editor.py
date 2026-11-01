"""编辑器交互测试：400% 下画挖空 → 缩回 100% 位置大小不变；撤销 / 重做有效。"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QColor, QFontMetricsF, QImage, QKeyEvent, QMouseEvent

from ankisnip.base import annotations as ann
from ankisnip.editor_canvas import EditorCanvas


def _event(kind, x: float, y: float, button=Qt.LeftButton):
    return QMouseEvent(kind, QPointF(x, y), button, button, Qt.NoModifier)


def _drag(canvas: EditorCanvas, p0: QPointF, p1: QPointF) -> None:
    canvas.mousePressEvent(_event(QEvent.Type.MouseButtonPress, p0.x(), p0.y()))
    canvas.mouseMoveEvent(_event(QEvent.Type.MouseMove, p1.x(), p1.y()))
    canvas.mouseReleaseEvent(_event(QEvent.Type.MouseButtonRelease, p1.x(), p1.y()))


@pytest.fixture()
def canvas():
    img = QImage(400, 300, QImage.Format_RGB32)
    img.fill(Qt.white)
    c = EditorCanvas(img)
    c.resize(800, 600)
    c.fit_to_window()
    return c


def test_mask_created_at_400_percent(canvas):
    canvas.set_tool(ann.ToolType.MASK)
    canvas.set_absolute_zoom(4.0)
    assert canvas.transform.absolute_zoom == pytest.approx(4.0, rel=0.05)

    a = canvas.transform.image_to_widget(100, 100)   # 归一化 0.25 / 0.333
    b = canvas.transform.image_to_widget(180, 160)   # 归一化 0.45 / 0.533
    _drag(canvas, a, b)

    assert len(canvas.stack.items) == 1
    masks = canvas.normalized_masks()
    assert len(masks) == 1
    assert masks[0].nx == pytest.approx(0.25, abs=1e-6)
    assert masks[0].ny == pytest.approx(100 / 300, abs=1e-6)
    assert masks[0].nw == pytest.approx(0.20, abs=1e-6)
    assert masks[0].nh == pytest.approx(60 / 300, abs=1e-6)


def test_mask_geometry_survives_zoom_change(canvas):
    canvas.set_tool(ann.ToolType.MASK)
    canvas.set_absolute_zoom(4.0)
    a = canvas.transform.image_to_widget(100, 100)
    b = canvas.transform.image_to_widget(180, 160)
    _drag(canvas, a, b)
    before = canvas.normalized_masks()[0]

    canvas.set_absolute_zoom(1.0)
    after = canvas.normalized_masks()[0]
    assert (after.nx, after.ny, after.nw, after.nh) == pytest.approx(
        (before.nx, before.ny, before.nw, before.nh), abs=1e-9)

    canvas.fit_to_window()
    after2 = canvas.normalized_masks()[0]
    assert (after2.nx, after2.ny, after2.nw, after2.nh) == pytest.approx(
        (before.nx, before.ny, before.nw, before.nh), abs=1e-9)


def test_undo_redo_for_mask_arrow_and_text(canvas):
    canvas.set_tool(ann.ToolType.MASK)
    _drag(canvas, QPointF(20, 20), QPointF(80, 80))
    canvas.set_tool(ann.ToolType.ARROW)
    _drag(canvas, QPointF(100, 100), QPointF(200, 200))
    canvas.stack.add(ann.TextAnnotation(canvas.style, QPointF(10, 250), "重点"))
    assert len(canvas.stack.items) == 3

    canvas.undo()
    assert len(canvas.stack.items) == 2
    canvas.undo()
    assert len(canvas.stack.items) == 1
    assert isinstance(canvas.stack.items[0], ann.MaskAnnotation)
    canvas.redo()
    assert len(canvas.stack.items) == 2
    assert isinstance(canvas.stack.items[1], ann.ArrowAnnotation)


def test_right_click_deletes_mask(canvas):
    canvas.set_tool(ann.ToolType.MASK)
    _drag(canvas, QPointF(20, 20), QPointF(80, 80))
    assert len(canvas.stack.items) == 1
    canvas.set_tool(None)
    canvas.mousePressEvent(
        _event(QEvent.Type.MouseButtonPress, 50, 50, Qt.RightButton))
    assert len(canvas.stack.items) == 0


def test_tiny_drag_is_rejected(canvas):
    canvas.set_tool(ann.ToolType.MASK)
    _drag(canvas, QPointF(50, 50), QPointF(51, 51))
    assert len(canvas.stack.items) == 0


def test_wheel_zoom_keeps_anchor():
    img = QImage(400, 300, QImage.Format_RGB32)
    img.fill(Qt.white)
    c = EditorCanvas(img)
    c.resize(800, 600)
    c.fit_to_window()
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QWheelEvent

    anchor = QPointF(300.0, 220.0)
    before = c.transform.widget_to_image(anchor.x(), anchor.y())
    c.wheelEvent(QWheelEvent(anchor, anchor, QPoint(0, 0), QPoint(0, 120),
                             Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False))
    after = c.transform.widget_to_image(anchor.x(), anchor.y())
    assert after.x() == pytest.approx(before.x(), abs=1e-6)
    assert after.y() == pytest.approx(before.y(), abs=1e-6)
    assert c.transform.scale > 1.0


def test_text_annotation_stays_on_one_line():
    """中文按字符数估算会偏窄；必须用字体度量，且渲染不能自动换行。"""
    style = ann.Style(QColor("#ff3e05"), 3, 24)
    text = "这是一段比较长的中文文字用于验证不会自动换行"
    note = ann.TextAnnotation(style, QPointF(10, 10), text)
    rect = note.bounding_rect()
    metrics = QFontMetricsF(note._font())
    assert rect.width() >= metrics.horizontalAdvance(text)
    assert rect.height() < metrics.lineSpacing() * 1.9, "单行文字不应折成两行"


def test_number_keys_request_palette_color(canvas):
    got: list = []
    canvas.colorIndexSelected.connect(got.append)
    canvas.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key_5, Qt.NoModifier))
    assert got == [4], "数字键 5 应选择第 5 个颜色（索引 4）"


def test_text_annotation_keeps_manual_line_breaks():
    style = ann.Style(QColor("#ff3e05"), 3, 24)
    note = ann.TextAnnotation(style, QPointF(10, 10), "第一行\n第二行")
    metrics = QFontMetricsF(note._font())
    assert note.bounding_rect().height() >= metrics.lineSpacing() * 2


def test_mask_handle_cursors_are_directional(canvas):
    canvas.set_tool(ann.ToolType.MASK)
    _drag(canvas, QPointF(50, 50), QPointF(150, 120))
    mask = canvas.stack.items[0]
    canvas.set_tool(None)
    canvas._selected = mask

    rect = canvas.transform.image_rect_to_widget(mask.bounding_rect())
    canvas._update_cursor(QPointF(rect.left(), rect.center().y()))
    assert canvas.cursor().shape() == Qt.SizeHorCursor
    canvas._update_cursor(QPointF(rect.center().x(), rect.top()))
    assert canvas.cursor().shape() == Qt.SizeVerCursor
    canvas._update_cursor(rect.topLeft())
    assert canvas.cursor().shape() == Qt.SizeFDiagCursor
    canvas._update_cursor(rect.topRight())
    assert canvas.cursor().shape() == Qt.SizeBDiagCursor
