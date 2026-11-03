"""导出前的「合成」：把图片 + 箭头 + 文字栅格化成一张**原图分辨率**的图片。

任务书 §1 / §5-C 的硬约束：

* **参与合成**：arrow、text（以及基座已有的 rect / ellipse / pen / mosaic / …）
* **不参与合成**：``mask``（挖空）—— 它只以坐标数据另行保存

这是"图片里能看到箭头文字、看不到挖空遮罩"这条验收项的唯一实现点。
"""

from __future__ import annotations

from typing import Iterable, List, Sequence, Tuple

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen

from .base import annotations as ann


#: 永远不会被合成进图片的标注类型。
NON_BAKED_TYPES = (ann.MaskAnnotation,)


def normalize_image(image: QImage) -> QImage:
    """把 ``image`` 归一到「1 像素 = 1 原图像素、dpr=1」的语义。

    Qt 的 ``QPainter`` 在 devicePixelRatio>1 的绘图设备上用的是**逻辑坐标**，
    每一次 draw 都会被再乘一次 dpr。截图本体带 dpr（Retina 上是 2），
    而标注坐标是原图物理像素，两者直接相乘的结果就是：

    * 只有 dpr=1 的显示器看起来正常；
    * Retina 上整张贴图的标注被放大一倍、溢出画布 —— 看起来"完全没合成进去"。

    因此凡是这里开 painter 的地方，入口一律先把 dpr 归一（尺寸不变，只是
    去掉缩放语义）。若图片本来就是 dpr=1，返回原对象，零拷贝。
    """
    if image.isNull():
        return image
    ratio = image.devicePixelRatio()
    if not ratio or abs(ratio - 1.0) < 1e-6:
        return image
    copy = image.copy()
    copy.setDevicePixelRatio(1.0)
    return copy


def split_annotations(items: Iterable[ann.Annotation]) -> Tuple[List[ann.Annotation], List[ann.Annotation]]:
    """把标注分成 ``(masks, baked)`` 两组。

    ``masks`` 以坐标数据的形式持久化；``baked`` 直接画进图片。
    """
    masks: List[ann.Annotation] = []
    baked: List[ann.Annotation] = []
    for item in items:
        if isinstance(item, NON_BAKED_TYPES):
            masks.append(item)
        else:
            baked.append(item)
    return masks, baked


def bake(image: QImage, annotations: Sequence[ann.Annotation]) -> QImage:
    """把 ``annotations`` 画到 ``image`` 的副本上，返回**新**图片。

    结果尺寸严格等于 ``image.size()``（原图分辨率），且挖空不在其中。
    """
    if image.isNull():
        raise ValueError("待合成的图片为空")

    # dpr 归一：见 normalize_image() 的说明。少了这一步，Retina 上所有标注
    # 都会被再放大一倍。
    base = normalize_image(image)
    result = base.copy()
    if not annotations:
        return result

    painter = QPainter(result)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setRenderHint(QPainter.SmoothPixmapTransform)
    painter.setRenderHint(QPainter.TextAntialiasing)
    try:
        # 1:1 绘制：标注坐标就是原图像素坐标，不做任何视图变换。
        ctx = ann.RenderContext(base_image=base)
        for item in annotations:
            item.draw(painter, ctx)
    finally:
        painter.end()
    return result


def bake_for_export(image: QImage, annotations: Sequence[ann.Annotation]) -> QImage:
    """导出语义的合成：先剔除挖空，再栅格化。

    这是「保存后的图片里看不到挖空遮罩」这条验收项的入口。
    """
    _, baked = split_annotations(annotations)
    return bake(image, baked)


def bake_including_masks(image: QImage, annotations: Sequence[ann.Annotation],
                         fill_alpha: int = 255, draw_border: bool = False) -> QImage:
    """**含挖空**的合成，用于「复制 / 保存图片」。

    保存到 Anki 时挖空只以坐标数据保存（走 :func:`bake_for_export`）；
    但复制或另存图片时，用户要的就是"看到什么就拿到什么"，
    所以遮挡块必须画进图片里 —— 否则复制出去的是一张没遮住的原图。
    """
    masks, others = split_annotations(annotations)
    result = bake(image, others)
    if not masks:
        return result

    painter = QPainter(result)
    painter.setRenderHint(QPainter.Antialiasing)
    try:
        for item in masks:
            rect = item.bounding_rect()
            color = QColor(item.style.color)
            color.setAlpha(int(fill_alpha))
            painter.setPen(Qt.NoPen)
            painter.setBrush(color)
            painter.drawRect(rect)
            if draw_border:
                painter.setPen(QPen(QColor("#0a84ff"), 1.0, Qt.DashLine))
                painter.setBrush(Qt.NoBrush)
                painter.drawRect(rect)
    finally:
        painter.end()
    return result


def bake_and_save(image: QImage, annotations: Sequence[ann.Annotation], path: str,
                  quality: int = -1) -> str:
    """合成并写入磁盘（PNG / JPG 由后缀决定）。失败时抛出 :class:`IOError`。"""
    result = bake(image, annotations)
    ok = result.save(path, None, quality)
    if not ok:
        raise IOError(f"图片保存失败：{path}")
    return path


def to_png_bytes(image: QImage) -> bytes:
    """把 ``QImage`` 编码成 PNG 字节（AnkiConnect ``storeMediaFile`` 需要）。"""
    from PySide6.QtCore import QBuffer, QByteArray

    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QBuffer.WriteOnly)
    try:
        ok = image.save(buf, "PNG")
    finally:
        buf.close()
    if not ok:
        raise IOError("PNG 编码失败")
    return bytes(ba.data())


def ensure_opaque(image: QImage) -> QImage:
    """JPEG 不支持 alpha；需要时铺白底。"""
    if image.hasAlphaChannel():
        solid = QImage(image.size(), QImage.Format_RGB32)
        solid.fill(Qt.white)
        painter = QPainter(solid)
        painter.drawImage(0, 0, image)
        painter.end()
        return solid
    return image
