"""选区 + 标注**合一**的截图覆盖层（不再弹独立编辑器窗口）。

用户反馈驱动的设计
------------------
上一版是「选区覆盖层 → 关闭 → 弹出独立编辑器窗口」，两段式、割裂。
现在改成通用截图工具的形态：**同一个窗口里完成框选与标注**。

* :attr:`Phase.SELECT` —— 默认态。暗化背景 + 选区高亮；选区内拖动 = 移动选区、
  手柄 = 缩放选区（**直接复用基座** ``CaptureOverlay`` 的鼠标逻辑，一行未改）。
  工具条此时已经出现，可以先挑工具。
* :attr:`Phase.DRAW` —— 双击选区、或从工具条选了某个工具后进入。此时在**选区矩形**
  位置挂载一个 :class:`~ankisnip.editor_canvas.EditorCanvas`（它是覆盖层的子控件，
  几何严格等于选区矩形），标注就在原地画，不再弹新窗口。

进入绘制态后选区被锁住；工具条第一个按钮「⛶」可以退回选区态重框，已画的标注保留
（标注栈由覆盖层持有，画布只是它的视图）。

坐标与导出
----------
* 标注坐标空间 = 选区图片的**原图像素**（与 ``editor_canvas`` 一致）。
* 选区矩形 = 视口，滚轮缩放 / 空格或中键平移 / 适应窗口全部保留，
  因此「400% 下挖空、缩回 100% 位置大小不变」这条验收依然成立。
* 导出按原图分辨率合成，挖空不参与，见 :mod:`ankisnip.rasterize`。
"""

from __future__ import annotations

from enum import Enum, auto
from typing import List, Optional

from PySide6.QtCore import QPointF, QRect, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap

from .base import annotations as ann
from .base.overlay import CaptureOverlay
from .editor_canvas import EditorCanvas
from .editor_toolbar import EditorToolbar


class Phase(Enum):
    """覆盖层的两个阶段。"""

    SELECT = auto()   # 选区态：可移动 / 缩放选区
    DRAW = auto()     # 绘制态：标注画布已挂上，拖拽即绘制


