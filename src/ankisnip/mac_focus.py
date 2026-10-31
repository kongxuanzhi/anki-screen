"""macOS「前台（激活态）」管理。

背景
----
截图覆盖层、设置 / 文件对话框等窗口会把本应用推成 **active**，收起来以后系统
并不一定会把前台还回去。留在前台的后果是：没有 key window 却占着焦点，全局
热键事件可能被挡在派发链上游之外。

托盘菜单已经改用原生 ``NSStatusItem.menu``（见 ``base/app.py::_build_tray``），
高亮、焦点、Esc 收尾都由 AppKit 负责，不再需要这里的补丁。

``[NSApp deactivate]`` 是常规解法，但它只说"我不要前台了"、不指定接任者，
在部分机型/时序下会被 AppKit 的收尾动作吃掉。所以这里做成**多步退让**：

1. 立刻 ``deactivate``；
2. 若仍是前台，恢复激活策略（Accessory）再 ``deactivate``；
3. 仍不行就把前台**显式交接**给事先记录的那个 App（``NSRunningApplication``）；
4. AppKit 三招都不见效时，改用 AppleScript 让系统改前台进程
   （见 :func:`applescript_frontmost`）。

交接对象的来源见 :func:`record_frontmost`：启动时 / 截图开始时记录当时的前台
App（那时用户肯定还没离开他原本的 App），等窗口收起后照原样还回去。
"""

from __future__ import annotations

import logging
import sys
from typing import Callable, Optional, Sequence

LOG = logging.getLogger(__name__)

_IS_MAC = sys.platform == "darwin"

#: 退让序列的时间点（毫秒，相对菜单关闭那一刻）。第 0 步是"立刻"。
#:
#: 前三步是 AppKit 原生调用；最后一步是 AppleScript 兜底——它会碰
#: ``System Events``，首次可能弹一次自动化授权，所以只在前三步都失败时用。
RESIGN_STEPS: Sequence[int] = (0, 120, 400, 900)

#: ``NSApplicationActivateIgnoringOtherApps``
_ACTIVATE_IGNORING = 0x0002

#: 上一次记录到的「别人的 App」pid —— 焦点要还给谁。
_LAST_EXTERNAL_PID: Optional[int] = None

#: 退让序列的世代号。每次 :func:`cancel_pending_resign` 自增，
#: 正在排队的后续步骤发现号对不上就自动作废。
_GENERATION: int = 0


def cancel_pending_resign() -> None:
    """作废还在排队的退让步骤。

    场景：用户在菜单里点了「截图」。``aboutToHide`` 排下的退让是延迟若干毫秒
    才执行的，那时截图界面已经把自己顶到前台了——延迟的 ``deactivate`` 会把
    刚起来的截图界面一脚踹下去（表现：截图界面点不动 / 快捷键失灵）。

    所以凡是"接下来我们自己要占前台"的入口（``start_capture`` 等），
    都要先调这个。
    """
    global _GENERATION
    _GENERATION += 1


# --------------------------------------------------------------------------- #
# Objective-C 运行时
# --------------------------------------------------------------------------- #
def _load_objc():
    """加载 ``libobjc``。

    macOS 上运行时路径有 ``libobjc.dylib`` 与 ``libobjc.A.dylib`` 两个写法，
    不同版本只有一个存在，所以都试一遍。
    """
    import ctypes

    last: Optional[Exception] = None
    for path in ("/usr/lib/libobjc.dylib", "/usr/lib/libobjc.A.dylib"):
        try:
            lib = ctypes.cdll.LoadLibrary(path)
        except OSError as exc:  # pragma: no cover - 平台相关
            last = exc
            continue
        lib.objc_getClass.restype = ctypes.c_void_p
        lib.objc_getClass.argtypes = [ctypes.c_char_p]
        lib.sel_registerName.restype = ctypes.c_void_p
        lib.sel_registerName.argtypes = [ctypes.c_char_p]
        return lib
    raise RuntimeError(f"加载 Objective-C 运行时失败：{last}")


def _send(obj: int, selector: str, restype, argtypes=(), args=()) -> object:
    """给 ``obj`` 发一条 ``objc_msgSend`` 消息。"""
    import ctypes

    lib = _load_objc()
    sel = lib.sel_registerName(selector.encode())
    if not sel:
        raise RuntimeError(f"找不到 selector：{selector}")

    send = lib.objc_msgSend
    send.restype = restype
    send.argtypes = [ctypes.c_void_p, ctypes.c_void_p, *argtypes]
    return send(ctypes.c_void_p(obj), ctypes.c_void_p(sel), *args)


