"""坐标变换的**唯一出入口**。

任务书 §3.3 要求所有绘制、命中检测、拖拽创建、调整手柄都必须走同一对变换函数，
禁止业务代码里直接混用屏幕坐标。这里把它收敛成一个 :class:`ViewTransform`。

坐标空间
--------
* **image**：原图像素坐标。编辑器里所有标注（箭头 / 文字 / 挖空）都存这个空间，
  与基座 ``annotations.py`` 的"selection-local"约定一致。
* **widget**：编辑器画布的控件坐标（鼠标事件给出的就是这个）。
* **norm**：相对原图的归一化坐标 0~1。**只在持久化时使用**，不进渲染流程。

变换（``imgDisplayW/H`` 为 scale=1、未平移时图片适应窗口的显示尺寸）::

    screenX = nx * imgDisplayW * scale + tx
    screenY = ny * imgDisplayH * scale + ty
    nx = (screenX - tx) / (imgDisplayW * scale)

由于 ``imgDisplayW == imageWidth * fitScale``，image 空间与 norm 空间只差一个常数，
因此 :meth:`image_to_widget` 与 :meth:`norm_to_widget` 共用同一套参数，不会出现
"两套真理"。
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF

MIN_SCALE = 0.05
MAX_SCALE = 40.0


@dataclass
class ViewTransform:
    """图片 → 画布 的视图变换。

    Attributes:
        img_w / img_h: 原图分辨率（像素）。
        fit_scale: scale=1 时图片适应窗口所用的缩放（image px → 显示 px）。
        scale: 用户缩放倍率（1.0 = 适应窗口）。
        tx / ty: 平移量（widget 像素）。
    """

    img_w: float
    img_h: float
    fit_scale: float = 1.0
    scale: float = 1.0
    tx: float = 0.0
    ty: float = 0.0

    # -- 派生量 -----------------------------------------------------------
    @property
    def img_display_w(self) -> float:
        """scale=1、未平移时图片的显示宽度 ``imgDisplayW``。"""
        return self.img_w * self.fit_scale

    @property
    def img_display_h(self) -> float:
        return self.img_h * self.fit_scale

    @property
    def total_scale(self) -> float:
        """image px → widget px 的总缩放。"""
        return self.fit_scale * self.scale

    @property
    def display_w(self) -> float:
        """图片当前在画布上的显示宽度。"""
        return self.img_display_w * self.scale

    @property
    def display_h(self) -> float:
        return self.img_display_h * self.scale

    # -- 正变换 -----------------------------------------------------------
    def image_to_widget(self, x: float, y: float) -> QPointF:
        return QPointF(x * self.total_scale + self.tx, y * self.total_scale + self.ty)

    def image_rect_to_widget(self, rect: QRectF) -> QRectF:
        top_left = self.image_to_widget(rect.x(), rect.y())
        return QRectF(top_left.x(), top_left.y(), rect.width() * self.total_scale,
                      rect.height() * self.total_scale)

    def norm_to_widget(self, nx: float, ny: float) -> QPointF:
        return QPointF(nx * self.img_display_w * self.scale + self.tx,
                       ny * self.img_display_h * self.scale + self.ty)

    # -- 逆变换 -----------------------------------------------------------
    def widget_to_image(self, x: float, y: float) -> QPointF:
        s = self.total_scale or 1e-9
        return QPointF((x - self.tx) / s, (y - self.ty) / s)

    def widget_to_norm(self, x: float, y: float) -> tuple:
        dx = self.img_display_w * self.scale or 1e-9
        dy = self.img_display_h * self.scale or 1e-9
        return (x - self.tx) / dx, (y - self.ty) / dy

    # -- 视图操作 ---------------------------------------------------------
    def fit(self, viewport_w: float, viewport_h: float, margin: float = 16.0) -> None:
        """适应窗口：scale=1，居中。"""
        if self.img_w <= 0 or self.img_h <= 0 or viewport_w <= 0 or viewport_h <= 0:
            self.fit_scale = 1.0
        else:
            self.fit_scale = min((viewport_w - margin) / self.img_w,
                                 (viewport_h - margin) / self.img_h)
            self.fit_scale = max(self.fit_scale, 1e-6)
        self.scale = 1.0
        self.center(viewport_w, viewport_h)

    def center(self, viewport_w: float, viewport_h: float) -> None:
        self.tx = (viewport_w - self.display_w) / 2.0
        self.ty = (viewport_h - self.display_h) / 2.0

    def center_on(self, image_x: float, image_y: float,
                  viewport_w: float, viewport_h: float) -> None:
        """平移视图，让图像点 ``(image_x, image_y)`` 落在视口中心（全屏放大用）。"""
        self.tx = viewport_w / 2.0 - image_x * self.total_scale
        self.ty = viewport_h / 2.0 - image_y * self.total_scale

    def zoom_at(self, widget_x: float, widget_y: float, factor: float) -> None:
        """以 ``(widget_x, widget_y)`` 为锚点缩放：该点下的图像像素保持不动。"""
        new_scale = min(max(self.scale * factor, MIN_SCALE), MAX_SCALE)
        factor = new_scale / (self.scale or 1e-9)
        # 锚点在缩放后的位置不变 ⇒ 反解平移量
        self.tx = widget_x - (widget_x - self.tx) * factor
        self.ty = widget_y - (widget_y - self.ty) * factor
        self.scale = new_scale

    def pan(self, dx: float, dy: float) -> None:
        self.tx += dx
        self.ty += dy

    def set_zoom(self, zoom: float, viewport_w: float = 0.0, viewport_h: float = 0.0) -> None:
        """设定"图片像素 : 屏幕像素"的绝对倍率（100% = 1:1）。"""
        target = min(max(zoom / (self.fit_scale or 1e-9), MIN_SCALE), MAX_SCALE)
        if viewport_w and viewport_h:
            cx, cy = viewport_w / 2.0, viewport_h / 2.0
            self.zoom_at(cx, cy, target / (self.scale or 1e-9))
        else:
            self.scale = target

    @property
    def absolute_zoom(self) -> float:
        """图片像素相对屏幕的显示倍率（1.0 = 100%）。"""
        return self.total_scale
