"""Full-screen capture overlay: region selection + live annotation.

One :class:`CaptureOverlay` is created per physical screen. Everything is done
in widget-local *logical* coordinates (the overlay covers exactly one screen, so
widget-local == screen-logical). Annotations are stored in these same
coordinates and only re-based to the selection when the final image is composed.
"""

from __future__ import annotations

from enum import Enum, auto
from typing import List, Optional

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QGuiApplication,
    QImage,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QLineEdit, QWidget

from . import annotations as ann
from .capture import ScreenShot
from .config import Config
from .toolbar import CaptureToolbar

_HANDLE = 8.0       # half-size of a resize handle in px
_EDGE_GRAB = 6.0    # 边框可拖拽 / 显示缩放光标的宽度
_DIM = QColor(0, 0, 0, 110)

#: 选区手柄 → 方向光标。除了 8 个点，整条边也可以拖拽缩放。
_HANDLE_CURSORS = {
    "tl": Qt.SizeFDiagCursor, "br": Qt.SizeFDiagCursor,
    "tr": Qt.SizeBDiagCursor, "bl": Qt.SizeBDiagCursor,
    "ml": Qt.SizeHorCursor,  "mr": Qt.SizeHorCursor,
    "tm": Qt.SizeVerCursor,  "bm": Qt.SizeVerCursor,
}


class _Mode(Enum):
    IDLE = auto()
    CREATING = auto()        # dragging out the initial selection
    MOVING_SEL = auto()      # dragging the selection rectangle
    RESIZING_SEL = auto()    # dragging a selection handle
    DRAWING = auto()         # drawing an annotation


# handle positions as (name, normalized x, normalized y)
_HANDLES = [
    ("tl", 0.0, 0.0), ("tm", 0.5, 0.0), ("tr", 1.0, 0.0),
    ("ml", 0.0, 0.5), ("mr", 1.0, 0.5),
    ("bl", 0.0, 1.0), ("bm", 0.5, 1.0), ("br", 1.0, 1.0),
]


