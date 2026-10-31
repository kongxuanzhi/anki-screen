"""导出几何：原图分辨率 / 设备像素比下的坐标正确性。

背景（用户反馈）
----------------
macOS Retina 上 ``QPixmap`` 带 ``devicePixelRatio = 2``：屏幕截图 logical 尺寸只有
物理像素的一半。此前的测试一律用 ``dpr = 1`` 的 Pixmap 构造覆盖层，于是
**image / widget / norm 三套坐标在这种用例下完全重合**，任何"少乘或多乘一个 dpr"
的缺陷都不会失败 —— 而真机上恰恰就是这个 bug 的表现（标注位置偏移 / 完全看不到）。

本模块用 **真实 Retina 场景**（logical 400x300 / physical 800x600，选区覆盖层
dpr=2）锁定两条硬约束：

1. **画布上用鼠标画的标注 → 导出图处于原图（物理）分辨率**，且像素位置对应
   鼠标落点（不能偏半分辨率、也不能整体偏移）。
2. **挖空的归一化坐标与视图缩放无关**，且等价于 物理像素 / 物理尺寸。
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QMouseEvent, QPixmap

from ankisnip.base import annotations as ann
from ankisnip.mask_colors import MASK_COLORS
from ankisnip.overlay_ext import AnkiCaptureOverlay
from ankisnip.settings import (EXPORT_SCALE_NATIVE, EXPORT_SCALE_SCREEN,
                               Settings)

DPR = 2.0
LOGICAL_W, LOGICAL_H = 400, 300          # 屏幕 logical 尺寸
PHYS_W, PHYS_H = int(LOGICAL_W * DPR), int(LOGICAL_H * DPR)   # 物理像素
SELECTION = QRectF(40, 30, 200, 150)     # logical 选区


class _RetinaShot:
    """``dpr = 2`` 的伪截图：logical 400x300，物理 800x600。"""

    def __init__(self):
        pix = QPixmap(PHYS_W, PHYS_H)
        pix.setDevicePixelRatio(DPR)
        pix.fill(Qt.white)
        self.screen = None
        self.pixmap = pix
        self._geo = QRect(0, 0, LOGICAL_W, LOGICAL_H)

    @property
    def geometry(self):
        return self._geo


def _event(kind, x, y, button=Qt.LeftButton):
    return QMouseEvent(kind, QPointF(x, y), button, button, Qt.NoModifier)


def _drag(widget, x0, y0, x1, y1):
    widget.mousePressEvent(_event(QEvent.Type.MouseButtonPress, x0, y0))
    widget.mouseMoveEvent(_event(QEvent.Type.MouseMove, x1, y1))
    widget.mouseReleaseEvent(_event(QEvent.Type.MouseButtonRelease, x1, y1))


@pytest.fixture()
def retina_overlay():
    """原生像素口径的 Retina 覆盖层。

    默认是「与屏幕一致」导出（下面另有专门用例），本模块锁定的是**原生像素**
    这条路上的坐标正确性，所以显式打开 ``native``。
    """
    settings = Settings()
    settings.export_scale = EXPORT_SCALE_NATIVE
    # 像素断言依赖固定的红色；模板支持的红色是 #ff3e05（7 色之一）。
    # 不要把测试绑死在 Config 的默认色上（默认已对齐模板的 #ff8d05）。
    settings.pen_color = "#ff3e05"
    ov = AnkiCaptureOverlay(_RetinaShot(), settings)
    ov._selection = QRectF(SELECTION)
    ov._ensure_toolbar()
    yield ov
    ov.close()


def _is_red(image, x, y):
    """该像素是不是画笔默认红（允许抗锯齿边缘）。"""
    c = image.pixelColor(x, y)
    return c.red() > 180 and c.green() < 100 and c.blue() < 100


def _red_pixels_near(image, x, y, r=6):
    return any(_is_red(image, px, py)
               for px in range(x - r, x + r + 1)
               for py in range(y - r, y + r + 1)
               if 0 <= px < image.width() and 0 <= py < image.height())


def _ink_bbox(image):
    """所有非白像素的包围盒 ``(x0, y0, x1, y1)``。"""
    pts = [(x, y) for x in range(image.width()) for y in range(image.height())
           if image.pixelColor(x, y) != Qt.white]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


# 每种图形拖动 (10,10)→(190,140)（widget）后，image 坐标 (20,20)→(380,280)
# 上真正应该有笔画的位置
_START, _END = (20, 20), (380, 280)
_STROKE_POINTS = {
    ann.ToolType.ARROW: [_START, (200, 150), _END],
    ann.ToolType.PEN: [_START, (200, 150), _END],
    # 矩形：只有边框，四个角与上下边中点有线，中心是空的
    ann.ToolType.RECT: [(20, 20), (380, 20), (20, 280), (380, 280), (200, 20), (200, 280)],
    # 椭圆：内切于同一个矩形，只接触四条边的中点，角点是空的
    ann.ToolType.ELLIPSE: [(200, 20), (380, 150), (200, 280), (20, 150)],
}
_HOLLOW = {ann.ToolType.RECT, ann.ToolType.ELLIPSE}


# --- 1. 取图：必须是原图（物理）分辨率 ---------------------------------- #
def test_selection_image_is_full_resolution(retina_overlay):
    image = retina_overlay.selection_image()
    assert (image.width(), image.height()) == (int(200 * DPR), int(150 * DPR)), \
        "选区图必须是原图物理分辨率，不是 logical 尺寸"


# --- 1a. 默认口径：清晰优先，且旧配置要被纠正 ---------------------------- #
def test_new_user_gets_native_by_default():
    """默认必须是原生像素。

    曾默认过「与屏幕一致」，实测下来是糊的：屏幕点口径等于把 Retina 的真实
    采样丢掉一半，而这张图之后在 Anki / 预览里又会被放大回来看。
    """
    settings = Settings()
    settings._s.remove("export_scale")
    settings._s.remove("_export_scale_migrated")
    assert settings.export_scale == EXPORT_SCALE_NATIVE


def test_legacy_screen_default_is_migrated_once():
    """已存在的 screen 配置要被纠正；但用户之后自己选的值必须保留。"""
    settings = Settings()
    try:
        settings._s.setValue("export_scale", EXPORT_SCALE_SCREEN)
        settings._s.remove("_export_scale_migrated")
        assert settings.export_scale == EXPORT_SCALE_NATIVE, \
            "旧默认值必须纠成 native，否则老用户永远糊着"

        settings.export_scale = EXPORT_SCALE_SCREEN
        assert settings.export_scale == EXPORT_SCALE_SCREEN, \
            "用户显式选择不能被迁移逻辑反复覆盖"
        assert Settings().export_scale == EXPORT_SCALE_SCREEN
    finally:
        settings.export_scale = EXPORT_SCALE_NATIVE
        settings._s.sync()


# --- 1b. 导出口径：两种模式各自的输出尺寸 ------------------------------- #
@pytest.mark.parametrize("scale,expected", [
    (EXPORT_SCALE_NATIVE, (int(200 * DPR), int(150 * DPR))),
    ("screen", (200, 150)),
])
def test_export_size_follows_setting(scale, expected):
    """Retina 上「保存的图比截图大」的开关必须真的生效。

    native  = 保留抓取到的原生像素（200×150 的选区 → 400×300 的文件）
    screen  = 1 图片像素 = 1 屏幕点（和框选范围一样大，默认）
    """
    settings = Settings()
    settings.export_scale = scale
    ov = AnkiCaptureOverlay(_RetinaShot(), settings)
    try:
        ov._selection = QRectF(SELECTION)
        ov._ensure_toolbar()
        image = ov.selection_image()
        assert (image.width(), image.height()) == expected
        # 两种口径都要把 dpr 归一，否则 QPainter 会再乘一次 dpr。
        assert image.devicePixelRatio() == 1.0
        composed = ov.compose(include_masks=True).toImage()
        assert (composed.width(), composed.height()) == expected, \
            "没有标注时走基座 compose，也必须服从同一个口径"
    finally:
        ov.close()


def test_export_size_screen_mode_keeps_annotation_geometry():
    """screen 模式下画出来的标注，位置要对得上（只是整体按屏幕点重采样）。"""
    settings = Settings()
    settings.export_scale = "screen"
    settings.pen_color = "#ff3e05"  # 像素断言依赖固定红色（7 色之一）
    ov = AnkiCaptureOverlay(_RetinaShot(), settings)
    try:
        ov._selection = QRectF(SELECTION)
        ov._ensure_toolbar()
        ov._on_tool_selected(ann.ToolType.ARROW)
        canvas = ov._canvas
        assert canvas.image.width() == 200, "画布应当直接用屏幕点口径的图像"
        _drag(canvas, 10.0, 10.0, 190.0, 140.0)

        arrow = ov.annotation_items()[0]
        assert arrow.start.x() == pytest.approx(10.0, abs=1.0)
        assert arrow.end.x() == pytest.approx(190.0, abs=1.0)

        image = ov.compose(include_masks=True).toImage()
        assert _red_pixels_near(image, 10, 10)
        assert _red_pixels_near(image, 190, 140)
        # 归一化坐标与导出口径无关
        ov._on_tool_selected(ann.ToolType.MASK)
        _drag(ov._canvas, 0.0, 0.0, 200.0, 150.0)
        rect = ov._canvas.normalized_masks()[0]
        assert (rect.nw, rect.nh) == pytest.approx((1.0, 1.0), abs=0.01)
    finally:
        ov.close()


# --- 2. 画布绘制 → 导出图落在正确的物理像素 ----------------------------- #
@pytest.mark.parametrize("tool", [ann.ToolType.ARROW, ann.ToolType.RECT,
                                  ann.ToolType.ELLIPSE, ann.ToolType.PEN])
def test_annotation_bakes_at_full_resolution(retina_overlay, tool):
    """鼠标在画布上画的标注，导出图里必须出现在**对应位置**（含 dpr 缩放）。

    画布 = 选区 logical 矩形（200x150），图片 = 400x300 物理像素。
    因此在画布 widget 坐标 (10,10) 落笔，对应的应该是图片物理像素 (20,20)。
    """
    retina_overlay._on_tool_selected(tool)
    canvas = retina_overlay._canvas
    assert canvas.image.width() == int(200 * DPR)

    _drag(canvas, 10.0, 10.0, 190.0, 140.0)
    items = retina_overlay.annotation_items()
    assert len(items) == 1, f"{tool} 未进入标注栈"

    image = retina_overlay.compose(include_masks=True).toImage()
    assert (image.width(), image.height()) == (SELECTION.width() * DPR,
                                               SELECTION.height() * DPR)

    # 整体包围盒：笔宽 3px + 抗锯齿，允许 ±3px。
    x0, y0, x1, y1 = _ink_bbox(image)
    assert (x0, y0) == pytest.approx((20, 20), abs=3), \
        f"{tool}: 笔画起点落在 {x0,y0}（应≈(20,20)）—— dpr 缩放不对"
    assert (x1, y1) == pytest.approx((380, 280), abs=3), \
        f"{tool}: 笔画终点落在 {x1,y1}（应≈(380,280)）—— dpr 缩放不对"

    # 分形状的精确位置：各不相同（矩形走边框、椭圆内切），不能一刀切。
    for point in _STROKE_POINTS[tool]:
        assert _red_pixels_near(image, *point, r=3), \
            f"{tool}: 导出图 {point} 附近应有笔画"
    if tool in _HOLLOW:
        assert not _red_pixels_near(image, 200, 150, r=2), \
            f"{tool}: {tool} 内部不应被填充"


def test_unsupported_pen_color_is_normalized_and_persisted():
    """历史配置里的任意 pen_color 在进入编辑器时收敛到模板的 7 色。"""
    settings = Settings()
    settings.export_scale = EXPORT_SCALE_NATIVE
    settings.pen_color = "#FF3B30"
    ov = AnkiCaptureOverlay(_RetinaShot(), settings)
    try:
        supported = {value for value, _label in MASK_COLORS}
        assert ov._style.color.name() in supported
        assert settings.pen_color in supported, "归一后的颜色要写回配置"
    finally:
        ov.close()


def test_annotation_coordinates_are_image_pixels(retina_overlay):
    """标注对象存的坐标必须是图像物理像素（= dpr × widget 坐标）。"""
    retina_overlay._on_tool_selected(ann.ToolType.ARROW)
    canvas = retina_overlay._canvas
    _drag(canvas, 10.0, 10.0, 190.0, 140.0)

    arrow = retina_overlay.annotation_items()[0]
    assert arrow.start.x() == pytest.approx(20.0, abs=1.0)
    assert arrow.start.y() == pytest.approx(20.0, abs=1.0)
    assert arrow.end.x() == pytest.approx(380.0, abs=1.0)
    assert arrow.end.y() == pytest.approx(280.0, abs=1.0)


def test_toolbar_color_and_width_reach_new_annotations(retina_overlay):
    """工具栏改了颜色 / 线宽之后，新画的标注必须跟着变。

    画布原来自己 new 了一个 Style，和覆盖层的 ``_style`` 是两个对象，
    于是工具栏上的调色、线宽对画布毫无作用 —— 画出来永远是默认红 3px。
    """
    from PySide6.QtGui import QColor

    retina_overlay._on_tool_selected(ann.ToolType.ARROW)
    canvas = retina_overlay._canvas
    assert canvas.style is retina_overlay._style

    retina_overlay._on_color_changed(QColor("#00aa55"))
    retina_overlay._on_width_changed(7)
    _drag(canvas, 10.0, 10.0, 190.0, 140.0)

    arrow = retina_overlay.annotation_items()[0]
    assert arrow.style.color.name() == "#00aa55"
    assert arrow.style.width == 7

    # 导出图里也要是这个颜色（而不是默认红）
    image = retina_overlay.compose(include_masks=True).toImage()
    pixel = image.pixelColor(20, 20)
    assert pixel.green() > 120 and pixel.red() < 120, pixel.name()


# --- 3. 挖空：归一化坐标在 Retina 下同样正确 ---------------------------- #
def test_mask_normalized_under_retina(retina_overlay):
    """挖空铺满画布 → 归一化坐标应为整张图 0~1。"""
    retina_overlay._on_tool_selected(ann.ToolType.MASK)
    canvas = retina_overlay._canvas
    _drag(canvas, 0.0, 0.0, 200.0, 150.0)

    rect = canvas.normalized_masks()[0]
    assert (rect.nx, rect.ny) == pytest.approx((0.0, 0.0), abs=0.01)
    assert (rect.nw, rect.nh) == pytest.approx((1.0, 1.0), abs=0.01)


def test_mask_is_not_in_anki_image_but_is_in_copy_image(retina_overlay):
    """保存到 Anki：挖空不进图；复制/另存：遮挡必须画出来（原图分辨率）。"""
    retina_overlay._on_tool_selected(ann.ToolType.MASK)
    canvas = retina_overlay._canvas
    _drag(canvas, 0.0, 0.0, 200.0, 150.0)
    expected = QColor(retina_overlay.annotation_items()[0].style.color)

    anki = retina_overlay.compose(include_masks=False).toImage()
    middle = anki.pixelColor(200, 150)
    assert middle.name() in ("#ffffff", "#000000") or middle.alpha() == 0, \
        "保存到 Anki 的图里不允许出现遮挡块"

    copy = retina_overlay.compose(include_masks=True).toImage()
    pixel = copy.pixelColor(200, 150)
    assert pixel.red() == expected.red() and pixel.green() == expected.green(), \
        "复制/另存的图里必须能看到遮挡块（且在原图分辨率位置）"


# --- 4. 500% 放大下绘制，缩回 100% 后坐标不变 --------------------------- #
def test_drawing_under_zoom_lands_correctly(retina_overlay):
    retina_overlay._on_tool_selected(ann.ToolType.ARROW)
    canvas = retina_overlay._canvas
    canvas.set_absolute_zoom(4.0)

    a = canvas.transform.image_to_widget(20, 20)
    b = canvas.transform.image_to_widget(380, 280)
    _drag(canvas, a.x(), a.y(), b.x(), b.y())

    image = retina_overlay.compose(include_masks=True).toImage()
    assert _red_pixels_near(image, 20, 20)
    assert _red_pixels_near(image, 380, 280)
