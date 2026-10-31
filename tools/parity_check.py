#!/usr/bin/env python3
"""``kong`` 对拍的独立运行器（不依赖 pytest，方便手工核对）。

用法::

    python tools/parity_check.py

需要 node 在 PATH 中（或用环境变量 ``ANKISNIP_NODE`` 指定路径）。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from ankisnip.kong import MaskRect, build_kong  # noqa: E402

CASES = [
    [
        {"id": "ab3kx9", "x": "32.0%", "y": "18.0%", "color": "#057fff",
         "w": "10.0%", "h": "6.0%", "rx": "0", "ry": "0", "opacity": "1.0", "tip": ""},
    ],
    [
        {"id": "qw7z2m", "x": "50.5%", "y": "60.2%", "color": "#FF0000",
         "w": "5.0%", "h": "3.3%", "rx": "0", "ry": "0", "opacity": "1.0", "tip": "提示"},
        {"id": "zz9kk1", "x": "0.0%", "y": "0.0%", "color": "#30d158",
         "w": "100.0%", "h": "12.5%", "rx": "0", "ry": "0", "opacity": "1.0", "tip": ""},
    ],
]


def _node() -> str | None:
    if os.environ.get("ANKISNIP_NODE"):
        return os.environ["ANKISNIP_NODE"]
    found = shutil.which("node")
    if found:
        return found
    managed = Path.home() / ".workbuddy/binaries/node/versions/22.22.2/bin/node"
    return str(managed) if managed.exists() else None


def main() -> int:
    node = _node()
    if node is None:
        print("未找到 node，无法对拍。请安装 node 或设置 ANKISNIP_NODE。")
        return 2

    ok = True
    for i, masks in enumerate(CASES, 1):
        proc = subprocess.run(
            [node, str(ROOT / "tools" / "kong_ref.js"),
             json.dumps({"masks": masks}, ensure_ascii=False)],
            capture_output=True, text=True, check=True,
        )
        expected = json.loads(proc.stdout)["kong"]
        rects = [
            MaskRect(nx=float(m["x"].rstrip("%")) / 100.0,
                     ny=float(m["y"].rstrip("%")) / 100.0,
                     nw=float(m["w"].rstrip("%")) / 100.0,
                     nh=float(m["h"].rstrip("%")) / 100.0,
                     tip=m.get("tip", ""), color=m["color"])
            for m in masks
        ]
        got = build_kong(rects, ids=[m["id"] for m in masks])
        same = got == expected
        ok = ok and same
        print(f"用例 {i}: {'一致 ✓' if same else '不一致 ✗'}")
        if not same:
            print("  JS :", expected)
            print("  PY :", got)
    print("\n结论：", "Python 与现有插件输出逐字节一致" if ok else "存在偏差，请勿发版")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
