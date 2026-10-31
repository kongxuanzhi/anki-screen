"""``kong`` 对拍测试：Python 实现必须与现有 Anki 插件（JS）逐字节一致。

对应任务书 §4.3「编写对拍测试：同一组 masks 输入，现有插件与新模块输出的
kong 字符串必须一致」以及验收清单第 9 条。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from ankisnip.kong import MaskRect, build_kong, parse_kong
from ankisnip.kong.crypto import b32decode, b32encode, decrypt, encrypt

ROOT = Path(__file__).resolve().parent.parent
REF_JS = ROOT / "tools" / "kong_ref.js"

# 与 tools/kong_ref.js 的输入形状一致（插件内部用的百分比字符串形式）
CASES = [
    [{"id": "ab3kx9", "x": "32.0%", "y": "18.0%", "color": "#057fff",
      "w": "10.0%", "h": "6.0%", "rx": "0", "ry": "0", "opacity": "1.0", "tip": ""}],
    [{"id": "qw7z2m", "x": "50.5%", "y": "60.2%", "color": "#FF0000",
      "w": "5.0%", "h": "3.3%", "rx": "0", "ry": "0", "opacity": "1.0", "tip": "提示"},
     {"id": "zz9kk1", "x": "0.0%", "y": "0.0%", "color": "#30d158",
      "w": "100.0%", "h": "12.5%", "rx": "0", "ry": "0", "opacity": "1.0", "tip": ""}],
]

NODE_CANDIDATES = ["node", "nodejs", "/usr/local/bin/node", "/opt/homebrew/bin/node"]


def _node() -> str | None:
    for candidate in NODE_CANDIDATES:
        if shutil.which(candidate):
            return candidate
    managed = Path.home() / ".workbuddy/binaries/node/versions/22.22.2/bin/node"
    return str(managed) if managed.exists() else None


@pytest.mark.skipif(_node() is None, reason="未找到 node，跳过与 JS 的对拍")
@pytest.mark.parametrize("masks", CASES)
def test_kong_matches_existing_plugin(masks):
    """同一组输入 → Python 与 JS 输出的 kong 字符串必须完全一致。"""
    node = _node()
    assert node is not None
    proc = subprocess.run(
        [node, str(REF_JS), json.dumps({"masks": masks}, ensure_ascii=False)],
        capture_output=True, text=True, check=True,
    )
    expected = json.loads(proc.stdout)["kong"]

    rects = [
        MaskRect(
            nx=float(m["x"].rstrip("%")) / 100.0,
            ny=float(m["y"].rstrip("%")) / 100.0,
            nw=float(m["w"].rstrip("%")) / 100.0,
            nh=float(m["h"].rstrip("%")) / 100.0,
            tip=m.get("tip", ""),
            color=m["color"],
        )
        for m in masks
    ]
    got = build_kong(rects, ids=[m["id"] for m in masks])
    assert got == expected


def test_encrypt_roundtrip():
    token = encrypt('["32.0%","18.0%","#057fff","10.0%","6.0%","0","0","1.0",""]')
    assert token == token.lower()
    assert decrypt(token).startswith('["32.0%"')


def test_b32_roundtrip():
    for raw in [b"", b"a", b"ab", b"abc", b"abcd", b"abcde", b"YWJjZGVmZ2g=", b"x" * 37]:
        assert b32decode(b32encode(raw)) == raw


def test_build_kong_shape():
    rects = [MaskRect(0.1, 0.2, 0.3, 0.4), MaskRect(0.5, 0.5, 0.1, 0.1)]
    kong = build_kong(rects, ids=["aaaaaa", "bbbbbb"])
    assert kong.startswith("aaaaaa")
    assert kong.endswith(";")
    assert kong.count(";") == 2

    parsed = parse_kong(kong)
    assert [p[0] for p in parsed] == ["aaaaaa", "bbbbbb"]
    for (_id, rect), src in zip(parsed, rects):
        assert rect.nx == pytest.approx(src.nx)
        assert rect.ny == pytest.approx(src.ny)
        assert rect.nw == pytest.approx(src.nw)
        assert rect.nh == pytest.approx(src.nh)


def test_no_masks_means_empty_kong():
    assert build_kong([]) == ""