def _ns_app_raw() -> int:
    """``NSApplication.sharedApplication`` 的地址。"""
    import ctypes

    lib = _load_objc()
    send = lib.objc_msgSend
    send.restype = ctypes.c_void_p
    send.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    ptr = send(ctypes.c_void_p(lib.objc_getClass(b"NSApplication")),
               ctypes.c_void_p(lib.sel_registerName(b"sharedApplication")))
    return int(ptr or 0)


ns_app = _ns_app_raw


# --------------------------------------------------------------------------- #
# 原子操作
# --------------------------------------------------------------------------- #
def is_app_active() -> bool:
    """本进程当前是不是前台应用。"""
    if not _IS_MAC:
        return False
    try:
        import ctypes

        return bool(_send(ns_app(), "isActive", restype=ctypes.c_bool))
    except Exception:  # pragma: no cover - 平台相关
        LOG.debug("读取 NSApp.isActive 失败", exc_info=True)
        return False


def deactivate() -> bool:
    """``[NSApp deactivate]``：纯粹退让，不指定接任者。"""
    if not _IS_MAC:
        return False
    try:
        _send(ns_app(), "deactivate", restype=None)
        return True
    except Exception:  # pragma: no cover - 平台相关
        LOG.warning("[NSApp deactivate] 调用失败", exc_info=True)
        return False


#: 启动时的激活策略。LSUIElement 应用应当是 Accessory(1)；
#: 弹 NSMenu 时 AppKit/Qt 有时会把它临时改成 Regular(0) 且不再改回去，
#: 那正是"菜单栏图标一直高亮"的直接原因。
_START_POLICY: Optional[int] = None


def activation_policy() -> Optional[int]:
    """``[NSApp activationPolicy]``（0=Regular 1=Accessory 2=Prohibited）。"""
    if not _IS_MAC:
        return None
    try:
        import ctypes

        return int(_send(ns_app(), "activationPolicy", restype=ctypes.c_int))
    except Exception:  # pragma: no cover - 平台相关
        LOG.debug("读取 activationPolicy 失败", exc_info=True)
        return None


def remember_policy() -> Optional[int]:
    """记下启动时的策略，作为之后恢复的目标。"""
    global _START_POLICY
    if _START_POLICY is None:
        _START_POLICY = activation_policy()
        LOG.debug("启动时激活策略 = %s", _START_POLICY)
    return _START_POLICY


def restore_policy() -> bool:
    """把激活策略恢复成启动时那样（通常是 Accessory）。

    针对"弹过菜单之后图标一直高亮"：退让之后再把策略掰回 Accessory，
    菜单高亮会随之消失（Dock 里冒出来的图标也会收回去）。
    """
    target = _START_POLICY if _START_POLICY is not None else remember_policy()
    if not _IS_MAC or target is None:
        return False
    current = activation_policy()
    if current == target:
        return False
    try:
        import ctypes

        ok = bool(_send(ns_app(), "setActivationPolicy:", restype=ctypes.c_bool,
                        argtypes=(ctypes.c_int,), args=(int(target),)))
        LOG.info("激活策略 %s -> %s（%s）", current, target,
                 "已恢复" if ok else "被拒绝")
        return ok
    except Exception:  # pragma: no cover - 平台相关
        LOG.warning("恢复激活策略失败", exc_info=True)
        return False


def hide_app() -> bool:
    """``[NSApp hide:]``：兜底手段。"""
    if not _IS_MAC:
        return False
    try:
        _send(ns_app(), "hide:", restype=None)
        return True
    except Exception:  # pragma: no cover - 平台相关
        LOG.warning("[NSApp hide:] 调用失败", exc_info=True)
        return False


def _workspace_app_pid(selector: str) -> Optional[int]:
    """``[NSWorkspace sharedWorkspace]`` 上某个应用的 pid。"""
    if not _IS_MAC:
        return None
    try:
        import ctypes

        lib = _load_objc()
        workspace = _send(lib.objc_getClass(b"NSWorkspace"), "sharedWorkspace",
                          restype=ctypes.c_void_p)
        app = _send(int(workspace or 0), selector, restype=ctypes.c_void_p)
        if not app:
            return None
        pid = _send(int(app), "processIdentifier", restype=ctypes.c_int)
        return int(pid) or None
    except Exception:  # pragma: no cover - 平台相关
        LOG.debug("读取 %s 失败", selector, exc_info=True)
        return None


