"""热键录制控件：用户按下组合键即可生成热键，不需要手写记法。

包含三块：

* :func:`hotkey_from_qt_event` —— Qt 按键事件 → :class:`~ankisnip.hotkey.Hotkey`
* :class:`HotkeyCaptureDialog` —— 模态"请按下快捷键"窗口
* :class:`HotkeyEdit` —— 设置表单里的一行（只读显示 + 录制 / 清除按钮）
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .hotkey import Hotkey, HotkeyError

#: 只按下这些键时不算一个完整热键，继续等待主键
_MODIFIER_KEYS = {
    Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_Meta, Qt.Key_AltGr,
    Qt.Key_CapsLock, Qt.Key_NumLock, Qt.Key_Super_L, Qt.Key_Super_R,
    Qt.Key_Hyper_L, Qt.Key_Hyper_R, Qt.Key_Mode_switch,
    Qt.Key_unknown,
}

#: Qt 功能键 → 规范 token
_QT_NAMED_KEYS = {
    Qt.Key_Space: "space",
    Qt.Key_Tab: "tab",
    Qt.Key_Return: "return",
    Qt.Key_Enter: "return",
    Qt.Key_Escape: "esc",
    Qt.Key_Backspace: "backspace",
    Qt.Key_Delete: "delete",
    Qt.Key_Up: "up",
    Qt.Key_Down: "down",
    Qt.Key_Left: "left",
    Qt.Key_Right: "right",
    Qt.Key_Home: "home",
    Qt.Key_End: "end",
    Qt.Key_PageUp: "pageup",
    Qt.Key_PageDown: "pagedown",
    Qt.Key_QuoteLeft: "grave",
    Qt.Key_Minus: "minus",
    Qt.Key_Equal: "equal",
    Qt.Key_BracketLeft: "lbracket",
    Qt.Key_BracketRight: "rbracket",
    Qt.Key_Backslash: "backslash",
    Qt.Key_Semicolon: "semicolon",
    Qt.Key_Apostrophe: "quote",
    Qt.Key_Comma: "comma",
    Qt.Key_Period: "period",
    Qt.Key_Slash: "slash",
    Qt.Key_CapsLock: "capslock",
    Qt.Key_Insert: "insert",
    Qt.Key_Print: "printscreen",
    Qt.Key_ScrollLock: "scrolllock",
    Qt.Key_Pause: "pause",
    Qt.Key_NumLock: "numlock",
    Qt.Key_Menu: "menu",
}


def hotkey_from_qt_event(event: QKeyEvent) -> Hotkey:
    """把 Qt 按键事件转成 :class:`Hotkey`。

    表里没有的键（如小键盘、多媒体键）退化为 ``vk<原生键码>``，这样任何键
    都能被原生后端直接使用。
    """
    mods = set()
    m = event.modifiers()
    if m & Qt.ControlModifier:
        mods.add("ctrl")
    if m & Qt.AltModifier:
        mods.add("alt")
    if m & Qt.ShiftModifier:
        mods.add("shift")
    if m & Qt.MetaModifier:
        mods.add("cmd")

    key_code = event.key()
    token: Optional[str] = None

    if Qt.Key_A <= key_code <= Qt.Key_Z:
        token = chr(key_code).lower()
    elif Qt.Key_0 <= key_code <= Qt.Key_9:
        token = chr(key_code)
    elif Qt.Key_F1 <= key_code <= Qt.Key_F35:
        token = f"f{key_code - Qt.Key_F1 + 1}"
    elif key_code in _QT_NAMED_KEYS:
        token = _QT_NAMED_KEYS[key_code]
    else:
        native = event.nativeVirtualKey()
        if native:
            token = f"vk{int(native)}"
        else:
            text = event.text()
            if len(text) == 1 and text.isascii() and text.isalnum():
                token = text.lower()
    if token is None:
        raise HotkeyError("这个按键暂不支持，请换一个（建议：字母 / 数字 / F1~F12）")
    return Hotkey(mods=frozenset(mods), key=token)


class HotkeyCaptureDialog(QDialog):
    """模态录制窗口：按下组合键后自动关闭并返回结果。"""

    def __init__(self, current: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("设置快捷键")
        self.setModal(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumWidth(360)

        self._hotkey: Optional[Hotkey] = None
        try:
            self._current = Hotkey.parse(current)
        except HotkeyError:
            self._current = None

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("请按下新的快捷键组合…"))
        self._preview = QLabel(self._preview_text())
        self._preview.setAlignment(Qt.AlignCenter)
        self._preview.setStyleSheet(
            "font-size: 20px; padding: 18px; border: 1px dashed #888; border-radius: 6px;")
        layout.addWidget(self._preview)

        self._hint = QLabel("按 Esc 取消；只按修饰键不会生效。")
        self._hint.setWordWrap(True)
        self._hint.setStyleSheet("color: #c0c0c0;")
        layout.addWidget(self._hint)

        buttons = QHBoxLayout()
        clear = QPushButton("清除")
        clear.clicked.connect(self._clear)
        buttons.addWidget(clear)
        buttons.addStretch(1)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        layout.addLayout(buttons)

    def _preview_text(self) -> str:
        return self._current.display() if self._current else "（未设置）"

    def _clear(self) -> None:
        self._hotkey = None
        self._cleared = True
        self.accept()

    # -- 结果 --------------------------------------------------------------- #
    @property
    def hotkey(self) -> Optional[Hotkey]:
        """录制到的热键；``None`` 表示用户取消。"""
        return self._hotkey

    @property
    def cleared(self) -> bool:
        return getattr(self, "_cleared", False)

    # -- 输入 --------------------------------------------------------------- #
    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        code = event.key()
        if code in _MODIFIER_KEYS:
            self._hint.setText("请再按一个普通键（例如字母 A 或 F5）…")
            return
        if code == Qt.Key_Escape and not (event.modifiers()
                                          & (Qt.ControlModifier | Qt.AltModifier
                                             | Qt.MetaModifier)):
            self.reject()
            return
        try:
            hotkey = hotkey_from_qt_event(event)
        except HotkeyError as exc:
            self._hint.setText(str(exc))
            return
        self._hotkey = hotkey
        self.accept()


class HotkeyEdit(QWidget):
    """表单里的热键行：只读显示 + 「录制」/「清除」按钮。"""

    hotkeyChanged = Signal(str)  # noqa: N815 - Qt 命名风格

    def __init__(self, spec: str, parent: QWidget | None = None,
                 pause_hotkey=None) -> None:
        super().__init__(parent)
        self._spec = spec or ""
        self._pause_hotkey = pause_hotkey

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._display = QLineEdit(self)
        self._display.setReadOnly(True)
        self._display.setPlaceholderText("未设置")
        self._display.setToolTip("点击「录制」后按下组合键即可，不需要手写")
        layout.addWidget(self._display, 1)

        self._record_btn = QPushButton("录制…")
        self._record_btn.setToolTip("按下你想要的快捷键组合")
        self._record_btn.clicked.connect(self._record)
        layout.addWidget(self._record_btn)

        clear_btn = QPushButton("清除")
        clear_btn.clicked.connect(self._clear)
        layout.addWidget(clear_btn)

        self._refresh()

    # -- 取值 --------------------------------------------------------------- #
    def spec(self) -> str:
        return self._spec

    def set_spec(self, spec: str) -> None:
        self._spec = spec or ""
        self._refresh()
        self.hotkeyChanged.emit(self._spec)

    # -- 交互 --------------------------------------------------------------- #
    def _record(self) -> None:
        # 录制期间必须挂起全局热键，否则按下的组合键会被系统当成"开始截图"。
        with self._suspend():
            dialog = HotkeyCaptureDialog(self._spec, self)
            dialog.setFocus(Qt.OtherFocusReason)
            accepted = dialog.exec()
        if not accepted:
            return
        if dialog.cleared:
            self.set_spec("")
            return
        if dialog.hotkey is not None:
            self.set_spec(dialog.hotkey.to_spec())

    def _clear(self) -> None:
        self.set_spec("")

    def _refresh(self) -> None:
        from .hotkey import format_hotkey

        self._display.setText(format_hotkey(self._spec) if self._spec else "")

    def _suspend(self):
        import contextlib

        if self._pause_hotkey is None:
            return contextlib.nullcontext()
        return self._pause_hotkey()
