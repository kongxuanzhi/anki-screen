"""快捷键设置：只负责全局截图热键，和「设置…」分开。"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from .hotkey_capture import HotkeyEdit


class HotkeyDialog(QDialog):
    """录制 / 修改全局热键，并展示当前注册状态。"""

    def __init__(self, settings, status_text: str = "", parent=None) -> None:
        # 调用方可能误传 QObject（AnkiSnipApp 不是 QWidget）；QDialog 只接受
        # QWidget，传错会直接抛 TypeError，菜单看起来就"点了没反应"。
        if parent is not None and not isinstance(parent, QWidget):
            parent = None
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("快捷键设置")
        self.setMinimumWidth(460)

        layout = QVBoxLayout(self)
        title = QLabel("全局截图快捷键")
        title.setStyleSheet("font-weight: 600;")
        layout.addWidget(title)

        self.hotkey_edit = HotkeyEdit(settings.hotkey, self)
        layout.addWidget(self.hotkey_edit)

        hint = QLabel("点击「录制…」后直接按下组合键；录制期间旧热键会暂时挂起。")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        if status_text:
            status = QLabel(status_text)
            status.setWordWrap(True)
            status.setStyleSheet("color: #888;")
            layout.addWidget(status)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def spec(self) -> str:
        return self.hotkey_edit.spec()

    def apply(self) -> None:
        self.settings.hotkey = self.spec()
