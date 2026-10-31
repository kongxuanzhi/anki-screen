"""``kong`` 字段的序列化 / 加密（移植自现有 Anki 插件，见 :mod:`.crypto`）。"""

from .crypto import AES_KEY, decrypt, encrypt  # noqa: F401
from .serialize import (  # noqa: F401
    DEFAULT_MASK_COLOR,
    KONG_ID_LENGTH,
    KONG_SEPARATOR,
    MaskRect,
    build_img_html,
    build_kong,
    mask_to_kong_entry,
    parse_kong,
    random_id,
    to_percent,
    to_point,
)

__all__ = [
    "AES_KEY",
    "encrypt",
    "decrypt",
    "MaskRect",
    "build_kong",
    "build_img_html",
    "parse_kong",
    "mask_to_kong_entry",
    "random_id",
    "to_percent",
    "to_point",
    "DEFAULT_MASK_COLOR",
    "KONG_ID_LENGTH",
    "KONG_SEPARATOR",
]