class AnkiCaptureOverlay(CaptureOverlay):
    """基座选区覆盖层的扩展：就地标注 + Anki 保存动作。"""

    #: 双击进入绘制态时默认使用的工具（本软件的主用途就是挖空）。
    DEFAULT_TOOL = ann.ToolType.MASK

    def __init__(self, shot, config) -> None:
        super().__init__(shot, config)
        # 图片挖空模板只支持 7 个颜色：老配置里的任意 pen_color 在进入
        # 编辑器前收敛到最近的支持色，并写回配置，避免继续产出不支持的色值。
        from .mask_colors import normalize

        normalized = normalize(self._style.color.name())
        if normalized != self._style.color.name().lower():
            self._style.color = QColor(normalized)
            self._config.pen_color = normalized
        # 字号也要沿用上次保存的值（基座 Style 默认 18，Settings 默认 24）。
        self._style.font_size = int(
            getattr(self._config, "font_size", self._style.font_size))
        self._phase = Phase.SELECT
        self._canvas: Optional[EditorCanvas] = None
        # 标注栈由覆盖层持有：绘制态的画布被销毁重建时标注不会丢。
        self._draw_stack = ann.AnnotationStack()
        self._last_tool: Optional[ann.ToolType] = self.DEFAULT_TOOL

    # -- 阶段切换 --------------------------------------------------------- #
    @property
    def phase(self) -> Phase:
        return self._phase

    def enter_draw_mode(self, tool: Optional[ann.ToolType] = None) -> None:
        """进入绘制态：把标注画布挂到选区矩形上。"""
        if self._selection is None:
            return
        if tool is not None:
            self._last_tool = tool
        tool = tool if tool is not None else self._last_tool

        if self._phase is Phase.DRAW and self._canvas is not None:
            self._canvas.set_tool(tool)
            self._canvas.setFocus(Qt.OtherFocusReason)
            self._sync_toolbar_tool(tool)
            return

        image = self.selection_image()
        if image.isNull():
            return

        sel = self._selection.normalized()
        rect = QRect(int(round(sel.left())), int(round(sel.top())),
                     max(1, int(round(sel.width()))), max(1, int(round(sel.height()))))

        # style 与覆盖层共用，工具栏调色 / 改线宽会立刻作用于后续绘制。
        canvas = EditorCanvas(image, self, stack=self._draw_stack, style=self._style)
        canvas.setGeometry(rect)
        canvas.fit_to_window(margin=0.0)     # 视口 = 选区，铺满不留边
        canvas.viewChanged.connect(self._refresh_toolbar_state)
        canvas.stackChanged.connect(self._refresh_toolbar_state)
        canvas.colorIndexSelected.connect(self._on_color_index)
        canvas.cancelRequested.connect(self._cancel)
        canvas.saveRequested.connect(lambda: self._finish("anki"))
        canvas.set_tool(tool)
        canvas.show()
        canvas.raise_()
        canvas.setFocus(Qt.OtherFocusReason)
        # 选区贴着屏幕边缘时工具条会落在选区内部，必须保证它压在画布之上。
        if self._toolbar is not None:
            self._toolbar.raise_()

        self._canvas = canvas
        self._phase = Phase.DRAW
        self._sync_toolbar_tool(tool)
        self._refresh_toolbar_state()
        self.update()

    def leave_draw_mode(self) -> None:
        """退回选区态（标注保留，可以重新框范围）。"""
        if self._phase is not Phase.DRAW:
            return
        if self._canvas is not None:
            self._canvas.hide()
            self._canvas.deleteLater()
            self._canvas = None
        self._phase = Phase.SELECT
        self.setFocus(Qt.OtherFocusReason)
        self._refresh_toolbar_state()
        self.update()

    # -- 工具条 ------------------------------------------------------------ #
    def _ensure_toolbar(self) -> None:
        """覆写基座实现：换成 AnkiSnip 的标注工具条（选区态就已出现）。"""
        if self._toolbar is not None:
            self._reposition_toolbar()
            return
        tb = EditorToolbar(self._style.color, self._style.width,
                           self._style.font_size, self)
        tb.toolSelected.connect(self._on_tool_selected)
        tb.colorChanged.connect(self._on_color_changed)
        tb.widthChanged.connect(self._on_width_changed)
        tb.fontSizeChanged.connect(self._on_font_size_changed)
        tb.undoRequested.connect(self._undo)
        tb.redoRequested.connect(self._redo)
        tb.zoomInRequested.connect(lambda: self._with_canvas(lambda c: c.zoom_by(1.25)))
        tb.zoomOutRequested.connect(lambda: self._with_canvas(lambda c: c.zoom_by(1 / 1.25)))
        tb.zoomResetRequested.connect(
            lambda: self._with_canvas(lambda c: c.set_absolute_zoom(1.0)))
        tb.fitRequested.connect(lambda: self._with_canvas(lambda c: c.fit_to_window(margin=0.0)))
        tb.reselectRequested.connect(self.leave_draw_mode)
        tb.saveImageRequested.connect(lambda: self._finish("save"))
        tb.copyRequested.connect(lambda: self._finish("copy"))
        tb.cancelRequested.connect(self._cancel)
        tb.saveToAnkiRequested.connect(lambda: self._finish("anki"))
        self._toolbar = tb
        tb.show()
        self._reposition_toolbar()
        self._refresh_toolbar_state()

    def _reposition_toolbar(self) -> None:
        tb = self._toolbar
        if tb is None or self._selection is None:
            return
        tb.adjustSize()
        sel = self._selection.normalized()
        x = int(min(max(sel.left(), 0), max(0, self.width() - tb.width())))
        y = int(sel.bottom() + 8)
        if y + tb.height() > self.height():
            y = int(sel.top() - tb.height() - 8)
        if y < 0:
            y = int(min(sel.top() + 8, max(0, self.height() - tb.height())))
        tb.move(x, y)

    def _on_tool_selected(self, tool: Optional[ann.ToolType]) -> None:
        if tool is None:
            # 「选择 / 移动标注」只在绘制态有意义；选区态保持原样（选区仍可拖动）
            if self._phase is Phase.DRAW and self._canvas is not None:
                self._canvas.set_tool(None)
                self._canvas.setFocus(Qt.OtherFocusReason)
                # 同步工具条：选择工具不需要第二行（颜色 / 线宽 / 字号）。
                self._sync_toolbar_tool(None)
            return
        self.enter_draw_mode(tool)

    def _on_font_size_changed(self, size: int) -> None:
        self._style.font_size = int(size)
        # 记住字号，下次截图沿用（和颜色 / 线宽一样持久化）。
        self._config.font_size = int(size)
        if self._canvas is not None:
            self._canvas.set_font_size(int(size))

    def _on_color_index(self, index: int) -> None:
        """数字键 1-7：切换模板的 7 个颜色。"""
        tb = self._toolbar
        if tb is not None:
            tb.select_color_index(index)

    def _with_canvas(self, fn) -> None:
        """把动作转发给标注画布；还在选区态就先进入绘制态。"""
        if self._phase is not Phase.DRAW or self._canvas is None:
            self.enter_draw_mode()
        if self._canvas is not None:
            fn(self._canvas)

    def _undo(self) -> None:
        if self._canvas is not None:
            self._canvas.undo()
        else:
            self._stack.undo()
        self._refresh_toolbar_state()
        self.update()

    def _redo(self) -> None:
        if self._canvas is not None:
            self._canvas.redo()
        else:
            self._stack.redo()
        self._refresh_toolbar_state()
        self.update()

    def _sync_toolbar_tool(self, tool: Optional[ann.ToolType]) -> None:
        tb = self._toolbar
        if tb is not None:
            tb.select_tool(tool)
            # 上下文第二行的显隐会改变工具条高度，重新贴到选区下方。
            self._reposition_toolbar()

    def _refresh_toolbar_state(self) -> None:
        tb = self._toolbar
        if tb is None:
            return
        editing = self._phase is Phase.DRAW
        tb.set_editing(editing)
        canvas = self._canvas
        if canvas is None:
            tb.set_zoom_text("选区")
            tb.set_mask_count(len([a for a in self._draw_stack.items
                                   if isinstance(a, ann.MaskAnnotation)]))
        else:
            tb.set_zoom_text(f"{canvas.transform.absolute_zoom * 100:.0f}%")
            tb.set_mask_count(len(canvas.mask_rects()))
        # 第二行出现 / 消失后尺寸变了，重新定位。
        self._reposition_toolbar()

    # -- 鼠标 -------------------------------------------------------------- #
    def mousePressEvent(self, event) -> None:  # noqa: N802
        # 绘制态下右键落在选区外时不要直接丢弃整张截图（里面可能已经画了不少）。
        # 选区外右键 = 取消 是基座在选区态的既有行为，保持不变。
        if self._phase is Phase.DRAW and event.button() == Qt.RightButton:
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        """双击选区 → 直接进入绘制态（默认工具：挖空）。"""
        if self._phase is Phase.SELECT and self._selection is not None:
            if self._selection.normalized().contains(event.position()):
                self.enter_draw_mode()
                return
        super().mouseDoubleClickEvent(event)

    def _update_cursor(self, pos: QPointF) -> None:
        # 绘制态选区已锁住，不再显示缩放光标；选区内的光标由画布自己管。
        if getattr(self, "_phase", Phase.SELECT) is Phase.DRAW:
            self.setCursor(Qt.CrossCursor)
            return
        super()._update_cursor(pos)

    # -- 绘制 -------------------------------------------------------------- #
    def _draw_selection_chrome(self, painter: QPainter, sel) -> None:
        """绘制态只留边框，不再画选区手柄（手柄的语义变成标注手柄了）。"""
        if self._phase is Phase.DRAW:
            painter.setPen(QPen(QColor("#0a84ff"), 1.5))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(sel)
            return
        super()._draw_selection_chrome(painter, sel)

    # -- 取图 -------------------------------------------------------------- #
    @property
    def export_scale(self) -> str:
        """导出图片的像素口径（见 :mod:`ankisnip.settings`）。

        覆盖层的 ``config`` 生产环境总是 ``Settings``；单测里可能传基座的
        ``Config``，此时按默认策略处理。
        """
        return str(getattr(self._config, "export_scale", "screen"))

    def export_pixel_size(self, sel) -> tuple:
        """给定选区（widget / 逻辑坐标），返回导出图应有的像素尺寸。

        ``native`` 模式返回 ``None``，表示"保留抓取到的原生像素"。
        """
        if self.export_scale == "native":
            return ()
        return (max(1, int(round(sel.width()))), max(1, int(round(sel.height()))))

    def _resize_to(self, image: QImage, size: tuple) -> QImage:
        """按策略把图像缩到目标像素尺寸（``size`` 为空则原样返回）。"""
        if not size or (image.width(), image.height()) == size:
            return image
        scaled = image.scaled(size[0], size[1], Qt.IgnoreAspectRatio,
                              Qt.SmoothTransformation)
        scaled.setDevicePixelRatio(1.0)
        return scaled

    def selection_image(self) -> QImage:
        """返回选区图像（不含标注），像素口径由 ``export_scale`` 决定。

        AnkiSnip 注入：必须把 ``devicePixelRatio`` 归一成 1。

        ``QPixmap.toImage()`` 会把 pixmap 的 dpr 继承给 ``QImage``。而 Qt 的
        ``QPainter`` 在 dpr>1 的绘图设备上使用**逻辑坐标**——所有绘图操作会被
        再乘一次 dpr。标注坐标是物理像素，Retina（dpr=2）上就会被整整放大一倍
        并溢出到画布外，表现是"箭头/矩形完全没烧进图片里"。

        本函数的结果是后续一切处理（画布显示、`rasterize.bake`、kong 归一化）
        的输入，必须统一为"一个 QImage 像素 = 一个原图像素"。dpr 语义只在
        :meth:`compose` 出口那一层恢复。
        """
        if self._selection is None:
            return QImage()
        sel = self._selection.normalized()
        full = self._pixmap.toImage()
        # 用比例反推设备像素系数，避免依赖 toImage() 的 dpr 语义差异。
        ratio = (full.width() / self.width()) if self.width() else 1.0
        rect = QRect(
            int(round(sel.x() * ratio)),
            int(round(sel.y() * ratio)),
            max(1, int(round(sel.width() * ratio))),
            max(1, int(round(sel.height() * ratio))),
        )
        image = full.copy(rect)
        image.setDevicePixelRatio(1.0)
        # 抓取回来的是原生像素；默认要缩成"和框选范围一样大"的屏幕点口径。
        return self._resize_to(image, self.export_pixel_size(sel))

    def annotation_items(self) -> List[ann.Annotation]:
        """本次截图的所有标注（绘制态的优先；基座栈兜底）。"""
        if len(self._draw_stack):
            return self._draw_stack.items
        return self._stack.items

    def compose(self, include_masks: Optional[bool] = None) -> Optional[QPixmap]:
        """导出图：按原图分辨率合成。

        * 保存到 Anki（``include_masks=False``，默认）：挖空**不**进图片，
          只以 ``kong`` 坐标数据另存 —— 这是挖空能被模板渲染出来的前提。
        * 复制 / 另存图片（``include_masks=True``）：挖空**要**画进去，
          否则用户复制到别处拿到的是一张没遮住的原图。
        """
        if include_masks is None:
            include_masks = bool(getattr(self, "_include_masks_in_export", False))
        if self._selection is None or self._selection.isEmpty():
            return super().compose()
        sel = self._selection.normalized()
        size = self.export_pixel_size(sel)
        if len(self._draw_stack):
            from .rasterize import bake_for_export, bake_including_masks

            image = self.selection_image()
            baked = (bake_including_masks(image, self._draw_stack.items)
                     if include_masks else bake_for_export(image, self._draw_stack.items))
            pixmap = QPixmap.fromImage(self._resize_to(baked, size))
            pixmap.setDevicePixelRatio(self._dpr if not size else 1.0)
            return pixmap
        base = super().compose()
        if base is None:
            return None
        image = base.toImage()
        image.setDevicePixelRatio(1.0)
        return QPixmap.fromImage(self._resize_to(image, size))

    def _finish(self, action: str) -> None:
        # 画布里的行内文字输入框是画布的子控件，基座只提交它自己的那个。
        if self._canvas is not None:
            self._canvas._commit_text_edit()
        # 「保存到 Anki」之外的动作（复制 / 另存 / 贴图）都要把遮挡画出来。
        self._include_masks_in_export = action != "anki"
        super()._finish(action)

    # -- 提示 -------------------------------------------------------------- #
    def show_toast(self, text: str, msec: int = 1400) -> None:
        """在选区上显示一条会自动消失的浮层提示（复制等即时反馈用）。

        刻意**不**只依赖托盘通知：覆盖层是全屏最上层窗口，系统横幅不在它里面，
        用户在截图界面里是看不到的。
        """
        from .toast import show_toast

        anchor = None
        if self._selection is not None:
            sel = self._selection.normalized()
            anchor = sel.toRect()
        show_toast(self, text, msec=msec, anchor_rect=anchor)

    # -- 键盘 -------------------------------------------------------------- #
    def keyPressEvent(self, event) -> None:  # noqa: N802
        key = event.key()
        if key in (Qt.Key_Return, Qt.Key_Enter):
            if self._phase is Phase.DRAW:
                self._finish("anki")
            elif self._selection is not None:
                self.enter_draw_mode()
            return
        super().keyPressEvent(event)
