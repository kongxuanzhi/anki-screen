"""开机自启动：macOS 用 LaunchAgent，Windows 用启动文件夹脚本。"""

from __future__ import annotations

import os
import plistlib
import sys
from pathlib import Path

_LABEL = "com.ankisnip.app"
_WIN_STARTUP_SCRIPT = "AnkiSnip.cmd"


def _executable() -> Path:
    """打包后的可执行文件；开发模式下不允许设置，避免登录时启动一个裸 Python。"""
    if not getattr(sys, "frozen", False):
        raise RuntimeError("开机自启动只支持打包后的 AnkiSnip 应用。")
    return Path(sys.executable).resolve()


def _launch_agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{_LABEL}.plist"


def _windows_startup_path() -> Path:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("找不到 %APPDATA%，无法设置 Windows 开机自启动。")
    return (Path(appdata) / "Microsoft" / "Windows" / "Start Menu"
            / "Programs" / "Startup" / _WIN_STARTUP_SCRIPT)


def set_enabled(enabled: bool) -> None:
    """打开 / 关闭开机自启动；失败时抛 ``RuntimeError``。"""
    if sys.platform == "darwin":
        _set_mac(enabled)
    elif sys.platform == "win32":
        _set_windows(enabled)
    else:
        raise RuntimeError("当前平台不支持开机自启动。")


def _set_mac(enabled: bool) -> None:
    path = _launch_agent_path()
    if not enabled:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return
    executable = _executable()
    # 从 .app 内部运行时用 LaunchServices 打开整个 bundle，保证 LSUIElement
    # / TCC 身份与双击启动一致；否则回退到直接执行。
    parts = executable.parts
    app_index = next((i for i, part in enumerate(parts) if part.endswith(".app")), None)
    if app_index is not None:
        app_bundle = Path(*parts[:app_index + 1])
        arguments = ["/usr/bin/open", "-a", str(app_bundle)]
    else:
        arguments = [str(executable)]
    payload = {
        "Label": _LABEL,
        "ProgramArguments": arguments,
        "RunAtLoad": True,
        "KeepAlive": False,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        plistlib.dump(payload, handle)


def _set_windows(enabled: bool) -> None:
    path = _windows_startup_path()
    if not enabled:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return
    executable = _executable()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '@echo off\r\n'
        f'start "" "{executable}"\r\n',
        encoding="utf-8",
    )
