"""现有 Anki 插件 `kong` 字段的加解密——Python 侧等价实现。

**这是移植，不是发明。** 逐行对齐现有插件的实现：

* ``ankiaddon/www/src/js/utils/utils.js`` → ``encrypt()`` / ``decrypt()``
  - AES，key = ``2024169163483647``（16 字节，AES-128），Utf8.parse 后作为**二进制 key**
  - mode = ECB，padding = Pkcs7
  - ``CipherParams.toString()`` → 密文的 **标准 base64**（带 ``=`` 填充，因为用
    WordArray 作 key 时不产生 salt，OpenSSLFormatter 不会加 ``Salted__`` 头）
  - 再对该 base64 字符串做 base32 编码
* base32 实现来自模板内嵌的 ``window.base32``（
  ``ankiaddon/www/src/js/utils/base32.min.js``，webpack externals 把
  ``base32_min`` 映射到全局 ``base32``）。字母表为
  ``0123456789abcdefghjkmnpqrtuvwxyz``（32 字符，5 bit/字符，MSB-first，**无填充**）。

任务书 §4 要求「不要自行发明新格式」，因此本模块不做任何格式调整；
``tools/kong_ref.js`` + ``tests/test_kong_parity.py`` 会与 JS 原实现逐字节对拍。
"""

from __future__ import annotations

import base64

from Crypto.Cipher import AES

#: 16 字节 AES key，与 utils.js 中 ``var aseKey = '2024169163483647'`` 一致。
AES_KEY = b"2024169163483647"

#: 模板内嵌 base32 实现的字母表（与 RFC4648 不同，注意开头是数字）。
BASE32_ALPHABET = "0123456789abcdefghjkmnpqrtuvwxyz"

_B32_DECODE_TABLE = {c: i for i, c in enumerate(BASE32_ALPHABET)}


def _pkcs7_pad(data: bytes, block: int = 16) -> bytes:
    pad = block - (len(data) % block)
    return data + bytes([pad]) * pad


def _pkcs7_unpad(data: bytes) -> bytes:
    if not data:
        return data
    pad = data[-1]
    if 1 <= pad <= 16 and data[-pad:] == bytes([pad]) * pad:
        return data[:-pad]
    return data


def b32encode(data: bytes) -> str:
    """把字节串编码为模板使用的 base32（无填充，小写，自定义字母表）。"""
    bits = 0
    nbits = 0
    out = []
    for byte in data:
        bits = (bits << 8) | byte
        nbits += 8
        while nbits >= 5:
            nbits -= 5
            out.append(BASE32_ALPHABET[(bits >> nbits) & 0x1F])
    if nbits:
        out.append(BASE32_ALPHABET[(bits << (5 - nbits)) & 0x1F])
    return "".join(out)


def b32decode(text: str) -> bytes:
    """:func:`b32encode` 的逆运算（解析既有卡片时需要）。"""
    bits = 0
    nbits = 0
    out = bytearray()
    for ch in text.lower():
        val = _B32_DECODE_TABLE.get(ch)
        if val is None:
            # 与 JS Decoder.readChar 保持一致：遇到非法字符静默跳过
            continue
        bits = (bits << 5) | val
        nbits += 5
        if nbits >= 8:
            nbits -= 8
            out.append((bits >> nbits) & 0xFF)
    return bytes(out)


def encrypt(message: str) -> str:
    """与 ``utils.js`` 的 ``encrypt()`` 完全等价：

    ``base32.encode(base64(AES-ECB-PKCS7(utf8(message), key)))``
    """
    cipher = AES.new(AES_KEY, AES.MODE_ECB)
    ct = cipher.encrypt(_pkcs7_pad(message.encode("utf-8")))
    b64 = base64.b64encode(ct).decode("ascii")
    return b32encode(b64.encode("ascii"))


def decrypt(token: str) -> str:
    """与 ``utils.js`` 的 ``decrypt()`` 等价（新格式分支：全小写 ⇒ 先 base32 解码）。

    兼容旧格式（直接是 base64 密文、用 12 字节 key 的那一版）不在此实现，
    遇到时抛出 :class:`ValueError`，由调用方决定如何处理。
    """
    if not token:
        return ""
    if token.lower() == token:
        # 新格式：base32 解出的是 **base64 文本**，还要再解一次
        raw = base64.b64decode(b32decode(token), validate=False)
    else:
        # 旧分支：明文含大写字母 ⇒ 直接就是 base64 密文
        raw = base64.b64decode(token, validate=False)
    cipher = AES.new(AES_KEY, AES.MODE_ECB)
    pt = _pkcs7_unpad(cipher.decrypt(raw))
    return pt.decode("utf-8", errors="replace")
