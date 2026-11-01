"""应用入口：系统托盘 + 全局热键 + 截图会话编排 + Anki 保存。

托盘与全局热键**复用基座** ``base.app.SnipApp``（pynput + QSystemTrayIcon +
macOS 权限引导），这里只覆写：

* ``overlay_cls`` → :class:`~ankisnip.overlay_ext.AnkiCaptureOverlay`
* ``config_cls``  → :class:`~ankisnip.settings.Settings`
* ``_build_tray`` → 追加「设置」「打开日志」等菜单项
* ``_on_completed`` → 处理新增的 ``anki`` 动作（覆盖层就地标注后直接保存）
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QAction, QDesktopServices, QGuiApplication, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QMessageBox,
    QSystemTrayIcon,
)

from . import logging_setup
from .anki_connect import AnkiConnect, AnkiConnectError
from .base.app import SnipApp, _fallback_icon
from .base import permissions
from .overlay_ext import AnkiCaptureOverlay
from .saver import AnkiSaver
from .settings import Settings
from .settings_dialog import SettingsDialog

LOG = logging.getLogger("ankisnip.app")
_IS_MAC = sys.platform == "darwin"


class AnkiSnipApp(SnipApp):
    """SnipApp 的 Anki 化扩展。"""

    overlay_cls = AnkiCaptureOverlay
    config_cls = Settings

    def __init__(self, app: QApplication) -> None:
        super().__init__(app)
        self._config: Settings
        self._saver = AnkiSaver(self._config)
        self._hotkey_suspend_depth = 0
        self._first_run_check()
        # Anki 开着就把笔记类型的真实模板抓下来，供以后离线导出 .apkg 用。
        # 放到单次定时器里，避免拖慢启动（也避免启动瞬间 Anki 还没起来）。
        from PySide6.QtCore import QTimer

        QTimer.singleShot(1500, lambda: self._saver.ensure_template_snapshot(force=True))

    # -- 托盘 -------------------------------------------------------------- #
    def _build_tray(self) -> None:
        super()._build_tray()
        menu = self._tray_menu
        last = menu.actions()[-1]  # 「退出」之前插入

        # 「牌组目录」两项紧跟「截图」，替代原来的保存目录两项。
        actions = menu.actions()
        after_capture = actions[1] if len(actions) > 1 else None
        hotkey_action = QAction("快捷键设置…", menu)
        hotkey_action.triggered.connect(self._open_hotkey_settings)
        open_deck = QAction("打开牌组目录", menu)
        open_deck.triggered.connect(self._open_offline_dir)
        archive = QAction("归档离线牌包", menu)
        archive.triggered.connect(self._archive_offline_pack)
        choose_deck = QAction("设置牌组目录…", menu)
        choose_deck.triggered.connect(self._choose_offline_dir)
        for action in (hotkey_action, open_deck, archive, choose_deck):
            if after_capture is not None:
                menu.insertAction(after_capture, action)
            else:
                menu.addAction(action)

        for title, slot in [
            ("设置…", self._open_settings),
            ("检查 Anki 连接", self._check_anki),
        ]:
            action = QAction(title, menu)
            action.triggered.connect(slot)
            menu.insertAction(last, action)

        # 基类已经 setContextMenu()；再设一次，确保插入的菜单项进了原生 NSMenu。
        self._tray.setContextMenu(menu)
        self._tray.setToolTip("AnkiSnip — 截图挖空直达 Anki")

    # -- 热键 -------------------------------------------------------------- #
    def _hotkey_suspended(self):
        """返回一个上下文管理器：期间挂起全局热键（录制新热键时用）。

        支持嵌套：设置对话框整体挂起一次，里面的录制窗口再挂起一次，
        只有最外层退出时才恢复注册，避免录制过程中热键被重新激活。
        """
        import contextlib

        @contextlib.contextmanager
        def _suspend():
            manager = getattr(self, "_hotkey_manager", None)
            self._hotkey_suspend_depth += 1
            try:
                if manager is not None and self._hotkey_suspend_depth == 1:
                    manager.stop()
                yield
            finally:
                self._hotkey_suspend_depth -= 1
                if manager is not None and self._hotkey_suspend_depth == 0:
                    if getattr(self._config, "hotkey_enabled", True):
                        manager.restart(self._config.hotkey)
                        self._set_capture_label(with_hotkey=manager.status.ok)
                    else:
                        manager.stop()
                        self._set_capture_label(with_hotkey=False)

        return _suspend()

    def _open_hotkey_settings(self) -> None:
        from .hotkey_dialog import HotkeyDialog

        # 注意：AnkiSnipApp 是 QObject，不是 QWidget，不能当 QDialog 的 parent；
        # 传进去会抛 TypeError，菜单点了就"没反应"。
        dialog = HotkeyDialog(self._config, self.hotkey_status_text())
        # 对话框开着（尤其录制时）不能让旧热键抢按键。
        with self._hotkey_suspended():
            accepted = dialog.exec()
        if not accepted:
            return
        dialog.apply()
        # 用户主动设置快捷键 = 打开全局热键。
        self._config.hotkey_enabled = True
        status = self.reload_hotkey()
        if status is not None and not status.ok:
            self._show_message(
                "快捷键设置",
                "已保存，但注册失败：\n" + status.error,
                QMessageBox.Warning)
        else:
            display = status.display if status is not None else dialog.spec()
            self._show_message("快捷键设置", f"已生效：{display}")

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self._config)
        # 对话框一关就把前台交还回去（事件驱动，见 mac_focus.install_resign_on_close）。
        if _IS_MAC:
            try:
                from .mac_focus import install_resign_on_close

                install_resign_on_close(dialog)
            except Exception:  # pragma: no cover - 平台相关
                LOG.debug("挂载设置对话框的关闭钩子失败", exc_info=True)
        # 设置期间挂起热键：否则在对话框里按下当前快捷键会直接触发截图。
        with self._hotkey_suspended():
            dialog.exec()
        # 没有确定 / 取消：对话框里的改动已经即时保存，关闭后再应用副作用。
        dialog.apply()
        self._saver = AnkiSaver(self._config)
        try:
            from .autostart import set_enabled as set_autostart

            set_autostart(self._config.launch_at_login)
        except Exception as exc:  # noqa: BLE001 - 只影响自启动，不阻塞保存
            LOG.warning("设置开机自启动失败", exc_info=True)
            self._show_message(
                "开机自启动",
                f"设置失败：\n{exc}",
                QMessageBox.Warning)
        self.reload_hotkey()
        # 设置对话框是新开的窗口，关掉后本应用会留在前台，照样把焦点退回去。
        self._resign_frontend()

    # -- 弹窗：关闭即交还前台 ---------------------------------------------- #
    def _make_dialog_hook(self, dialog):
        """给任意对话框挂上「关闭即交还前台」并返回它（便于链式使用）。"""
        if _IS_MAC:
            try:
                from .mac_focus import install_resign_on_close

                install_resign_on_close(dialog)
            except Exception:  # pragma: no cover - 平台相关
                LOG.debug("挂载对话框关闭钩子失败", exc_info=True)
        return dialog

    def _show_message(self, title: str, text: str, icon=QMessageBox.Information,
                      extra_label: str | None = None,
                      extra_slot=None) -> None:
        """托盘菜单动作的明确反馈（不依赖系统通知，通知可能被关掉）。"""
        box = QMessageBox(icon, title, text, QMessageBox.Ok)
        extra = None
        if extra_label:
            extra = box.addButton(extra_label, QMessageBox.ActionRole)
        self._make_dialog_hook(box)
        box.exec()
        if extra is not None and box.clickedButton() is extra and extra_slot:
            extra_slot()

    def _save_pixmap(self, pixmap: "QPixmap") -> None:
        """另存为：用对话框实例（而非静态函数）以便挂上关闭钩子。"""
        from datetime import datetime

        from PySide6.QtWidgets import QFileDialog

        save_dir = self._config.ensure_save_dir()
        default = save_dir / f"Snip_{datetime.now():%Y%m%d_%H%M%S}.png"
        dialog = QFileDialog(None, "保存截图", str(default),
                             "PNG 图片 (*.png);;JPEG 图片 (*.jpg)")
        dialog.setAcceptMode(QFileDialog.AcceptSave)
        dialog.setDefaultSuffix("png")
        self._make_dialog_hook(dialog)
        if not dialog.exec():
            return
        files = dialog.selectedFiles()
        if not files:
            return
        path = files[0]
        pixmap.save(path)
        self._config.save_dir = str(Path(path).parent)
        self._notify(f"已保存到 {path}")

    # -- 离线牌包 ---------------------------------------------------------- #
    def _batch(self):
        from .card_batch import CardBatch

        s = self._config
        return CardBatch(s.offline_dir, deck_name=s.deck_name,
                         model_name=s.model_name, field_names=s.field_names(),
                         mode=s.batch_mode, tags=list(s.tags))

    def _open_offline_dir(self) -> None:
        directory = Path(self._config.offline_dir)
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._show_message(
                "打开牌组目录",
                f"无法打开 / 创建目录：\n{directory}\n\n{exc}",
                QMessageBox.Warning)
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))

    def _choose_offline_dir(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            None, "选择牌组目录", self._config.offline_dir)
        if not directory:
            return
        self._config.offline_dir = directory
        self._show_message(
            "牌组目录已设置",
            f"离线牌包会保存到：\n{directory}")

    def _archive_offline_pack(self) -> None:
        """把当前累积的牌包封口留档，之后重新开始一个新批次。"""
        try:
            batch = self._batch()
            if not batch.apkg_path.is_file():
                self._show_message(
                    "归档离线牌包",
                    "还没有可归档的离线牌包。\n\n"
                    "在 Anki 没运行时「保存到 Anki」的卡片会先攒在：\n"
                    f"{self._config.offline_dir}",
                    QMessageBox.Information,
                    extra_label="打开目录",
                    extra_slot=self._open_offline_dir,
                )
                return
            total = batch.count
            archived = batch.archive()
        except Exception as exc:  # pragma: no cover - IO 异常
            LOG.exception("归档离线牌包失败")
            self._show_message(
                "归档离线牌包", f"归档失败：\n{exc}", QMessageBox.Critical)
            return
        self._show_message(
            "归档离线牌包",
            f"已归档 {total} 张卡片的牌包：\n{archived}\n\n"
            "双击即可导入 Anki；之后保存的卡片会攒进新牌包。",
            QMessageBox.Information,
            extra_label="打开目录",
            extra_slot=lambda: QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(Path(archived).parent))),
        )

    def _check_anki(self) -> None:
        client = AnkiConnect(self._config.anki_url, self._config.anki_timeout)
        try:
            version = client.version()
            decks = client.deck_names()
        except AnkiConnectError as exc:
            LOG.warning("检查 Anki 连接失败", exc_info=True)
            self._show_message(
                "检查 Anki 连接",
                "无法连接 AnkiConnect。\n\n"
                "请确认 Anki 已启动、已安装 AnkiConnect，"
                "并且地址 / 端口正确。",
                QMessageBox.Warning)
            return
        # 顺手刷新模板快照：离线导出 .apkg 时能不能渲染挖空，全靠它。
        ok = self._saver.ensure_template_snapshot(force=True)
        snapshot = "已更新" if ok else "抓取失败（离线导出可能不显示挖空）"
        self._show_message(
            "检查 Anki 连接",
            f"AnkiConnect v{version} 连接正常。\n"
            f"牌组：{len(decks)} 个\n"
            f"模板快照：{snapshot}")

    def start_capture(self) -> None:
        """快照当前 overlay 列表，调用后再重连新增 overlays。"""
        known = {id(o) for o in self._overlays}
        super().start_capture()
        for overlay in self._overlays:
            if id(overlay) in known:
                continue
            # 基座把 cancelled 直接连到 _close_overlays；这里换成会同时
            # "交还前台"的版本 —— 用户按 Esc 取消后不该还占着激活态。
            try:
                overlay.cancelled.disconnect(self._close_overlays)
            except (RuntimeError, TypeError):  # pragma: no cover - 未连接时
                pass
            overlay.cancelled.connect(self._on_overlay_cancelled)

    def _on_overlay_cancelled(self) -> None:
        """取消截图：关掉覆盖层并把前台还给之前的应用。"""
        super()._close_overlays()
        self._resign_frontend()

    def _resign_frontend(self) -> None:
        """macOS：把激活状态交还给截图之前那个应用。

        覆盖层是满屏窗口，弹起时本应用会被推成前台；菜单同理。收不起来会留下
        两个可见的后遗症：托盘图标一直高亮、全局热键失灵。
        """
        if not _IS_MAC:
            return
        try:
            from .mac_focus import resign_activation

            resign_activation()
        except Exception:  # pragma: no cover - 平台相关
            LOG.debug("交还前台失败", exc_info=True)

    # -- 完成动作 ---------------------------------------------------------- #
    #: 哪些动作做完**不**关闭截图界面（还能接着改标注继续操作）。
    _KEEP_OPEN_ACTIONS = frozenset({"copy"})

    def _on_completed(self, action: str, pixmap: QPixmap, origin) -> None:
        if action == "copy":
            # 复制是"中途动作"：不要关掉界面，用户很可能还想接着画 / 再复制一次。
            QGuiApplication.clipboard().setPixmap(pixmap)
            self._toast("已复制到剪贴板")
            return
        # 其余动作都先关界面再干活：尤其是「另存为」要弹文件对话框，
        # 全屏覆盖层压在上面会把对话框挤到不可见。
        image, items = None, []
        if action == "anki":
            image, items = self._collect_annotations()
        self._close_overlays()
        if action == "anki":
            if image is None or image.isNull():
                self._notify("选区为空，已取消。")
                self._resign_frontend()
                return
            self._save_to_anki(image, items)
            return
        super()._on_completed(action, pixmap, origin)
        # 「另存为」由文件对话框自己的 finished 钩子负责让位；「贴图」没有任何
        # 后续弹窗，得显式交还。不能提前：resign 会把模态对话框压到别处后面。
        if action == "pin":
            self._resign_frontend()

    def _collect_annotations(self):
        """标注已就地画在覆盖层里：直接从覆盖层取"原图 + 标注栈"。"""
        for overlay in self._overlays:
            if hasattr(overlay, "annotation_items"):
                return overlay.selection_image(), overlay.annotation_items()
        return None, []

    def _toast(self, text: str, msec: int = 1400) -> None:
        """给还开着的覆盖层发一条浮层提示。"""
        for overlay in self._overlays:
            fn = getattr(overlay, "show_toast", None)
            if callable(fn):
                fn(text, msec)

    # -- 保存 -------------------------------------------------------------- #
    def _save_to_anki(self, image, items) -> None:
        """保存流程用 try/finally 包住，好让结束时一定能把前台交还回去。"""
        try:
            try:
                prepared = self._saver.prepare(image, items)
                result = self._saver.save(prepared, self._config.temp_dir)
            except Exception as exc:  # 兜底：任何异常都要给用户可读反馈
                LOG.exception("保存失败")
                self._notify(f"保存失败：{exc}", icon=QSystemTrayIcon.Warning,
                             timeout=10000)
                box = QMessageBox(QMessageBox.Critical, "保存失败", str(exc),
                                  QMessageBox.Ok)
                self._make_dialog_hook(box)
                box.exec()
                return
            if not result.ok:
                self._notify("保存失败：" + result.message,
                             icon=QSystemTrayIcon.Warning, timeout=10000)
                box = QMessageBox(QMessageBox.Critical, "保存失败", result.message,
                                  QMessageBox.Ok)
                self._make_dialog_hook(box)
                box.exec()
                return
            self._notify(result.message)
            # 离线导出不再自动弹 Finder：牌包现在是累积的同一个文件，每次保存
            # 都弹一个窗口会很吵。要看/要导入可以用托盘菜单「打开牌组目录」。
        finally:
            # 错误对话框已经关掉，此刻可以安全地把前台交还给来处。
            self._resign_frontend()

    # -- 首次运行引导 ------------------------------------------------------ #
    def _first_run_check(self) -> None:
        if self._config.first_run_done:
            return
        messages = []
        if _IS_MAC:
            manager = getattr(self, "_hotkey_manager", None)
            if not getattr(manager, "active", False):
                messages.append(
                    "全局热键未生效：可在托盘菜单「热键状态…」查看原因，"
                    "或在「设置…」里点击「录制…」换一个组合键。")
            if not permissions.screen_capture_allowed():
                messages.append("未授予「屏幕录制」：截图只会拍到壁纸。")
        client = AnkiConnect(self._config.anki_url, self._config.anki_timeout)
        if not client.is_available():
            messages.append(
                "未检测到 AnkiConnect：将自动改用离线 .apkg 导出。\n"
                "如需直连，请在 Anki 中安装插件 AnkiConnect（代码 2055492159）。"
            )
        if messages:
            self._notify("\n".join(messages))
        self._config.mark_first_run_done()

    def _notify(self, message: str, icon=QSystemTrayIcon.Information, timeout: int = 6000) -> None:
        if self._tray.supportsMessages():
            self._tray.showMessage("AnkiSnip", message, icon, timeout)


def _run_focus_selftest(holder: "AnkiSnipApp") -> None:
    """``--focus-selftest``：在真正的 bundle 里量一次"交还前台"是否奏效。

    AppKit 的这类调用只在正式 bundle（LSUIElement）里才有效，脱离 bundle 用
    脚本怎么测都不作数，所以把它做成应用的自检开关。结论写到
    ``~/Library/Application Support/ankisnip/logs/focus-selftest.txt``。
    """
    from PySide6.QtCore import QTimer

    from . import mac_focus

    lines: list[str] = []
    steps = [
        (600, "start", None),
        (1200, "activate", None),
        (1800, "resign", None),
        (3200, "report", None),
        (3600, "quit", None),
    ]

    def at(kind: str) -> None:
        if kind == "start":
            mac_focus.remember_policy()
            lines.append(f"启动策略 policy={mac_focus.activation_policy()} "
                         f"isActive={mac_focus.is_app_active()}")
        elif kind == "activate":
            mac_focus.activate_self()
            lines.append("主动激活后 isActive=%s" % mac_focus.is_app_active())
        elif kind == "resign":
            holder._resign_frontend()
            lines.append("已发起交还前台序列")
        elif kind == "report":
            lines.append("最终 isActive=%s policy=%s" % (mac_focus.is_app_active(),
                                                        mac_focus.activation_policy()))
            try:
                path = logging_setup.log_dir() / "focus-selftest.txt"
                path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                print(f"[focus-selftest] 结果已写入 {path}")
            except Exception:  # pragma: no cover
                print("[focus-selftest]", "\n".join(lines))
        else:
            holder._app.quit()

    for msec, kind, _ in steps:
        QTimer.singleShot(msec, lambda k=kind: at(k))


def main() -> int:
    logging_setup.setup()
    app = QApplication(sys.argv)
    app.setApplicationName("AnkiSnip")
    app.setOrganizationName("ankisnip")
    app.setQuitOnLastWindowClosed(False)

    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.warning(None, "AnkiSnip", "系统托盘不可用，程序可能无法正常运行。")

    holder = AnkiSnipApp(app)  # noqa: F841 - 生命周期与 app 一致
    if "--focus-selftest" in sys.argv:
        _run_focus_selftest(holder)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
