"""Floating toolbar shown next to the selection during capture."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QButtonGroup,
    QColorDialog,
    QFrame,
    QHBoxLayout,
    QPushButton,
    QSpinBox,
    QToolButton,
    QWidget,
)

from .annotations import ToolType

# (tool, short label, tooltip)
_TOOLS = [
    (ToolType.RECT, "▭", "矩形"),
    (ToolType.ELLIPSE, "◯", "椭圆"),
    (ToolType.ARROW, "↗", "箭头"),
    (ToolType.PEN, "✎", "画笔"),
    (ToolType.TEXT, "T", "文字"),
    (ToolType.MOSAIC, "▦", "马赛克"),
    (ToolType.HIGHLIGHT, "▥", "高亮"),
    (ToolType.NUMBER, "①", "序号"),
    (ToolType.MASK, "▢", "挖空（不合成进图片，仅保存坐标）"),  # AnkiSnip 注入
]


class CaptureToolbar(QFrame):
    """Compact toolbar emitting signals for tool/style/action changes."""

    toolSelected = Signal(object)  # ToolType
    colorChanged = Signal(QColor)
    widthChanged = Signal(int)
    undoRequested = Signal()
    redoRequested = Signal()
    saveRequested = Signal()
    copyRequested = Signal()
    pinRequested = Signal()
    confirmRequested = Signal()
    cancelRequested = Signal()

    def __init__(self, color: QColor, width: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("CaptureToolbar")
        self.setFrameShape(QFrame.NoFrame)
        self.setStyleSheet(
            """
            #CaptureToolbar { background: #2b2b2b; border-radius: 6px; }
            QToolButton, QPushButton {
                color: #eee; background: transparent; border: none;
                padding: 4px 7px; font-size: 15px; border-radius: 4px;
            }
            QToolButton:hover, QPushButton:hover { background: #3d3d3d; }
            QToolButton:checked { background: #0a84ff; color: white; }
            QPushButton#confirm { color: #30d158; }
            QPushButton#cancel { color: #ff453a; }
            """
        )
        self._color = QColor(color)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(2)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        for tool, label, tip in _TOOLS:
            btn = QToolButton(self)
            btn.setText(label)
            btn.setToolTip(tip)
            btn.setCheckable(True)
            btn.clicked.connect(lambda _=False, t=tool: self.toolSelected.emit(t))
            self._group.addButton(btn)
            layout.addWidget(btn)

        self._add_separator(layout)

        # color swatch
        self._color_btn = QPushButton(self)
        self._color_btn.setToolTip("颜色")
        self._color_btn.setFixedSize(22, 22)
        self._update_color_swatch()
        self._color_btn.clicked.connect(self._pick_color)
        layout.addWidget(self._color_btn)

        # line width
        self._width_spin = QSpinBox(self)
        self._width_spin.setToolTip("线宽")
        self._width_spin.setRange(1, 30)
        self._width_spin.setValue(width)
        self._width_spin.setFixedWidth(48)
        self._width_spin.valueChanged.connect(self.widthChanged.emit)
        layout.addWidget(self._width_spin)

        self._add_separator(layout)

        for text, tip, signal, name in [
            ("↶", "撤销", self.undoRequested, None),
            ("↷", "重做", self.redoRequested, None),
            ("💾", "保存", self.saveRequested, None),
            ("⧉", "复制", self.copyRequested, None),
            ("📌", "贴图", self.pinRequested, None),
            ("✔", "完成", self.confirmRequested, "confirm"),
            ("✕", "取消", self.cancelRequested, "cancel"),
        ]:
            btn = QPushButton(text, self)
            btn.setToolTip(tip)
            if name:
                btn.setObjectName(name)
            btn.clicked.connect(signal.emit)
            layout.addWidget(btn)

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
