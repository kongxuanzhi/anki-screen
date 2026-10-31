"""Screen capture backend.

Uses Qt's own :meth:`QScreen.grabWindow` to grab each screen. Qt returns a
``QPixmap`` with the correct device-pixel-ratio already applied, so no manual
DPI / coordinate math is needed and it works the same across Windows and macOS
(respecting the granted Screen Recording permission on macOS).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import List, Optional

from PySide6.QtGui import QGuiApplication, QPixmap, QScreen


@dataclass
class ScreenShot:
    """A frozen capture of a single physical screen.

    ``pixmap`` already has its device-pixel-ratio set, so its *logical* size
    equals ``screen.geometry()`` and annotation coordinates can be used directly.
    """

    screen: QScreen
    pixmap: QPixmap

    @property
    def geometry(self):
        return self.screen.geometry()


def _grab_screen(screen: QScreen) -> Optional[QPixmap]:
    geo = screen.geometry()
    # grabWindow(0) captures the whole screen for this QScreen.
    pixmap = screen.grabWindow(0)
    if pixmap.isNull() or pixmap.width() == 0 or pixmap.height() == 0:
        print(
            f"[mysphinx-snip] capture failed for screen {screen.name()!r} "
            f"(geometry={geo}, dpr={screen.devicePixelRatio()})",
            file=sys.stderr,
        )
        return None
    # Safety net: if Qt handed back a physical-pixel pixmap without a dpr, infer
    # it from the size so the logical size matches the screen geometry.
    if pixmap.devicePixelRatio() == 1.0 and geo.width() and pixmap.width() > geo.width():
        pixmap.setDevicePixelRatio(pixmap.width() / geo.width())
    print(
        f"[mysphinx-snip] captured screen {screen.name()!r}: geometry={geo}, "
        f"dpr={pixmap.devicePixelRatio()}, pixmap={pixmap.size()}",
        file=sys.stderr,
    )
    return pixmap


def grab_all_screens() -> List[ScreenShot]:
    """Capture every connected screen, returning one :class:`ScreenShot` each.

    Screens whose capture fails are skipped; an empty list signals total failure.
    """
    shots: List[ScreenShot] = []
    for screen in QGuiApplication.screens():
        pixmap = _grab_screen(screen)
        if pixmap is not None:
            shots.append(ScreenShot(screen=screen, pixmap=pixmap))
    return shots


def screen_at(shots: List[ScreenShot], global_pos) -> ScreenShot:
    """Return the :class:`ScreenShot` whose geometry contains ``global_pos``.

    Falls back to the first shot (primary screen) when no screen matches.
    """
    for shot in shots:
        if shot.geometry.contains(global_pos):
            return shot
    return shots[0]
