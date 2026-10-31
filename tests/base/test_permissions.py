"""Tests for the cross-platform short-circuit behaviour of permissions.py.

On any non-macOS platform the permission checks must report "allowed" and the
open/request helpers must be harmless no-ops, so the rest of the app behaves
identically on Windows/Linux.
"""

from snip import permissions


def test_non_mac_checks_allow(monkeypatch):
    monkeypatch.setattr(permissions, "_IS_MAC", False)
    assert permissions.accessibility_trusted() is True
    assert permissions.screen_capture_allowed() is True
    assert permissions.request_screen_capture() is True


def test_non_mac_open_helpers_are_noops(monkeypatch):
    monkeypatch.setattr(permissions, "_IS_MAC", False)
    # Should not raise and should not attempt to open any URL.
    permissions.open_accessibility_settings()
    permissions.open_screen_recording_settings()
