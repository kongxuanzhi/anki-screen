"""``mac_focus``：交还前台的编排逻辑。

真正的 AppKit 调用只在 macOS bundle 里有效，CI 上跑不了（模块在非 macOS 一律
返回 False，不抛错）。这里测的是**编排本身**——多步退让什么时候该收手、
关闭钩子挂到哪个信号上——用注入的探针替代系统调用。
"""

from __future__ import annotations

import pytest

from ankisnip import mac_focus


@pytest.fixture()
def fake_mac(monkeypatch):
    """把模块伪装成"在 macOS 上跑"，并接管所有真实系统调用。"""
    monkeypatch.setattr(mac_focus, "_IS_MAC", True)
    calls: list = []

    monkeypatch.setattr(mac_focus, "is_app_active", lambda: True)
    monkeypatch.setattr(mac_focus, "deactivate",
                        lambda: calls.append("deactivate") or True)
    monkeypatch.setattr(mac_focus, "restore_policy",
                        lambda: calls.append("restore_policy") or True)
    monkeypatch.setattr(mac_focus, "activate_pid",
                        lambda pid: calls.append(f"activate:{pid}") or True)
    monkeypatch.setattr(mac_focus, "hide_app",
                        lambda: calls.append("hide") or True)
    monkeypatch.setattr(mac_focus, "menu_bar_owning_pid", lambda: None)
    yield calls


def _collect(states, monkeypatch, fake_mac, external_pid=None):
    """跑一遍退让序列，返回 ``(每步做了什么, 调度记录)``。"""
    done: list = []
    scheduled: list = []

    def resign_once(index: int, pid) -> None:
        done.append((index, pid))

    it = iter(states)

    def probe() -> bool:
        return next(it)

    monkeypatch.setattr(mac_focus, "resign_activation", mac_focus.resign_activation)
    mac_focus.resign_activation(
        external_pid=external_pid,
        call_later=lambda msec, fn: (scheduled.append(msec), fn()),
        is_active=probe,
        resign_once=resign_once,
    )
    return done, scheduled


# --- 1. 一路让不出去：四步依次加码 --------------------------------------- #
def test_escalates_through_all_steps(monkeypatch, fake_mac):
    mac_focus.set_external_target(4242)
    done, scheduled = _collect([True] * 5, monkeypatch, fake_mac)

    assert [index for index, _ in done] == [0, 1, 2, 3], "四步都要试到"
    assert scheduled == [120, 280, 500], "后续步按 RESIGN_STEPS 的间隔排程"
    # 交接目标要一直带下去
    assert done[-1][1] == 4242


# --- 2. 中途让出去了就立刻收手 ------------------------------------------- #
def test_stops_as_soon_as_it_worked(monkeypatch, fake_mac):
    mac_focus.set_external_target(99)
    done, _ = _collect([True, False], monkeypatch, fake_mac)
    assert [index for index, _ in done] == [0], "第 0 步之后已让出，不该继续抢焦点"


def test_noop_when_already_resigned(monkeypatch, fake_mac):
    mac_focus.set_external_target(99)
    done, _ = _collect([False], monkeypatch, fake_mac)
    assert done == [], "本来就不是前台，什么也别做"


# --- 3. 每一步都先问"还是不是前台" --------------------------------------- #
def test_probes_before_each_step(monkeypatch, fake_mac):
    seen = []

    monkeypatch.setattr(mac_focus, "is_app_active", lambda: True)
    states = iter([True, True, True, True])

    def probe() -> bool:
        value = next(states)
        seen.append(value)
        return value

    mac_focus.resign_activation(
        call_later=lambda msec, fn: fn(),
        is_active=probe,
        resign_once=lambda index, pid: seen.append(f"step{index}"),
    )
    assert seen == [True, "step0", True, "step1", True, "step2", True, "step3"]


# --- 4. 关闭钩子：菜单用 aboutToHide，对话框用 finished ------------------- #
def test_menu_hook_uses_about_to_hide(monkeypatch, fake_mac):
    from PySide6.QtWidgets import QMenu

    monkeypatch.setattr(mac_focus, "_IS_MAC", True)
    mac_focus._HOOKED_IDS.clear()
    menu = QMenu()

    fired: list = []
    monkeypatch.setattr(mac_focus, "resign_activation",
                        lambda *a, **k: fired.append(1))
    assert mac_focus.install_resign_on_close(menu) is True

    menu.aboutToHide.emit()
    _drain()
    assert fired, "菜单隐藏后应发起一次交还"


def test_dialog_hook_uses_finished(monkeypatch, fake_mac):
    from PySide6.QtWidgets import QDialog

    monkeypatch.setattr(mac_focus, "_IS_MAC", True)
    mac_focus._HOOKED_IDS.clear()
    dialog = QDialog()

    fired: list = []
    monkeypatch.setattr(mac_focus, "resign_activation",
                        lambda *a, **k: fired.append(1))
    assert mac_focus.install_resign_on_close(dialog) is True

    dialog.finished.emit(0)  # finished 带一个 int 参数，钩子必须吃得下
    _drain()
    assert fired


