"""覆盖层上的轻量浮层提示（toast）。

为什么要自己写：托盘通知 macOS 上是从屏幕右上角滑入的系统横幅，**不在截图覆盖层里**，
而覆盖层是全屏窗口（覆盖在最上层）—— 复制后如果直接关闭窗口，用户只是"感觉"复制成功了；
不关窗口又什么都看不到。所以这里在覆盖层内部画一个短暂的半透明标签，
复制/保存这类即时反馈既不打断操作，也看得见。

特点：
* ``WA_TransparentForMouseEvents`` —— 不吃鼠标，覆盖层的标注操作完全不受影响；
* 纯 ``QPropertyAnimation`` 淡出 + ``deleteLater``，不需要额外的定时器管理；
* 位置在**选区矩形**（没有选区时用窗口）的水平居中处，紧挨着用户视线焦点。
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt
from PySide6.QtWidgets import QGraphicsOpacityEffect, QLabel, QWidget

_STYLE = """
QLabel#AnkiSnipToast {
    background: rgba(28, 28, 30, 0.88);
    color: #f5f5f7;
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 8px;
    padding: 8px 16px;
    font-size: 14px;
}
"""


def show_toast(parent: QWidget, text: str, msec: int = 1400,
               anchor_rect=None) -> QLabel:
    """在 ``parent`` 上显示一条会自动消失的提示，返回那个标签。"""
    label = QLabel(parent)
    label.setObjectName("AnkiSnipToast")
    label.setStyleSheet(_STYLE)
    label.setText(text)
    label.setAttribute(Qt.WA_TransparentForMouseEvents)
    label.setFocusPolicy(Qt.NoFocus)
    label.adjustSize()

    box = anchor_rect or parent.rect()
    cx = int(box.center().x())
    cy = int(box.center().y())
    label.move(max(0, cx - label.width() // 2), max(0, cy - label.height() // 2))
    label.raise_()
    label.show()

    effect = QGraphicsOpacityEffect(label)
    label.setGraphicsEffect(effect)
    fade_out_delay = max(0, msec - 400)

    def _fade() -> None:
        anim = QPropertyAnimation(effect, b"opacity", label)
        anim.setDuration(400)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.InOutQuad)
        anim.finished.connect(label.deleteLater)
        anim.start()
        # 动画对象必须在动画期间存活，否则会被 GC 掉导致动画停在半途。
        label._keepalive = (effect, anim)  # type: ignore[attr-defined]

    from PySide6.QtCore import QTimer

    QTimer.singleShot(fade_out_delay, _fade)
    return label
