"""快捷键设置：截图 + 附加动作（打开牌组目录 / 打开设置界面），和「设置…」分开。

三个热键都在一个窗口里录制；任一条重复会被拒绝（同一个组合键只能属于一个动作）。
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QLabel,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from .hotkey_capture import HotkeyEdit

#: 附加动作：属性名 → 界面文案（顺序即界面顺序）
_EXTRA_ROWS = [
    ("hotkey_open_dir", "打开牌组目录"),
    ("hotkey_archive", "归档离线牌组"),
    ("hotkey_open_settings", "打开设置界面"),
]


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
        title = QLabel("全局快捷键")
        title.setStyleSheet("font-weight: 600;")
        layout.addWidget(title)

        grid = QGridLayout()
        grid.setColumnMinimumWidth(0, 110)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)

        self.hotkey_edit = HotkeyEdit(settings.hotkey, self)
        grid.addWidget(QLabel("截图"), 0, 0)
        grid.addWidget(self.hotkey_edit, 0, 1)

        self.extra_edits: dict[str, HotkeyEdit] = {}
        for row, (attr, label) in enumerate(_EXTRA_ROWS, start=1):
            edit = HotkeyEdit(getattr(settings, attr, ""), self)
            self.extra_edits[attr] = edit
            grid.addWidget(QLabel(label), row, 0)
            grid.addWidget(edit, row, 1)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)

        hint = QLabel("点击「录制…」后直接按下组合键；录制期间旧热键会暂时挂起。"
                      "「清除」= 不注册该项。")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        if status_text:
            status = QLabel(status_text)
            status.setWordWrap(True)
            status.setStyleSheet("color: #888;")
            layout.addWidget(status)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def spec(self) -> str:
        return self.hotkey_edit.spec()

    def _validate_and_accept(self) -> None:
        """同一组合键不能分给两个动作，否则两个动作都收不到（后端会冲突）。"""
        labels = [("截图", self.hotkey_edit.spec())]
        labels += [(label, edit.spec())
                   for (attr, label), edit in zip(_EXTRA_ROWS, self.extra_edits.values())]
        seen: dict[str, str] = {}
        for label, spec in labels:
            if not spec:
                continue
            if spec in seen:
                QMessageBox.warning(
                    self, "快捷键重复",
                    f"「{label}」和「{seen[spec]}」设置了同一个快捷键，"
                    "请改掉其中一个。")
                return
            seen[spec] = label
        self.accept()

    def apply(self) -> None:
        self.settings.hotkey = self.spec()
        for attr, edit in self.extra_edits.items():
            setattr(self.settings, attr, edit.spec())
