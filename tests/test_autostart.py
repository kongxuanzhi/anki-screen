"""开机自启动：macOS LaunchAgent 写入 / 删除。"""

from __future__ import annotations

import plistlib
import sys

import pytest

from ankisnip import autostart

pytestmark = pytest.mark.skipif(
    sys.platform != "darwin", reason="macOS LaunchAgent 路径")


def test_launch_agent_points_at_app_bundle(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(
        sys, "executable", "/Applications/AnkiSnip.app/Contents/MacOS/AnkiSnip")

    autostart.set_enabled(True)
    path = tmp_path / "Library/LaunchAgents/com.ankisnip.app.plist"
    assert path.is_file()
    payload = plistlib.loads(path.read_bytes())
    assert payload["Label"] == "com.ankisnip.app"
    assert payload["ProgramArguments"] == [
        "/usr/bin/open", "-a", "/Applications/AnkiSnip.app"]
    assert payload["RunAtLoad"] is True

    autostart.set_enabled(False)
    assert not path.exists()


def test_launch_agent_requires_frozen_app(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delattr(sys, "frozen", raising=False)

    with pytest.raises(RuntimeError):
        autostart.set_enabled(True)
