"""完成动作后的界面行为。

用户的具体要求：
* **复制** —— 不关界面，浮层提示「已复制」，还能接着改标注再复制；
* **保存到图片 / 保存到 Anki** —— 点完就关掉界面（「另存为」要弹文件对话框，
  全屏覆盖层压在顶层会把对话框挤没了）。

这两条都是"点了按钮以后看得见的行为"，一旦被后续重构改坏很难自己察觉，
所以在这里钉死。
"""

from __future__ import annotations

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QPixmap, QImage

from ankisnip.app import AnkiSnipApp
from ankisnip.base.config import Config
from ankisnip.overlay_ext import AnkiCaptureOverlay
from ankisnip.settings import Settings


class _Harness(AnkiSnipApp):
    """只装配完成动作所需的最小状态，不建托盘 / 热键。"""

    def __init__(self) -> None:
        from PySide6.QtCore import QObject
        from PySide6.QtWidgets import QApplication

        QObject.__init__(self)
        self._config = Settings()
        self._app = QApplication.instance()
        self._overlays = []
        self.closed = False
        self.notifications: list[str] = []
        self.toasts: list[str] = []
        self.saved_to_anki: list = []
        self.saved_pixmaps: list = []

    # -- 记录替身 ---------------------------------------------------------- #
    def _notify(self, message, icon=None, timeout=0):
        self.notifications.append(message)

    def _toast(self, text, msec=0):
        self.toasts.append(text)

    def _close_overlays(self):
        self.closed = True
        super()._close_overlays()

    def _save_to_anki(self, image, items):
        self.saved_to_anki.append((image, items))

    def _save_pixmap(self, pixmap):
        self.saved_pixmaps.append(pixmap)


class _FakeShot:
    def __init__(self, pixmap: QPixmap, geo: QRect):
        self.screen = None
        self.pixmap = pixmap
        self._geo = geo

    @property
    def geometry(self):
        return self._geo


def _overlay_with_selection():
    geo = QRect(0, 0, 320, 240)
    pix = QPixmap(320, 240)
    pix.fill(Qt.white)
    ov = AnkiCaptureOverlay(_FakeShot(pix, geo), Config())
    ov._selection = QRectF(20, 20, 160, 120)
    return ov


def test_copy_keeps_window_open_and_toasts():
    ov = _overlay_with_selection()
    app = _Harness()
    app._overlays = [ov]
    pixmap = QPixmap(40, 30)
    pixmap.fill(Qt.red)

    app._on_completed("copy", pixmap, None)

    assert app.closed is False, "复制后必须保留界面，用户可能还想接着改"
    assert app.toasts == ["已复制到剪贴板"]
    from PySide6.QtGui import QGuiApplication

    assert not QGuiApplication.clipboard().pixmap().isNull()
    ov.close()


def test_save_image_closes_window():
    ov = _overlay_with_selection()
    app = _Harness()
    app._overlays = [ov]
    pixmap = QPixmap(40, 30)

    app._on_completed("save", pixmap, None)

    assert app.closed is True
    assert len(app.saved_pixmaps) == 1
    assert app._overlays == []
    ov.close()


def test_save_to_anki_closes_window_and_passes_annotations():
    ov = _overlay_with_selection()
    app = _Harness()
    app._overlays = [ov]

    app._on_completed("anki", QPixmap(40, 30), None)

    assert app.closed is True
    assert len(app.saved_to_anki) == 1
    image, items = app.saved_to_anki[0]
    assert isinstance(image, QImage) and not image.isNull()
    assert image.size() == ov.selection_image().size()
    ov.close()


def test_empty_selection_saving_to_anki_reports_and_quits():
    app = _Harness()
    ov = _overlay_with_selection()
    ov._selection = None
    app._overlays = [ov]

    app._on_completed("anki", QPixmap(10, 10), None)

    assert app.saved_to_anki == []
    assert any("选区为空" in m for m in app.notifications)
    ov.close()


def test_toast_widget_appears_and_is_click_through():
    """浮层提示要看得见，但不能吃掉鼠标（否则挡住标注）。"""
    from ankisnip.toast import show_toast

    ov = _overlay_with_selection()
    ov.show()
    label = show_toast(ov, "已复制到剪贴板", msec=50)
    try:
        assert label.isVisible()
        assert label.text() == "已复制到剪贴板"
        from PySide6.QtCore import Qt as _Qt

        assert label.testAttribute(_Qt.WA_TransparentForMouseEvents)
        assert label.width() > 0
    finally:
        label.deleteLater()
        ov.close()
