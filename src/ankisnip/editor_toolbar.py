"""编辑器工具条。

说明：基座 ``base.toolbar.CaptureToolbar`` 是「选区浮动条」，动作是
复制 / 保存 / 贴图 / 完成 / 取消，语义与编辑器不同，因此这里是新增模块；
但工具集合、``Style``、``Annotation`` 家族仍然复用基座（``base.annotations``）。

布局分两行：

* 第一行常驻：回到选区 / 主工具 / 撤销重做 / 缩放菜单 / 更多 / 复制 / 保存 / 取消；
* 第二行上下文：只在绘制态出现，按当前工具显示 7 色调色板、线宽、字号或删除。

图片挖空模板只支持 7 个颜色且透明度固定，调色板因此收敛到
:mod:`ankisnip.mask_colors`，不再提供任意取色器。
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .base.annotations import ToolType
from .mask_colors import DEFAULT_COLOR, MASK_COLORS, normalize

#: 主工具（常驻第一行）。
_PRIMARY_TOOLS = [
    (ToolType.MASK, "▢", "挖空：只保存坐标，不烧录进图片（模板支持 7 色）"),
    (ToolType.ARROW, "↗", "箭头（会烧录进图片）"),
    (ToolType.TEXT, "T", "文字（会烧录进图片）"),
    (None, "✥", "选择 / 移动标注"),
]

#: 低频工具，收进「更多标注」。
_SECONDARY_TOOLS = [
    (ToolType.RECT, "矩形"),
    (ToolType.ELLIPSE, "椭圆"),
    (ToolType.HIGHLIGHT, "高亮"),
]

#: 线宽只影响这些工具。
_WIDTH_TOOLS = (ToolType.ARROW, ToolType.RECT, ToolType.ELLIPSE)

_ALL_DRAW_TOOLS = (
    ToolType.MASK, ToolType.ARROW, ToolType.TEXT,
    ToolType.RECT, ToolType.ELLIPSE, ToolType.HIGHLIGHT,
)

_STYLE = """
#EditorToolbar { background: #2b2b2b; border-radius: 6px; }
#EditorContextBar { background: #232323; border-radius: 4px; }
QToolButton, QPushButton {
    color: #eee; background: transparent; border: none;
    padding: 4px 8px; font-size: 15px; border-radius: 4px;
}
QToolButton:hover, QPushButton:hover { background: #3d3d3d; }
QToolButton:checked { background: #0a84ff; color: white; }
QToolButton:disabled { color: #777; }
QPushButton#save { background: #30d158; color: #10240f; font-weight: 600; }
QLabel { color: #bbb; padding: 0 4px; }
QSpinBox {
    color: #eee; background: #1c1c1c; border: 1px solid #444;
    border-radius: 3px; padding: 1px 3px;
}
QMenu { background: #2b2b2b; color: #eee; border: 1px solid #444; }
QMenu::item:selected { background: #0a84ff; }
"""


class EditorToolbar(QFrame):
    """编辑器工具条（两行上下文布局）。"""

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

        color = QColor(color)
        self._color = QColor(
            normalize(color.name() if color.isValid() else DEFAULT_COLOR))
        self._active_tool: Optional[ToolType] = None
        self._editing = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 4, 6, 4)
        outer.setSpacing(4)
        row = QHBoxLayout()
        row.setSpacing(2)
        outer.addLayout(row)

        self._reselect_btn = QPushButton("⛶", self)
        self._reselect_btn.setToolTip("回到选区：重新调整截图范围")
        self._reselect_btn.clicked.connect(self.reselectRequested.emit)
        row.addWidget(self._reselect_btn)

        self._add_separator(row)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._tool_buttons: list = []
        for tool, label, tip in _PRIMARY_TOOLS:
            btn = QToolButton(self)
            btn.setText(label)
            btn.setToolTip(tip)
            btn.setCheckable(True)
            if tool is None:
                btn.setChecked(True)
            btn.clicked.connect(lambda _=False, t=tool: self.toolSelected.emit(t))
            self._group.addButton(btn)
            self._tool_buttons.append((btn, tool))
            row.addWidget(btn)

        self._add_separator(row)

        for text, tip, signal in [
            ("↶", "撤销 Ctrl+Z", self.undoRequested),
            ("↷", "重做 Ctrl+Y", self.redoRequested),
        ]:
            btn = QPushButton(text, self)
            btn.setToolTip(tip)
            btn.clicked.connect(signal.emit)
            row.addWidget(btn)

        spacer = QWidget(self)
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        row.addWidget(spacer)

        self._zoom_btn = QToolButton(self)
        self._zoom_btn.setText("100%")
        self._zoom_btn.setToolTip("缩放：放大 / 缩小 / 适应窗口 / 100%")
        self._zoom_btn.setPopupMode(QToolButton.InstantPopup)
        self._zoom_menu = QMenu(self._zoom_btn)
        for text, signal in (
            ("放大", self.zoomInRequested),
            ("缩小", self.zoomOutRequested),
            ("适应窗口", self.fitRequested),
            ("100%", self.zoomResetRequested),
        ):
            action = self._zoom_menu.addAction(text)
            action.triggered.connect(lambda _=False, s=signal: s.emit())
        self._zoom_btn.setMenu(self._zoom_menu)
        row.addWidget(self._zoom_btn)

        self._more_btn = QToolButton(self)
        self._more_btn.setText("⋯")
        self._more_btn.setToolTip("更多标注 / 保存图片 / 删除选中")
        self._more_btn.setPopupMode(QToolButton.InstantPopup)
        self._more_menu = QMenu(self._more_btn)
        more_tools = self._more_menu.addMenu("更多标注")
        for tool, title in _SECONDARY_TOOLS:
            action = more_tools.addAction(title)
            action.triggered.connect(
                lambda _=False, t=tool: self.toolSelected.emit(t))
        self._more_menu.addSeparator()
        save_image = self._more_menu.addAction("保存图片…")
        save_image.triggered.connect(lambda _=False: self.saveImageRequested.emit())
        delete = self._more_menu.addAction("删除选中")
        delete.triggered.connect(lambda _=False: self.deleteRequested.emit())
        self._more_btn.setMenu(self._more_menu)
        row.addWidget(self._more_btn)

        copy_btn = QPushButton("复制", self)
        copy_btn.setToolTip("把烧录后的图片复制到剪贴板（含挖空）")
        copy_btn.clicked.connect(self.copyRequested.emit)
        row.addWidget(copy_btn)

        self._mask_label = QLabel("", self)
        self._mask_label.setVisible(False)
        row.addWidget(self._mask_label)

        self._save_btn = QPushButton("保存到 Anki", self)
        self._save_btn.setObjectName("save")
        self._save_btn.setToolTip("烧录箭头/文字，挖空写入 kong 字段，新建 Anki 卡片")
        self._save_btn.clicked.connect(self.saveToAnkiRequested.emit)
        row.addWidget(self._save_btn)

        cancel_btn = QPushButton("✕", self)
        cancel_btn.setToolTip("放弃本次截图（Esc）")
        cancel_btn.clicked.connect(self.cancelRequested.emit)
        row.addWidget(cancel_btn)

        self._context = self._build_context(width, font_size)
        outer.addWidget(self._context)
        self._update_context()

    # -- 上下文行 ---------------------------------------------------------- #
    def _build_context(self, width: int, font_size: int) -> QFrame:
        frame = QFrame(self)
        frame.setObjectName("EditorContextBar")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(4)

        # 7 色调色板（模板只支持这些颜色）
        self._ctx_color = QWidget(frame)
        color_layout = QHBoxLayout(self._ctx_color)
        color_layout.setContentsMargins(0, 0, 0, 0)
        color_layout.setSpacing(3)
        self._color_group = QButtonGroup(self)
        self._color_group.setExclusive(True)
        self._color_buttons: dict = {}
        for value, label in MASK_COLORS:
            btn = QToolButton(self._ctx_color)
            btn.setCheckable(True)
            btn.setFixedSize(20, 20)
            btn.setToolTip(f"{label} · {value}")
            btn.setStyleSheet(
                f"QToolButton {{ background: {value}; border: 1px solid #555;"
                f" border-radius: 9px; }}"
                f"QToolButton:hover {{ border: 1px solid #bbb; }}"
                f"QToolButton:checked {{ border: 2px solid #fff; }}"
            )
            btn.clicked.connect(
                lambda _=False, c=value: self._select_color(c))
            self._color_group.addButton(btn)
            self._color_buttons[value] = btn
            color_layout.addWidget(btn)
        layout.addWidget(self._ctx_color)

        self._ctx_width = QWidget(frame)
        width_layout = QHBoxLayout(self._ctx_width)
        width_layout.setContentsMargins(0, 0, 0, 0)
        width_layout.setSpacing(2)
        width_layout.addWidget(QLabel("线宽", self._ctx_width))
        self._width_spin = QSpinBox(self._ctx_width)
        self._width_spin.setRange(1, 30)
        self._width_spin.setValue(max(1, int(width)))
        self._width_spin.setFixedWidth(48)
        self._width_spin.valueChanged.connect(self.widthChanged.emit)
        width_layout.addWidget(self._width_spin)
        layout.addWidget(self._ctx_width)

        self._ctx_font = QWidget(frame)
        font_layout = QHBoxLayout(self._ctx_font)
        font_layout.setContentsMargins(0, 0, 0, 0)
        font_layout.setSpacing(2)
        font_layout.addWidget(QLabel("字号", self._ctx_font))
        self._font_spin = QSpinBox(self._ctx_font)
        self._font_spin.setRange(8, 200)
        self._font_spin.setValue(max(8, int(font_size)))
        self._font_spin.setFixedWidth(56)
        self._font_spin.valueChanged.connect(self.fontSizeChanged.emit)
        font_layout.addWidget(self._font_spin)
        layout.addWidget(self._ctx_font)

        spacer = QWidget(frame)
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        layout.addWidget(spacer)
        self._sync_color_swatch()
        return frame

    def _update_context(self) -> None:
        """按「是否绘制态 + 当前工具」显示上下文控件。

        选择/移动工具不需要第二行（删除走 Delete 键或「⋯」菜单），
        只有真正会用到颜色/线宽/字号的绘制工具才展开。
        """
        tool = self._active_tool
        visible = self._editing and tool in _ALL_DRAW_TOOLS
        self._context.setVisible(visible)
        if not visible:
            self.adjustSize()
            return
        self._ctx_color.setVisible(True)
        self._ctx_width.setVisible(tool in _WIDTH_TOOLS)
        self._ctx_font.setVisible(tool == ToolType.TEXT)
        self.adjustSize()

    # -- 内部 ------------------------------------------------------------- #
    def _add_separator(self, layout: QHBoxLayout) -> None:
        sep = QFrame(self)
        sep.setFrameShape(QFrame.VLine)
        sep.setStyleSheet("color: #555;")
        layout.addWidget(sep)

    def _select_color(self, value: str) -> None:
        color = QColor(value)
        if color.name().lower() == self._color.name().lower():
            return
        self._color = color
        self._sync_color_swatch()
        self.colorChanged.emit(color)

    def _sync_color_swatch(self) -> None:
        current = self._color.name().lower()
        for value, btn in self._color_buttons.items():
            btn.setChecked(value == current)

    # -- 外部接口 ---------------------------------------------------------- #
    def select_tool(self, tool) -> None:
        """同步按钮的选中态（覆盖层进入绘制态时由外部驱动）。"""
        self._active_tool = tool
        for btn, t in self._tool_buttons:
            btn.setChecked((t is None and tool is None) or t == tool)
        self._update_context()

    def set_editing(self, editing: bool) -> None:
        """选区态 / 绘制态切换（决定第二行是否出现）。"""
        self._editing = bool(editing)
        self._update_context()

    def set_reselect_enabled(self, enabled: bool) -> None:
        self._reselect_btn.setEnabled(enabled)

    def set_zoom_text(self, text: str) -> None:
        # QToolButton 的 InstantPopup 会自己画下拉指示符，文字里不要再带 ▾。
        self._zoom_btn.setText(text)
        self._zoom_btn.setEnabled(text != "选区")

    def set_mask_count(self, count: int) -> None:
        self._mask_label.setText(f"▢ {count}")
        self._mask_label.setVisible(count > 0)
        self._mask_label.setToolTip(
            f"本次将写入 {count} 个挖空" if count else "")
        self._save_btn.setToolTip(
            f"烧录箭头/文字，{count} 个挖空写入 kong 字段，新建 Anki 卡片"
            if count else "当前没有挖空，将只保存图片（kong 字段为空）"
        )
