"""Persistent application configuration backed by ``QSettings``."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QSettings, QStandardPaths

ORG = "mysphinx"
APP = "mysphinx-snip"

# Default global hotkey in pynput notation (see pynput.keyboard.GlobalHotKeys).
DEFAULT_HOTKEY = "<cmd>+<shift>+a" if sys.platform == "darwin" else "<ctrl>+<shift>+a"


def _default_save_dir() -> str:
    pictures = QStandardPaths.writableLocation(QStandardPaths.PicturesLocation)
    base = Path(pictures) if pictures else Path.home()
    return str(base / "Snips")


class Config:
    """Thin typed wrapper around ``QSettings``."""

    def __init__(self) -> None:
        self._s = QSettings(ORG, APP)

    @property
    def save_dir(self) -> str:
        return str(self._s.value("save_dir", _default_save_dir()))

    @save_dir.setter
    def save_dir(self, value: str) -> None:
        self._s.setValue("save_dir", value)

    @property
    def hotkey(self) -> str:
        return str(self._s.value("hotkey", DEFAULT_HOTKEY))

    @hotkey.setter
    def hotkey(self, value: str) -> None:
        self._s.setValue("hotkey", value)

    @property
    def pen_color(self) -> str:
        return str(self._s.value("pen_color", "#FF3B30"))

    @pen_color.setter
    def pen_color(self, value: str) -> None:
        self._s.setValue("pen_color", value)

    @property
    def pen_width(self) -> int:
        return int(self._s.value("pen_width", 3))

    @pen_width.setter
    def pen_width(self, value: int) -> None:
        self._s.setValue("pen_width", int(value))

    @property
    def pin_opacity(self) -> float:
        return float(self._s.value("pin_opacity", 1.0))

    @pin_opacity.setter
    def pin_opacity(self, value: float) -> None:
        self._s.setValue("pin_opacity", float(value))

    def ensure_save_dir(self) -> Path:
        path = Path(self.save_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path
