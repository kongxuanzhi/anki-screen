"""挖空悬停透视 + Carbon 事件去重。

背景（用户反馈）
----------------
1. **鼠标放在挖空上，底下的内容看不出来。** 挖空默认是不透明色块
   （``FILL_ALPHA = 230``），画完以后用户只能"凭记忆"判断这块挡住的是不是目标句子。
   现在悬停时降到 ``HOVER_ALPHA``，等于是"半透明预览"，移开又恢复实心。
2. **同一个 EventRef 会走两条派发链。** 为了修复"托盘菜单之后快捷键失灵"，
   Carbon 处理器装到了 application 与 dispatcher **两个**目标上。同一帧里回调会被
   调用两次 → 一次按键截两张图。这里按 EventRef 指针去重。
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QPointF, QRect, Qt
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter, QPixmap

from ankisnip.base import annotations as ann
from ankisnip.settings import Settings


def _event(kind, x, y, button=Qt.LeftButton):
    return QMouseEvent(kind, QPointF(x, y), button, button, Qt.NoModifier)


def _drag(widget, x0, y0, x1, y1):
    widget.mousePressEvent(_event(QEvent.Type.MouseButtonPress, x0, y0))
    widget.mouseMoveEvent(_event(QEvent.Type.MouseMove, x1, y1))
    widget.mouseReleaseEvent(_event(QEvent.Type.MouseButtonRelease, x1, y1))


class _Shot:
    """dpr = 1 的伪截图：屏幕 logical 400x300，选区 200x150。"""

    def __init__(self):
        pix = QPixmap(400, 300)
        pix.fill(Qt.white)
        self.screen = None
        self.pixmap = pix
        self._geo = QRect(0, 0, 400, 300)

    @property
    def geometry(self):
        return self._geo


@pytest.fixture()
def overlay():
    from ankisnip.overlay_ext import AnkiCaptureOverlay

    ov = AnkiCaptureOverlay(_Shot(), Settings())
    ov._selection = QRect(40, 30, 200, 150).toRectF()
    ov._ensure_toolbar()
    yield ov
    ov.close()


# --- 1. 绘制层：悬停真的把不透明度降下来了 ------------------------------ #
def _paint_mask(mask, hovered: bool) -> QColor:
    """把同一个挖空画到白底画布上，返回中心像素。"""
    image = QImage(100, 100, QImage.Format_RGB32)
    image.fill(Qt.white)
    mask.hovered = hovered
    painter = QPainter(image)
    mask.draw(painter, ann.RenderContext(base_image=image))
    painter.end()
    return image.pixelColor(50, 50)


def test_mask_is_opaque_by_default():
    style = ann.Style(QColor("#ff0000"), 2, 18)
    mask = ann.MaskAnnotation(style, QPointF(10, 10))
    mask.update_to(QPointF(90, 90))

    solid = _paint_mask(mask, hovered=False)
    assert solid.red() > 200 and solid.green() < 80, \
        f"默认应该是接近实心的遮挡块，实际 {solid.name()}"


def test_mask_reveals_content_while_hovered():
    style = ann.Style(QColor("#ff0000"), 2, 18)
    mask = ann.MaskAnnotation(style, QPointF(10, 10))
    mask.update_to(QPointF(90, 90))

    solid = _paint_mask(mask, hovered=False)
    ghost = _paint_mask(mask, hovered=True)
    # 透明白底 → 绿通道被拉回来，视觉上就是"能看见下面"
    assert ghost.green() > 150, \
        f"悬停时要能透出底下的内容，实际 {ghost.name()}（默认 {solid.name()}）"
    assert ghost.green() > solid.green() + 100
    # 松开鼠标回到不透明
    assert _paint_mask(mask, hovered=False).green() < 80


# --- 2. 画布：鼠标下面的那块挖空才是"悬停" ------------------------------ #
def test_hover_follows_the_mouse(overlay):
    overlay._on_tool_selected(ann.ToolType.MASK)
    canvas = overlay._canvas
    _drag(canvas, 10.0, 10.0, 100.0, 80.0)
    mask = overlay.annotation_items()[0]
    assert isinstance(mask, ann.MaskAnnotation)

    # 离开"绘制工具"，进入"选中/编辑"态，悬停才有意义
    overlay._on_tool_selected(None)
    canvas.set_tool(None)
    assert mask.hovered is False

    canvas.mouseMoveEvent(_event(QEvent.Type.MouseMove, 50.0, 40.0))
    assert canvas._hovered is mask
    canvas.paintEvent(None)          # _apply_hover_flags 在绘制前写标志
    assert mask.hovered is True

    canvas.mouseMoveEvent(_event(QEvent.Type.MouseMove, 190.0, 140.0))
    assert canvas._hovered is None
    canvas.paintEvent(None)
    assert mask.hovered is False


def test_hover_clears_when_the_mouse_leaves(overlay):
    overlay._on_tool_selected(ann.ToolType.MASK)
    canvas = overlay._canvas
    _drag(canvas, 10.0, 10.0, 100.0, 80.0)
    mask = overlay.annotation_items()[0]

    canvas.set_tool(None)
    canvas.mouseMoveEvent(_event(QEvent.Type.MouseMove, 50.0, 40.0))
    assert canvas._hovered is mask

    canvas.leaveEvent(QEvent(QEvent.Type.Leave))
    assert canvas._hovered is None
    assert mask.hovered is False


def test_only_the_hovered_mask_is_transparent(overlay):
    """两块挖空重叠时，只透鼠标下面那一块，另一块保持实心。"""
    overlay._on_tool_selected(ann.ToolType.MASK)
    canvas = overlay._canvas
    _drag(canvas, 0.0, 0.0, 100.0, 150.0)
    _drag(canvas, 100.0, 0.0, 200.0, 150.0)
    left, right = overlay.annotation_items()
    assert isinstance(left, ann.MaskAnnotation) and isinstance(right, ann.MaskAnnotation)

    canvas.set_tool(None)
    # 命中取最上层：右边那块是最后画的，(150,75) 落在它里面
    canvas.mouseMoveEvent(_event(QEvent.Type.MouseMove, 150.0, 75.0))
    canvas.paintEvent(None)
    assert right.hovered is True
    assert left.hovered is False


# --- 3. Carbon：同一个 EventRef 只回调一次 ------------------------------ #
@pytest.mark.skipif(__import__("sys").platform != "darwin", reason="仅 macOS 后端")
def test_carbon_event_is_deduplicated_across_targets():
    from ankisnip.hotkey import _MacCarbonBackend

    backend = _MacCarbonBackend()
    calls = []
    # 旧实现用 _user_callback；现在按 id 分发，直接打桩 _callback_for_event。
    backend._callback_for_event = lambda event: (lambda: calls.append(1))

    # 两个目标上的处理器收到的是同一个 EventRef
    backend._on_hotkey_event(None, 0x1000, None)
    backend._on_hotkey_event(None, 0x1000, None)
    assert len(calls) == 1, "同一次按键走两条派发链，必须只触发一次"

    # 下一次按键是新 EventRef，要照常生效
    backend._on_hotkey_event(None, 0x2000, None)
    assert len(calls) == 2

    # 指针被系统复用但属于新的一次按键时不能被误吞
    backend._on_hotkey_event(None, 0x2000, None)
    assert len(calls) == 2
    backend._on_hotkey_event(None, 0x3000, None)
    assert len(calls) == 3


@pytest.mark.skipif(__import__("sys").platform != "darwin", reason="仅 macOS 后端")
def test_carbon_callback_for_event_dispatches_by_id():
    """多条热键共用一个事件处理器，按 EventHotKeyID.id 分发；签名不符忽略。"""
    import ctypes

    from ankisnip.hotkey import _MacCarbonBackend

    backend = _MacCarbonBackend()
    struct = backend._types()[0]

    class FakeCarbon:
        def GetEventParameter(self, event, _name, _kind, _types,
                              _size, _actual, out):
            target = ctypes.cast(out, ctypes.POINTER(struct))
            target.contents.signature = \
                0x414B534E if (event & 0xFF) != 99 else 0xDEADBEEF
            target.contents.id = event & 0xFF
            return 0

    backend._carbon = FakeCarbon()
    backend._callbacks = {1: lambda: None, 2: lambda: None}

    assert backend._callback_for_event(0x1001) is backend._callbacks[1]
    assert backend._callback_for_event(0x1002) is backend._callbacks[2]
    assert backend._callback_for_event(0x1003) is None  # 没注册的 id
    assert backend._callback_for_event(0x1063) is None  # 签名不是本应用
