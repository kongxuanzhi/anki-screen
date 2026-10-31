"""macOS privacy-permission helpers (Accessibility + Screen Recording).

macOS gates two capabilities this app needs behind System Settings → Privacy:

* **Accessibility / Input Monitoring** — required by ``pynput`` to register the
  global hotkey (via a ``CGEventTap``). Without it the listener silently
  receives nothing and macOS logs "This process is not trusted!".
* **Screen Recording** — required by ``QScreen.grabWindow`` (CoreGraphics) to
  capture window contents. Without it a capture only contains the wallpaper.

These helpers call the relevant system frameworks through ``ctypes`` (no extra
dependency). On every non-macOS platform the checks short-circuit to "allowed"
and the request/open helpers are no-ops, so behaviour elsewhere is unchanged.
"""

from __future__ import annotations

import ctypes
import sys

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices

_IS_MAC = sys.platform == "darwin"

_ACCESSIBILITY_URL = (
    "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
)
_SCREEN_RECORDING_URL = (
    "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"
)


def _load(framework: str):
    """Load a macOS system framework by name, or ``None`` on failure."""
    path = f"/System/Library/Frameworks/{framework}.framework/{framework}"
    try:
        return ctypes.cdll.LoadLibrary(path)
    except OSError:
        return None


def accessibility_trusted() -> bool:
    """Whether this process may monitor input events (needed for the hotkey)."""
    if not _IS_MAC:
        return True
    lib = _load("ApplicationServices")
    if lib is None or not hasattr(lib, "AXIsProcessTrusted"):
        return True  # can't tell — assume allowed and let pynput try
    lib.AXIsProcessTrusted.restype = ctypes.c_bool
    return bool(lib.AXIsProcessTrusted())


def screen_capture_allowed() -> bool:
    """Whether this process may capture the screen (needed for any screenshot).

    Uses ``CGPreflightScreenCaptureAccess`` (macOS 10.15+). If the symbol is
    unavailable (older macOS) we assume allowed.
    """
    if not _IS_MAC:
        return True
    lib = _load("CoreGraphics")
    if lib is None or not hasattr(lib, "CGPreflightScreenCaptureAccess"):
        return True
    lib.CGPreflightScreenCaptureAccess.restype = ctypes.c_bool
    return bool(lib.CGPreflightScreenCaptureAccess())


def request_screen_capture() -> bool:
    """Request Screen Recording access, prompting the user on first call.

    ``CGRequestScreenCaptureAccess`` registers this process in the Screen
    Recording list and shows the system prompt if not yet decided. Returns the
    current grant status.
    """
    if not _IS_MAC:
        return True
    lib = _load("CoreGraphics")
    if lib is None or not hasattr(lib, "CGRequestScreenCaptureAccess"):
        return screen_capture_allowed()
    lib.CGRequestScreenCaptureAccess.restype = ctypes.c_bool
    return bool(lib.CGRequestScreenCaptureAccess())


def open_accessibility_settings() -> None:
    if _IS_MAC:
        QDesktopServices.openUrl(QUrl(_ACCESSIBILITY_URL))


def open_screen_recording_settings() -> None:
    if _IS_MAC:
        QDesktopServices.openUrl(QUrl(_SCREEN_RECORDING_URL))
