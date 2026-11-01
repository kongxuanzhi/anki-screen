"""托盘交互：菜单交给系统原生托盘（``QSystemTrayIcon.setContextMenu``）。

早期为了"左键直接截图、右键菜单"自己 ``QMenu.exec()`` 弹菜单，跟状态项的点击
跟踪 / ``NSEventTrackingRunLoopMode`` 较劲，修出了图标残留高亮、快捷键失灵、
菜单项点不动一堆问题。现在改成系统原生菜单：AppKit 负责弹出、焦点、高亮、
Esc 和菜单项派发。macOS 上左键 / 右键都会弹菜单；Windows / Linux 上仍是
左键截图、右键菜单。
"""

from __future__ import annotations

import sys

import pytest
from PySide6.QtWidgets import QSystemTrayIcon

from ankisnip.app import AnkiSnipApp
from ankisnip.base.app import SnipApp
from ankisnip.base.config import Config
from ankisnip.settings import Settings


def test_plain_trigger_requests_capture():
    """非 macOS 的左键触发仍直接截图（macOS 不连这个信号，菜单接管一切）。"""
    assert SnipApp._tray_activation_is_capture(
        QSystemTrayIcon.Trigger) is True
    assert SnipApp._tray_activation_is_capture(
        QSystemTrayIcon.DoubleClick) is True
    assert SnipApp._tray_activation_is_capture(
        QSystemTrayIcon.Context) is False


class _TrayHarness(SnipApp):
    """只装配托盘，不装热键 / 不弹权限提示（离屏测试不需要）。"""

    def __init__(self) -> None:
        from PySide6.QtCore import QObject
        from PySide6.QtWidgets import QApplication

        QObject.__init__(self)
        self._config = Config()
        self._app = QApplication.instance()
        self._build_tray()


def test_tray_menu_is_the_native_context_menu():
    """菜单必须挂到托盘上，由 AppKit / 系统托盘原生弹出。"""
    obj = _TrayHarness()

    try:
        assert obj._tray.contextMenu() is obj._tray_menu
        titles = [a.text() for a in obj._tray_menu.actions()]
        assert titles[0].startswith("截图")
    finally:
        obj._tray.hide()


def test_capture_action_has_a_menu_shortcut():
    """「截图」菜单项带全局热键：原生菜单打开时它负责触发（Carbon 会被挡住）。"""
    obj = _TrayHarness()
    try:
        obj._set_capture_label(True)
        assert not obj._capture_action.shortcut().isEmpty()
    finally:
        obj._tray.hide()


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS 的 Ctrl/Meta 互换")
def test_qt_shortcut_text_swaps_ctrl_and_meta_on_mac():
    from PySide6.QtGui import QKeySequence

    from ankisnip.base.app import _qt_shortcut_text

    def native(spec: str) -> str:
        return QKeySequence(_qt_shortcut_text(spec)).toString(QKeySequence.NativeText)

    assert native("<cmd>+<shift>+a") == "⇧⌘A"
    assert native("<ctrl>+<cmd>+s") == "⌃⌘S"


class _AnkiTrayHarness(AnkiSnipApp):
    """只装配 AnkiSnip 的托盘（不装热键 / 不弹权限提示）。"""

    def __init__(self) -> None:
        from PySide6.QtCore import QObject
        from PySide6.QtWidgets import QApplication

        QObject.__init__(self)
        self._config = Settings()
        self._app = QApplication.instance()
        self._build_tray()


def test_anki_tray_menu_entries_are_inserted_before_quit():
    obj = _AnkiTrayHarness()
    try:
        assert obj._tray.contextMenu() is obj._tray_menu
        titles = [a.text() for a in obj._tray_menu.actions()]
        assert titles[0].startswith("截图")
        assert titles[1] == "快捷键设置…", "快捷键入口紧跟「截图」"
        # 打开目录 → 设置目录 → 归档（归档是最不常用的维护动作，放最后）
        assert titles[2] == "打开牌组目录"
        assert titles[3] == "设置牌组目录…"
        assert titles[4] == "归档离线牌组"
        for gone in ("打开保存目录", "设置保存目录…", "打开牌包目录",
                     "打开日志目录", "热键状态…", "归档离线牌包"):
            assert gone not in titles
        for expected in ("快捷键设置…", "打开牌组目录", "设置牌组目录…", "设置…",
                         "归档离线牌组", "检查 Anki 连接"):
            assert expected in titles
        assert titles.index("退出") == len(titles) - 1
    finally:
        obj._tray.hide()


def test_extra_menu_items_carry_configured_shortcuts():
    """「打开牌组目录」「归档」「设置…」和「截图」一样，把全局热键挂到菜单项上。"""
    obj = _AnkiTrayHarness()
    try:
        obj._config.reset()  # 会话内共享测试 ini，先清掉其它测试写入的值
        obj._refresh_extra_menu_shortcuts()
        assert obj._open_deck_action.shortcut().isEmpty()  # 默认未设置
        obj._config.hotkey_open_dir = "<cmd>+<shift>+d"
        obj._config.hotkey_archive = "<cmd>+<shift>+g"
        obj._config.hotkey_open_settings = "<cmd>+<shift>+p"
        obj._refresh_extra_menu_shortcuts()
        assert not obj._open_deck_action.shortcut().isEmpty()
        assert not obj._archive_action.shortcut().isEmpty()
        assert not obj._settings_action.shortcut().isEmpty()
    finally:
        obj._tray.hide()
