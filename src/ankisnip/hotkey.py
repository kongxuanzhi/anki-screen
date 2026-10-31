"""全局热键：统一记法 → 平台原生注册 → pynput 兜底。

为什么不能直接只用 pynput
------------------------
macOS 上 pynput 的全局键盘监听建立在 ``CGEventTap`` 之上，必须授予
「辅助功能 / 输入监控」权限，否则监听器**静默地什么都不收**，除了终端里一行
"process is not trusted" 之外没有任何反馈 —— 这正是"托盘点击能截图、按快捷键
没反应"的根因。

Carbon 的 ``RegisterEventHotKey`` 是系统级热键注册，**不需要辅助功能权限**，
Windows 的 ``RegisterHotKey`` 同理（比全局键盘钩子更稳，也不受其它钩子影响）。
因此这里把原生实现作为首选后端，pynput 只作为兜底。

对外接口
--------
* :class:`Hotkey` —— 与平台无关的热键描述，可序列化为 pynput 记法字符串
  （兼容历史配置），也可转成本平台的原生 ``(modifiers, virtual key)``。
* :class:`GlobalHotkeyManager` —— 注册 / 注销 / 热重载，并给出可读状态，
  便于在 UI 上直接告诉用户"用的是哪个后端、为什么失败"。

新增模块，不改动基座已有逻辑；基座 ``base/app.py`` 只是把 ``_install_hotkey``
的实现换成调用本模块（见 VENDORED.md / docs/01-变更说明.md）。
"""

from __future__ import annotations

import ctypes
import logging
import re
import sys
import threading
from dataclasses import dataclass, field
from typing import Callable, Dict, FrozenSet, List, Optional, Tuple

LOG = logging.getLogger("ankisnip.hotkey")

_IS_MAC = sys.platform == "darwin"
_IS_WIN = sys.platform == "win32"

# --------------------------------------------------------------------------- #
# 记法与常量
# --------------------------------------------------------------------------- #

#: 修饰键规范名。顺序固定，保证同一热键总是序列化 / 展示成同一字符串。
#: 展示顺序沿用平台惯例（mac: ⌃⌥⇧⌘；PC: Ctrl+Alt+Shift+Win）。
MOD_ORDER: Tuple[str, ...] = ("ctrl", "alt", "shift", "cmd")

#: 序列化顺序：为了和历史配置 ``<cmd>+<shift>+a`` 保持一致，cmd 放在 shift 前。
_SPEC_ORDER: Tuple[str, ...] = ("ctrl", "alt", "cmd", "shift")

_MOD_ALIASES: Dict[str, str] = {
    "ctrl": "ctrl", "control": "ctrl", "ctrl_l": "ctrl", "ctrl_r": "ctrl",
    "alt": "alt", "option": "alt", "opt": "alt", "alt_l": "alt", "alt_r": "alt",
    "alt_gr": "alt", "altgr": "alt",
    "shift": "shift", "shift_l": "shift", "shift_r": "shift",
    "cmd": "cmd", "command": "cmd", "super": "cmd", "supr": "cmd",
    "win": "cmd", "meta": "cmd", "cmd_l": "cmd", "cmd_r": "cmd",
}

#: pynput 记法中修饰键的写法
_PYNPUT_MODS: Dict[str, str] = {
    "ctrl": "<ctrl>", "alt": "<alt>", "shift": "<shift>", "cmd": "<cmd>",
}

#: macOS / Windows 上修饰键的展示名
_DISPLAY_MODS_MAC: Dict[str, str] = {
    "cmd": "⌘", "alt": "⌥", "ctrl": "⌃", "shift": "⇧",
}
_DISPLAY_MODS_PC: Dict[str, str] = {
    "cmd": "Win", "alt": "Alt", "ctrl": "Ctrl", "shift": "Shift",
}

