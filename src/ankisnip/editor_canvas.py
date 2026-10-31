"""可缩放 / 可平移的标注画布。

职责边界
--------
* **复用基座**：``annotations.Annotation`` 家族（箭头 / 文字 / 挖空 …）、
  ``AnnotationStack``（撤销 / 重做）、``RenderContext``、``Style``。
* **本模块新增**：视图变换驱动的绘制与鼠标交互（滚轮缩放、空格 / 中键平移、
  适应窗口）、命中选中、拖拽移动、挖空手柄缩放、行内文字输入。

所有鼠标坐标都必须先经 :class:`~ankisnip.coords.ViewTransform` 转成 image 坐标，
再交给标注对象——业务代码里不允许出现裸的屏幕坐标运算。
"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QImage,
    QMouseEvent,
    QPainter,
    QPen,
    QPixmap,
    QWheelEvent,
)
from PySide6.QtWidgets import QLineEdit, QSizePolicy, QWidget

from .base import annotations as ann
from .coords import ViewTransform

_HANDLE_HALF = 4.0
_HANDLE_POSITIONS = [
    ("tl", 0.0, 0.0), ("tm", 0.5, 0.0), ("tr", 1.0, 0.0),
    ("ml", 0.0, 0.5), ("mr", 1.0, 0.5),
    ("bl", 0.0, 1.0), ("bm", 0.5, 1.0), ("br", 1.0, 1.0),
]
_BACKGROUND = QColor("#1e1e1e")
_HANDLE_COLOR = QColor("#0a84ff")


class EditorCanvas(QWidget):
    """显示原图 + 标注，支持缩放 / 平移 / 编辑。"""

    stackChanged = Signal()
    viewChanged = Signal()
    saveRequested = Signal()
    cancelRequested = Signal()

    def __init__(self, image: QImage, parent: QWidget | None = None,
                 stack: Optional["ann.AnnotationStack"] = None,
                 style: Optional["ann.Style"] = None) -> None:
        super().__init__(parent)
        self.image = image
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        # 选区可能很小，下限必须低于选区尺寸，否则子控件会撑破选区矩形。
        self.setMinimumSize(8, 8)

        # 允许外部持有标注栈：覆盖层在"选区态 / 绘制态"之间来回切换时，
        # 画布会被销毁重建，但标注必须活着。
        self.stack = stack if stack is not None else ann.AnnotationStack()
        # style 允许与覆盖层**共用同一个对象**：工具栏改了颜色 / 线宽之后，
        # 新画的标注立刻跟着变（标注在创建时会 copy 一份自己的样式）。
        self.style = style if style is not None else ann.Style(QColor(ann.Style().color), 3, 18)
        self.transform = ViewTransform(image.width(), image.height(), 1.0)

        self.active_tool: Optional[ann.ToolType] = None
        self._current: Optional[ann.Annotation] = None
        self._selected: Optional[ann.Annotation] = None
        self._moving: Optional[ann.Annotation] = None
        self._move_origin = QPointF()
        self._hovered: Optional[ann.Annotation] = None
        self._panning = False
        self._pan_origin = QPoint()
        self._space_held = False
        self._resize_handle = ""
        self._resize_origin = QRectF()
        self._text_edit: Optional[QLineEdit] = None
        self._text_pos = QPointF()

        self.fit_to_window()

    # -- 视图 ------------------------------------------------------------ #
    def fit_to_window(self, margin: float = 16.0) -> None:
        """适应窗口。``margin=0`` 表示严格铺满视口（覆盖层里视口 = 选区矩形）。"""
        self.transform.fit(self.width(), self.height(), margin)
        self.update()
        self.viewChanged.emit()

    def set_absolute_zoom(self, zoom: float) -> None:
        self.transform.set_zoom(zoom, self.width(), self.height())
        self.update()
        self.viewChanged.emit()

    def zoom_by(self, factor: float, anchor: Optional[QPointF] = None) -> None:
        if anchor is None:
            anchor = QPointF(self.width() / 2.0, self.height() / 2.0)
        self.transform.zoom_at(anchor.x(), anchor.y(), factor)
        self.update()
        self.viewChanged.emit()

    # -- 工具 / 样式 ------------------------------------------------------ #
    def set_tool(self, tool: Optional[ann.ToolType]) -> None:
        self._commit_text_edit()
        self.active_tool = tool
        if tool is None:
            self.setCursor(Qt.ArrowCursor)
        else:
            self.setCursor(Qt.CrossCursor)
        self.update()

    def set_color(self, color: QColor) -> None:
        self.style.color = QColor(color)
        if isinstance(self._selected, ann.MaskAnnotation):
            self._selected.style.color = QColor(color)
        self.update()

    def set_width(self, width: int) -> None:
        self.style.width = int(width)

    def set_font_size(self, size: int) -> None:
        self.style.font_size = int(size)

    # -- 撤销 / 重做 / 删除 ----------------------------------------------- #
    def undo(self) -> None:
        self._commit_text_edit()
        self._selected = None
        self.stack.undo()
        self.stackChanged.emit()
        self.update()

    def redo(self) -> None:
        self._commit_text_edit()
        self._selected = None
        self.stack.redo()
        self.stackChanged.emit()
        self.update()

    def delete_selected(self) -> None:
        if self._selected is not None and self.stack.remove(self._selected):
            self._selected = None
            self.stackChanged.emit()
            self.update()

    # -- 数据导出 --------------------------------------------------------- #
    def mask_rects(self) -> List["ann.MaskAnnotation"]:
        return [a for a in self.stack.items if isinstance(a, ann.MaskAnnotation)]

    def normalized_masks(self):
        """返回 ``MaskRect`` 列表（归一化坐标 0~1），供 kong 序列化使用。"""
        from .kong.serialize import MaskRect

        out = []
        for m in self.mask_rects():
            nx, ny, nw, nh = m.to_norm(self.image.width(), self.image.height())
            if nw <= 0 or nh <= 0:
                continue
            out.append(MaskRect(nx=nx, ny=ny, nw=nw, nh=nh, color=m.style.color.name()))
        return out

    # -- Qt 事件 ---------------------------------------------------------- #
    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        # 保持图片中心不动的"软适应"：仅重新居中，不改变用户设定的缩放。
        self.transform.center(self.width(), self.height())
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.setRenderHint(QPainter.TextAntialiasing)
        painter.fillRect(self.rect(), _BACKGROUND)

        tf = self.transform
        top_left = tf.image_to_widget(0, 0)
        painter.drawImage(
            QRectF(top_left.x(), top_left.y(), tf.display_w, tf.display_h), self.image
        )

        # ---- 标注（world 变换：业务代码只给 image 坐标）----
        painter.save()
        painter.translate(tf.tx, tf.ty)
        painter.scale(tf.total_scale, tf.total_scale)
        ctx = ann.RenderContext(base_image=self.image)
        self._apply_hover_flags()
        for item in self.stack.items:
            item.draw(painter, ctx)
        if self._current is not None:
            self._current.draw(painter, ctx)
        painter.restore()

        # ---- 选中框 / 手柄（widget 坐标，保持 1px 手感）----
        if self._selected is not None:
            self._draw_selection(painter, self._selected)
        painter.end()

    # -- 悬停（挖空透视）-------------------------------------------------- #
    def _apply_hover_flags(self) -> None:
        """把"当前悬停在哪块挖空上"写进绘制状态。

        悬停是**显示态**而不是数据态：它不进撤销栈、不参与序列化，
        所以只在每帧绘制前临时设置到标注对象上。
        """
        for item in self.stack.items:
            if isinstance(item, ann.MaskAnnotation):
                item.hovered = item is self._hovered

    def _update_hover(self, widget_pos) -> None:
        """鼠标移动时更新悬停目标；变化了才重绘（避免每像素一次 repaint）。"""
        image_pos = self.transform.widget_to_image(widget_pos.x(), widget_pos.y())
        hit = self.stack.hit_test(image_pos)
        # 只有挖空需要"透视"；选中/非选中都给反馈，用户能先看清再决定改不改。
        new = hit if isinstance(hit, ann.MaskAnnotation) else None
        if new is self._hovered:
            return
        self._hovered = new
        self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802
        if self._hovered is not None:
            self._hovered = None
            self.update()
        super().leaveEvent(event)

    def _draw_selection(self, painter: QPainter, item: ann.Annotation) -> None:
        rect = self.transform.image_rect_to_widget(item.bounding_rect())
        painter.setPen(QPen(_HANDLE_COLOR, 1.0, Qt.DashLine))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(rect)
        painter.setPen(QPen(QColor("white"), 1.0))
        painter.setBrush(_HANDLE_COLOR)
        for _name, nx, ny in _HANDLE_POSITIONS:
            c = QPointF(rect.x() + nx * rect.width(), rect.y() + ny * rect.height())
            painter.drawRect(QRectF(c.x() - _HANDLE_HALF, c.y() - _HANDLE_HALF,
                                    2 * _HANDLE_HALF, 2 * _HANDLE_HALF))

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        delta = event.angleDelta().y()
        if delta == 0:
            return
        factor = 1.25 if delta > 0 else 1 / 1.25
        self.zoom_by(factor, event.position())
        event.accept()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        key = event.key()
        mods = event.modifiers()
        if key == Qt.Key_Space and not self._space_held:
            self._space_held = True
            self.setCursor(Qt.OpenHandCursor)
            event.accept()
            return
        if key == Qt.Key_Escape:
            self.cancelRequested.emit()
        elif key in (Qt.Key_Delete, Qt.Key_Backspace):
            self.delete_selected()
        elif key == Qt.Key_Z and mods & Qt.ControlModifier:
            self.undo()
        elif key == Qt.Key_Y and mods & Qt.ControlModifier:
            self.redo()
        elif key in (Qt.Key_Return, Qt.Key_Enter):
            self._commit_text_edit()
        elif key == Qt.Key_S and mods & Qt.ControlModifier:
            self._commit_text_edit()
            self.saveRequested.emit()
        elif key == Qt.Key_0 and mods & Qt.ControlModifier:
            self.fit_to_window()
        else:
            super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key_Space:
            self._space_held = False
            self.setCursor(Qt.ArrowCursor if self.active_tool is None else Qt.CrossCursor)
            event.accept()
            return
        super().keyReleaseEvent(event)

    # -- 鼠标 ------------------------------------------------------------- #
    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        widget_pos = event.position()
        image_pos = self.transform.widget_to_image(widget_pos.x(), widget_pos.y())

        # 中键 / 空格 → 平移
        if event.button() == Qt.MiddleButton or (self._space_held and event.button() == Qt.LeftButton):
            self._panning = True
            self._pan_origin = widget_pos.toPoint()
            self.setCursor(Qt.ClosedHandCursor)
            return
        if event.button() == Qt.RightButton:
            hit = self.stack.hit_test(image_pos)
            if hit is not None:
                self._selected = hit
                self.delete_selected()
            return
        if event.button() != Qt.LeftButton:
            return

        self._commit_text_edit()

        # 无工具 → 选中 / 移动 / 缩放手柄
        if self.active_tool is None:
            handle = self._handle_at(widget_pos)
            if handle and isinstance(self._selected, ann.MaskAnnotation):
                self._resize_handle = handle
                self._resize_origin = QRectF(self._selected.bounding_rect())
                return
            hit = self.stack.hit_test(image_pos)
            self._selected = hit
            if hit is not None:
                self._moving = hit
                self._move_origin = image_pos
            self.update()
            return

        # 有工具 → 创建新标注
        self._selected = None
        if self.active_tool == ann.ToolType.TEXT:
            self._begin_text(image_pos, widget_pos)
            return
        if self.active_tool == ann.ToolType.NUMBER:
            badge = ann.NumberAnnotation(self.style, image_pos, self.stack.next_number())
            self.stack.add(badge)
            self.stackChanged.emit()
            self.update()
            return
        self._current = ann.create_annotation(self.active_tool, self.style, image_pos)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        widget_pos = event.position()
        if self._panning:
            delta = widget_pos.toPoint() - self._pan_origin
            self.transform.pan(delta.x(), delta.y())
            self._pan_origin = widget_pos.toPoint()
            self.update()
            return

        image_pos = self.transform.widget_to_image(widget_pos.x(), widget_pos.y())

        if self._current is not None:
            self._current.update_to(image_pos)
            self.update()
            return
        if self._moving is not None:
            delta = image_pos - self._move_origin
            self._moving.move(delta.x(), delta.y())
            self._move_origin = image_pos
            self.update()
            return
        if self._resize_handle and isinstance(self._selected, ann.MaskAnnotation):
            self._resize_mask(widget_pos)
            self.update()
            return
        # 空闲（没在画 / 没在拖 / 没在缩放手柄）才谈得上"悬停透视"。
        if self._current is None and self._moving is None:
            self._update_hover(widget_pos)
        if self.active_tool is None:
            self._update_cursor(widget_pos)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._panning:
            self._panning = False
            self.setCursor(Qt.ArrowCursor if self.active_tool is None else Qt.CrossCursor)
            return
        if self._resize_handle:
            self._resize_handle = ""
            self.update()
            return
        if self._current is not None:
            if self._current.is_valid():
                self.stack.add(self._current)
                self.stackChanged.emit()
            self._current = None
        self._moving = None
        self.update()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        """双击挖空 → 删除（与现有 Anki 插件的交互习惯一致）。"""
        image_pos = self.transform.widget_to_image(event.position().x(), event.position().y())
        hit = self.stack.hit_test(image_pos)
        if isinstance(hit, ann.MaskAnnotation):
            self._selected = hit
            self.delete_selected()

    # -- 手柄 ------------------------------------------------------------- #
    def _handle_at(self, widget_pos: QPointF) -> str:
        if self._selected is None:
            return ""
        rect = self.transform.image_rect_to_widget(self._selected.bounding_rect())
        for name, nx, ny in _HANDLE_POSITIONS:
            c = QPointF(rect.x() + nx * rect.width(), rect.y() + ny * rect.height())
            if (abs(widget_pos.x() - c.x()) <= _HANDLE_HALF + 2
                    and abs(widget_pos.y() - c.y()) <= _HANDLE_HALF + 2):
                return name
        return ""

    def _resize_mask(self, widget_pos: QPointF) -> None:
        """只给挖空实现手柄缩放（基座其他标注没有 resize 接口，不动它们）。"""
        mask = self._selected
        origin = self._resize_origin
        p = self.transform.widget_to_image(widget_pos.x(), widget_pos.y())
        left, top = origin.x(), origin.y()
        right, bottom = origin.right(), origin.bottom()
        h = self._resize_handle
        if "l" in h:
            left = min(p.x(), right - 1)
        if "r" in h:
            right = max(p.x(), left + 1)
        if "t" in h:
            top = min(p.y(), bottom - 1)
        if "b" in h:
            bottom = max(p.y(), top + 1)
        mask.start = QPointF(left, top)
        mask.end = QPointF(right, bottom)

    def _update_cursor(self, widget_pos: QPointF) -> None:
        if self._handle_at(widget_pos):
            self.setCursor(Qt.SizeFDiagCursor)
        else:
            self.setCursor(Qt.ArrowCursor)

    # -- 行内文字 --------------------------------------------------------- #
    def _begin_text(self, image_pos: QPointF, widget_pos: QPointF) -> None:
        edit = QLineEdit(self)
        px = max(8, int(self.style.font_size * self.transform.total_scale))
        edit.setStyleSheet(
            f"background: rgba(0,0,0,170); color: {self.style.color.name()};"
            f" border: 1px dashed #0a84ff; font-size: {px}px;"
        )
        edit.move(widget_pos.toPoint())
        edit.resize(220, px + 12)
        edit.editingFinished.connect(self._commit_text_edit)
        edit.show()
        edit.setFocus()
        self._text_edit = edit
        self._text_pos = QPointF(image_pos)

    def _commit_text_edit(self) -> None:
        if self._text_edit is None:
            return
        text = self._text_edit.text()
        edit = self._text_edit
        self._text_edit = None
        edit.deleteLater()
        if text.strip():
            self.stack.add(ann.TextAnnotation(self.style, self._text_pos, text))
            self.stackChanged.emit()
        self.update()

    # -- 其它 ------------------------------------------------------------- #
    def to_pixmap(self) -> QPixmap:
        return QPixmap.fromImage(self.image)
