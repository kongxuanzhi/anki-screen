"""Application entry point: system tray, global hotkey, capture orchestration."""

from __future__ import annotations

import datetime as _dt
import logging
import sys
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import QObject, QPoint, Qt, Signal
from PySide6.QtGui import (
    QAction,
    QCursor,
    QFont,
    QGuiApplication,
    QIcon,
    QKeySequence,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QMenu,
    QMessageBox,
    QSystemTrayIcon,
)

from . import capture, permissions
from .config import Config
from .overlay import CaptureOverlay
from .pin import PinWindow

_IS_MAC = sys.platform == "darwin"

LOG = logging.getLogger(__name__)


#: 热键记法 → Qt 键名。
#: 注意 macOS 上 Qt 的 Ctrl 就是 ⌘、Meta 才是 ⌃（历史遗留的互换），所以
#: cmd/ctrl 的写法在两个平台上是对调的；写反了菜单快捷键会变成错误的组合。
_QT_MODS = (
    {"ctrl": "Meta", "alt": "Alt", "shift": "Shift", "cmd": "Ctrl"}
    if _IS_MAC
    else {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "cmd": "Meta"}
)
_QT_KEYS = {
    "space": "Space", "tab": "Tab", "return": "Return", "esc": "Esc",
    "backspace": "Backspace", "delete": "Delete",
    "up": "Up", "down": "Down", "left": "Left", "right": "Right",
    "home": "Home", "end": "End", "pageup": "PgUp", "pagedown": "PgDn",
    "grave": "`", "minus": "-", "equal": "=", "lbracket": "[",
    "rbracket": "]", "backslash": "\\", "semicolon": ";", "quote": "'",
    "comma": ",", "period": ".", "slash": "/",
}


def _qt_shortcut_text(spec: str) -> str:
    """把配置里的热键（pynput 记法）转成 QKeySequence 能解析的字符串。

    这个快捷键会挂在菜单的「截图」项上：**菜单打开时**由 AppKit 的 key
    equivalent 处理，菜单关闭时由 Carbon 全局热键处理。这样菜单开着按快捷键
    也能截图（此时 Carbon 热键被系统挡住）。
    """
    try:
        from ..hotkey import Hotkey

        hotkey = Hotkey.parse(spec)
    except Exception:  # pragma: no cover - 配置损坏时
        return ""
    if hotkey.key.startswith("vk"):
        return ""  # 原始键码 QKeySequence 不认识，保持无菜单快捷键
    parts = [_QT_MODS[m] for m in ("ctrl", "alt", "shift", "cmd") if m in hotkey.mods]
    key = hotkey.key
    if len(key) == 1:
        parts.append(key.upper())
    else:
        parts.append(_QT_KEYS.get(key, key.upper()))
    return "+".join(parts)


def _fallback_icon() -> QIcon:
    """A drawn "S" badge icon so we don't need to ship binary assets."""
    size = 64
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)

    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing)

    badge = QPainterPath()
    margin = 4
    badge.addRoundedRect(margin, margin, size - 2 * margin, size - 2 * margin, 16, 16)

    painter.setPen(Qt.NoPen)
    gradient = QLinearGradient(0, 0, size, size)
    gradient.setColorAt(0.0, "#5B8DEF")
    gradient.setColorAt(1.0, "#9B5DE5")
    painter.setBrush(gradient)
    painter.drawPath(badge)

    font = QFont("Helvetica")
    font.setBold(True)
    font.setPixelSize(int(size * 0.58))
    painter.setFont(font)
    painter.setPen(Qt.white)
    painter.drawText(pix.rect(), Qt.AlignCenter, "S")

    painter.end()
    return QIcon(pix)