def frontmost_pid() -> Optional[int]:
    """当前前台应用的 pid（取不到返回 ``None``）。"""
    return _workspace_app_pid("frontmostApplication")


def menu_bar_owning_pid() -> Optional[int]:
    """菜单栏当前归属应用的 pid（Accessory 应用激活时它也仍是上一个 App）。

    托盘菜单／状态项点击会把本应用顶成前台，但 LSUIElement 应用没有自己的
    菜单栏，系统菜单栏仍由点击之前那个普通 App 拥有。手里的交接目标失效时，
    用它来"还焦点"最合适。
    """
    import os

    pid = _workspace_app_pid("menuBarOwningApplication")
    return None if pid == os.getpid() else pid


def activate_self() -> bool:
    """把自己推到前台（自检用：模拟"菜单把应用顶上来"那一刻）。"""
    if not _IS_MAC:
        return False
    try:
        import ctypes

        _send(ns_app(), "activateIgnoringOtherApps:", restype=None,
              argtypes=(ctypes.c_bool,), args=(True,))
        return True
    except Exception:  # pragma: no cover - 平台相关
        LOG.warning("主动激活失败", exc_info=True)
        return False


#: ``NSScreenSaverWindowLevel``：高于菜单栏（24）和 Dock（20）。
_ABOVE_MENU_BAR_LEVEL = 1000


def raise_window_above_menu_bar(window) -> bool:
    """把 Qt 窗口的 ``NSWindow.level`` 抬到菜单栏之上（截图覆盖层用）。

    Qt 的 ``WindowStaysOnTopHint`` 只到普通浮动层，macOS 菜单栏仍盖在上面，
    覆盖层最上方一条没法选区。截图本身（``QScreen.grabWindow``）一直包含
    菜单栏；把窗口抬到菜单栏之上后，用户就能把菜单栏一起框进来。
    """
    if not _IS_MAC:
        return False
    try:
        import ctypes

        view = int(window.winId())
        if not view:
            return False
        target = _send(view, "window", restype=ctypes.c_void_p)
        if not target:
            return False
        _send(int(target), "setLevel:", restype=None,
              argtypes=(ctypes.c_long,), args=(_ABOVE_MENU_BAR_LEVEL,))
        return True
    except Exception:  # pragma: no cover - 平台相关
        LOG.debug("抬高覆盖层窗口层级失败", exc_info=True)
        return False


def activate_pid(pid: int) -> bool:
    """把前台交给 ``pid`` 对应的 App。

    比 ``deactivate`` 更强的一步：系统要求**明确指定接任者**，
    而不只是说"我不要了"。
    """
    if not _IS_MAC or not pid:
        return False
    try:
        import ctypes

        lib = _load_objc()
        target = _send(lib.objc_getClass(b"NSRunningApplication"),
                       "runningApplicationWithProcessIdentifier:",
                       restype=ctypes.c_void_p, argtypes=(ctypes.c_int,),
                       args=(int(pid),))
        if not target:
            LOG.warning("pid=%s 对应的运行中的应用不存在", pid)
            return False
        ok = _send(int(target), "activateWithOptions:", restype=ctypes.c_bool,
                   argtypes=(ctypes.c_uint64,), args=(_ACTIVATE_IGNORING,))
        LOG.info("焦点交接给 pid=%s -> %s", pid, "成功" if ok else "被拒绝")
        return bool(ok)
    except Exception:  # pragma: no cover - 平台相关
        LOG.warning("交接前台给 pid=%s 失败", pid, exc_info=True)
        return False


