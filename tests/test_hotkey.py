"""全局热键：记法解析、原生键码映射、管理器后端选择。"""

from __future__ import annotations

import sys

import pytest

from ankisnip.hotkey import (
    Hotkey,
    HotkeyError,
    GlobalHotkeyManager,
    default_hotkey,
    format_hotkey,
)
from ankisnip.hotkey_capture import hotkey_from_qt_event

_IS_MAC = sys.platform == "darwin"
_IS_WIN = sys.platform == "win32"


# -- 解析 / 序列化 --------------------------------------------------------- #
def test_parse_pynput_spec():
    hk = Hotkey.parse("<cmd>+<shift>+a")
    assert hk.mods == frozenset({"cmd", "shift"})
    assert hk.key == "a"


def test_parse_legacy_default():
    hk = Hotkey.parse(default_hotkey())
    assert hk.has_modifier()
    assert hk.key == "a"


def test_roundtrip_spec():
    for spec in ("<cmd>+<shift>+a", "<ctrl>+<alt>+<f5>", "<shift>+<space>",
                 "<ctrl>+<shift>+<65>"):
        assert Hotkey.parse(spec).to_spec() == spec, spec


def test_vk_spec_is_pynput_compatible():
    # pynput 的 HotKey.parse 只认 "<数字>"，所以 vk 必须输出成这种形式
    assert Hotkey.parse("<ctrl>+<vk65>").to_spec() == "<ctrl>+<65>"


def test_parse_tolerates_human_notation():
    assert Hotkey.parse("Cmd+Shift+A").to_spec() == "<cmd>+<shift>+a"
    assert Hotkey.parse("ctrl shift f1").to_spec() == "<ctrl>+<shift>+<f1>"


def test_modifier_order_is_canonical():
    a = Hotkey.parse("<shift>+<ctrl>+a")
    b = Hotkey.parse("<ctrl>+<shift>+a")
    assert a.to_spec() == b.to_spec() == "<ctrl>+<shift>+a"


def test_parse_rejects_bad_input():
    for bad in ("", "   ", "<ctrl>+", "ctrl+"):
        with pytest.raises(HotkeyError):
            Hotkey.parse(bad)


def test_unknown_modifier():
    hk = Hotkey.parse("<ctrl>+<shift>+a")
    assert hk.mods == frozenset({"ctrl", "shift"})
    with pytest.raises(HotkeyError):
        Hotkey(mods=frozenset({"hyper"}), key="a")


def test_display_is_human_readable():
    text = Hotkey.parse("<cmd>+<shift>+a").display()
    assert "A" in text
    assert "<" not in text  # 不再展示 pynput 记法


def test_format_hotkey_falls_back_gracefully():
    assert format_hotkey("not a hotkey") == "not a hotkey"
    assert format_hotkey("") == "（未设置）"


# -- 原生键码 -------------------------------------------------------------- #
@pytest.mark.skipif(not (_IS_MAC or _IS_WIN), reason="需要原生平台的键码表")
def test_native_keycode_mac_or_win():
    hk = Hotkey.parse("<cmd>+<shift>+a")
    mods, vk = hk.native()
    assert mods != 0
    assert vk == (0x00 if _IS_MAC else 0x41)


def test_native_vk_escape_hatch():
    hk = Hotkey.parse("<ctrl>+<vk65>")
    mods, vk = hk.native()
    assert vk == 65


def test_native_rejects_unsupported_key():
    hk = Hotkey(mods=frozenset(), key="notakey")
    with pytest.raises(HotkeyError):
        hk.native()


# -- Qt 事件 → Hotkey ------------------------------------------------------ #
def test_hotkey_from_qt_event(qt_key_event):
    event = qt_key_event("A", ctrl=True, shift=True)
    hk = hotkey_from_qt_event(event)
    assert hk.mods == frozenset({"ctrl", "shift"})
    assert hk.key == "a"
    assert hk.to_spec() == "<ctrl>+<shift>+a"


def test_hotkey_from_qt_event_function_key(qt_key_event):
    hk = hotkey_from_qt_event(qt_key_event("F5", alt=True))
    assert hk.key == "f5"
    assert hk.to_spec() == "<alt>+<f5>"


def test_hotkey_from_qt_event_falls_back_to_vk(qt_key_event):
    event = qt_key_event("MediaTogglePlayPause", meta=True, native_vk=0xB0)
    hk = hotkey_from_qt_event(event)
    assert hk.key == "vk176"
    assert hk.mods == frozenset({"cmd"})


# -- 管理器 ---------------------------------------------------------------- #
def test_manager_reports_failure_for_invalid_spec():
    manager = GlobalHotkeyManager(lambda: None)
    status = manager.start("this is not a hotkey")
    assert not status.ok
    assert status.error


