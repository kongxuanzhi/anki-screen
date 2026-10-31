"""PyInstaller 入口脚本。

打包时作为顶层脚本运行，因此不能用包内相对导入；这里对两种场景都做了适配。
"""

import pathlib
import sys

try:
    from ankisnip.app import main
except ImportError:  # 源码树里直接跑
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
    from ankisnip.app import main

if __name__ == "__main__":
    raise SystemExit(main())
