"""编辑器工具条。

说明：基座 ``base.toolbar.CaptureToolbar`` 是「选区浮动条」，动作是
复制 / 保存 / 贴图 / 完成 / 取消，语义与编辑器不同，因此这里是新增模块；
但工具集合、``Style``、``Annotation`` 家族仍然复用基座（``base.annotations``）。
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QButtonGroup,
    QColorDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QToolButton,
    QWidget,
)

from .base.annotations import ToolType

# (tool, 图标, 提示) —— 挖空来自基座 ToolType.MASK（已在 base 中注入）
_TOOLS = [
    (None, "▩", "选择 / 移动（Esc 取消选中）"),
    (ToolType.ARROW, "↗", "箭头（会烧录进图片）"),
    (ToolType.TEXT, "T", "文字（会烧录进图片）"),
    (ToolType.MASK, "▢", "挖空（**不会**烧录进图片，只保存坐标）"),
    (ToolType.RECT, "▭", "矩形"),
    (ToolType.ELLIPSE, "◯", "椭圆"),
    (ToolType.HIGHLIGHT, "▥", "高亮"),
]

_STYLE = """
#EditorToolbar { background: #2b2b2b; border-radius: 6px; }
QToolButton, QPushButton {
    color: #eee; background: transparent; border: none;
    padding: 4px 8px; font-size: 15px; border-radius: 4px;
}
QToolButton:hover, QPushButton:hover { background: #3d3d3d; }
QToolButton:checked { background: #0a84ff; color: white; }
QPushButton#save { background: #30d158; color: #10240f; font-weight: 600; }
QLabel { color: #bbb; padding: 0 4px; }
"""


class EditorToolbar(QFrame):
    """编辑器顶部工具条。"""

    toolSelected = Signal(object)          # Optional[ToolType]
    colorChanged = Signal(QColor)
    widthChanged = Signal(int)
    fontSizeChanged = Signal(int)
    undoRequested = Signal()
    redoRequested = Signal()
    deleteRequested = Signal()
    zoomInRequested = Signal()
    zoomOutRequested = Signal()
    zoomResetRequested = Signal()
    fitRequested = Signal()
    saveToAnkiRequested = Signal()
    saveImageRequested = Signal()
    # AnkiSnip：标注已合进截图覆盖层，工具条同时承担"会话级"动作
    reselectRequested = Signal()   # 回到选区态，重新框选范围
    copyRequested = Signal()
    cancelRequested = Signal()

    def __init__(self, color: QColor, width: int, font_size: int,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("EditorToolbar")
        self.setFrameShape(QFrame.NoFrame)
        self.setStyleSheet(_STYLE)
        self._color = QColor(color)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(2)

        self._reselect_btn = QPushButton("⛶", self)
        self._reselect_btn.setToolTip("回到选区：重新调整截图范围")
        self._reselect_btn.clicked.connect(self.reselectRequested.emit)
        layout.addWidget(self._reselect_btn)

        self._add_separator(layout)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._tool_buttons: list = []
        for tool, label, tip in _TOOLS:
            btn = QToolButton(self)
            btn.setText(label)
            btn.setToolTip(tip)
            btn.setCheckable(True)
            if tool is None:
                btn.setChecked(True)
            btn.clicked.connect(lambda _=False, t=tool: self.toolSelected.emit(t))
            self._group.addButton(btn)
            self._tool_buttons.append((btn, tool))
            layout.addWidget(btn)

        self._add_separator(layout)

        self._color_btn = QPushButton(self)
        self._color_btn.setToolTip("颜色")
        self._color_btn.setFixedSize(22, 22)
        self._update_color_swatch()
        self._color_btn.clicked.connect(self._pick_color)
        layout.addWidget(self._color_btn)

        self._width_spin = QSpinBox(self)
        self._width_spin.setToolTip("线宽")
        self._width_spin.setRange(1, 30)
        self._width_spin.setValue(width)
        self._width_spin.setFixedWidth(48)
        self._width_spin.valueChanged.connect(self.widthChanged.emit)
        layout.addWidget(self._width_spin)

        self._font_spin = QSpinBox(self)
        self._font_spin.setToolTip("字号（图片像素）")
        self._font_spin.setRange(8, 200)
        self._font_spin.setValue(font_size)
        self._font_spin.setFixedWidth(56)
        self._font_spin.valueChanged.connect(self.fontSizeChanged.emit)
        layout.addWidget(self._font_spin)

        self._add_separator(layout)

        for text, tip, signal in [
            ("↶", "撤销 Ctrl+Z", self.undoRequested),
            ("↷", "重做 Ctrl+Y", self.redoRequested),
            ("🗑", "删除选中 Delete", self.deleteRequested),
        ]:
            btn = QPushButton(text, self)
            btn.setToolTip(tip)
            btn.clicked.connect(signal.emit)
            layout.addWidget(btn)

        self._add_separator(layout)

        for text, tip, signal in [
            ("−", "缩小（滚轮）", self.zoomOutRequested),
            ("＋", "放大（滚轮）", self.zoomInRequested),
            ("1:1", "100%", self.zoomResetRequested),
            ("⤢", "适应窗口", self.fitRequested),
        ]:
            btn = QPushButton(text, self)
            btn.setToolTip(tip)
            btn.clicked.connect(signal.emit)
            layout.addWidget(btn)

        self._zoom_label = QLabel("100%", self)
        self._zoom_label.setMinimumWidth(52)
        self._zoom_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._zoom_label)

        spacer = QWidget(self)
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        layout.addWidget(spacer)

        save_img = QPushButton("保存图片…", self)
        save_img.setToolTip("把烧录后的图片存到磁盘（挖空不进图片）")
        save_img.clicked.connect(self.saveImageRequested.emit)
        layout.addWidget(save_img)

        copy_btn = QPushButton("复制", self)
        copy_btn.setToolTip("把烧录后的图片复制到剪贴板")
        copy_btn.clicked.connect(self.copyRequested.emit)
        layout.addWidget(copy_btn)

        self._save_btn = QPushButton("保存到 Anki", self)
        self._save_btn.setObjectName("save")
        self._save_btn.setToolTip("烧录箭头/文字，挖空写入 kong 字段，新建 Anki 卡片")
        self._save_btn.clicked.connect(self.saveToAnkiRequested.emit)
        layout.addWidget(self._save_btn)

        cancel_btn = QPushButton("✕", self)
        cancel_btn.setToolTip("放弃本次截图（Esc）")
        cancel_btn.clicked.connect(self.cancelRequested.emit)
        layout.addWidget(cancel_btn)

    # -- 内部 ------------------------------------------------------------- #
    def _add_separator(self, layout: QHBoxLayout) -> None:
        sep = QFrame(self)
        sep.setFrameShape(QFrame.VLine)
        sep.setStyleSheet("color: #555;")
        layout.addWidget(sep)

    def _update_color_swatch(self) -> None:
        self._color_btn.setStyleSheet(
            f"background: {self._color.name()}; border: 1px solid #888; border-radius: 4px;"
        )

    def _pick_color(self) -> None:
        chosen = QColorDialog.getColor(self._color, self, "选择颜色")
        if chosen.isValid():
            self._color = chosen
            self._update_color_swatch()
            self.colorChanged.emit(chosen)

    def select_tool(self, tool) -> None:
        """同步按钮的选中态（覆盖层进入绘制态时由外部驱动）。"""
        for btn, t in self._tool_buttons:
            if t is None:
                btn.setChecked(tool is None)
            else:
                btn.setChecked(t == tool)

    def set_reselect_enabled(self, enabled: bool) -> None:
        self._reselect_btn.setEnabled(enabled)

    def set_zoom_text(self, text: str) -> None:
        self._zoom_label.setText(text)

    def set_mask_count(self, count: int) -> None:
        self._save_btn.setToolTip(
            f"烧录箭头/文字，{count} 个挖空写入 kong 字段，新建 Anki 卡片"
            if count else "当前没有挖空，将只保存图片（kong 字段为空）"
        )
