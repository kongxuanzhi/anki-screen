"""日志文件配置——便于用户反馈问题时直接把日志发过来。"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QStandardPaths

LOG_NAME = "ankisnip"


def log_dir() -> Path:
    base = QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation)
    if not base:
        base = str(Path.home() / ".local" / "share")
    path = Path(base) / "ankisnip" / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def log_file() -> Path:
    return log_dir() / f"ankisnip-{datetime.now():%Y%m}.log"


def setup(level: int = logging.INFO) -> Path:
    """把 ``ankisnip`` 命名空间的日志同时输出到控制台与滚动日志文件。"""
    path = log_file()
    root = logging.getLogger(LOG_NAME)
    root.setLevel(level)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    file_handler = logging.FileHandler(path, encoding="utf-8")
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(fmt)
    root.addHandler(stream)

    root.propagate = False
    root.info("日志已启动：%s", path)
    return path
