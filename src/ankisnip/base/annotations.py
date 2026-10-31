"""Annotation data model, rendering and undo/redo stack.

This module is intentionally free of any windowing / event-loop dependency so it
can be unit-tested headlessly (an offscreen ``QGuiApplication`` is enough). Each
annotation works in *selection-local* logical coordinates: ``(0, 0)`` is the
top-left corner of the captured selection.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import List, Optional

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
)


class ToolType(str, Enum):
    """The selectable annotation tools shown in the toolbar."""

    RECT = "rect"
    ELLIPSE = "ellipse"
    ARROW = "arrow"
    PEN = "pen"
    TEXT = "text"
    MOSAIC = "mosaic"
    HIGHLIGHT = "highlight"
    NUMBER = "number"
    # ---- AnkiSnip 注入 ----
    # 挖空（遮挡块）。与 mosaic 的本质区别：mosaic 会烧录进导出图片，
    # MASK 不参与栅格化，只以归一化坐标持久化到 Anki 的 kong 字段。
    MASK = "mask"


@dataclass
class Style:
    """Visual style shared by annotations (color, stroke width, font size)."""

    color: QColor = field(default_factory=lambda: QColor("#FF3B30"))
    width: int = 3
    font_size: int = 18

    def copy(self) -> "Style":
        return replace(self, color=QColor(self.color))


@dataclass
class RenderContext:
    """Context passed to :meth:`Annotation.draw`.

    ``base_image`` is the selection region as a ``QImage`` in the same local
    logical coordinate space as the annotations. It is used by tools that need
    to sample the underlying pixels (e.g. mosaic). It may be ``None`` for pure
    vector tools.
    """

    base_image: Optional[QImage] = None


class Annotation:
    """Base class for every annotation.

    Subclasses implement :meth:`draw`. The default geometry helpers operate on
    :meth:`bounding_rect`; override them when a tighter hit area is desired.
    """

    def __init__(self, style: Style) -> None:
        self.style = style.copy()

    # -- editing lifecycle -------------------------------------------------
    def update_to(self, point: QPointF) -> None:
        """Called while the user is still dragging to define the shape."""

    def is_valid(self) -> bool:
        """Whether the annotation is large enough to be worth keeping."""
        return True

    # -- geometry ----------------------------------------------------------
    def bounding_rect(self) -> QRectF:  # pragma: no cover - overridden
        raise NotImplementedError

    def hit_test(self, point: QPointF) -> bool:
        return self.bounding_rect().adjusted(-4, -4, 4, 4).contains(point)

    def move(self, dx: float, dy: float) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    # -- rendering ---------------------------------------------------------
    def draw(self, painter: QPainter, ctx: RenderContext) -> None:  # pragma: no cover
        raise NotImplementedError

    def _pen(self) -> QPen:
        pen = QPen(self.style.color, self.style.width)
        pen.setJoinStyle(Qt.RoundJoin)
        pen.setCapStyle(Qt.RoundCap)
        return pen


class _TwoPointAnnotation(Annotation):
    """Shared base for shapes defined by a start and end point."""

    def __init__(self, style: Style, start: QPointF) -> None:
        super().__init__(style)
        self.start = QPointF(start)
        self.end = QPointF(start)

    def update_to(self, point: QPointF) -> None:
        self.end = QPointF(point)

    def bounding_rect(self) -> QRectF:
        return QRectF(self.start, self.end).normalized()

    def is_valid(self) -> bool:
        r = self.bounding_rect()
        return r.width() >= 3 or r.height() >= 3

    def move(self, dx: float, dy: float) -> None:
        self.start += QPointF(dx, dy)
        self.end += QPointF(dx, dy)


class RectAnnotation(_TwoPointAnnotation):
    def draw(self, painter: QPainter, ctx: RenderContext) -> None:
        painter.setPen(self._pen())
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(self.bounding_rect())


class EllipseAnnotation(_TwoPointAnnotation):
    def draw(self, painter: QPainter, ctx: RenderContext) -> None:
        painter.setPen(self._pen())
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(self.bounding_rect())


class HighlightAnnotation(_TwoPointAnnotation):
    """A translucent highlighter rectangle (multiply-like)."""

    def draw(self, painter: QPainter, ctx: RenderContext) -> None:
        color = QColor(self.style.color)
        color.setAlpha(90)
        painter.save()
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        painter.drawRect(self.bounding_rect())
        painter.restore()


class ArrowAnnotation(_TwoPointAnnotation):
    def is_valid(self) -> bool:
        line = self.end - self.start
        return math.hypot(line.x(), line.y()) >= 5

    def hit_test(self, point: QPointF) -> bool:
        # distance from point to the arrow line segment
        ax, ay = self.start.x(), self.start.y()
        bx, by = self.end.x(), self.end.y()
        dx, dy = bx - ax, by - ay
        length_sq = dx * dx + dy * dy
        if length_sq == 0:
            return False
        t = max(0.0, min(1.0, ((point.x() - ax) * dx + (point.y() - ay) * dy) / length_sq))
        proj = QPointF(ax + t * dx, ay + t * dy)
        d = point - proj
        return math.hypot(d.x(), d.y()) <= max(6.0, self.style.width + 4)

    def draw(self, painter: QPainter, ctx: RenderContext) -> None:
        painter.save()
        pen = self._pen()
        painter.setPen(pen)
        painter.drawLine(self.start, self.end)

        # arrow head
        angle = math.atan2(self.end.y() - self.start.y(), self.end.x() - self.start.x())
        head = max(12.0, self.style.width * 3.5)
        spread = math.radians(25)
        p1 = QPointF(
            self.end.x() - head * math.cos(angle - spread),
            self.end.y() - head * math.sin(angle - spread),
        )
        p2 = QPointF(
            self.end.x() - head * math.cos(angle + spread),
            self.end.y() - head * math.sin(angle + spread),
        )
        painter.setBrush(self.style.color)
        painter.drawPolygon(QPolygonF([self.end, p1, p2]))
        painter.restore()


class PenAnnotation(Annotation):
    """Freehand stroke captured as a poly-line."""

    def __init__(self, style: Style, start: QPointF) -> None:
        super().__init__(style)
        self.points: List[QPointF] = [QPointF(start)]

    def update_to(self, point: QPointF) -> None:
        self.points.append(QPointF(point))

    def is_valid(self) -> bool:
        return len(self.points) >= 2

    def _path(self) -> QPainterPath:
        path = QPainterPath(self.points[0])
        for p in self.points[1:]:
            path.lineTo(p)
        return path

    def bounding_rect(self) -> QRectF:
        return self._path().boundingRect()

    def move(self, dx: float, dy: float) -> None:
        self.points = [p + QPointF(dx, dy) for p in self.points]

    def draw(self, painter: QPainter, ctx: RenderContext) -> None:
        painter.setPen(self._pen())
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(self._path())


class TextAnnotation(Annotation):
    """A piece of text anchored at a top-left position."""

    def __init__(self, style: Style, pos: QPointF, text: str = "") -> None:
        super().__init__(style)
        self.pos = QPointF(pos)
        self.text = text

    def _font(self) -> QFont:
        font = QFont()
        font.setPixelSize(self.style.font_size)
        return font

    def is_valid(self) -> bool:
        return bool(self.text.strip())

    def bounding_rect(self) -> QRectF:
        # Cheap estimate that avoids QFontMetrics so it works headlessly too.
        lines = self.text.split("\n") or [""]
        char_w = self.style.font_size * 0.62
        width = max((len(line) for line in lines), default=0) * char_w
        height = len(lines) * self.style.font_size * 1.3
        return QRectF(self.pos.x(), self.pos.y(), max(width, 8.0), max(height, self.style.font_size))

    def move(self, dx: float, dy: float) -> None:
        self.pos += QPointF(dx, dy)

    def draw(self, painter: QPainter, ctx: RenderContext) -> None:
        painter.save()
        painter.setPen(QPen(self.style.color))
        painter.setFont(self._font())
        rect = self.bounding_rect().adjusted(0, 0, 40, 10)
        painter.drawText(rect, Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap, self.text)
        painter.restore()


class NumberAnnotation(Annotation):
    """An auto-incrementing numbered badge (filled circle with a number)."""

    RADIUS = 14.0

    def __init__(self, style: Style, center: QPointF, number: int) -> None:
        super().__init__(style)
        self.center = QPointF(center)
        self.number = number

    def bounding_rect(self) -> QRectF:
        r = self.RADIUS
        return QRectF(self.center.x() - r, self.center.y() - r, 2 * r, 2 * r)

    def hit_test(self, point: QPointF) -> bool:
        d = point - self.center
        return math.hypot(d.x(), d.y()) <= self.RADIUS

    def move(self, dx: float, dy: float) -> None:
        self.center += QPointF(dx, dy)

    def draw(self, painter: QPainter, ctx: RenderContext) -> None:
        painter.save()
        painter.setPen(QPen(QColor("white"), 2))
        painter.setBrush(self.style.color)
        painter.drawEllipse(self.center, self.RADIUS, self.RADIUS)
        font = QFont()
        font.setPixelSize(int(self.RADIUS))
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QPen(QColor("white")))
        painter.drawText(self.bounding_rect(), Qt.AlignCenter, str(self.number))
        painter.restore()


class MosaicAnnotation(_TwoPointAnnotation):
    """Pixelates the underlying image within a rectangle."""

    BLOCK = 10

    def draw(self, painter: QPainter, ctx: RenderContext) -> None:
        rect = self.bounding_rect()
        if ctx.base_image is None or rect.width() < 1 or rect.height() < 1:
            # Fallback: opaque block so the area is still obscured.
            painter.fillRect(rect, QColor(120, 120, 120))
            return
        irect = rect.toRect()
        patch = ctx.base_image.copy(irect)
        if patch.isNull():
            return
        small_w = max(1, irect.width() // self.BLOCK)
        small_h = max(1, irect.height() // self.BLOCK)
        pixelated = patch.scaled(
            small_w, small_h, Qt.IgnoreAspectRatio, Qt.FastTransformation
        ).scaled(
            irect.width(), irect.height(), Qt.IgnoreAspectRatio, Qt.FastTransformation
        )
        painter.drawImage(irect.topLeft(), pixelated)


class MaskAnnotation(_TwoPointAnnotation):
    """An occlusion rectangle ("挖空").

    Rendered on top of the image in the editor but **never rasterized** into the
    exported picture. Its geometry is persisted as *normalized* (0..1) values so
    it survives zoom/pan and any display resolution.

    Coordinates stored on the instance are image pixels (same space as every
    other annotation); :meth:`to_norm` performs the conversion at persist time.
    """

    #: Fill alpha while the mask is being drawn / shown in the editor.
    FILL_ALPHA = 230
    #: AnkiSnip 注入：鼠标悬停时的填充不透明度。
    #: 挖空平时是不透明色块，画完就看不见底下的内容了——悬停时压到近乎透明，
    #: 用户可以确认"这块挡住的是不是我想挖的那句话"，移开又恢复实心。
    HOVER_ALPHA = 70

    #: AnkiSnip 注入：悬停标志。由画布在每帧绘制前写入，**不参与持久化**。
    hovered: bool = False

    def draw(self, painter: QPainter, ctx: RenderContext) -> None:
        rect = self.bounding_rect()
        color = QColor(self.style.color)
        # AnkiSnip 注入：悬停 → 近乎透明，露出被挡住的内容。
        alpha = self.HOVER_ALPHA if getattr(self, "hovered", False) else self.FILL_ALPHA
        color.setAlpha(alpha)
        painter.save()
        painter.setPen(QPen(QColor("#0a84ff"), 1.0, Qt.DashLine))
        painter.setBrush(color)
        painter.drawRect(rect)
        painter.restore()

    # -- normalized <-> image pixels -------------------------------------
    def to_norm(self, img_w: float, img_h: float) -> tuple:
        """Return ``(nx, ny, nw, nh)`` in 0..1 given the original image size."""
        if img_w <= 0 or img_h <= 0:
            raise ValueError("image size must be positive")
        r = self.bounding_rect()
        return (
            min(max(r.x() / img_w, 0.0), 1.0),
            min(max(r.y() / img_h, 0.0), 1.0),
            min(max(r.width() / img_w, 0.0), 1.0),
            min(max(r.height() / img_h, 0.0), 1.0),
        )

    @classmethod
    def from_norm(cls, style: Style, nx: float, ny: float, nw: float, nh: float,
                  img_w: float, img_h: float) -> "MaskAnnotation":
        """Inverse of :meth:`to_norm`."""
        start = QPointF(nx * img_w, ny * img_h)
        end = QPointF((nx + nw) * img_w, (ny + nh) * img_h)
        obj = cls(style, start)
        obj.update_to(end)
        return obj


_TOOL_CLASSES = {
    ToolType.RECT: RectAnnotation,
    ToolType.ELLIPSE: EllipseAnnotation,
    ToolType.ARROW: ArrowAnnotation,
    ToolType.PEN: PenAnnotation,
    ToolType.HIGHLIGHT: HighlightAnnotation,
    ToolType.MOSAIC: MosaicAnnotation,
    ToolType.MASK: MaskAnnotation,  # AnkiSnip 注入
}


def create_annotation(tool: ToolType, style: Style, start: QPointF) -> Annotation:
    """Factory for drag-created annotations (rect/ellipse/arrow/pen/etc.).

    ``TEXT`` and ``NUMBER`` are created via their own constructors because they
    need extra arguments (text content, badge number).
    """
    cls = _TOOL_CLASSES.get(tool)
    if cls is None:
        raise ValueError(f"{tool} is not a drag-created annotation")
    return cls(style, start)


class AnnotationStack:
    """Ordered list of annotations with undo/redo support."""

    def __init__(self) -> None:
        self._items: List[Annotation] = []
        self._redo: List[Annotation] = []

    @property
    def items(self) -> List[Annotation]:
        return list(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def add(self, annotation: Annotation) -> None:
        self._items.append(annotation)
        self._redo.clear()

    def can_undo(self) -> bool:
        return bool(self._items)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo(self) -> Optional[Annotation]:
        if not self._items:
            return None
        item = self._items.pop()
        self._redo.append(item)
        return item

    def redo(self) -> Optional[Annotation]:
        if not self._redo:
            return None
        item = self._redo.pop()
        self._items.append(item)
        return item

    def clear(self) -> None:
        self._items.clear()
        self._redo.clear()

    def next_number(self) -> int:
        """The value the next :class:`NumberAnnotation` should display."""
        return sum(1 for a in self._items if isinstance(a, NumberAnnotation)) + 1

    def draw_all(self, painter: QPainter, ctx: RenderContext) -> None:
        for annotation in self._items:
            annotation.draw(painter, ctx)

    def hit_test(self, point: QPointF) -> Optional[Annotation]:
        for annotation in reversed(self._items):
            if annotation.hit_test(point):
                return annotation
        return None

    # -- AnkiSnip 注入 -----------------------------------------------------
    def remove(self, annotation: Annotation) -> bool:
        """Delete an existing annotation (used by the mask erase gesture)."""
        try:
            self._items.remove(annotation)
        except ValueError:
            return False
        self._redo.clear()
        return True
