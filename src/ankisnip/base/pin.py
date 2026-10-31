"""Pin-to-screen floating window for a captured image."""

from __future__ import annotations

import datetime as _dt
import sys as _sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QPoint, QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QGuiApplication, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QFileDialog, QLabel, QMenu, QWidget

from .config import Config

_ZOOM_STEP = 1.1
_MIN_SCALE = 0.2
_MAX_SCALE = 8.0
_RESIZE_MARGIN = 16  # bottom-right corner zone that resizes instead of moves
_CLOSE_BTN_RADIUS = 9  # close button circle radius, in logical px
_CLOSE_BTN_MARGIN = 6  # gap from the top-right corner


class PinWindow(QLabel):
    """A frameless, always-on-top label showing a pinned screenshot.

    Drag the interior to move it, drag the bottom-right corner to resize it, and
    the wheel zooms. The context menu offers copy / save / opacity / close. The
    window stays above other applications and does not hide when another app is
    focused (no ``Qt.Tool`` flag, which would auto-hide it on macOS).
    """

    def __init__(
        self,
        pixmap: QPixmap,
        config: Optional[Config] = None,
        position: Optional[QPoint] = None,
    ) -> None:
        super().__init__(None)
        self._config = config or Config()
        self._original = pixmap
        dpr = pixmap.devicePixelRatio() or 1.0
        self._logical_w = pixmap.width() / dpr
        self._logical_h = pixmap.height() / dpr
        self._scale = 1.0

        self._mode: Optional[str] = None  # "move" | "resize"
        self._drag_offset = QPoint()
        self._resize_start_global = QPoint()
        self._resize_start_w = 0
        self._hover_close = False
        # Desired top-left in global coordinates; re-asserted in showEvent so
        # macOS can't re-center the frameless window on first show.
        self._target_pos = QPoint(position) if position is not None else None

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setMouseTracking(True)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_menu)
        self.setStyleSheet("border: 1px solid rgba(0,0,0,120);")
        self.setWindowOpacity(self._config.pin_opacity)
        self._apply_scale()
        if self._target_pos is not None:
            self.move(self._target_pos)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # macOS repositions a frameless window on show by an amount that varies
        # with the window's size/position (not a fixed offset), so a single
        # setPosition lands off. Instead, place it then measure the real landing
        # spot and nudge by the residual, iterating a few times until it sits on
        # the target. frameGeometry() reflects the true on-screen position.
        if self._target_pos is not None:
            self._desired = QPoint(self._target_pos)
            self._target_pos = None
            self._request = QPoint(self._desired)
            self._correct_tries = 4
            self._set_pos(self._request)
            QTimer.singleShot(0, self._correct_position)

    def _set_pos(self, pos: QPoint) -> None:
        handle = self.windowHandle()
        if handle is not None:
            handle.setPosition(pos)
        else:
            self.move(pos)

    def _correct_position(self) -> None:
        actual = self.frameGeometry().topLeft()
        residual = self._desired - actual
        if residual.manhattanLength() <= 1 or self._correct_tries <= 0:
            print(
                f"[mysphinx-snip] pin final: desired {self._desired}, actual {actual}",
                file=_sys.stderr,
            )
            return
        self._correct_tries -= 1
        self._request = self._request + residual
        self._set_pos(self._request)
        QTimer.singleShot(0, self._correct_position)

    # -- presentation ------------------------------------------------------
    def _apply_scale(self) -> None:
        # Be explicit about device-pixel-ratio: size the window to the *logical*
        # target (so scale 1.0 matches the selected region) and render the
        # pixmap at physical resolution with its dpr set, so it stays crisp on
        # HiDPI. Relying on QPixmap.scaled()'s implicit dpr handling previously
        # halved the window on Retina.
        dpr = self._original.devicePixelRatio() or 1.0
        target_w = self._logical_w * self._scale
        target_h = self._logical_h * self._scale
        # Strip the source dpr first so scaled() works in plain physical pixels
        # (its dpr handling is inconsistent), then reapply dpr explicitly.
        src = QPixmap(self._original)
        src.setDevicePixelRatio(1.0)
        physical = QSize(max(1, round(target_w * dpr)), max(1, round(target_h * dpr)))
        scaled = src.scaled(physical, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        scaled.setDevicePixelRatio(dpr)
        self.setPixmap(scaled)
        self.setFixedSize(QSize(round(target_w), round(target_h)))

    # -- interaction -------------------------------------------------------
    def _in_resize_zone(self, pos: QPoint) -> bool:
        return (
            pos.x() >= self.width() - _RESIZE_MARGIN
            and pos.y() >= self.height() - _RESIZE_MARGIN
        )

    def _close_button_center(self) -> QPointF:
        return QPointF(
            self.width() - _CLOSE_BTN_MARGIN - _CLOSE_BTN_RADIUS,
            _CLOSE_BTN_MARGIN + _CLOSE_BTN_RADIUS,
        )

    def _in_close_zone(self, pos) -> bool:
        center = self._close_button_center()
        dx, dy = pos.x() - center.x(), pos.y() - center.y()
        return dx * dx + dy * dy <= _CLOSE_BTN_RADIUS ** 2

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return
        local = event.position().toPoint()
        if self._in_close_zone(local):
            self.close()
            return
        global_pos = event.globalPosition().toPoint()
        if self._in_resize_zone(local):
            self._mode = "resize"
            self._resize_start_global = global_pos
            self._resize_start_w = self.width()
        else:
            self._mode = "move"
            self._drag_offset = global_pos - self.frameGeometry().topLeft()
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._mode == "move" and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
        elif self._mode == "resize" and event.buttons() & Qt.LeftButton:
            dx = event.globalPosition().toPoint().x() - self._resize_start_global.x()
            new_w = max(_MIN_SCALE * self._logical_w, self._resize_start_w + dx)
            scale = max(_MIN_SCALE, min(_MAX_SCALE, new_w / self._logical_w))
            self._scale = scale
            self._apply_scale()
            event.accept()
        else:
            local = event.position().toPoint()
            was_hover = self._hover_close
            self._hover_close = self._in_close_zone(local)
            if self._hover_close != was_hover:
                self.update()
            if self._hover_close:
                self.setCursor(Qt.ArrowCursor)
            else:
                # hover: show the resize cursor over the corner, move cursor elsewhere
                zone = self._in_resize_zone(local)
                self.setCursor(Qt.SizeFDiagCursor if zone else Qt.SizeAllCursor)

    def mouseReleaseEvent(self, event) -> None:
        self._mode = None

    def leaveEvent(self, event) -> None:
        if self._hover_close:
            self._hover_close = False
            self.update()
        super().leaveEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        self.close()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.close()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if not self._hover_close:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        center = self._close_button_center()
        rect = QRectF(
            center.x() - _CLOSE_BTN_RADIUS,
            center.y() - _CLOSE_BTN_RADIUS,
            _CLOSE_BTN_RADIUS * 2,
            _CLOSE_BTN_RADIUS * 2,
        )
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, 170))
        painter.drawEllipse(rect)
        pen = QPen(QColor("white"), 1.6)
        painter.setPen(pen)
        inset = _CLOSE_BTN_RADIUS * 0.45
        painter.drawLine(
            QPointF(center.x() - inset, center.y() - inset),
            QPointF(center.x() + inset, center.y() + inset),
        )
        painter.drawLine(
            QPointF(center.x() - inset, center.y() + inset),
            QPointF(center.x() + inset, center.y() - inset),
        )

    def wheelEvent(self, event) -> None:
        delta = event.angleDelta().y()
        if delta == 0:
            return
        factor = _ZOOM_STEP if delta > 0 else 1 / _ZOOM_STEP
        self._scale = max(_MIN_SCALE, min(_MAX_SCALE, self._scale * factor))
        center = self.frameGeometry().center()
        self._apply_scale()
        # keep roughly centered on the same spot
        new_top_left = center - QPoint(self.width() // 2, self.height() // 2)
        self.move(new_top_left)
        event.accept()

    # -- context menu ------------------------------------------------------
    def _show_menu(self, pos) -> None:
        menu = QMenu(self)
        menu.addAction(_action(menu, "复制", self._copy))
        menu.addAction(_action(menu, "保存…", self._save))
        menu.addSeparator()

        opacity_menu = menu.addMenu("不透明度")
        for label, value in [("100%", 1.0), ("80%", 0.8), ("60%", 0.6), ("40%", 0.4)]:
            opacity_menu.addAction(_action(opacity_menu, label, lambda v=value: self._set_opacity(v)))

        menu.addAction(_action(menu, "重置缩放", self._reset_scale))
        menu.addSeparator()
        menu.addAction(_action(menu, "关闭", self.close))
        menu.exec(self.mapToGlobal(pos))

    def _copy(self) -> None:
        QGuiApplication.clipboard().setPixmap(self._original)

    def _save(self) -> None:
        save_dir = self._config.ensure_save_dir()
        default = save_dir / f"Snip_{_dt.datetime.now():%Y%m%d_%H%M%S}.png"
        path, _ = QFileDialog.getSaveFileName(
            self, "保存图片", str(default), "PNG 图片 (*.png);;JPEG 图片 (*.jpg)"
        )
        if path:
            self._original.save(path)
            self._config.save_dir = str(Path(path).parent)

    def _set_opacity(self, value: float) -> None:
        self.setWindowOpacity(value)
        self._config.pin_opacity = value

    def _reset_scale(self) -> None:
        self._scale = 1.0
        self._apply_scale()


def _action(parent, text, slot) -> QAction:
    action = QAction(text, parent)
    action.triggered.connect(lambda _=False: slot())
    return action