def test_manager_uses_first_available_backend(monkeypatch):
    calls = []

    class FakeBackend:
        name = "fake"

        def __init__(self, fail=False):
            self.fail = fail

        @classmethod
        def available(cls):
            return True

        def start(self, hotkey, callback):
            calls.append(hotkey.to_spec())
            if self.fail:
                from ankisnip.hotkey import HotkeyError

                raise HotkeyError("boom")
            callback()

        def stop(self):
            calls.append("stop")

    import ankisnip.hotkey as hotkey_mod

    monkeypatch.setattr(hotkey_mod, "_backend_candidates",
                        lambda: [FakeBackend(fail=True), FakeBackend()])
    manager = GlobalHotkeyManager(lambda: None)
    status = manager.start("<ctrl>+<shift>+a")
    assert status.ok
    assert status.backend == "fake"
    assert status.display
    manager.stop()
    assert "stop" in calls


def test_manager_all_backends_failing(monkeypatch):
    class Dead:
        name = "dead"

        @classmethod
        def available(cls):
            return False

        def start(self, hotkey, callback):
            raise AssertionError("should not be called")

        def stop(self):
            pass

    import ankisnip.hotkey as hotkey_mod

    monkeypatch.setattr(hotkey_mod, "_backend_candidates", lambda: [Dead()])
    manager = GlobalHotkeyManager(lambda: None)
    status = manager.start("<ctrl>+<shift>+a")
    assert not status.ok
    assert "dead" in status.tried


def test_manager_restart_swaps_hotkey(monkeypatch):
    seen = []

    class Recorder:
        name = "recorder"

        @classmethod
        def available(cls):
            return True

        def start(self, hotkey, callback):
            seen.append(hotkey.to_spec())

        def stop(self):
            seen.append(None)

    import ankisnip.hotkey as hotkey_mod

    monkeypatch.setattr(hotkey_mod, "_backend_candidates", lambda: [Recorder()])
    manager = GlobalHotkeyManager(lambda: None)
    manager.start("<ctrl>+<shift>+a")
    manager.restart("<ctrl>+<alt>+b")
    assert seen == ["<ctrl>+<shift>+a", None, "<ctrl>+<alt>+b"]


def test_hotkey_dialog_exposes_hotkey_recorder():
    """快捷键设置是独立弹窗，使用录制控件而不是手输文本框。"""
    from ankisnip.hotkey_dialog import HotkeyDialog
    from ankisnip.settings import Settings

    settings = Settings()
    settings.hotkey = "<cmd>+<shift>+a"
    dialog = HotkeyDialog(settings)
    assert dialog.hotkey_edit.spec() == "<cmd>+<shift>+a"
    assert "A" in dialog.hotkey_edit._display.text()
    assert "<" not in dialog.hotkey_edit._display.text()

    dialog.hotkey_edit.set_spec("<ctrl>+<alt>+<f5>")
    dialog.apply()
    assert settings.hotkey == "<ctrl>+<alt>+<f5>"


def test_hotkey_dialog_ignores_non_widget_parent():
    """AnkiSnipApp 是 QObject：误当 QDialog parent 会抛 TypeError（菜单点了没反应）。"""
    from PySide6.QtCore import QObject

    from ankisnip.hotkey_dialog import HotkeyDialog
    from ankisnip.settings import Settings

    dialog = HotkeyDialog(Settings(), "状态", QObject())
    assert dialog.parent() is None


def test_settings_dialog_only_exposes_image_field_choice():
    """笔记类型 / 字段映射不应出现在设置界面；只让用户选挖空图片字段。"""
    from ankisnip.settings import Settings
    from ankisnip.settings_dialog import SettingsDialog

    dialog = SettingsDialog(Settings())
    assert hasattr(dialog, "field_combo")
    for hidden in ("model_name", "image_field", "kong_field",
                   "question_field", "relation_field", "field_order"):
        assert hidden not in dialog.edits
    assert "anki_url" in dialog.edits
    assert not hasattr(dialog, "snapshot_label")
    for widget in ("hotkey_enabled", "launch_at_login"):
        assert hasattr(dialog, widget), widget
    for gone in ("delay_spin", "tool_combo", "color_combo", "font_spin"):
        assert not hasattr(dialog, gone), gone


def test_settings_dialog_autosaves_on_change():
    """设置窗口没有确定/取消：改动后自动写回。"""
    from PySide6.QtCore import QEventLoop, QTimer

    from ankisnip.settings import Settings
    from ankisnip.settings_dialog import SettingsDialog

    settings = Settings()
    settings.hotkey_enabled = True
    dialog = SettingsDialog(settings)
    dialog.hotkey_enabled.setChecked(False)

    loop = QEventLoop()
    QTimer.singleShot(400, loop.quit)
    loop.exec()

    assert settings.hotkey_enabled is False