def test_hook_is_idempotent(monkeypatch, fake_mac):
    from PySide6.QtWidgets import QMenu

    mac_focus._HOOKED_IDS.clear()
    menu = QMenu()
    fired: list = []
    monkeypatch.setattr(mac_focus, "resign_activation",
                        lambda *a, **k: fired.append(1))

    assert mac_focus.install_resign_on_close(menu) is True
    assert mac_focus.install_resign_on_close(menu) is False, "重复挂载会被拒绝"

    menu.aboutToHide.emit()
    _drain()
    assert len(fired) == 1, "挂两次就交还两次，会把焦点抢回来"


# --- 5. AppleScript 兜底：只在 AppKit 全线失败后登场 --------------------- #
def test_applescript_fallback_after_appkit_failed(monkeypatch, fake_mac):
    """前三步 AppKit 都不见效 → 第 4 步改用 AppleScript。

    它会碰 System Events（可能弹自动化授权），所以必须是最后的手段。
    """
    seen: list = []
    mac_focus.set_external_target(4242)
    mac_focus.resign_activation(
        call_later=lambda msec, fn: fn(),
        is_active=lambda: True,
        applescript=lambda pid: seen.append(pid) or True,
    )
    assert seen == [4242], "AppKit 三步失败后才用 AppleScript，且带交接目标"
    assert "activate:4242" in fake_mac, "AppleScript 之前先试过 AppKit 交接"


def test_applescript_skipped_without_target(monkeypatch, fake_mac):
    seen: list = []
    mac_focus.set_external_target(None)
    mac_focus.resign_activation(
        call_later=lambda msec, fn: fn(),
        is_active=lambda: True,
        applescript=lambda pid: seen.append(pid) or True,
    )
    assert seen == [], "没有交接目标时不该调 AppleScript（激活个空气没意义）"
    assert "hide" in fake_mac


def test_applescript_frontmost_builds_pid_script(monkeypatch, fake_mac):
    argv_seen: list = []
    ok = mac_focus.applescript_frontmost(
        4242, runner=lambda argv: argv_seen.append(argv) or 0)
    assert ok is True
    script = argv_seen[0][2]
    assert argv_seen[0][0] == "/usr/bin/osascript"
    assert "unix id is 4242" in script, "pid 要写进脚本，别把焦点还给错人"
    assert "System Events" in script


# --- 6. 取消：我们自己要前台时，作废还在排队的退让 ----------------------- #
def test_cancel_voids_queued_steps(monkeypatch, fake_mac):
    fired: list = []
    queued: list = []
    mac_focus.resign_activation(
        call_later=lambda msec, fn: queued.append(fn),
        is_active=lambda: True,
        resign_once=lambda index, pid: fired.append(index),
    )
    assert fired == [0], "第 0 步立刻执行"
    assert queued, "后续步骤应排在队列里"

    mac_focus.cancel_pending_resign()
    for fn in list(queued):
        fn()
    assert fired == [0], "作废后排队步骤不得再动作（否则会把刚起来的截图界面踹下去）"


# --- 7. 激活策略的收尾与"是否仍在前台"无关 ------------------------------ #
def test_policy_restored_even_when_not_active(fake_mac):
    """deactivate 已经生效（isActive=False）时也要恢复激活策略。"""
    mac_focus.resign_activation(is_active=lambda: False)
    assert "restore_policy" in fake_mac


# --- 8. 交接目标：frontmost 是自己时退到「菜单栏归属应用」 ---------------- #
def test_record_frontmost_falls_back_to_menu_bar_owner(monkeypatch, fake_mac):
    import os

    mac_focus.set_external_target(None)
    monkeypatch.setattr(mac_focus, "frontmost_pid", lambda: os.getpid())
    monkeypatch.setattr(mac_focus, "menu_bar_owning_pid", lambda: 777)
    assert mac_focus.record_frontmost() == 777


def test_record_frontmost_keeps_target_when_no_candidate(monkeypatch, fake_mac):
    import os

    mac_focus.set_external_target(1234)
    monkeypatch.setattr(mac_focus, "frontmost_pid", lambda: os.getpid())
    monkeypatch.setattr(mac_focus, "menu_bar_owning_pid", lambda: None)
    assert mac_focus.record_frontmost() == 1234, "没有新目标时别把旧的丢了"


def test_resign_uses_menu_bar_owner_as_fallback(monkeypatch, fake_mac):
    """没记录到交接目标（点图标时已被顶成前台）时，交给菜单栏归属应用。"""
    mac_focus.set_external_target(None)
    monkeypatch.setattr(mac_focus, "menu_bar_owning_pid", lambda: 4242)

    done: list = []
    mac_focus.resign_activation(
        call_later=lambda msec, fn: fn(),
        is_active=lambda: True,
        resign_once=lambda index, pid: done.append((index, pid)),
    )
    assert done and done[0] == (0, 4242)


def _drain() -> None:
    """把 singleShot(0) 排队的回调跑掉。"""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    assert app is not None
    app.processEvents()