class CaptureOverlay(QWidget):
    """A single-screen capture window."""

    # action name in {"copy", "save", "pin"}, composed pixmap, selection origin
    # (global top-left of the selection, so a pin can appear in place)
    completed = Signal(str, QPixmap, QPoint)
    cancelled = Signal()

    def __init__(self, shot: ScreenShot, config: Config) -> None:
        super().__init__(None)
        self._shot = shot
        self._config = config
        self._pixmap = shot.pixmap
        self._dpr = self._pixmap.devicePixelRatio()
        # Logical-resolution copy of the screen, used to sample pixels for mosaic.
        self._base_image: QImage = self._pixmap.toImage().scaled(
            self.size_logical(), Qt.IgnoreAspectRatio, Qt.SmoothTransformation
        )

        self._mode = _Mode.IDLE
        self._selection: Optional[QRectF] = None
        self._drag_anchor = QPointF()
        self._sel_origin = QRectF()
        self._resize_handle = ""

        self._stack = ann.AnnotationStack()
        self._style = ann.Style(QColor(config.pen_color), config.pen_width)
        self._active_tool: Optional[ann.ToolType] = None
        self._current: Optional[ann.Annotation] = None
        self._moving_annotation: Optional[ann.Annotation] = None

        self._toolbar: Optional[CaptureToolbar] = None
        self._text_edit: Optional[QLineEdit] = None

        # No Qt.Tool: on macOS a Tool window never becomes the key window, so it
        # wouldn't receive Esc until clicked. A plain frameless top window can.
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.CrossCursor)
        self.setMouseTracking(True)
        self.setGeometry(shot.geometry)

    def size_logical(self):
        geo = self._shot.geometry
        return geo.size()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # Grab keyboard focus immediately so Esc works without clicking first.
        self.activateWindow()
        self.raise_()
        self.setFocus(Qt.OtherFocusReason)

    # ------------------------------------------------------------------ #
    # painting
    # ------------------------------------------------------------------ #
    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        # frozen screenshot, then a dim veil over everything
        painter.drawPixmap(0, 0, self._pixmap)
        painter.fillRect(self.rect(), _DIM)

        if self._selection is not None:
            sel = self._selection.normalized()
            # punch the selection back to full brightness
            painter.save()
            painter.setClipRect(sel)
            painter.drawPixmap(0, 0, self._pixmap)
            ctx = ann.RenderContext(base_image=self._base_image)
            self._stack.draw_all(painter, ctx)
            if self._current is not None:
                self._current.draw(painter, ctx)
            painter.restore()

            self._draw_selection_chrome(painter, sel)

    def _draw_selection_chrome(self, painter: QPainter, sel: QRectF) -> None:
        pen = QPen(QColor("#0a84ff"), 1.5)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(sel)

        # size label
        text = f"{int(sel.width())} × {int(sel.height())}"
        painter.setPen(QColor("white"))
        label_pos = sel.topLeft() + QPointF(2, -6)
        if label_pos.y() < 12:
            label_pos = sel.topLeft() + QPointF(2, 16)
        painter.fillRect(QRectF(label_pos.x() - 2, label_pos.y() - 13, 78, 16), QColor(0, 0, 0, 160))
        painter.drawText(label_pos, text)

        # resize handles (only when not drawing annotations)
        if self._active_tool is None:
            painter.setBrush(QColor("#0a84ff"))
            painter.setPen(QPen(QColor("white"), 1))
            for _name, nx, ny in _HANDLES:
                c = QPointF(sel.x() + nx * sel.width(), sel.y() + ny * sel.height())
                painter.drawRect(QRectF(c.x() - _HANDLE / 2, c.y() - _HANDLE / 2, _HANDLE, _HANDLE))

    # ------------------------------------------------------------------ #
    # mouse handling
    # ------------------------------------------------------------------ #
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.LeftButton:
            if event.button() == Qt.RightButton:
                self._cancel()
            return
        pos = event.position()

        if self._selection is None:
            self._mode = _Mode.CREATING
            self._selection = QRectF(pos, pos)
            return

        # selection exists -------------------------------------------------
        if self._active_tool is None:
            handle = self._handle_at(pos)
            if handle:
                self._mode = _Mode.RESIZING_SEL
                self._resize_handle = handle
                self._sel_origin = QRectF(self._selection)
                return
            if self._selection.contains(pos):
                self._mode = _Mode.MOVING_SEL
                self._drag_anchor = pos
                self._sel_origin = QRectF(self._selection)
                return
            return

        # an annotation tool is active ------------------------------------
        if not self._selection.normalized().contains(pos):
            return
        self._commit_text_edit()
        if self._active_tool == ann.ToolType.TEXT:
            self._begin_text(pos)
            return
        if self._active_tool == ann.ToolType.NUMBER:
            badge = ann.NumberAnnotation(self._style, pos, self._stack.next_number())
            self._stack.add(badge)
            self.update()
            return
        self._mode = _Mode.DRAWING
        self._current = ann.create_annotation(self._active_tool, self._style, pos)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        pos = event.position()
        if self._mode == _Mode.CREATING and self._selection is not None:
            self._selection.setBottomRight(pos)
            self.update()
        elif self._mode == _Mode.MOVING_SEL and self._selection is not None:
            delta = pos - self._drag_anchor
            self._selection = self._sel_origin.translated(delta)
            self._reposition_toolbar()
            self.update()
        elif self._mode == _Mode.RESIZING_SEL and self._selection is not None:
            self._resize_selection(pos)
            self._reposition_toolbar()
            self.update()
        elif self._mode == _Mode.DRAWING and self._current is not None:
            self._current.update_to(pos)
            self.update()
        else:
            self._update_cursor(pos)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.LeftButton:
            return
        if self._mode == _Mode.CREATING and self._selection is not None:
            self._selection = self._selection.normalized()
            self._ensure_toolbar()
        elif self._mode == _Mode.DRAWING and self._current is not None:
            if self._current.is_valid():
                self._stack.add(self._current)
            self._current = None
        self._mode = _Mode.IDLE
        self.update()

    # ------------------------------------------------------------------ #
    # selection helpers
    # ------------------------------------------------------------------ #
    def _handle_at(self, pos: QPointF) -> str:
        if self._selection is None:
            return ""
        sel = self._selection.normalized()
        # 先认 8 个手柄圆点（含角点）
        for name, nx, ny in _HANDLES:
            c = QPointF(sel.x() + nx * sel.width(), sel.y() + ny * sel.height())
            if abs(pos.x() - c.x()) <= _HANDLE and abs(pos.y() - c.y()) <= _HANDLE:
                return name
        # 再认边框：整条边（含边框内外 6px）都能拖拽缩放，不再只能抓 8 个小点。
        if (sel.left() - _EDGE_GRAB <= pos.x() <= sel.right() + _EDGE_GRAB
                and sel.top() - _EDGE_GRAB <= pos.y() <= sel.bottom() + _EDGE_GRAB):
            near_left = abs(pos.x() - sel.left()) <= _EDGE_GRAB
            near_right = abs(pos.x() - sel.right()) <= _EDGE_GRAB
            near_top = abs(pos.y() - sel.top()) <= _EDGE_GRAB
            near_bottom = abs(pos.y() - sel.bottom()) <= _EDGE_GRAB
            if near_left and near_top:
                return "tl"
            if near_right and near_top:
                return "tr"
            if near_left and near_bottom:
                return "bl"
            if near_right and near_bottom:
                return "br"
            if near_left:
                return "ml"
            if near_right:
                return "mr"
            if near_top:
                return "tm"
            if near_bottom:
                return "bm"
        return ""

    def _resize_selection(self, pos: QPointF) -> None:
        r = QRectF(self._sel_origin)
        h = self._resize_handle
        if "l" in h:
            r.setLeft(pos.x())
        if "r" in h:
            r.setRight(pos.x())
        if "t" in h:
            r.setTop(pos.y())
        if "b" in h:
            r.setBottom(pos.y())
        self._selection = r

    def _update_cursor(self, pos: QPointF) -> None:
        if self._selection is None or self._active_tool is not None:
            self.setCursor(Qt.CrossCursor)
            return
        handle = self._handle_at(pos)
        if handle in _HANDLE_CURSORS:
            # 边缘 / 角点给方向光标（双箭头），让人一眼知道可以拖拽缩放。
            self.setCursor(_HANDLE_CURSORS[handle])
        elif self._selection.normalized().contains(pos):
            self.setCursor(Qt.SizeAllCursor)
        else:
            self.setCursor(Qt.CrossCursor)

    # ------------------------------------------------------------------ #
    # text annotation inline editing
    # ------------------------------------------------------------------ #
    def _begin_text(self, pos: QPointF) -> None:
        edit = QLineEdit(self)
        font = edit.font()
        font.setPixelSize(self._style.font_size)
        edit.setFont(font)
        edit.setFrame(False)
        edit.setStyleSheet(
            "QLineEdit { background: transparent; border: none; padding: 0;"
            f" color: {self._style.color.name()};"
            " selection-background-color: rgba(10,132,255,120); }"
        )
        edit.move(pos.toPoint())
        edit.resize(180, self._style.font_size + 8)
        edit.editingFinished.connect(self._commit_text_edit)
        edit.show()
        edit.setFocus()
        self._text_edit = edit
        self._text_pos = QPointF(pos)

    def _commit_text_edit(self) -> None:
        if self._text_edit is None:
            return
        text = self._text_edit.text()
        edit = self._text_edit
        self._text_edit = None
        edit.deleteLater()
        if text.strip():
            self._stack.add(ann.TextAnnotation(self._style, self._text_pos, text))
            self.update()

    # ------------------------------------------------------------------ #
    # toolbar
    # ------------------------------------------------------------------ #
    def _ensure_toolbar(self) -> None:
        if self._toolbar is not None:
            self._reposition_toolbar()
            return
        tb = CaptureToolbar(self._style.color, self._style.width, self)
        tb.toolSelected.connect(self._on_tool_selected)
        tb.colorChanged.connect(self._on_color_changed)
        tb.widthChanged.connect(self._on_width_changed)
        tb.undoRequested.connect(self._undo)
        tb.redoRequested.connect(self._redo)
        tb.saveRequested.connect(lambda: self._finish("save"))
        tb.copyRequested.connect(lambda: self._finish("copy"))
        tb.pinRequested.connect(lambda: self._finish("pin"))
        tb.confirmRequested.connect(lambda: self._finish("copy"))
        tb.cancelRequested.connect(self._cancel)
        self._toolbar = tb
        tb.show()
        self._reposition_toolbar()

    def _reposition_toolbar(self) -> None:
        if self._toolbar is None or self._selection is None:
            return
        tb = self._toolbar
        tb.adjustSize()
        sel = self._selection.normalized()
        x = int(min(max(sel.left(), 0), self.width() - tb.width()))
        y = int(sel.bottom() + 8)
        if y + tb.height() > self.height():
            y = int(sel.top() - tb.height() - 8)
        if y < 0:
            y = int(sel.top() + 8)
        tb.move(x, y)

    def _on_tool_selected(self, tool: ann.ToolType) -> None:
        self._active_tool = tool
        self.setCursor(Qt.CrossCursor)
        self.update()

    def _on_color_changed(self, color: QColor) -> None:
        self._style.color = QColor(color)
        self._config.pen_color = color.name()

    def _on_width_changed(self, width: int) -> None:
        self._style.width = width
        self._config.pen_width = width

    def _undo(self) -> None:
        self._stack.undo()
        self.update()

    def _redo(self) -> None:
        self._stack.redo()
        self.update()

    # ------------------------------------------------------------------ #
    # keyboard
    # ------------------------------------------------------------------ #
    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if key == Qt.Key_Escape:
            self._cancel()
        elif key in (Qt.Key_Return, Qt.Key_Enter):
            if self._text_edit is not None:
                self._commit_text_edit()
            elif self._selection is not None:
                self._finish("copy")
        elif key == Qt.Key_Z and event.modifiers() & Qt.ControlModifier:
            self._undo()
        elif key == Qt.Key_Y and event.modifiers() & Qt.ControlModifier:
            self._redo()

    # ------------------------------------------------------------------ #
    # composition / finish
    # ------------------------------------------------------------------ #
    def compose(self) -> Optional[QPixmap]:
        if self._selection is None:
            return None
        sel = self._selection.normalized()
        if sel.width() < 1 or sel.height() < 1:
            return None
        phys = QRect(
            int(sel.x() * self._dpr),
            int(sel.y() * self._dpr),
            int(sel.width() * self._dpr),
            int(sel.height() * self._dpr),
        )
        result = self._pixmap.copy(phys)
        result.setDevicePixelRatio(self._dpr)

        painter = QPainter(result)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.translate(-sel.topLeft())
        ctx = ann.RenderContext(base_image=self._base_image)
        self._stack.draw_all(painter, ctx)
        painter.end()
        return result

    def _finish(self, action: str) -> None:
        self._commit_text_edit()
        pixmap = self.compose()
        if pixmap is None:
            return
        # Global top-left of the selection, computed before the overlay closes.
        sel = self._selection.normalized()
        origin = self.mapToGlobal(sel.topLeft().toPoint())
        self.completed.emit(action, pixmap, origin)

    def _cancel(self) -> None:
        self.cancelled.emit()
