"""设置对话框：热键、牌组、笔记类型、字段映射、AnkiConnect 端口、离线目录。"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from .anki_connect import AnkiConnect, AnkiConnectError
from .card_batch import MODE_DAILY, MODE_PER_NOTE, MODE_SINGLE
from .hotkey_capture import HotkeyEdit
from .settings import EXPORT_SCALE_NATIVE, EXPORT_SCALE_SCREEN, Settings

#: 离线牌包归并方式：下拉文案 → 内部值
_BATCH_MODES = [
    ("按天累积：AnkiSnip-日期.apkg（推荐）", MODE_DAILY),
    ("单一累积：所有卡片永远进同一个 AnkiSnip.apkg", MODE_SINGLE),
    ("每张一个文件（旧行为）", MODE_PER_NOTE),
]

#: 导出图片尺寸：下拉文案 → 内部值
#: 默认「原生像素」：屏幕点口径等于把 Retina 的真实采样丢掉一半，
#: 之后在 Anki / 预览里又会被放大回来看 —— 那就是"糊"的来源。
_EXPORT_SCALES = [
    ("原生像素（推荐）：Retina 上长宽各 2 倍，最清晰", EXPORT_SCALE_NATIVE),
    ("与屏幕一致：导出图和框选范围一样大，体积小一半但偏软", EXPORT_SCALE_SCREEN),
]


class SettingsDialog(QDialog):
    """编辑 :class:`~ankisnip.settings.Settings`。"""

    def __init__(self, settings: Settings, parent=None, pause_hotkey=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("AnkiSnip 设置")
        self.setMinimumWidth(520)

        form = QFormLayout()
        self.edits: dict[str, QLineEdit] = {}

        def row(key: str, title: str, value: str, tip: str = "") -> None:
            edit = QLineEdit(value)
            if tip:
                edit.setToolTip(tip)
            form.addRow(QLabel(title), edit)
            self.edits[key] = edit

        # 热键：按下即生成，不手输记法。
        self.hotkey_edit = HotkeyEdit(settings.hotkey, self,
                                      pause_hotkey=pause_hotkey)
        self.hotkey_edit.setToolTip("点击「录制…」后直接按下想要的组合键")
        form.addRow(QLabel("全局快捷键"), self.hotkey_edit)

        # -- 牌组：Anki 开着时直接下拉选，不用手敲（子牌组带 :: 很容易敲错）。
        deck_row = QHBoxLayout()
        self.deck_combo = QComboBox(self)
        self.deck_combo.setEditable(True)
        self.deck_combo.setMinimumWidth(320)
        self.deck_combo.addItems([settings.deck_name])
        self.deck_combo.setCurrentText(settings.deck_name)
        self.deck_combo.setToolTip(
            "点右侧「刷新」读取 Anki 里的牌组列表；也可以直接手输一个新的牌组名，"
            "保存时会自动创建。")
        deck_row.addWidget(self.deck_combo, 1)
        refresh = QPushButton("刷新")
        refresh.setToolTip("从 AnkiConnect 读取牌组列表（需要 Anki 正在运行）")
        refresh.clicked.connect(self._refresh_decks)
        deck_row.addWidget(refresh)
        form.addRow(QLabel("保存到的牌组"), self._layout_widget(deck_row))
        self.edits["deck_name"] = self.deck_combo.lineEdit()

        row("model_name", "笔记类型名", settings.model_name)
        row("image_field", "图片字段", settings.image_field,
            "字段内容形如 <img src=\"x.png\" kong=\"...\">")
        row("kong_field", "挖空字段（kong）", settings.kong_field,
            "现有模板里与图片字段是同一个（pic_kong）")
        row("question_field", "题面字段", settings.question_field)
        row("relation_field", "关联字段", settings.relation_field)
        row("field_order", "字段顺序（逗号分隔）", ",".join(settings.field_order),
            "仅离线导出 .apkg 时需要与模板一致")
        row("anki_url", "AnkiConnect 地址", settings.anki_url)
        row("tags", "标签（逗号分隔）", ",".join(settings.tags))

        dir_row = QHBoxLayout()
        self.dir_edit = QLineEdit(settings.offline_dir)
        dir_row.addWidget(self.dir_edit, 1)
        pick = QPushButton("选择…")
        pick.clicked.connect(self._pick_dir)
        dir_row.addWidget(pick)
        form.addRow(QLabel("离线导出目录"), self._layout_widget(dir_row))

        self.batch_combo = QComboBox(self)
        for label, value in _BATCH_MODES:
            self.batch_combo.addItem(label, value)
        current_mode = settings.batch_mode
        index = next((i for i, (_, v) in enumerate(_BATCH_MODES) if v == current_mode), 0)
        self.batch_combo.setCurrentIndex(index)
        self.batch_combo.setToolTip(
            "决定离线保存时卡片怎么攒在同一个 .apkg 里。\n"
            "牌组始终是同一个（上面那个），这里只决定文件怎么切分。")
        form.addRow(QLabel("离线牌包归并方式"), self.batch_combo)

        self.export_combo = QComboBox(self)
        for label, value in _EXPORT_SCALES:
            self.export_combo.addItem(label, value)
        current_scale = settings.export_scale
        self.export_combo.setCurrentIndex(
            next((i for i, (_, v) in enumerate(_EXPORT_SCALES) if v == current_scale), 0))
        self.export_combo.setToolTip(
            "Retina 屏幕抓取到的是原生像素（比看到的密一倍）。\n"
            "默认按屏幕点导出：图片大小和框选范围一致，文件也更小；\n"
            "选「原生像素」则保留全部细节，代价是长宽各 2 倍。")
        form.addRow(QLabel("导出图片尺寸"), self.export_combo)

        # 模板快照：离线 .apkg 能不能显示挖空，全看它是否存在。
        snap_row = QHBoxLayout()
        self.snapshot_label = QLabel("")
        self.snapshot_label.setWordWrap(True)
        snap_row.addWidget(self.snapshot_label, 1)
        snap_btn = QPushButton("立即抓取")
        snap_btn.setToolTip("需要 Anki 正在运行且装有 AnkiConnect")
        snap_btn.clicked.connect(self._capture_snapshot)
        snap_row.addWidget(snap_btn)
        form.addRow(QLabel("模板快照"), self._layout_widget(snap_row))
        self._refresh_snapshot_label()

        self.auto_create = QCheckBox("牌组不存在时自动创建")
        self.auto_create.setChecked(True)
        self.auto_create.setToolTip("关闭后，牌组缺失会直接报错提示")
        form.addRow("", self.auto_create)

        buttons = QHBoxLayout()
        self.test_btn = QPushButton("测试 AnkiConnect")
        self.test_btn.clicked.connect(self._test_connection)
        buttons.addWidget(self.test_btn)
        buttons.addStretch(1)
        ok = QPushButton("确定")
        ok.clicked.connect(self.accept)
        buttons.addWidget(ok)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)

        outer = QVBoxLayout(self)
        outer.addLayout(form)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        outer.addWidget(self.status)
        outer.addLayout(buttons)

    # -- 辅助 -------------------------------------------------------------- #
    def _layout_widget(self, layout: QHBoxLayout):
        from PySide6.QtWidgets import QWidget

        w = QWidget(self)
        w.setLayout(layout)
        return w

    def _pick_dir(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择离线导出目录",
                                                     self.dir_edit.text())
        if directory:
            self.dir_edit.setText(directory)

    def _refresh_snapshot_label(self) -> None:
        from . import template_store

        snap = template_store.load_snapshot(self.settings.model_name)
        if snap is None:
            self.snapshot_label.setText(
                "⚠️ 还没有「%s」的模板快照。\n没有快照时导出的 .apkg 不会显示挖空。"
                % self.settings.model_name)
            return
        when = str(snap.get("captured_at", "未知时间"))
        self.snapshot_label.setText(
            f"✓ 已就绪（{when} 抓取）\n离线导出的 .apkg 会用它，导入后挖空可正常显示。")

    def _capture_snapshot(self) -> None:
        from . import template_store

        client = AnkiConnect(self.settings.anki_url)
        snap = template_store.capture_from_anki(client, self.settings.model_name)
        if snap is None:
            self.status.setText("✗ 抓取失败：请确认 Anki 正在运行、AnkiConnect 已安装，"
                                "且笔记类型名填写正确。")
        else:
            self.status.setText(f"✓ 模板快照已更新（笔记类型 id {snap.get('model_id')}）")
        self._refresh_snapshot_label()

    def _refresh_decks(self) -> None:
        """从 Anki 拉取牌组列表填进下拉框（离线时保留手写）。"""
        client = AnkiConnect(self.edits["anki_url"].text().strip())
        try:
            decks = client.deck_names()
        except AnkiConnectError as exc:
            self.status.setText(f"✗ 读取牌组失败：{exc}")
            return
        if not decks:
            self.status.setText("Anki 里还没有牌组，可以新建一个 —— 直接在上面的框里敲名字。")
            return
        # 保留用户可能已经手输但 Anki 里还没有的名字，别把它抹掉。
        current = self.deck_combo.currentText().strip()
        names = sorted(decks)
        if current and current not in names:
            names.insert(0, current)
        self.deck_combo.blockSignals(True)
        self.deck_combo.clear()
        self.deck_combo.addItems(names)
        self.deck_combo.setCurrentText(current if current else names[0])
        self.deck_combo.blockSignals(False)
        self.status.setText(f"✓ 已读取 {len(decks)} 个牌组；当前牌组："
                            f"{self.deck_combo.currentText()}")

    def _test_connection(self) -> None:
        client = AnkiConnect(self.edits["anki_url"].text().strip())
        try:
            version = client.version()
            decks = client.deck_names()
        except AnkiConnectError as exc:
            self.status.setText(f"✗ {exc}")
            return
        self.status.setText(f"✓ AnkiConnect v{version} 可用；牌组 {len(decks)} 个："
                            f" {', '.join(decks[:8])}")

    # -- 取值 -------------------------------------------------------------- #
    def apply(self) -> None:
        s = self.settings
        s.hotkey = self.hotkey_edit.spec()
        s.deck_name = self.edits["deck_name"].text().strip()
        s.model_name = self.edits["model_name"].text().strip()
        s.image_field = self.edits["image_field"].text().strip()
        s.kong_field = self.edits["kong_field"].text().strip()
        s.question_field = self.edits["question_field"].text().strip()
        s.relation_field = self.edits["relation_field"].text().strip()
        s.field_order = [f.strip() for f in self.edits["field_order"].text().split(",")
                         if f.strip()]
        s.anki_url = self.edits["anki_url"].text().strip()
        s.tags = [t.strip() for t in self.edits["tags"].text().split(",") if t.strip()]
        s.offline_dir = self.dir_edit.text().strip()
        s.batch_mode = self.batch_combo.currentData() or MODE_DAILY
        s.export_scale = self.export_combo.currentData() or EXPORT_SCALE_NATIVE