def applescript_frontmost(pid: Optional[int],
                          runner: Optional[Callable[[Sequence[str]], int]] = None
                          ) -> bool:
    """AppleScript 兜底：让系统把 ``pid`` 提到前台。

    为什么需要它
    ------------
    Qt 从 6.4.3/6.5.0 起改用 ``NSStatusItem.menu`` 托管托盘菜单（QTBUG-103515），
    那条路上的"菜单关闭后取消高亮 / 退回前台"由系统自动完成。但**我们没有走
    那条路**：为了"左键不弹菜单、右键才弹"，菜单是 ``QMenu.exec()`` 手动弹的，
    跟 ``NSStatusItem`` 没有关系，于是没人负责收尾——``deactivate`` 和
    ``activateWithOptions:`` 都可能被无视（实测三步打完 ``isActive`` 仍是 YES）。

    这一步绕开 AppKit，直接让 System Events 改前台进程，系统顺带把状态项的
    高亮和激活态一起收掉。

    代价
    ----
    首次调用会触发 macOS 的「自动化」授权（控制 System Events），所以只放在
    退让序列的**最后一步**：正常路径不会走到，也就不会弹授权。

    Args:
        pid: 要提到前台的进程；为空则直接返回 False（不做无意义的激活）。
        runner: 测试注入用，``f(argv) -> returncode``。
    """
    if not _IS_MAC or not pid:
        return False
    script = (
        'tell application "System Events"\n'
        f"    set frontmost of (first process whose unix id is {int(pid)}) to true\n"
        "end tell"
    )
    argv = ["/usr/bin/osascript", "-e", script]
    try:
        if runner is not None:
            code = int(runner(argv))
        else:
            import subprocess

            completed = subprocess.run(argv, capture_output=True, text=True,
                                       timeout=8)
            code = int(completed.returncode)
            if code != 0:
                LOG.warning("AppleScript 交还前台失败（code=%s）：%s", code,
                            (completed.stderr or "").strip())
    except Exception:  # pragma: no cover - 平台相关
        LOG.warning("AppleScript 交还前台异常（pid=%s）", pid, exc_info=True)
        return False
    LOG.info("AppleScript 交还前台给 pid=%s -> %s", pid, "成功" if code == 0 else "失败")
    return code == 0


# --------------------------------------------------------------------------- #
# 交接目标
# --------------------------------------------------------------------------- #
def record_frontmost() -> Optional[int]:
    """记下当前前台 App 的 pid，作为之后焦点交接的目标。

    必须在**我们还没抢焦点**时调用——``start_capture`` 的开头最合适，
    那时用户一定还停在他原本的 App 上。指向自己的情况会被跳过。
    """
    global _LAST_EXTERNAL_PID
    if not _IS_MAC:
        return None
    import os

    pid = frontmost_pid()
    if not pid or pid == os.getpid():
        # 点击托盘图标时 AppKit 可能已经把我们顶成前台了；这时记录到的
        # frontmost 是"自己"，没用。退一步问菜单栏归谁 —— 对 Accessory
        # 应用来说，那就是用户点击图标之前所在的 App。
        pid = menu_bar_owning_pid()
    if pid and pid != os.getpid():
        _LAST_EXTERNAL_PID = pid
        LOG.debug("记录前台应用 pid=%s（焦点稍后还给它）", pid)
    return _LAST_EXTERNAL_PID


def external_target() -> Optional[int]:
    """当前记录的交接目标。"""
    return _LAST_EXTERNAL_PID


def set_external_target(pid: Optional[int]) -> None:
    """直接指定交接目标（自检与测试用）。"""
    global _LAST_EXTERNAL_PID
    _LAST_EXTERNAL_PID = pid


# --------------------------------------------------------------------------- #
# 退让序列
# --------------------------------------------------------------------------- #
def _default_call_later() -> Callable[[int, Callable], None]:
    from PySide6.QtCore import QTimer

    def _later(msec: int, fn: Callable) -> None:
        QTimer.singleShot(max(0, int(msec)), fn)

    return _later


