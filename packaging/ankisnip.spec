# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（Windows / macOS 通用）。

用法：
    pyinstaller packaging/ankisnip.spec --noconfirm
产物：
    dist/AnkiSnip.exe            (Windows)
    dist/AnkiSnip.app            (macOS)
"""

import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent            # noqa: F821
SRC = ROOT / "src"
ICON = str(ROOT / "packaging" / "icon.png")

IS_MAC = sys.platform == "darwin"

# pycryptodome 的底层 C 模块是动态导入的，PyInstaller 扫不到，必须显式声明。
HIDDEN_IMPORTS = [
    "Crypto",
    "Crypto.Cipher",
    "Crypto.Cipher._raw_ecb",
    "Crypto.Util",
    "Crypto.Util._raw_api",
    "Crypto.Random",
    "genanki",
    "pynput",
    "pynput.keyboard",
    "pynput.mouse",
]

EXCLUDES = [
    "tkinter",
    "unittest",
    "pydoc",
    "doctest",
    "pytest",
    "PySide6.QtNetwork",        # 不用网络模块（AnkiConnect 走 urllib）
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtMultimedia",
    "PySide6.Qt3DCore",
]

block_cipher = None

a = Analysis(                                       # noqa: F821
    [str(ROOT / "packaging" / "ankisnip_main.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=[],
    hiddenimports=HIDDEN_IMPORTS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)   # noqa: F821

if IS_MAC:
    # macOS 下必须用 onedir：.app bundle 本身已经是一个目录，
    # 再套 onefile 会被 macOS 安全机制嫌弃（PyInstaller v7 起直接报错）。
    exe = EXE(                                      # noqa: F821
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="AnkiSnip",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        runtime_tmpdir=None,
        console=False,
        disable_windowed_traceback=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )
    app = BUNDLE(                                   # noqa: F821
        exe,
        a.binaries,
        a.zipfiles,
        a.datas,
        name="AnkiSnip.app",
        icon=ICON,
        bundle_identifier="com.ankisnip.app",
        info_plist={
            "NSHighResolutionCapable": True,
            "NSScreenCaptureDescription": "需要屏幕录制权限才能截取屏幕内容。",
            "NSAppleEventsUsageDescription": "用于打开系统设置中的隐私面板。",
            "LSUIElement": True,             # 不出现在 Dock（纯托盘常驻）
        },
    )
else:
    exe = EXE(                                      # noqa: F821
        pyz,
        a.scripts,
        a.binaries,
        a.zipfiles,
        a.datas,
        [],
        name="AnkiSnip",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        upx_exclude=[],
        runtime_tmpdir=None,
        console=False,                 # 常驻托盘软件：不要控制台窗口
        disable_windowed_traceback=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=ICON,
    )
