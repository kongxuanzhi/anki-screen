"""设置对话框。

分两页：

* 「模板与保存」：当前模板、导入 .apkg / 从 Anki 选择模板、挖空图片写入字段、
  标签、保存到的牌组、牌组目录、离线牌组归并方式、导出图片尺寸、AnkiConnect 地址；
* 「高级」：常用设置（启用全局热键 / 开机自启动）。

全局快捷键的录制不在这里，改由托盘菜单「快捷键设置…」单独弹窗。
"""

from __future__ import annotations

import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .anki_connect import AnkiConnect, AnkiConnectError
from .card_batch import MODE_DAILY, MODE_PER_NOTE, MODE_SINGLE
from .settings import (EXPORT_SCALE_NATIVE, EXPORT_SCALE_SCREEN, Settings)

#: 离线牌包归并方式：下拉文案 → 内部值
_BATCH_MODES = [
    ("按天累积：AnkiSnip-日期.apkg（推荐）", MODE_DAILY),
    ("单一累积：所有卡片永远进同一个 AnkiSnip.apkg", MODE_SINGLE),
    ("每张一个文件（旧行为）", MODE_PER_NOTE),
]

#: 导出图片尺寸：下拉文案 → 内部值
_EXPORT_SCALES = [
    ("原生像素（推荐）：Retina 上长宽各 2 倍，最清晰", EXPORT_SCALE_NATIVE),
    ("与屏幕一致：导出图和框选范围一样大，体积小一半但偏软", EXPORT_SCALE_SCREEN),
]