#: 主键别名 → 规范 token
_KEY_ALIASES: Dict[str, str] = {
    "esc": "esc", "escape": "esc",
    "return": "return", "enter": "return", "ret": "return",
    "space": "space", "tab": "tab",
    "backspace": "backspace", "delete": "delete", "del": "delete",
    "up": "up", "down": "down", "left": "left", "right": "right",
    "home": "home", "end": "end", "page_up": "pageup", "pageup": "pageup",
    "page_down": "pagedown", "pagedown": "pagedown",
    "grave": "grave", "backtick": "grave", "tilde": "grave",
    "minus": "minus", "equal": "equal", "equals": "equal",
    "bracket_left": "lbracket", "lbracket": "lbracket",
    "bracket_right": "rbracket", "rbracket": "rbracket",
    "backslash": "backslash", "semicolon": "semicolon",
    "quote": "quote", "apostrophe": "quote",
    "comma": "comma", "period": "period", "dot": "period", "slash": "slash",
    "caps_lock": "capslock", "capslock": "capslock",
    "insert": "insert", "print_screen": "printscreen", "printscreen": "printscreen",
    "scroll_lock": "scrolllock", "pause": "pause",
    "num_lock": "numlock", "menu": "menu",
    **{f"f{i}": f"f{i}" for i in range(1, 25)},
    **{f"media_play_pause": "play", "play": "play"},
}


def _mac_table() -> Dict[str, int]:
    """macOS 虚拟键码（``kVK_*``，来自 ``HIToolbox/Events.h``）。"""
    letters = {
        "a": 0x00, "b": 0x0B, "c": 0x08, "d": 0x02, "e": 0x0E, "f": 0x03,
        "g": 0x05, "h": 0x04, "i": 0x22, "j": 0x26, "k": 0x28, "l": 0x25,
        "m": 0x2E, "n": 0x2D, "o": 0x1F, "p": 0x23, "q": 0x0C, "r": 0x0F,
        "s": 0x01, "t": 0x11, "u": 0x20, "v": 0x09, "w": 0x0D, "x": 0x07,
        "y": 0x10, "z": 0x06,
    }
    digits = {str(i): v for i, v in enumerate(
        [0x1D, 0x12, 0x13, 0x14, 0x15, 0x17, 0x16, 0x1A, 0x1C, 0x19])}
    fkeys = {f"f{i}": v for i, v in zip(
        range(1, 13),
        [0x7A, 0x78, 0x63, 0x76, 0x60, 0x61, 0x62, 0x64, 0x65, 0x6D, 0x67, 0x6F])}
    named = {
        "space": 0x31, "tab": 0x30, "return": 0x24, "esc": 0x35,
        "backspace": 0x33, "delete": 0x75,
        "up": 0x7E, "down": 0x7D, "left": 0x7B, "right": 0x7C,
        "home": 0x73, "end": 0x77, "pageup": 0x74, "pagedown": 0x79,
        "grave": 0x32, "minus": 0x1B, "equal": 0x18,
        "lbracket": 0x21, "rbracket": 0x1E, "backslash": 0x2A,
        "semicolon": 0x29, "quote": 0x27, "comma": 0x2B,
        "period": 0x2F, "slash": 0x2C, "capslock": 0x39,
        "insert": 0x72, "printscreen": 0x6B, "scrolllock": 0x6A,
        "pause": 0x71, "numlock": 0x47, "menu": 0x6E, "play": 0x22,
    }
    # 数字键盘
    for i in range(10):
        named[f"num{i}"] = [0x52, 0x53, 0x54, 0x55, 0x56, 0x57, 0x58, 0x59, 0x5C, 0x5D][i]
    return {**letters, **digits, **fkeys, **named}


def _win_table() -> Dict[str, int]:
    """Windows 虚拟键码（``VK_*``）。"""
    table = {chr(c): c for c in range(ord("A"), ord("Z") + 1)}
    table.update({str(i): ord(str(i)) for i in range(10)})
    table.update({f"f{i}": 0x6F + i for i in range(1, 25)})
    table.update({
        "space": 0x20, "tab": 0x09, "return": 0x0D, "esc": 0x1B,
        "backspace": 0x08, "delete": 0x2E,
        "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
        "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
        "grave": 0xC0, "minus": 0xBD, "equal": 0xBB,
        "lbracket": 0xDB, "rbracket": 0xDD, "backslash": 0xDC,
        "semicolon": 0xBA, "quote": 0xDE, "comma": 0xBC,
        "period": 0xBE, "slash": 0xBF, "capslock": 0x14,
        "insert": 0x2D, "printscreen": 0x2C, "scrolllock": 0x91,
        "pause": 0x13, "numlock": 0x90, "menu": 0x5D, "play": 0xB3,
    })
    for i in range(10):
        table[f"num{i}"] = 0x60 + i
    return table


MAC_KEYS = _mac_table()
WIN_KEYS = _win_table()

