"""pytest 全局夹具。

注意：基座自带的 ``tests/test_annotations.py`` 在 offscreen 下会崩，是因为它
没有创建 ``QGuiApplication``（``QPainter.drawText`` 需要字体数据库）。
这里统一在会话级创建，避免踩同一个坑。
"""

import os
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session", autouse=True)
def _qt_app(tmp_path_factory):
    """会话级 Qt 应用 + **配置隔离**。

    历史上单测直接读写用户真实的 ``~/Library/Preferences/com.ankisnip.*.plist``，
    把 ``offline_dir`` 写成了 pytest 的临时目录（用户侧表现为"设置不生效"）。
    这里强制把 :class:`~ankisnip.settings.Settings` 的持久化目标切到临时 ini。
    """
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])

    from ankisnip.settings import set_test_scope

    ini = tmp_path_factory.mktemp("qsettings") / "ankisnip-test.ini"
    set_test_scope(str(ini))
    # 双保险：基座 Config 也走同一个临时域
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope,
                      str(tmp_path_factory.mktemp("qsettings-native")))

    yield app

    set_test_scope("")


@pytest.fixture(autouse=True)
def _isolate_base_config():
    """**每个用例**前后把配置键值保护起来。

    覆盖层的 ``_on_color_changed`` 会顺手写 ``pen_color`` / ``pen_width``。
    而这两个值作用于导出结果，一旦泄漏就是"用例之间互相串色"（典型症状：
    改色用例把绿色写进共享 ini，后面画箭头的用例就找不到红色像素了）。

    两个作用域都要保护：

    * ``base.config.Config`` → ``QSettings("mysphinx", "mysphinx-snip")``；
    * ``ankisnip.settings.Settings`` → 它继承了前者的属性，但 ``self._s`` 指向
      AnkiSnip 自己的作用域。传哪个 config 给覆盖层，写就会落到哪里。

    漏过这道保护的表现是：跑一次测试，用户真实的画笔颜色 / 线宽被改掉。
    """
    from ankisnip.base.config import Config
    from ankisnip.settings import Settings

    keys = ("pen_color", "pen_width", "save_dir", "hotkey")
    snapshots = []
    for cls in (Config, Settings):
        obj = cls()
        snapshots.append((cls, {key: obj._s.value(key) for key in keys}))
    yield
    for cls, before in snapshots:
        try:
            after = cls()
            for key, value in before.items():
                if value is None:
                    after._s.remove(key)
                elif after._s.value(key) != value:
                    after._s.setValue(key, value)
            after._s.sync()
        except Exception:  # pragma: no cover - 清理失败不应让用例失败
            pass


@pytest.fixture()
def qt_key_event():
    """构造一个可控的 ``QKeyEvent``（可指定原生虚拟键码）。"""
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent

    class _KeyEvent(QKeyEvent):
        def __init__(self, key, mods, text, nvk):
            super().__init__(QEvent.KeyPress, key, mods, text)
            self._nvk = nvk

        def nativeVirtualKey(self):  # noqa: N802
            return self._nvk

    def make(name: str, ctrl=False, alt=False, shift=False, meta=False,
             native_vk: int = 0, text: str = ""):
        key = getattr(Qt, "Key_" + name)
        mods = Qt.NoModifier
        if ctrl:
            mods |= Qt.ControlModifier
        if alt:
            mods |= Qt.AltModifier
        if shift:
            mods |= Qt.ShiftModifier
        if meta:
            mods |= Qt.MetaModifier
        return _KeyEvent(key, mods, text, native_vk)

    return make
