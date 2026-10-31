"""把「归一化挖空坐标」序列化为现有 Anki 模板的 ``kong`` 字符串。

格式（**沿用现有插件，不发明新格式**）——对应
``ankiaddon/www/src/js/lib/pic1.js`` 的 ``saveContext(rects, 'add')``：

.. code-block:: text

    kong = (ID6 + base32(AES-ECB(base64(JSON)))) + ';' + ... + ';'

* ``ID6``：6 位随机小写 id，字符集与 ``utils.js`` 的 ``randomString()`` 一致
* ``JSON``：9 元素数组 ``[x, y, color, w, h, rx, ry, opacity, tip]``
  —— ``x/y/w/h`` 是**百分比字符串**（形如 ``"32.0%"``），因为模板里的 SVG rect
  用百分比定位，天然与图片分辨率无关
* 多个挖空用 ``;`` 分隔，末尾保留一个 ``;``（与插件 ``kong += str + ';'`` 一致）

坐标约定
--------
任务书 §3 要求运行时标注用 0~1 归一化坐标、且禁止把屏幕像素写进持久化数据。
这里做唯一一次换算：**归一化 → 百分比字符串**。百分比同样是"相对原图"的，
既满足「无屏幕像素」，又能被现有模板直接渲染。
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from typing import Iterable, List, Sequence

from .crypto import encrypt

#: utils.js randomString() 使用的字符集（随后 toLowerCase）
_ID_CHARS = "ABCDEFGHIJKLMNOPQRSTWXYZabcdefhijkmnprstwxyz2345678"

#: 插件 getCurrentColor() 的默认色，保证与插件里新画的挖空颜色一致
DEFAULT_MASK_COLOR = "#057fff"

KONG_SEPARATOR = ";"
KONG_ID_LENGTH = 6


def random_id(length: int = KONG_ID_LENGTH) -> str:
    """与 ``utils.js`` 的 ``randomString(length)`` 等价（小写）。"""
    return "".join(random.choice(_ID_CHARS) for _ in range(length)).lower()


def to_percent(value: float) -> str:
    """与 ``utils.js`` 的 ``toPercent()`` 等价：``(v*100).toFixed(1) + '%'``。"""
    return f"{value * 100:.1f}%"


def to_point(percent: str) -> float:
    """与 ``utils.js`` 的 ``toPoint()`` 等价（解析既有卡片时用）。"""
    return float(str(percent).replace("%", ""))


@dataclass(frozen=True)
class MaskRect:
    """一个待持久化的挖空（**相对原图的归一化坐标**，0~1）。"""

    nx: float
    ny: float
    nw: float
    nh: float
    tip: str = ""
    color: str = DEFAULT_MASK_COLOR

    def __post_init__(self) -> None:
        for name in ("nx", "ny", "nw", "nh"):
            value = getattr(self, name)
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} 必须是 0~1 的归一化坐标，收到 {value!r}")

    # -- 与模板约定的互转 -------------------------------------------------
    def to_template_array(self) -> list:
        """转成插件 ``rect2str()`` 用的 9 元素数组（坐标是百分比字符串）。"""
        return [
            to_percent(self.nx),
            to_percent(self.ny),
            self.color,
            to_percent(self.nw),
            to_percent(self.nh),
            "0",
            "0",
            "1.0",
            self.tip or "",
        ]

    @classmethod
    def from_template_array(cls, arr: Sequence) -> "MaskRect":
        x, y, color, w, h, _rx, _ry, _opacity, tip = (list(arr) + [""] * 9)[:9]
        return cls(
            nx=to_point(x) / 100.0,
            ny=to_point(y) / 100.0,
            nw=to_point(w) / 100.0,
            nh=to_point(h) / 100.0,
            tip=tip or "",
            color=color or DEFAULT_MASK_COLOR,
        )


def mask_to_kong_entry(mask: MaskRect, mask_id: str) -> str:
    """单个挖空 → ``ID6 + base32(...)``（不含分隔符）。"""
    payload = json.dumps(mask.to_template_array(), ensure_ascii=False, separators=(",", ":"))
    return f"{mask_id}{encrypt(payload)}"


def build_kong(masks: Iterable[MaskRect], ids: Iterable[str] | None = None) -> str:
    """把若干挖空拼成完整的 ``kong`` 字符串（末尾带 ``;``）。

    ``ids`` 用于测试时固定随机 id；生产环境留空即自动生成。
    """
    mask_list = list(masks)
    if not mask_list:
        return ""
    id_iter = list(ids) if ids is not None else [random_id() for _ in mask_list]
    if len(id_iter) != len(mask_list):
        raise ValueError("ids 数量必须与 masks 一致")
    parts = [mask_to_kong_entry(m, i) for m, i in zip(mask_list, id_iter)]
    return KONG_SEPARATOR.join(parts) + KONG_SEPARATOR


def build_img_html(media_filename: str, kong: str) -> str:
    """构造放入 ``pic_kong`` 字段的 HTML：``<img src="..." kong="...">``。"""
    if kong:
        return f'<img src="{media_filename}" kong="{kong}">'
    return f'<img src="{media_filename}">'


# -- 反向解析（测试 / 排查既有卡片用） ------------------------------------

def parse_kong(kong: str) -> List[tuple]:
    """解析 ``kong`` 字符串，返回 ``[(id, MaskRect), ...]``。"""
    from .crypto import decrypt  # 局部导入避免循环

    out: List[tuple] = []
    for part in kong.split(KONG_SEPARATOR):
        if len(part) < 10:
            continue
        mask_id = part[:KONG_ID_LENGTH]
        arr = json.loads(decrypt(part[KONG_ID_LENGTH:]))
        out.append((mask_id, MaskRect.from_template_array(arr)))
    return out