class SnipApp(QObject):
    """Owns the tray icon, hotkey listener and the active capture session."""

    # Emitted from the (non-Qt) hotkey thread; queued onto the GUI thread.
    _hotkeyPressed = Signal()

    # AnkiSnip 注入：允许子类替换覆盖层 / 配置实现（默认保持上游行为）。
    overlay_cls = CaptureOverlay
    config_cls = Config

    def __init__(self, app: QApplication) -> None:
        super().__init__()
        self._app = app
        self._config = self.config_cls()
        self._overlays: List[CaptureOverlay] = []
        self._pins: List[PinWindow] = []
        self._hotkey_listener = None
        self._hotkey_active = False

        # On macOS, ask for Screen Recording up-front so the process is
        # registered in the privacy list and the user gets the system prompt
        # before their first capture attempt.
        if _IS_MAC:
            permissions.request_screen_capture()
            # 先在"还没抢过焦点"时记下用户当前所在的 App，作为之后交还
            # 前台的兜底目标（点托盘图标那一刻 AppKit 可能已经把我们
            # 顶成前台，那时再记就只能记到自己）。
            try:
                from ..mac_focus import record_frontmost, remember_policy

                remember_policy()
                record_frontmost()
            except Exception:  # pragma: no cover - 平台相关
                LOG.debug("启动时记录前台应用失败", exc_info=True)

        self._build_tray()
        self._hotkeyPressed.connect(self.start_capture, Qt.QueuedConnection)
        self._install_hotkey()

    # ------------------------------------------------------------------ #
    # tray
    # ------------------------------------------------------------------ #
    def _build_tray(self) -> None:
        self._tray = QSystemTrayIcon(_fallback_icon(), self)
        self._tray.setToolTip("mysphinx-snip 截图工具")

        menu = QMenu()
        self._capture_action = QAction("截图", menu)
        self._capture_action.triggered.connect(self.start_capture)
        menu.addAction(self._capture_action)

        if _IS_MAC:
            menu.addSeparator()
            perm_menu = menu.addMenu("权限设置…")
            # 全局热键走 Carbon RegisterEventHotKey，不需要辅助功能；只有
            # pynput 兜底才需要，因此不再把「辅助功能」放进日常菜单。
            rec = QAction("屏幕录制（截图必需）", perm_menu)
            rec.triggered.connect(permissions.open_screen_recording_settings)
            perm_menu.addAction(rec)

        menu.addSeparator()
        quit_action = QAction("退出", menu)
        quit_action.triggered.connect(self._app.quit)
        menu.addAction(quit_action)

        # AnkiSnip 注入：把菜单交给系统托盘（macOS 上是 NSStatusItem.menu）。
        #
        # 这是唯一可靠的做法：AppKit 自己负责弹出、焦点、图标高亮、Esc 和菜单项
        # 派发。以前为了"左键截图、右键菜单"自己 exec() QMenu，每次都要跟状态项
        # 的点击跟踪 / NSEventTrackingRunLoopMode 较劲，结果就是图标残留高亮、
        # 快捷键失灵、菜单项点不动 —— Qt 官方修 QTBUG-103515 也是改用
        # NSStatusItem.menu。
        #
        # 行为：macOS 上左键 / 右键都会弹出这个菜单（系统标准行为），「截图」
        # 是第一项；Windows / Linux 上仍保持"左键截图、右键菜单"。
        self._tray_menu = menu
        self._tray.setContextMenu(menu)
        if not _IS_MAC:
            self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    def _on_tray_activated(self, reason) -> None:
        """非 macOS：左键（Trigger）直接截图；右键菜单由系统托盘自己弹。"""
        if self._tray_activation_is_capture(reason):
            self.start_capture()

    @staticmethod
    def _tray_activation_is_capture(reason) -> bool:
        return reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick)

    # ------------------------------------------------------------------ #
    # global hotkey (added: native-first, pynput fallback)
    # ------------------------------------------------------------------ #
    def _install_hotkey(self) -> None:
        """注册全局热键。

        改用 ``ankisnip.hotkey.GlobalHotkeyManager``：优先平台原生注册
        （macOS Carbon / Win32 RegisterHotKey，均**不需要**辅助功能权限），
        失败才回落到 pynput。上游实现保留为 ``_install_hotkey_pynput``，
        仅在本模块不可用时兜底。
        """
        try:
            from ..hotkey import GlobalHotkeyManager
        except Exception:  # pragma: no cover - 基座被单独使用时
            self._install_hotkey_pynput()
            return

        self._hotkey_manager = GlobalHotkeyManager(self._on_hotkey)
        if not getattr(self._config, "hotkey_enabled", True):
            self._set_capture_label(with_hotkey=False)
            return
        status = self._hotkey_manager.start(self._config.hotkey)
        self._set_capture_label(with_hotkey=status.ok)
        if status.ok:
            return
        LOG.error("全局热键注册失败：%s", status.error)
        self._notify(self._hotkey_failure_message(status))

    def _on_hotkey(self) -> None:
        """热键回调（日志用于排查"菜单开着时按键无效"这类问题）。"""
        LOG.info("全局热键触发")
        self._hotkeyPressed.emit()

    def _install_hotkey_pynput(self) -> None:
        """上游实现（pynput 全局监听），仅作兜底。

        macOS 上需要「辅助功能」权限，否则监听器静默失效。
        """
        if _IS_MAC and not permissions.accessibility_trusted():
            self._set_capture_label(with_hotkey=False)
            self._notify(
                "未授予「辅助功能」，全局热键暂不可用。\n"
                "可点菜单栏图标 →「截图」，或在「权限设置…」中开启后重启程序。"
            )
            return
        try:
            from pynput import keyboard
        except Exception as exc:  # pragma: no cover - platform dependent
            print(f"[mysphinx-snip] global hotkey unavailable: {exc}", file=sys.stderr)
            self._set_capture_label(with_hotkey=False)
            return
        try:
            self._hotkey_listener = keyboard.GlobalHotKeys(
                {self._config.hotkey: self._hotkeyPressed.emit}
            )
            self._hotkey_listener.start()
            self._hotkey_active = True
            self._set_capture_label(with_hotkey=True)
        except Exception as exc:  # pragma: no cover - platform dependent
            print(f"[mysphinx-snip] could not register hotkey: {exc}", file=sys.stderr)
            self._set_capture_label(with_hotkey=False)

    def _hotkey_failure_message(self, status) -> str:
        """把注册失败翻译成一句用户能看懂、能动手的提示。"""
        text = status.describe()
        if _IS_MAC:
            if not permissions.accessibility_trusted():
                text += ("\n\n未授予「辅助功能」，pynput 兜底方案无法使用。"
                         "可在托盘菜单「权限设置…」中开启，或换一个未被占用的组合键。")
        text += "\n\n也可以点托盘图标 →「截图」临时使用，或在「设置…」里更换快捷键。"
        return text

    def reload_hotkey(self):
        """热重载热键（改设置后立即生效，无需重启）。"""
        manager = getattr(self, "_hotkey_manager", None)
        if not getattr(self._config, "hotkey_enabled", True):
            if manager is not None:
                manager.stop()
            self._set_capture_label(with_hotkey=False)
            return getattr(manager, "status", None)
        if manager is None:
            self._install_hotkey()
            manager = getattr(self, "_hotkey_manager", None)
            return getattr(manager, "status", None)
        status = manager.restart(self._config.hotkey)
        self._set_capture_label(with_hotkey=status.ok)
        return status

    def hotkey_status_text(self) -> str:
        """当前热键状态（托盘菜单 / 对话框展示）。"""
        manager = getattr(self, "_hotkey_manager", None)
        if manager is None or not manager.status.spec:
            return f"热键：{self._config.hotkey}\n状态：未注册（未启用热键管理器）"
        status = manager.status
        if status.ok:
            return (f"热键：{status.display}\n"
                    f"状态：已生效\n"
                    f"后端：{status.backend}")
        return status.describe()

    def _set_capture_label(self, with_hotkey: bool) -> None:
        # 快捷键由菜单项自己显示在右侧（原生菜单的 key equivalent），标题里
        # 不再重复写一遍组合键；只有全局注册失败时补一句提示。
        if with_hotkey:
            self._capture_action.setText("截图")
        else:
            self._capture_action.setText("截图（全局热键未生效）")
        # 菜单项也带同一个快捷键：原生菜单打开时 Carbon 热键会被系统挡住，
        # 但 AppKit 会把菜单项的 key equivalent 派发给它。
        shortcut = _qt_shortcut_text(self._config.hotkey)
        self._capture_action.setShortcut(QKeySequence(shortcut) if shortcut else QKeySequence())
        self._capture_action.setShortcutContext(Qt.WidgetShortcut)

    # ------------------------------------------------------------------ #
    # capture session
    # ------------------------------------------------------------------ #
    def _prompt_screen_recording(self) -> None:
        # Ask the system again (re-registers the process) then guide the user.
        permissions.request_screen_capture()
        box = QMessageBox()
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("mysphinx-snip")
        box.setText(
            "需要「屏幕录制」权限才能截图。\n\n"
            "请在系统设置中为运行本程序的应用（如终端 Terminal/iTerm）"
            "勾选「屏幕录制」，然后完全退出并重启该应用。"
        )
        open_btn = box.addButton("打开设置", QMessageBox.AcceptRole)
        box.addButton("取消", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is open_btn:
            permissions.open_screen_recording_settings()

    def start_capture(self) -> None:
        if self._overlays:  # a capture is already in progress
            return
        # AnkiSnip 注入：此刻用户一定还停在他原本的 App 上——记下 pid，
        # 等截图界面收起后把前台照原样还回去（对应"图标一直高亮"的抱怨）。
        if _IS_MAC:
            try:
                from ..mac_focus import cancel_pending_resign, record_frontmost

                # 从托盘菜单里点「截图」时，aboutToHide 已经把退让排进队列了；
                # 那几步是延迟执行的，跑到的时候截图界面刚把自己顶到前台，
                # 于是延迟的 deactivate 会把截图界面一脚踹下去。先作废它们。
                cancel_pending_resign()
                record_frontmost()
            except Exception:  # pragma: no cover - 平台相关
                LOG.debug("记录前台应用失败", exc_info=True)
        if not permissions.screen_capture_allowed():
            self._prompt_screen_recording()
            return
        shots = capture.grab_all_screens()
        if not shots:
            QMessageBox.warning(
                None,
                "mysphinx-snip",
                "屏幕抓取失败，未获取到任何画面。\n请确认已授予「屏幕录制」权限并重启程序。",
            )
            return
        cursor_pos = QCursor.pos()
        for shot in shots:
            overlay = self.overlay_cls(shot, self._config)
            overlay.completed.connect(self._on_completed)
            overlay.cancelled.connect(self._close_overlays)
            self._overlays.append(overlay)
            # Bind the window to its physical screen and cover exactly that
            # screen's geometry (showFullScreen misbehaves across monitors on mac).
            overlay.setGeometry(shot.geometry)
            overlay.show()
            handle = overlay.windowHandle()
            if handle is not None:
                handle.setScreen(shot.screen)
            overlay.setGeometry(shot.geometry)
            overlay.raise_()
            if _IS_MAC:
                # 覆盖层要盖住菜单栏，否则最上方一条无法选区（截图里其实有）。
                try:
                    from ..mac_focus import raise_window_above_menu_bar

                    raise_window_above_menu_bar(overlay)
                except Exception:  # pragma: no cover - 平台相关
                    LOG.debug("抬高覆盖层窗口层级失败", exc_info=True)
        # Give keyboard focus to the overlay under the cursor.
        for overlay in self._overlays:
            if overlay.geometry().contains(cursor_pos):
                overlay.activateWindow()
                overlay.setFocus(Qt.OtherFocusReason)
                break
        else:
            self._overlays[0].activateWindow()
            self._overlays[0].setFocus(Qt.OtherFocusReason)

    def _close_overlays(self) -> None:
        for overlay in self._overlays:
            overlay.close()
        self._overlays.clear()

    def _on_completed(self, action: str, pixmap: QPixmap, origin: QPoint) -> None:
        self._close_overlays()
        if action == "copy":
            QGuiApplication.clipboard().setPixmap(pixmap)
            self._notify("已复制到剪贴板")
        elif action == "save":
            self._save_pixmap(pixmap)
        elif action == "pin":
            # Pass the position to the constructor so it is re-asserted in
            # showEvent (a plain move() before show() can be overridden on macOS).
            pin = PinWindow(pixmap, self._config, position=origin)
            pin.show()
            self._pins.append(pin)

    def _save_pixmap(self, pixmap: QPixmap) -> None:
        save_dir = self._config.ensure_save_dir()
        default = save_dir / f"Snip_{_dt.datetime.now():%Y%m%d_%H%M%S}.png"
        path, _ = QFileDialog.getSaveFileName(
            None, "保存截图", str(default), "PNG 图片 (*.png);;JPEG 图片 (*.jpg)"
        )
        if path:
            pixmap.save(path)
            self._config.save_dir = str(Path(path).parent)
            self._notify(f"已保存到 {path}")

    def _notify(self, message: str) -> None:
        if self._tray.supportsMessages():
            self._tray.showMessage("mysphinx-snip", message, _fallback_icon(), 2000)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("mysphinx-snip")
    app.setOrganizationName("mysphinx")
    # keep running with no visible window (tray-only app)
    app.setQuitOnLastWindowClosed(False)

    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.warning(None, "mysphinx-snip", "系统托盘不可用，程序可能无法正常运行。")

    snip = SnipApp(app)  # noqa: F841 - kept alive for the app lifetime
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