#: Carbon 修饰键掩码（``Events.h``）
_MAC_MODS: Dict[str, int] = {
    "cmd": 0x0100, "shift": 0x0200, "alt": 0x0800, "ctrl": 0x1000,
}
#: Win32 ``RegisterHotKey`` 的 ``fsModifiers``
_WIN_MODS: Dict[str, int] = {
    "alt": 0x0001, "ctrl": 0x0002, "shift": 0x0004, "cmd": 0x0008,
}
_WIN_MOD_NOREPEAT = 0x4000

#: 主键 token → 展示名（无表项时直接用大写 token）
_DISPLAY_KEYS: Dict[str, str] = {
    "esc": "Esc", "return": "Return", "space": "Space", "tab": "Tab",
    "backspace": "Backspace", "delete": "Del",
    "up": "↑", "down": "↓", "left": "←", "right": "→",
    "home": "Home", "end": "End", "pageup": "PgUp", "pagedown": "PgDn",
    "grave": "`", "minus": "-", "equal": "=", "lbracket": "[",
    "rbracket": "]", "backslash": "\\", "semicolon": ";", "quote": "'",
    "comma": ",", "period": ".", "slash": "/", "capslock": "CapsLock",
    "insert": "Ins", "printscreen": "PrtSc", "scrolllock": "ScrollLock",
    "pause": "Pause", "numlock": "NumLock", "menu": "Menu", "play": "▶",
}


class HotkeyError(RuntimeError):
    """热键解析或注册失败。"""


# --------------------------------------------------------------------------- #
# 数据模型
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Hotkey:
    """与平台无关的热键描述。

    ``key`` 为规范 token：单字符（``a``~``z``、``0``~``9``）、功能键
    （``f1``/``space``/``esc``…），或 ``vk<n>``（原始虚拟键码逃生舱，用于本模块
    表里没有的键）。
    """

    mods: FrozenSet[str] = frozenset()
    key: str = "a"

    # -- 构造 / 解析 ------------------------------------------------------- #
    def __post_init__(self) -> None:
        bad = set(self.mods) - set(MOD_ORDER)
        if bad:
            raise HotkeyError(f"未知修饰键：{', '.join(sorted(bad))}")
        if not self.key:
            raise HotkeyError("缺少主键")

    @classmethod
    def parse(cls, text: str) -> "Hotkey":
        """解析 pynput 记法，例如 ``<cmd>+<shift>+a`` / ``<ctrl>+<f5>``。

        同时容忍手写形式（``Cmd+Shift+A``、``ctrl shift f1``）。
        """
        if text is None:
            raise HotkeyError("热键为空")
        raw = str(text).strip()
        if not raw:
            raise HotkeyError("热键为空")
        # 同时接受 "<ctrl>+<shift>+a" 与 "Ctrl Shift A" 这类手写形式
        parts = [p for p in re.split(r"\s*\+\s*|\s+", raw) if p]
        if not parts:
            raise HotkeyError(f"无法解析热键：{text!r}")

        mods: set[str] = set()
        key: Optional[str] = None
        for part in parts:
            token = part
            if len(token) > 2 and token[0] == "<" and token[-1] == ">":
                token = token[1:-1]
            low = token.lower()
            if low in _MOD_ALIASES:
                mods.add(_MOD_ALIASES[low])
                continue
            if key is not None:
                raise HotkeyError(f"热键里有多个主键：{text!r}")
            key = _normalize_key(token)
        if key is None:
            raise HotkeyError(f"热键缺少主键：{text!r}")
        return cls(mods=frozenset(mods), key=key)

    # -- 序列化 ------------------------------------------------------------ #
    def to_spec(self) -> str:
        """pynput 记法（持久化用，与历史配置兼容）。"""
        mods = [m for m in _SPEC_ORDER if m in self.mods]
        return "+".join([_PYNPUT_MODS[m] for m in mods] + [self._pynput_key()])

    def _pynput_key(self) -> str:
        if self.key.startswith("vk"):
            return f"<{self.key[2:]}>"
        if len(self.key) == 1:
            return self.key.lower()
        return f"<{self.key}>"

    def display(self) -> str:
        """人类可读展示（mac 用符号，PC 用文字）。"""
        names = _DISPLAY_MODS_MAC if _IS_MAC else _DISPLAY_MODS_PC
        mods = [names[m] for m in MOD_ORDER if m in self.mods]
        key = self.key
        if key.startswith("vk"):
            key = f"键码 {key[2:]}"
        elif len(key) == 1:
            key = key.upper()
        else:
            key = _DISPLAY_KEYS.get(key, key.upper())
        sep = "" if _IS_MAC else "+"
        return sep.join(mods + [key]) if mods else key

    # -- 平台原生参数 ------------------------------------------------------ #
    def native(self) -> Tuple[int, int]:
        """返回本平台的 ``(modifiers, virtual_key)``。

        :raises HotkeyError: 该主键在本平台没有对应键码且不是 ``vk<n>``。
        """
        if _IS_MAC:
            table, mod_table = MAC_KEYS, _MAC_MODS
        elif _IS_WIN:
            table, mod_table = WIN_KEYS, _WIN_MODS
        else:
            raise HotkeyError("当前平台没有原生热键后端")
        vk = self._virtual_key(table)
        mods = 0
        for m in self.mods:
            mods |= mod_table.get(m, 0)
        return mods, vk

    def _virtual_key(self, table: Dict[str, int]) -> int:
        if self.key.startswith("vk"):
            try:
                return int(self.key[2:])
            except ValueError:
                raise HotkeyError(f"非法键码：{self.key}")
        vk = table.get(self.key.lower())
        if vk is None:
            raise HotkeyError(f"当前平台不支持按键 {self.key!r}")
        return vk

    def has_modifier(self) -> bool:
        return bool(self.mods)


