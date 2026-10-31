#!/usr/bin/env python
"""校验dist 里的 .app 是否真的装进了最新源码。

为什么需要它
------------
PyInstaller 会把 Python 代码打进压缩归档（CArchive + PYZ），肉眼分不清包到底是
新还是旧。**上一次踩到的事故：用户报告的 bug 其实早就在源码里修好了，但他一直在
跑一个 25 分钟前的陈旧 dist**。从那时起约定：改完源码必须重打包，并且用本脚本
做一次字符串级校验。

用法::

    python tools/verify_bundle.py            # 校检 dist/AnkiSnip.app
    python tools/verify_bundle.py 路径.app   # 指定 bundle

退出码 0 = 全部命中；1 = 包是旧的（该白名单字符串没找到）。
"""

from __future__ import annotations



import sys
import tempfile
from pathlib import Path
from types import CodeType

try:
    from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader
except Exception as exc:  # pragma: no cover - 只在没装 PyInstaller 时触发
    print("需要 PyInstaller 才能校验：%s" % exc)
    raise SystemExit(2)


def load_marks():
    """待命中的特征清单。

    注意：只能用**会被编译进字节码**的东西做锚点 —— docstring（字符串常量）、
    函数名、参数名。源码里的注释、完整语句在 .pyc 里根本不存在，拿它们当特征
    串会误判成"陈旧的包"。
    """
    return [
        # （模块, 检查种类, 特征, 说明）
        ("ankisnip.overlay_ext", "docstring", "AnkiSnip 注入：必须把",
         "选区图归一 dpr（否则 Retina 上标注被放大一倍）"),
        ("ankisnip.rasterize", "name", "normalize_image",
         "烧录入口归一化 dpr"),
        ("ankisnip.editor_canvas", "init_varname", "style",
         "画布与工具栏共用 Style（改色/改线宽生效）"),
        ("ankisnip.card_batch", "name", "CardBatch",
         "离线多卡片累积进同一个牌包"),
        ("ankisnip.card_batch", "docstring", "离线卡片批次",
         "批次累积而非一张一个文件"),
        ("ankisnip.toast", "docstring", "覆盖层上的轻量浮层提示",
         "复制后不关界面、就地弹「已复制」"),
        ("ankisnip.app", "name", "_KEEP_OPEN_ACTIONS",
         "复制不关界面、保存才关"),
        ("ankisnip.settings", "name", "batch_mode",
         "设置可选离线牌包归并方式"),
        ("ankisnip.settings", "name", "_migrate_export_scale",
         "导出尺寸默认改回原生像素（旧的 screen 配置要被纠正）"),
        ("ankisnip.mac_focus", "name", "install_resign_on_close",
         "菜单/弹窗关闭即交还前台（事件驱动）"),
        ("ankisnip.mac_focus", "name", "restore_policy",
         "恢复 activationPolicy，消除图标残留高亮"),
        ("ankisnip.mac_focus", "name", "applescript_frontmost",
         "AppKit 三步都失败后用 AppleScript 兜底交还前台"),
        ("ankisnip.mac_focus", "name", "cancel_pending_resign",
         "自己要占前台时作废排队的退让（别把截图界面踹下去）"),
        ("ankisnip.base.app", "name", "setContextMenu",
         "托盘菜单交给系统原生菜单（AppKit 管理焦点 / Esc / 高亮）"),
        ("ankisnip.base.app", "name", "_tray_activation_is_capture",
         "非 macOS 的左键截图路径"),
        ("ankisnip.base.app", "name", "_qt_shortcut_text",
         "菜单项带全局热键的 key equivalent（菜单打开时也能截图）"),
        ("ankisnip.mac_focus", "name", "raise_window_above_menu_bar",
         "截图覆盖层抬到菜单栏之上，可框选菜单栏"),
        ("ankisnip.overlay_ext", "name", "export_pixel_size",
         "导出尺寸策略落到取图出口"),
        ("ankisnip.base.annotations", "name", "HOVER_ALPHA",
         "挖空悬停时降透明度、透出内容"),
        ("ankisnip.editor_canvas", "name", "_update_hover",
         "画布跟踪鼠标悬停的挖空"),
        ("ankisnip.mask_colors", "name", "MASK_COLORS",
         "7 色调色板对齐图片挖空模板"),
        ("ankisnip.editor_toolbar", "name", "_build_context",
         "两行上下文工具条（7 色 / 线宽 / 字号）"),
        ("ankisnip.hotkey", "name", "GetEventDispatcherTarget",
         "Carbon 处理器同时装在 dispatcher 目标上"),
        ("ankisnip.mac_focus", "name", "menu_bar_owning_pid",
         "交接目标缺失时退到菜单栏归属应用"),
    ]


def collect(code) -> tuple:
    """递归收集一个 code 对象里的字符串常量、名称、变量名。"""
    strings: list = []
    names: set = set()
    varnames: set = set()
    initials: set = set()

    def _walk(node) -> None:
        for const in node.co_consts:
            if isinstance(const, CodeType):
                _walk(const)
            elif isinstance(const, str):
                strings.append(const)
        names.update(node.co_names)
        varnames.update(node.co_varnames)
        initials.update(node.co_varnames[:node.co_argcount])

    _walk(code)
    return strings, names, varnames, initials


def hit(strings, names, varnames, initials, kind: str, marker: str) -> bool:
    if kind == "docstring":
        return any(marker in s for s in strings)
    if kind == "name":
        return marker in names
    if kind == "init_varname":
        return marker in initials
    return False


def extract_pyz(app_path: Path) -> Path:
    """把 .app 里嵌入的 PYZ 归档解出来，返回临时文件路径。"""
    exe = app_path / "Contents" / "MacOS" / "AnkiSnip"
    if not exe.is_file():  # Windows 形态
        candidates = list(app_path.parent.glob("*.exe"))
        exe = candidates[0] if candidates else app_path
    reader = CArchiveReader(str(exe))
    entry = next(name for name in reader.toc if name.startswith("PYZ"))
    tmp = Path(tempfile.mkdtemp()) / entry
    tmp.write_bytes(reader.extract(entry))
    return tmp


def read_module(pyz_path: Path, module: str):
    reader = ZlibArchiveReader(str(pyz_path), check_pymagic=False)
    try:
        return reader.extract(module)
    except KeyError:
        return None


def main(argv) -> int:
    app = Path(argv[1]) if len(argv) > 1 else Path("dist/AnkiSnip.app")
    if not app.exists():
        print("找不到应用：%s" % app)
        return 1

    pyz = extract_pyz(app)
    print("校验 %s" % app)
    ok = True
    for module, kind, marker, desc in load_marks():
        code = read_module(pyz, module)
        if code is None:
            print("  ✗ %s 模块缺失" % module)
            ok = False
            continue
        found = hit(*collect(code), kind=kind, marker=marker)
        print("  %s %-24s %s" % ("✓" if found else "✗", module, desc))
        ok = ok and found
    print("结论：%s" % ("包是新的" if ok else "陈旧！请重新打包"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