def resign_activation(external_pid: Optional[int] = None,
                      call_later: Optional[Callable[[int, Callable], None]] = None,
                      steps: Sequence[int] = RESIGN_STEPS,
                      is_active: Optional[Callable[[], bool]] = None,
                      resign_once: Optional[Callable[[int, Optional[int]], None]] = None,
                      applescript: Optional[Callable[[Optional[int]], bool]] = None
                      ) -> None:
    """把前台让出去（多步退让 + 每步自检）。

    Args:
        external_pid: 交接目标；为空则用 :func:`record_frontmost` 记下的那个。
        call_later: ``f(msec, fn)`` 调度器，默认 ``QTimer.singleShot``。
        steps: 各步发生的时间点（毫秒）。
        is_active / resign_once: 便于测试注入的钩子。

    每一步都先问一次"现在还是不是前台"，已经让出去了就立刻收手 ——
    免得用户主动切走之后我们又横插一脚把焦点抢回来。
    """
    if not (_IS_MAC or is_active or resign_once):
        return  # 非 macOS 且没注入钩子：无事可做

    probe = is_active or is_app_active
    later = call_later or _default_call_later()
    generation = _GENERATION

    # AnkiSnip 注入：恢复激活策略与"还是不是前台"无关，也必须做
    # （这一步自身幂等，不会抢焦点）。
    if resign_once is None:
        restore_policy()

    def resolve_target() -> Optional[int]:
        if external_pid is not None:
            return external_pid
        pid = external_target()
        if pid:
            return pid
        # 记录目标可能是在我们已被顶成前台之后才发生的（拿到的是自己，
        # 被跳过）。退到"菜单栏归属应用"，把焦点明确交还给它。
        return menu_bar_owning_pid()

    def attempt(index: int, pid: Optional[int]) -> None:
        if resign_once is not None:
            resign_once(index, pid)
            return
        if index == 0:
            LOG.info("仍处于前台：执行 [NSApp deactivate]")
            deactivate()
        elif index == 1:
            LOG.info("第 1 步仍未让出：恢复激活策略 + 再次 deactivate")
            restore_policy()
            deactivate()
        elif index == 2:
            LOG.info("第 2 步：前台仍未让出，把焦点显式交给 pid=%s", pid)
            if pid:
                activate_pid(pid)
            else:
                LOG.warning("没有可交接的目标 App，改用 [NSApp hide:]")
                hide_app()
        else:
            # AppKit 三步都无效时，换 AppleScript 兜底。
            LOG.info("第 %d 步：AppKit 都没让出，改用 AppleScript 交给 pid=%s",
                     index, pid)
            if pid:
                if applescript is not None:
                    applescript(pid)
                else:
                    applescript_frontmost(pid)
            else:
                LOG.warning("没有可交接的目标 App，改用 [NSApp hide:]")
                hide_app()

    def step(index: int = 0) -> None:
        if generation != _GENERATION:
            LOG.info("退让序列已作废（期间有别的动作要前台），第 %d 步取消", index)
            return
        if not probe():
            LOG.info("激活态已交还（第 %d 步之前就已让出）", index)
            return
        attempt(index, resolve_target())
        if index + 1 < len(steps):
            later(steps[index + 1] - steps[index], lambda: step(index + 1))
        else:
            from PySide6.QtCore import QTimer

            QTimer.singleShot(150, lambda: _report_final(index))

    def _report_final(index: int) -> None:
        if generation != _GENERATION:
            return  # 已作废，不必再报
        if probe():
            LOG.warning("交还前台失败：%d 步都试过，本机仍是前台应用", len(steps))
        else:
            LOG.info("交还前台成功（第 %d 步生效）", index)

    step(0)


# --------------------------------------------------------------------------- #
# 事件驱动：挂在"关闭"这件事本身上
# --------------------------------------------------------------------------- #
#: 已经挂过钩子的对象（按 id 记），避免重复连接导致让位跑好几遍。
_HOOKED_IDS: set = set()


def install_resign_on_close(target) -> bool:
    """给菜单 / 对话框挂上「**关闭时**交还前台」。

    与其在每个调用点猜"现在该让位了吗"，不如直接挂在事件上——菜单有
    ``aboutToHide``，对话框（含 ``QMessageBox`` / ``QFileDialog``）有
    ``finished``，两者都在收尾那一刻发出。

    关闭瞬间 AppKit 自己的收尾还没走完，所以钩子只负责把让位排到下一轮
    事件循环，真正的动作仍是上面的多步序列。

    Returns:
        是否挂上了（非 macOS、对象没有合适信号、或已经挂过 → False）。
    """
    if not _IS_MAC:
        return False
    if id(target) in _HOOKED_IDS:
        return False

    signal = None
    for name in ("aboutToHide", "finished"):
        candidate = getattr(target, name, None)
        if candidate is not None and hasattr(candidate, "connect"):
            signal = candidate
            break
    if signal is None:
        return False

    from PySide6.QtCore import QTimer

    def _on_close(*_args, **_kwargs) -> None:
        QTimer.singleShot(0, lambda: resign_activation())

    try:
        signal.connect(_on_close)
    except Exception:  # pragma: no cover - 平台相关
        LOG.debug("挂载关闭钩子失败", exc_info=True)
        return False
    _HOOKED_IDS.add(id(target))
    LOG.debug("已给 %s 挂上「关闭即交还前台」", type(target).__name__)
    return True