class SettingsDialog(QDialog):
    """编辑 :class:`~ankisnip.settings.Settings`。"""

    def __init__(self, settings: Settings, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("AnkiSnip 设置")
        self.setMinimumWidth(580)

        self.edits: dict[str, QLineEdit] = {}
        self._template_fields: list[str] = []
        self.tabs = QTabWidget(self)

        self.tabs.addTab(self._build_common_tab(), "模板与保存")
        self.tabs.addTab(self._build_advanced_tab(), "高级")

        self.status = QLabel("")
        self.status.setWordWrap(True)

        buttons = QHBoxLayout()
        self.test_btn = QPushButton("测试 AnkiConnect")
        self.test_btn.clicked.connect(self._test_connection)
        buttons.addWidget(self.test_btn)
        buttons.addStretch(1)
        done = QPushButton("完成")
        done.clicked.connect(self._finish)
        buttons.addWidget(done)

        outer = QVBoxLayout(self)
        outer.addWidget(self.tabs)
        outer.addWidget(self.status)
        outer.addLayout(buttons)

        self._refresh_template_labels()

        # 选择即保存：不在弹窗里做确定 / 取消。
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(250)
        self._save_timer.timeout.connect(self.apply)
        self.hotkey_enabled.stateChanged.connect(self._schedule_save)
        self.launch_at_login.stateChanged.connect(self._schedule_save)
        self.field_combo.currentIndexChanged.connect(self._schedule_save)
        self.batch_combo.currentIndexChanged.connect(self._schedule_save)
        self.export_combo.currentIndexChanged.connect(self._schedule_save)
        self.deck_combo.currentTextChanged.connect(self._schedule_save)
        self.edits["tags"].editingFinished.connect(self._schedule_save)
        self.edits["anki_url"].editingFinished.connect(self._schedule_save)
        self.dir_edit.editingFinished.connect(self._schedule_save)

    # -- 页面 -------------------------------------------------------------- #
    def _make_grid(self, parent) -> QGridLayout:
        grid = QGridLayout(parent)
        grid.setColumnMinimumWidth(0, 120)
        grid.setColumnStretch(1, 1)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(8)
        grid.setContentsMargins(0, 0, 0, 0)
        return grid

    def _grid_row(self, grid: QGridLayout, row: int, title: str,
                  widget: QWidget, top: bool = False) -> QWidget:
        label = QLabel(title)
        label.setAlignment(Qt.AlignRight | (Qt.AlignTop if top else Qt.AlignVCenter))
        grid.addWidget(label, row, 0)
        grid.addWidget(widget, row, 1)
        return widget

    def _build_common_tab(self) -> QWidget:
        page = QWidget(self)
        grid = self._make_grid(page)
        s = self.settings

        self.template_label = QLabel("")
        self.template_label.setWordWrap(True)
        self._grid_row(grid, 0, "当前模板", self.template_label, top=True)

        tpl_row = QHBoxLayout()
        import_btn = QPushButton("导入 .apkg 模板…")
        import_btn.setToolTip("从 Anki 牌包里提取笔记类型；同时会尝试导入 Anki")
        import_btn.clicked.connect(self._import_apkg)
        tpl_row.addWidget(import_btn)
        pick_btn = QPushButton("从 Anki 选择模板…")
        pick_btn.setToolTip("列出 Anki 里现有的笔记类型，选一个并抓取模板")
        pick_btn.clicked.connect(self._pick_anki_template)
        tpl_row.addWidget(pick_btn)
        tpl_row.addStretch(1)
        self._grid_row(grid, 1, "模板操作", self._layout_widget(tpl_row))

        self.field_combo = QComboBox(self)
        self.field_combo.setMinimumWidth(260)
        self.field_combo.setToolTip(
            "模板里用来保存挖空图片 <img ... kong=...> 的字段；"
            "默认按模板自动识别（通常是 pic_kong）。")
        self._grid_row(grid, 2, "挖空图片写入字段", self.field_combo)

        tags_edit = QLineEdit(",".join(s.tags))
        self.edits["tags"] = tags_edit
        self._grid_row(grid, 3, "标签（逗号分隔）", tags_edit)

        deck_row = QHBoxLayout()
        self.deck_combo = QComboBox(self)
        self.deck_combo.setEditable(True)
        self.deck_combo.setMinimumWidth(260)
        self.deck_combo.addItems([s.deck_name])
        self.deck_combo.setCurrentText(s.deck_name)
        self.deck_combo.setToolTip(
            "点右侧「从Anki刷新」读取 Anki 里的牌组列表；也可以直接手输一个新名字，"
            "保存时会自动创建。")
        deck_row.addWidget(self.deck_combo, 1)
        refresh = QPushButton("从Anki刷新")
        refresh.setToolTip("从 AnkiConnect 读取牌组列表（需要 Anki 正在运行）")
        refresh.clicked.connect(self._refresh_decks)
        deck_row.addWidget(refresh)
        self._grid_row(grid, 4, "保存到的牌组", self._layout_widget(deck_row))
        self.edits["deck_name"] = self.deck_combo.lineEdit()

        dir_row = QHBoxLayout()
        self.dir_edit = QLineEdit(s.offline_dir)
        self.dir_edit.setToolTip(
            "离线牌组（.apkg）和卡片图片的保存目录；和托盘的「打开牌组目录」是同一个位置。")
        dir_row.addWidget(self.dir_edit, 1)
        pick = QPushButton("选择…")
        pick.clicked.connect(self._pick_dir)
        dir_row.addWidget(pick)
        self._grid_row(grid, 5, "牌组目录（离线牌组）", self._layout_widget(dir_row))

        self.batch_combo = QComboBox(self)
        for label, value in _BATCH_MODES:
            self.batch_combo.addItem(label, value)
        self.batch_combo.setCurrentIndex(
            next((i for i, (_, v) in enumerate(_BATCH_MODES)
                  if v == s.batch_mode), 0))
        self.batch_combo.setToolTip(
            "决定离线保存时卡片怎么攒在同一个 .apkg 里。牌组始终是同一个，"
            "这里只决定文件怎么切分。")
        self._grid_row(grid, 6, "离线牌组归并方式", self.batch_combo)

        self.export_combo = QComboBox(self)
        for label, value in _EXPORT_SCALES:
            self.export_combo.addItem(label, value)
        self.export_combo.setCurrentIndex(
            next((i for i, (_, v) in enumerate(_EXPORT_SCALES)
                  if v == s.export_scale), 0))
        self.export_combo.setToolTip(
            "Retina 屏幕抓取到的是原生像素（比看到的密一倍）。\n"
            "默认按屏幕点导出：图片大小和框选范围一致，文件也更小；\n"
            "选「原生像素」则保留全部细节，代价是长宽各 2 倍。")
        self._grid_row(grid, 7, "导出图片尺寸", self.export_combo)

        url_edit = QLineEdit(s.anki_url)
        self.edits["anki_url"] = url_edit
        self._grid_row(grid, 8, "AnkiConnect 地址", url_edit)
        grid.setRowStretch(9, 1)
        return page

    def _build_advanced_tab(self) -> QWidget:
        page = QWidget(self)
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)

        group = QGroupBox("常用设置", page)
        grid = self._make_grid(group)

        self.hotkey_enabled = QCheckBox("启用", group)
        self.hotkey_enabled.setChecked(self.settings.hotkey_enabled)
        self.hotkey_enabled.setToolTip("关闭后不再注册全局快捷键，托盘菜单仍可截图")
        self._grid_row(grid, 0, "全局热键", self.hotkey_enabled)

        self.launch_at_login = QCheckBox("启用", group)
        self.launch_at_login.setChecked(self.settings.launch_at_login)
        if sys.platform not in ("darwin", "win32"):
            self.launch_at_login.setEnabled(False)
            self.launch_at_login.setToolTip("当前平台暂不支持")
        self._grid_row(grid, 1, "开机自启动", self.launch_at_login)
        grid.setRowStretch(2, 1)

        outer.addWidget(group)
        outer.addStretch(1)
        return page

    # -- 辅助 -------------------------------------------------------------- #
    def _schedule_save(self, *_args) -> None:
        self._save_timer.start()

    def _finish(self) -> None:
        self._save_timer.stop()
        self.apply()
        self.accept()

    def closeEvent(self, event) -> None:  # noqa: N802
        self._save_timer.stop()
        self.apply()
        super().closeEvent(event)

    def _layout_widget(self, layout: QHBoxLayout) -> QWidget:
        w = QWidget(self)
        w.setLayout(layout)
        return w

    def _pick_dir(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择牌组目录",
                                                     self.dir_edit.text())
        if directory:
            self.dir_edit.setText(directory)

    def _refresh_template_labels(self) -> None:
        from . import template_store

        name = self.settings.model_name
        snap = template_store.load_snapshot(name)
        fields = list((snap or {}).get("fields") or self.settings.field_order or [])
        if not fields and self.settings.image_field:
            fields = [self.settings.image_field]
        self._template_fields = fields

        self.field_combo.blockSignals(True)
        self.field_combo.clear()
        self.field_combo.addItems(fields)
        selected = self.settings.image_field or (fields[0] if fields else "")
        if selected not in fields and fields:
            selected = fields[0]
        self.field_combo.setCurrentText(selected)
        self.field_combo.blockSignals(False)

        if snap is None:
            # 用户不关心快照状态，只显示当前模板名。
            self.template_label.setText(name)
            return
        self.template_label.setText(
            f"{name}\n字段：{', '.join(snap.get('fields') or [])}")

    def _apply_mapping(self, mapping: dict) -> None:
        s = self.settings
        s.model_name = str(mapping.get("model_name") or s.model_name)
        s.image_field = str(mapping.get("image_field") or s.image_field)
        s.kong_field = str(mapping.get("kong_field") or s.image_field)
        s.question_field = str(mapping.get("question_field") or "")
        s.relation_field = str(mapping.get("relation_field") or "")
        s.field_order = list(mapping.get("field_order") or s.field_order)
        self._refresh_template_labels()

    def _anki_failed(self, title: str) -> None:
        """连接 AnkiConnect 失败的统一反馈：状态栏一句话 + 简洁弹框。

        不贴原始异常：底层错误（连接拒绝 / 响应格式等）对用户没有意义。
        """
        self.status.setText("✗ 无法连接 AnkiConnect。")
        QMessageBox.warning(
            self, title,
            "无法连接 AnkiConnect。\n\n"
            "请确认 Anki 正在运行、已安装插件 AnkiConnect（代码 2055492159），"
            "且上面的地址正确。")

    def _pick_anki_template(self) -> None:
        """从 Anki 现有的笔记类型里直接选一个模板。

        和「从Anki刷新」同一控制流：结果一律弹框展示，状态栏只留一句话。
        """
        client = AnkiConnect(self.edits["anki_url"].text().strip())
        try:
            models = client.model_names()
        except AnkiConnectError:
            self._anki_failed("从 Anki 选择模板")
            return
        if not models:
            self.status.setText("Anki 里还没有笔记类型。")
            QMessageBox.information(self, "从 Anki 选择模板",
                                    "Anki 里还没有笔记类型。")
            return
        current = self.settings.model_name
        index = models.index(current) if current in models else 0
        name, accepted = QInputDialog.getItem(
            self, "从 Anki 选择模板", "Anki 里现有的笔记类型：",
            models, index, False)
        if not accepted:
            return
        self.settings.model_name = name
        self._capture_snapshot()

    def _capture_snapshot(self) -> None:
        from . import apkg_template, template_store

        name = self.settings.model_name
        if not name:
            self.status.setText("✗ 还没有模板：先导入 .apkg 或从 Anki 选择。")
            QMessageBox.warning(self, "从 Anki 选择模板",
                                "还没有模板：先导入 .apkg 或从 Anki 选择。")
            return
        client = AnkiConnect(self.edits["anki_url"].text().strip())
        snap = template_store.capture_from_anki(client, name)
        if snap is None:
            self._anki_failed("从 Anki 选择模板")
            return
        model = apkg_template.ApkgModel(
            model_id=int(snap.get("model_id") or 0),
            name=name,
            fields=list(snap.get("fields") or []),
            templates=dict(snap.get("templates") or {}),
            css=str(snap.get("css") or ""),
        )
        self._apply_mapping(apkg_template.field_mapping(model))
        self.status.setText(f"✓ 已抓取模板「{name}」。")
        QMessageBox.information(self, "从 Anki 选择模板",
                                f"已抓取模板「{name}」。")

    def _import_apkg(self) -> None:
        from . import apkg_template, template_store

        path, _ = QFileDialog.getOpenFileName(
            self, "选择 Anki 牌包（.apkg）", self.settings.offline_dir,
            "Anki 牌包 (*.apkg)")
        if not path:
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            try:
                models = apkg_template.read_models(path)
            except apkg_template.ApkgTemplateError as exc:
                QMessageBox.warning(self, "导入模板失败", str(exc))
                return

            model = models[0]
            if len(models) > 1:
                names = [m.name for m in models]
                current = names.index(self.settings.model_name) \
                    if self.settings.model_name in names else 0
                name, accepted = QInputDialog.getItem(
                    self, "选择笔记类型", "这个 .apkg 里有多个笔记类型：",
                    names, current, False)
                if not accepted:
                    return
                model = models[names.index(name)]

            template_store.save_snapshot(
                model.name, model.model_id, model.fields,
                model.templates, model.css)
            self._apply_mapping(apkg_template.field_mapping(model))

            # 用户选择「同时导入 Anki」：把整个 .apkg 交给 AnkiConnect。
            # 注意这会把包内卡片 / 牌组也一起导入。
            client = AnkiConnect(self.edits["anki_url"].text().strip()
                                 if "anki_url" in self.edits else self.settings.anki_url)
            if not client.is_available():
                QMessageBox.information(
                    self, "模板已保存",
                    "模板已保存到本机。\n\n"
                    "Anki 当前未连接，所以没有导入 Anki。"
                    "如果要直连保存，请先在 Anki 中导入这个 .apkg。")
                return
            try:
                client.import_package(path)
            except AnkiConnectError:
                QMessageBox.warning(
                    self, "模板已保存，但导入 Anki 失败",
                    "模板快照已保存，离线导出可以使用。\n\n"
                    "导入 Anki 失败，可以稍后在 Anki 里手动导入这个 .apkg。")
                return
            QMessageBox.information(
                self, "模板已导入",
                f"已提取模板「{model.name}」并保存快照。\n"
                "同时已导入 Anki（包括包内的卡片 / 牌组）。")
        finally:
            QApplication.restoreOverrideCursor()

    def _refresh_decks(self) -> None:
        """从 AnkiConnect 读取牌组列表；成功 / 失败都弹框反馈。"""
        client = AnkiConnect(self.edits["anki_url"].text().strip())
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            decks = client.deck_names()
        except AnkiConnectError:
            decks = None
        finally:
            QApplication.restoreOverrideCursor()

        if decks is None:
            self._anki_failed("从Anki刷新")
            return
        if not decks:
            self.status.setText("已连接 AnkiConnect，但 Anki 里还没有牌组。")
            QMessageBox.information(
                self, "从Anki刷新",
                "已连接 AnkiConnect，但 Anki 里还没有牌组。\n"
                "可以直接在「保存到的牌组」里输入一个新名字，保存时会自动创建。")
            return
        current = self.deck_combo.currentText().strip()
        names = sorted(decks)
        if current and current not in names:
            names.insert(0, current)
        self.deck_combo.blockSignals(True)
        self.deck_combo.clear()
        self.deck_combo.addItems(names)
        self.deck_combo.setCurrentText(current if current else names[0])
        self.deck_combo.blockSignals(False)
        self.status.setText(f"✓ 已从 AnkiConnect 刷新 {len(decks)} 个牌组。")
        QMessageBox.information(
            self, "从Anki刷新",
            f"已从 AnkiConnect 读取 {len(decks)} 个牌组。")

    def _test_connection(self) -> None:
        """测试 AnkiConnect：结果弹框反馈（与「从Anki刷新」同一控制流）。"""
        client = AnkiConnect(self.edits["anki_url"].text().strip())
        try:
            version = client.version()
            decks = client.deck_names()
        except AnkiConnectError:
            self._anki_failed("测试 AnkiConnect")
            return
        self.status.setText(f"✓ AnkiConnect v{version} 可用；牌组 {len(decks)} 个。")
        QMessageBox.information(
            self, "测试 AnkiConnect",
            f"AnkiConnect v{version} 连接正常。\n牌组：{len(decks)} 个。")

    # -- 取值 -------------------------------------------------------------- #
    def apply(self) -> None:
        s = self.settings
        s.deck_name = self.edits["deck_name"].text().strip()
        # 模板 / 其它字段由导入或从 Anki 选择时自动写入；这里只允许用户
        # 选择挖空图片写到哪个字段。
        selected_field = self.field_combo.currentText().strip()
        if selected_field:
            s.image_field = selected_field
            s.kong_field = selected_field
        if getattr(self, "_template_fields", None):
            s.field_order = list(self._template_fields)
        s.anki_url = self.edits["anki_url"].text().strip()
        s.tags = [t.strip() for t in self.edits["tags"].text().split(",") if t.strip()]
        s.offline_dir = self.dir_edit.text().strip()
        s.batch_mode = self.batch_combo.currentData() or MODE_DAILY
        s.export_scale = self.export_combo.currentData() or EXPORT_SCALE_NATIVE

        # 常用设置
        s.hotkey_enabled = self.hotkey_enabled.isChecked()
        s.launch_at_login = self.launch_at_login.isChecked()
