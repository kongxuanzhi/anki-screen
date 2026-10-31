"""Tests for PinWindow sizing, especially HiDPI device-pixel-ratio handling."""

from PySide6.QtGui import QColor, QPixmap

from ankisnip.base.pin import PinWindow


def _hidpi_pixmap(logical_w=200, logical_h=120, dpr=2.0) -> QPixmap:
    pm = QPixmap(int(logical_w * dpr), int(logical_h * dpr))
    pm.fill(QColor("green"))
    pm.setDevicePixelRatio(dpr)
    return pm


def test_pin_window_matches_logical_size_on_hidpi():
    pm = _hidpi_pixmap(200, 120, dpr=2.0)
    pin = PinWindow(pm)
    # Window size is in logical points and must equal the selection's logical
    # size (physical / dpr), NOT be halved again (the original shrink bug).
    assert pin.size().width() == 200
    assert pin.size().height() == 120


def test_pin_window_scale_doubles_logical_size():
    pin = PinWindow(_hidpi_pixmap(200, 120, dpr=2.0))
    pin._scale = 2.0
    pin._apply_scale()
    assert pin.size().width() == 400
    assert pin.size().height() == 240


def test_pin_window_dpr1_unchanged():
    pm = QPixmap(200, 120)
    pm.fill(QColor("blue"))  # dpr defaults to 1.0
    pin = PinWindow(pm)
    assert pin.size().width() == 200
    assert pin.size().height() == 120
