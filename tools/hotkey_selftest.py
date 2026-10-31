"""全局热键自检：真机注册一个临时热键，合成一次按键，看回调有没有被触发。

用法::

    python tools/hotkey_selftest.py            # 默认 <cmd>+<alt>+<shift>+k
    python tools/hotkey_selftest.py <ctrl>+<alt>+<shift>+k

不需要人工按键；合成事件通过 ``CGEventPost``（macOS）发出。
"""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from ankisnip.hotkey import GlobalHotkeyManager, Hotkey  # noqa: E402

_SPEC = sys.argv[1] if len(sys.argv) > 1 else "<cmd>+<alt>+<shift>+k"

# CGEventFlags
_FLAG_COMMAND = 1 << 20
_FLAG_SHIFT = 1 << 17
_FLAG_ALTERNATE = 1 << 19
_FLAG_CONTROL = 1 << 18


def _post_key(vk: int, mods: int) -> bool:
    """合成一次按键（按下 + 抬起）到 HID 层。"""
    if sys.platform != "darwin":
        return False
    try:
        cg = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
    except OSError:
        return False
    cg.CGEventCreateKeyboardEvent.restype = ctypes.c_void_p
    cg.CGEventCreateKeyboardEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint16,
                                              ctypes.c_bool]
    cg.CGEventSetFlags.argtypes = [ctypes.c_void_p, ctypes.c_uint64]
    cg.CGEventPost.argtypes = [ctypes.c_uint32, ctypes.c_void_p]

    flags = 0
    if mods & 0x0100:  # cmd
        flags |= _FLAG_COMMAND
    if mods & 0x0200:  # shift
        flags |= _FLAG_SHIFT
    if mods & 0x0800:  # alt/option
        flags |= _FLAG_ALTERNATE
    if mods & 0x1000:  # ctrl
        flags |= _FLAG_CONTROL

    for pressed in (True, False):
        event = cg.CGEventCreateKeyboardEvent(None, vk, pressed)
        if not event:
            return False
        cg.CGEventSetFlags(event, flags)
        cg.CGEventPost(0, event)  # kCGHIDEventTap
    return True


def main() -> int:
    app = QApplication(sys.argv)
    fired: list[float] = []

    manager = GlobalHotkeyManager(lambda: fired.append(0.0))
    status = manager.start(_SPEC)
    print(f"热键        : {_SPEC}  ({status.display})")
    print(f"注册结果    : ok={status.ok}")
    print(f"后端        : {status.backend or '（无）'}")
    if status.error:
        print(f"错误信息    : {status.error}")
    if not status.ok:
        print("已尝试      : " + "、".join(status.tried))
        return 1

    hotkey = Hotkey.parse(_SPEC)
    try:
        mods, vk = hotkey.native()
    except Exception as exc:
        print(f"无法取原生键码：{exc}")
        return 1

    # 一个可见的小窗口：合成事件在本机可能被系统拦掉，最后仍需人工按一次确认。
    from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

    hint = QWidget()
    hint.setWindowTitle("AnkiSnip 热键自检")
    layout = QVBoxLayout(hint)
    label = QLabel(f"请在 12 秒内按下 {status.display}\n（脚本也会尝试自动合成一次）")
    label.setAlignment(0x0004 | 0x0080)  # Qt.AlignCenter
    layout.addWidget(label)
    hint.resize(380, 120)
    hint.show()
    hint.raise_()

    def send():
        ok = _post_key(vk, mods)
        print(f"合成按键    : {'已发送' if ok else '当前平台无法合成（需人工按一次）'}")
        label.setText(f"请按下 {status.display}（剩余约 10 秒）…")

    def check():
        if fired:
            label.setText(f"✓ 已收到 {len(fired)} 次触发")
            print(f"回调触发    : {len(fired)} 次")
            manager.stop()
            app.quit()

    def finish():
        print(f"回调触发    : {len(fired)} 次")
        if not fired:
            print("未收到触发：请确认没有其它程序占用该组合键；\n"
                  "若长期无效，可授予「辅助功能」权限后使用 pynput 后端。")
        manager.stop()
        app.quit()

    timer = QTimer()
    timer.timeout.connect(check)
    timer.start(100)
    QTimer.singleShot(600, send)
    QTimer.singleShot(12000, finish)
    app.exec()
    return 0 if fired else 2


if __name__ == "__main__":
    raise SystemExit(main())
