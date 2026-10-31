"""Headless unit tests for the annotation model (geometry, undo/redo, render)."""

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QColor, QImage, QPainter

from ankisnip.base.annotations import (
    AnnotationStack,
    ArrowAnnotation,
    EllipseAnnotation,
    MosaicAnnotation,
    NumberAnnotation,
    PenAnnotation,
    RectAnnotation,
    RenderContext,
    Style,
    TextAnnotation,
    ToolType,
    create_annotation,
)


def _style():
    return Style(QColor("#FF0000"), 3)


# -- geometry ---------------------------------------------------------------

def test_rect_bounding_and_validity():
    rect = RectAnnotation(_style(), QPointF(10, 10))
    rect.update_to(QPointF(40, 30))
    assert rect.bounding_rect() == QRectF(10, 10, 30, 20)
    assert rect.is_valid()

    tiny = RectAnnotation(_style(), QPointF(0, 0))
    tiny.update_to(QPointF(1, 1))
    assert not tiny.is_valid()


def test_rect_move():
    rect = RectAnnotation(_style(), QPointF(0, 0))
    rect.update_to(QPointF(10, 10))
    rect.move(5, -3)
    assert rect.bounding_rect() == QRectF(5, -3, 10, 10)


def test_arrow_hit_test_along_line():
    arrow = ArrowAnnotation(_style(), QPointF(0, 0))
    arrow.update_to(QPointF(100, 0))
    assert arrow.hit_test(QPointF(50, 2))
    assert not arrow.hit_test(QPointF(50, 50))


def test_number_hit_test_is_circular():
    badge = NumberAnnotation(_style(), QPointF(50, 50), 1)
    assert badge.hit_test(QPointF(50, 50))
    assert not badge.hit_test(QPointF(50 + NumberAnnotation.RADIUS + 5, 50))


def test_pen_needs_two_points():
    pen = PenAnnotation(_style(), QPointF(0, 0))
    assert not pen.is_valid()
    pen.update_to(QPointF(5, 5))
    assert pen.is_valid()


def test_text_validity_depends_on_content():
    empty = TextAnnotation(_style(), QPointF(0, 0), "   ")
    assert not empty.is_valid()
    filled = TextAnnotation(_style(), QPointF(0, 0), "hi")
    assert filled.is_valid()


# -- factory ----------------------------------------------------------------

def test_factory_creates_expected_types():
    assert isinstance(create_annotation(ToolType.ELLIPSE, _style(), QPointF(0, 0)), EllipseAnnotation)
    assert isinstance(create_annotation(ToolType.MOSAIC, _style(), QPointF(0, 0)), MosaicAnnotation)


# -- stack / undo-redo ------------------------------------------------------

def test_stack_undo_redo_and_numbering():
    stack = AnnotationStack()
    assert not stack.can_undo()
    assert stack.next_number() == 1

    badge = NumberAnnotation(_style(), QPointF(0, 0), stack.next_number())
    stack.add(badge)
    assert stack.next_number() == 2
    assert len(stack) == 1

    stack.undo()
    assert len(stack) == 0
    assert stack.can_redo()

    stack.redo()
    assert len(stack) == 1

    # adding after undo clears the redo history
    stack.undo()
    stack.add(RectAnnotation(_style(), QPointF(0, 0)))
    assert not stack.can_redo()


def test_stack_hit_test_returns_topmost():
    stack = AnnotationStack()
    bottom = RectAnnotation(_style(), QPointF(0, 0))
    bottom.update_to(QPointF(100, 100))
    top = RectAnnotation(_style(), QPointF(10, 10))
    top.update_to(QPointF(50, 50))
    stack.add(bottom)
    stack.add(top)
    assert stack.hit_test(QPointF(30, 30)) is top


# -- rendering smoke test ---------------------------------------------------

def test_draw_all_renders_without_error():
    image = QImage(120, 120, QImage.Format_RGB32)
    image.fill(QColor("white"))
    ctx = RenderContext(base_image=image)

    stack = AnnotationStack()
    rect = RectAnnotation(_style(), QPointF(5, 5))
    rect.update_to(QPointF(60, 60))
    stack.add(rect)

    mosaic = MosaicAnnotation(_style(), QPointF(10, 10))
    mosaic.update_to(QPointF(80, 80))
    stack.add(mosaic)

    arrow = ArrowAnnotation(_style(), QPointF(0, 0))
    arrow.update_to(QPointF(100, 100))
    stack.add(arrow)

    stack.add(TextAnnotation(_style(), QPointF(5, 90), "hello"))
    stack.add(NumberAnnotation(_style(), QPointF(100, 20), 1))

    target = QImage(120, 120, QImage.Format_RGB32)
    target.fill(QColor("white"))
    painter = QPainter(target)
    stack.draw_all(painter, ctx)
    painter.end()

    # mosaic / annotations should have changed some pixels
    assert target != image