def _normalize_key(token: str) -> str:
    """把用户/Qt 给出的主键写法收敛成规范 token。"""
    low = token.lower()
    if low.startswith("vk"):
        body = low[2:].lstrip("_:")
        if not body.isdigit():
            raise HotkeyError(f"非法键码：{token!r}")
        return f"vk{int(body)}"
    if low in _KEY_ALIASES:
        return _KEY_ALIASES[low]
    if len(token) == 1:
        if token.isalnum() and token.isascii():
            return token.lower()
        raise HotkeyError(f"不支持的按键：{token!r}")
    if low.isdigit():  # pynput 的 "<65>" 形式：原始虚拟键码
        return f"vk{int(low)}"
    raise HotkeyError(f"不支持的按键：{token!r}")


def format_hotkey(spec: str) -> str:
    """把持久化字符串转成展示文本；解析失败时原样返回。"""
    try:
        return Hotkey.parse(spec).display()
    except HotkeyError:
        return spec or "（未设置）"


# --------------------------------------------------------------------------- #
# 后端
# --------------------------------------------------------------------------- #

class _Backend:
    """热键后端接口。"""

    name = "unknown"

    @classmethod
    def available(cls) -> bool:
        return False

    def start(self, hotkey: Hotkey, callback: Callable[[], None]) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError


