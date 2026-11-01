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

        def start(self, bindings):
            for hotkey, callback in bindings.values():
                calls.append(hotkey.to_spec())
                callback()
            if self.fail:
                from ankisnip.hotkey import HotkeyError

                raise HotkeyError("boom")

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

        def start(self, bindings):
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

        def start(self, bindings):
            for hotkey, _cb in bindings.values():
                seen.append(hotkey.to_spec())

        def stop(self):
            seen.append(None)

    import ankisnip.hotkey as hotkey_mod

    monkeypatch.setattr(hotkey_mod, "_backend_candidates", lambda: [Recorder()])
    manager = GlobalHotkeyManager(lambda: None)
    manager.start("<ctrl>+<shift>+a")
    manager.restart("<ctrl>+<alt>+b")
    assert seen == ["<ctrl>+<shift>+a", None, "<ctrl>+<alt>+b"]


def test_manager_registers_extra_bindings_together(monkeypatch):
    """附加热键和主热键整组交给同一个后端；每个绑定都有独立状态。"""
    received = []

    class Recorder:
        name = "recorder"

        @classmethod
        def available(cls):
            return True

        def start(self, bindings):
            received.append(list(bindings.items()))

        def stop(self):
            pass

    import ankisnip.hotkey as hotkey_mod

    monkeypatch.setattr(hotkey_mod, "_backend_candidates", lambda: [Recorder()])
    manager = GlobalHotkeyManager(lambda: None)
    manager.register("open_dir", "<cmd>+<shift>+d", lambda: None)
    manager.register("open_settings", "<cmd>+<shift>+p", lambda: None)
    status = manager.start("<cmd>+<shift>+a")
    assert status.ok
    # capture 第一，注册顺序稳定
    assert [name for name, _ in received[0]] == \
        ["capture", "open_dir", "open_settings"]
    assert manager.status_for("open_dir").ok
    assert manager.status_for("open_dir").spec == "<cmd>+<shift>+d"
    assert manager.status_for("open_settings").ok
    assert manager.status_for("nope") is None


def test_manager_extra_binding_with_empty_spec_is_skipped(monkeypatch):
    received = []

    class Recorder:
        name = "recorder"

        @classmethod
        def available(cls):
            return True

        def start(self, bindings):
            received.append(list(bindings.keys()))

        def stop(self):
            pass

    import ankisnip.hotkey as hotkey_mod

    monkeypatch.setattr(hotkey_mod, "_backend_candidates", lambda: [Recorder()])
    manager = GlobalHotkeyManager(lambda: None)
    manager.register("open_dir", "", lambda: None)
    manager.start("<ctrl>+<shift>+a")
    assert received[0] == ["capture"]
    assert manager.status_for("open_dir") is None


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


def test_hotkey_dialog_applies_extra_bindings():
    """快捷键设置同时保存四条热键：截图 / 打开牌组目录 / 归档 / 打开设置界面。"""
    from ankisnip.hotkey_dialog import HotkeyDialog
    from ankisnip.settings import Settings

    settings = Settings()
    settings.hotkey_open_dir = ""
    settings.hotkey_open_settings = ""
    settings.hotkey_archive = ""
    dialog = HotkeyDialog(settings)
    assert set(dialog.extra_edits) == \
        {"hotkey_open_dir", "hotkey_archive", "hotkey_open_settings"}

    dialog.hotkey_edit.set_spec("<cmd>+<shift>+a")
    dialog.extra_edits["hotkey_open_dir"].set_spec("<cmd>+<shift>+d")
    dialog.extra_edits["hotkey_archive"].set_spec("<cmd>+<shift>+g")
    dialog.extra_edits["hotkey_open_settings"].set_spec("")
    dialog.apply()
    assert settings.hotkey == "<cmd>+<shift>+a"
    assert settings.hotkey_open_dir == "<cmd>+<shift>+d"
    assert settings.hotkey_archive == "<cmd>+<shift>+g"
    assert settings.hotkey_open_settings == ""


def test_hotkey_dialog_rejects_duplicate_specs(monkeypatch):
    """同一个组合键不能同时分给两个动作。"""
    from PySide6.QtWidgets import QMessageBox

    from ankisnip.hotkey_dialog import HotkeyDialog
    from ankisnip.settings import Settings

    dialog = HotkeyDialog(Settings())
    dialog.hotkey_edit.set_spec("<cmd>+<shift>+a")
    dialog.extra_edits["hotkey_open_dir"].set_spec("<cmd>+<shift>+a")
    warned = []
    monkeypatch.setattr(QMessageBox, "warning",
                        lambda *args, **kwargs: warned.append(args))
    dialog._validate_and_accept()
    assert warned  # 弹了重复警告
    assert dialog.result() != 1  # 1 = Accepted，被拒后窗口还开着


def test_settings_expose_extra_hotkeys_with_empty_defaults():
    """附加全局热键默认空 = 不注册，避免默认抢占系统快捷键。"""
    from ankisnip.settings import Settings

    settings = Settings()
    settings.reset()  # 会话内共享测试 ini，先清掉其它测试写入的值
    assert settings.hotkey_open_dir == ""
    assert settings.hotkey_open_settings == ""
    settings.hotkey_open_dir = "<cmd>+<shift>+d"
    assert settings.hotkey_open_dir == "<cmd>+<shift>+d"


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
