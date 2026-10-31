"""图片挖空模板支持的 7 个颜色。

来源：Anki 笔记类型（``图片挖空模板sle1_0``）正面模板里的 ``colorPicker``。
模板只认这 7 个颜色，且透明度固定（``fill-opacity`` 由模板写死），
所以编辑器的调色板必须收敛到这里，不能再用任意取色器。
"""

from __future__ import annotations

from typing import Tuple

from PySide6.QtGui import QColor

#: ``(色值, 模板里的语义标签)``，顺序与模板的取色器一致。
MASK_COLORS: Tuple[Tuple[str, str], ...] = (
    ("#ff3e05", "铭记于心"),
    ("#ff8d05", "印象深刻"),
    ("#ecca05", "温故知新"),
    ("#40af04", "熟能生巧"),
    ("#057fff", "了解一二"),
    ("#7500ca", "晦涩难懂"),
    ("#808080", "不屑一顾"),
)

#: 模板里 ``#defaultcolor`` 的值。
DEFAULT_COLOR = "#ff8d05"

_SUPPORTED = {value for value, _label in MASK_COLORS}


def supported(value: str) -> bool:
    """``value`` 是否正好是模板支持的 7 色之一（忽略大小写）。"""
    return str(value).strip().lower() in _SUPPORTED


def normalize(value: str) -> str:
    """把任意颜色收敛到模板支持的 7 色（按 RGB 距离取最近）。

    历史配置里的 ``pen_color`` 可能是任意颜色（基座默认还是 ``#FF3B30``）；
    直接写进 kong 会得到模板不支持的色值，所以进出编辑器时统一归一。
    无法解析的颜色回退到 :data:`DEFAULT_COLOR`。
    """
    color = QColor(value)
    if not color.isValid():
        return DEFAULT_COLOR
    name = color.name().lower()
    if name in _SUPPORTED:
        return name

    def distance(item: Tuple[str, str]) -> int:
        target = QColor(item[0])
        return (
            (color.red() - target.red()) ** 2
            + (color.green() - target.green()) ** 2
            + (color.blue() - target.blue()) ** 2
        )

    return min(MASK_COLORS, key=distance)[0]


def label_for(value: str) -> str:
    """色值对应的模板语义标签；未知色值返回空串。"""
    low = str(value).strip().lower()
    for color, label in MASK_COLORS:
        if color == low:
            return label
    return ""