class _MacCarbonBackend(_Backend):
    """macOS：Carbon ``RegisterEventHotKey``（不需要辅助功能权限）。"""

    name = "macOS Carbon RegisterEventHotKey"

    _SIG = 0x414B534E  # 'AKSN'

    def __init__(self) -> None:
        self._carbon = None
        self._hotkey_ref = None
        # 一个卡通告可能装到多个事件目标上（见 ``start``），全部要能撤销。
        self._handler_refs: list = []
        self._callback_type = None
        self._callback = None
        self._user_callback: Optional[Callable[[], None]] = None
        #: 同一个 EventRef 去重：见 :meth:`_on_hotkey_event`。
        self._seen: tuple = ()

    @classmethod
    def available(cls) -> bool:
        return _IS_MAC and _load_carbon() is not None

    # -- ctypes 布局 ------------------------------------------------------- #
    @staticmethod
    def _types():
        class EventHotKeyID(ctypes.Structure):
            _fields_ = [("signature", ctypes.c_uint32), ("id", ctypes.c_uint32)]

        class EventTypeSpec(ctypes.Structure):
            _fields_ = [("eventClass", ctypes.c_uint32), ("eventKind", ctypes.c_uint32)]

        return EventHotKeyID, EventTypeSpec

    def start(self, hotkey: Hotkey, callback: Callable[[], None]) -> None:
        carbon = _load_carbon()
        if carbon is None:
            raise HotkeyError("无法加载 Carbon 框架")
        self._carbon = carbon
        self._user_callback = callback

        EventHotKeyID, EventTypeSpec = self._types()

        carbon.GetApplicationEventTarget.restype = ctypes.c_void_p
        carbon.GetEventDispatcherTarget.restype = ctypes.c_void_p
        carbon.InstallEventHandler.restype = ctypes.c_int32
        carbon.InstallEventHandler.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32,
            ctypes.POINTER(EventTypeSpec), ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        carbon.RegisterEventHotKey.restype = ctypes.c_int32
        carbon.RegisterEventHotKey.argtypes = [
            ctypes.c_uint32, ctypes.c_uint32, EventHotKeyID, ctypes.c_void_p,
            ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p),
        ]
        carbon.UnregisterEventHotKey.restype = ctypes.c_int32
        carbon.UnregisterEventHotKey.argtypes = [ctypes.c_void_p]

        mods, vk = hotkey.native()

        # 回调必须由实例持有，否则被 GC 后 Carbon 会跳进已释放的地址。
        self._callback_type = ctypes.CFUNCTYPE(
            ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
        self._callback = self._callback_type(self._on_hotkey_event)

        event_type = EventTypeSpec(0x6B657962, 5)  # 'keyb' / kEventHotKeyPressed
        # AnkiSnip 注入：**两个目标都装**，而不是"装成一个就不再试第二个"。
        #
        # 热键事件在应用内部的派发链是"当前焦点 → 应用"：托盘菜单弹出后，Qt 会把
        # 焦点留在本应用上却没有 key window，此时事件常常走不到 application target
        # 就被吞掉，表现就是"右键菜单关掉之后快捷键失灵"。dispatcher target 处在
        # 派发链更上游，能兜住这种情况。
        installed = 0
        for target_name in ("GetApplicationEventTarget", "GetEventDispatcherTarget"):
            try:
                target = getattr(carbon, target_name)
            except AttributeError:  # pragma: no cover - 老系统没有该符号
                continue
            handler_ref = ctypes.c_void_p()
            status = carbon.InstallEventHandler(
                target(), self._callback, 1, ctypes.byref(event_type),
                None, ctypes.byref(handler_ref))
            if status == 0:
                self._handler_refs.append(handler_ref)
                installed += 1
                LOG.debug("Carbon 事件处理器已安装（%s）", target_name)
        if not installed:
            raise HotkeyError("InstallEventHandler 失败（两个事件目标都装不上）")

        hotkey_id = EventHotKeyID(self._SIG, 1)
        ref = ctypes.c_void_p()
        status = carbon.RegisterEventHotKey(
            vk, mods, hotkey_id, carbon.GetApplicationEventTarget(), 0,
            ctypes.byref(ref))
        if status != 0:
            self.stop()
            if status == -9878:  # eventHotKeyExistsErr
                raise HotkeyError("该快捷键已被系统或其它程序占用")
            raise HotkeyError(f"RegisterEventHotKey 失败（OSStatus {status}）")
        self._hotkey_ref = ref

    def _on_hotkey_event(self, _handler, event, _userdata) -> int:
        # 同一个 EventRef 会先经过 dispatcher 再经过 application 两个目标，
        # 不按指针去重的话一次按键会触发两次截图。
        key = int(event) if event else 0
        if key and key in self._seen:
            return 0
        # 只留住最近一个：指针会被系统复用，留长了反而会漏掉真实按键。
        self._seen = (key,)
        try:
            if self._user_callback is not None:
                self._user_callback()
        except Exception:  # 绝不让异常穿过 C 回调边界
            LOG.exception("热键回调异常")
        return 0

    def stop(self) -> None:
        carbon = self._carbon
        if carbon is not None and self._hotkey_ref is not None:
            try:
                carbon.UnregisterEventHotKey(self._hotkey_ref)
            except Exception:
                LOG.debug("UnregisterEventHotKey 失败", exc_info=True)
        if carbon is not None and self._handler_refs:
            for handler_ref in self._handler_refs:
                try:
                    carbon.RemoveEventHandler(handler_ref)
                except Exception:
                    LOG.debug("RemoveEventHandler 失败（符号可能不可达）", exc_info=True)
        self._hotkey_ref = None
        self._handler_refs = []
        self._seen = ()
        self._user_callback = None
        self._callback = None
        self._callback_type = None
        self._carbon = None


class _Win32Backend(_Backend):
    """Windows：``RegisterHotKey`` + Qt 原生事件过滤（不需要全局钩子）。"""

    name = "Win32 RegisterHotKey"

    _WM_HOTKEY = 0x0312
    _ID = 0x414B

    def __init__(self) -> None:
        self._user32 = None
        self._hwnd = 0
        self._sink = None
        self._filter = None
        self._user_callback: Optional[Callable[[], None]] = None
        self._installed = False

    @classmethod
    def available(cls) -> bool:
        return _IS_WIN

    def start(self, hotkey: Hotkey, callback: Callable[[], None]) -> None:
        from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication
        from PySide6.QtWidgets import QWidget

        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._user_callback = callback

        self._sink = QWidget()
        self._sink.setWindowTitle("AnkiSnip hotkey sink")
        self._hwnd = int(self._sink.winId())

        mods, vk = hotkey.native()
        ok = self._user32.RegisterHotKey(
            ctypes.c_void_p(self._hwnd), self._ID,
            mods | _WIN_MOD_NOREPEAT, vk)
        if not ok:
            self.stop()
            err = ctypes.get_last_error()
            if err == 1409:  # ERROR_HOTKEY_ALREADY_REGISTERED
                raise HotkeyError("该快捷键已被其它程序占用")
            raise HotkeyError(f"RegisterHotKey 失败（GetLastError {err}）")

        filter_obj = _WinMessageFilter(self._handle_message)
        self._filter = filter_obj
        QCoreApplication.instance().installNativeEventFilter(filter_obj)
        self._installed = True

    def _handle_message(self, message: int) -> bool:
        class MSG(ctypes.Structure):
            _fields_ = [("hwnd", ctypes.c_void_p), ("message", ctypes.c_uint32),
                        ("wParam", ctypes.c_size_t), ("lParam", ctypes.c_size_t),
                        ("time", ctypes.c_uint32),
                        ("pt_x", ctypes.c_long), ("pt_y", ctypes.c_long)]

        try:
            msg = ctypes.cast(message, ctypes.POINTER(MSG)).contents
        except Exception:
            return False
        if msg.message == self._WM_HOTKEY and int(msg.wParam) == self._ID:
            if self._user_callback is not None:
                try:
                    self._user_callback()
                except Exception:
                    LOG.exception("热键回调异常")
            return True
        return False

    def stop(self) -> None:
        from PySide6.QtCore import QCoreApplication

        if self._installed and QCoreApplication.instance() is not None:
            try:
                QCoreApplication.instance().removeNativeEventFilter(self._filter)
            except Exception:
                LOG.debug("removeNativeEventFilter 失败", exc_info=True)
        self._installed = False
        if self._user32 is not None and self._hwnd:
            try:
                self._user32.UnregisterHotKey(ctypes.c_void_p(self._hwnd), self._ID)
            except Exception:
                LOG.debug("UnregisterHotKey 失败", exc_info=True)
        self._hwnd = 0
        self._user32 = None
        self._sink = None
        self._user_callback = None


class _WinMessageFilter:
    """Windows 消息过滤器包装（惰性继承 Qt 类型，避免非 Win 平台导入问题）。"""

    def __init__(self, handler: Callable[[int], bool]) -> None:
        from PySide6.QtCore import QAbstractNativeEventFilter

        class _Filter(QAbstractNativeEventFilter):
            def nativeEventFilter(self, event_type, message):  # noqa: N802
                try:
                    consumed = handler(int(message))
                except Exception:
                    LOG.exception("nativeEventFilter 异常")
                    return False, 0
                return bool(consumed), 0

        self._impl = _Filter()

    def __getattr__(self, item):
        return getattr(self._impl, item)


class _PynputBackend(_Backend):
    """兜底：``pynput.keyboard.GlobalHotKeys``（macOS 需要辅助功能权限）。"""

    name = "pynput GlobalHotKeys"

    def __init__(self) -> None:
        self._listener = None

    @classmethod
    def available(cls) -> bool:
        try:
            import pynput.keyboard  # noqa: F401
        except Exception:
            return False
        return True

    def start(self, hotkey: Hotkey, callback: Callable[[], None]) -> None:
        from pynput import keyboard

        spec = hotkey.to_spec()
        self._listener = keyboard.GlobalHotKeys({spec: callback})
        self._listener.start()
        # GlobalHotKeys.start() 不抛异常，但线程可能已死；探一下。
        if not self._listener.is_alive():
            raise HotkeyError("pynput 监听器线程启动后立即退出（可能未授予辅助功能权限）")
        if getattr(self._listener, "_running", True) is False:
            raise HotkeyError("pynput 监听器未运行（可能未授予辅助功能权限）")

    def stop(self) -> None:
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                LOG.debug("停止 pynput 监听器失败", exc_info=True)
        self._listener = None


def _load_carbon():
    if not _IS_MAC:
        return None
    try:
        return ctypes.cdll.LoadLibrary("/System/Library/Frameworks/Carbon.framework/Carbon")
    except OSError:
        LOG.debug("Carbon 框架不可用", exc_info=True)
        return None


def _backend_candidates() -> List[_Backend]:
    """按平台给出后端优先级。"""
    if _IS_MAC:
        return [_MacCarbonBackend(), _PynputBackend()]
    if _IS_WIN:
        return [_Win32Backend(), _PynputBackend()]
    return [_PynputBackend()]


# --------------------------------------------------------------------------- #
# 管理器
# --------------------------------------------------------------------------- #

@dataclass
class HotkeyStatus:
    """一次注册尝试的结果，直接给 UI 展示。"""

    ok: bool = False
    spec: str = ""
    display: str = ""
    backend: str = ""
    error: str = ""
    tried: List[str] = field(default_factory=list)

    def describe(self) -> str:
        if self.ok:
            base = f"已注册 {self.display}　（后端：{self.backend}）"
            return base + (f"\n\n注意：{self.error}" if self.error else "")
        parts = ["全局热键注册失败。"]
        if self.spec:
            parts.append(f"热键：{self.display or self.spec}")
        if self.tried:
            parts.append("已尝试：" + "、".join(self.tried))
        if self.error:
            parts.append("原因：" + self.error)
        return "\n".join(parts)


class GlobalHotkeyManager:
    """注册 / 注销 / 热重载全局热键，并记录可读状态。"""

    def __init__(self, callback: Callable[[], None]) -> None:
        self._callback = callback
        self._backend: Optional[_Backend] = None
        self._status = HotkeyStatus()
        self._lock = threading.RLock()

    # -- 生命周期 ---------------------------------------------------------- #
    def start(self, spec: str) -> HotkeyStatus:
        """注册热键；失败时逐个尝试后备后端。"""
        with self._lock:
            self.stop()
            tried: List[str] = []
            errors: List[str] = []
            try:
                hotkey = Hotkey.parse(spec)
            except HotkeyError as exc:
                self._status = HotkeyStatus(ok=False, spec=str(spec), error=str(exc))
                return self._status

            status = HotkeyStatus(spec=hotkey.to_spec(), display=hotkey.display())
            for backend in _backend_candidates():
                tried.append(backend.name)
                if not backend.available():
                    errors.append(f"{backend.name}：当前平台不可用")
                    continue
                try:
                    backend.start(hotkey, self._callback)
                except HotkeyError as exc:
                    errors.append(f"{backend.name}：{exc}")
                    continue
                except Exception as exc:  # ctypes/Qt 边界异常
                    LOG.warning("热键后端 %s 启动失败", backend.name, exc_info=True)
                    errors.append(f"{backend.name}：{exc}")
                    continue
                self._backend = backend
                status.ok = True
                status.backend = backend.name
                status.tried = tried
                status.error = ""
                LOG.info("全局热键 %s 已注册（%s）", status.display, backend.name)
                self._status = status
                return status

            status.ok = False
            status.tried = tried
            status.error = "；".join(errors) or "无可用后端"
            LOG.error("全局热键注册失败：%s", status.error)
            self._status = status
            return status

    def restart(self, spec: str) -> HotkeyStatus:
        """改设置后热重载（无需重启程序）。"""
        return self.start(spec)

    def stop(self) -> None:
        with self._lock:
            if self._backend is not None:
                try:
                    self._backend.stop()
                except Exception:
                    LOG.debug("停止热键后端失败", exc_info=True)
                self._backend = None
            self._status = HotkeyStatus()

    # -- 状态 --------------------------------------------------------------- #
    @property
    def status(self) -> HotkeyStatus:
        return self._status

    @property
    def backend_name(self) -> str:
        return self._status.backend or "（未注册）"

    @property
    def active(self) -> bool:
        return self._status.ok


def default_hotkey() -> str:
    """跨平台默认热键（pynput 记法）。"""
    return "<cmd>+<shift>+a" if _IS_MAC else "<ctrl>+<shift>+a"
