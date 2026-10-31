"""烧录导出测试：挖空**不能**进图片，箭头/文字**必须**进图片，分辨率等于原图。"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QImage

from ankisnip.base import annotations as ann
from ankisnip.rasterize import bake, bake_for_export, split_annotations


@pytest.fixture()
def image():
    img = QImage(240, 160, QImage.Format_RGB32)
    img.fill(Qt.white)
    return img


def _region_bytes(img: QImage, x: int, y: int, w: int, h: int) -> bytes:
    from PySide6.QtCore import QBuffer, QByteArray

    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QBuffer.WriteOnly)
    img.copy(x, y, w, h).save(buf, "PNG")
    buf.close()
    return bytes(ba.data())


def test_split_masks_from_baked():
    style = ann.Style()
    masks = [ann.MaskAnnotation(style, QPointF(10, 10))]
    masks[0].update_to(QPointF(60, 60))
    others = [
        ann.ArrowAnnotation(style, QPointF(0, 0)),
        ann.TextAnnotation(style, QPointF(5, 90), "重点"),
    ]
    got_masks, baked = split_annotations(masks + others)
    assert len(got_masks) == 1 and isinstance(got_masks[0], ann.MaskAnnotation)
    assert len(baked) == 2


def test_bake_keeps_original_resolution_and_excludes_mask(image):
    style = ann.Style(QColor("#FF0000"), 4)
    mask = ann.MaskAnnotation(style, QPointF(20, 20))
    mask.update_to(QPointF(120, 100))

    out = bake_for_export(image, [mask])
    assert out.size() == image.size()
    # 挖空区域必须与原图逐字节相同 —— 证明它没被烧进去
    assert _region_bytes(out, 20, 20, 100, 80) == _region_bytes(image, 20, 20, 100, 80)


def test_bake_burns_arrow_and_text(image):
    style = ann.Style(QColor("#FF0000"), 4, 24)
    arrow = ann.ArrowAnnotation(style, QPointF(10, 10))
    arrow.update_to(QPointF(200, 120))
    text = ann.TextAnnotation(style, QPointF(20, 130), "重点")

    out = bake(image, [arrow, text])
    assert out.size() == image.size()
    # 箭头 / 文字各自改写了像素
    assert _region_bytes(out, 5, 5, 210, 125) != _region_bytes(image, 5, 5, 210, 125)
    assert _region_bytes(out, 15, 125, 120, 30) != _region_bytes(image, 15, 125, 120, 30)


def test_bake_does_not_mutate_source(image):
    style = ann.Style()
    arrow = ann.ArrowAnnotation(style, QPointF(0, 0))
    arrow.update_to(QPointF(100, 100))
    before = _region_bytes(image, 0, 0, 240, 160)
    bake(image, [arrow])
    assert _region_bytes(image, 0, 0, 240, 160) == before


def test_mask_normalization_roundtrip():
    style = ann.Style()
    mask = ann.MaskAnnotation.from_norm(style, 0.32, 0.18, 0.10, 0.06, 1920, 1080)
    nx, ny, nw, nh = mask.to_norm(1920, 1080)
    assert (nx, ny, nw, nh) == pytest.approx((0.32, 0.18, 0.10, 0.06), abs=1e-9)


def test_mask_coords_are_resolution_independent():
    """同一个归一化挖空在 1x / 2x 截图上应落在同一相对位置。"""
    style = ann.Style()
    for w, h in [(800, 600), (1600, 1200), (1920, 1080)]:
        mask = ann.MaskAnnotation.from_norm(style, 0.25, 0.5, 0.2, 0.1, w, h)
        nx, ny, nw, nh = mask.to_norm(w, h)
        assert (nx, ny, nw, nh) == pytest.approx((0.25, 0.5, 0.2, 0.1), abs=1e-9)
