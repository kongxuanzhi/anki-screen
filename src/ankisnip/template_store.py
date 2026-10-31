"""笔记类型「模板快照」——离线导出 ``.apkg`` 的渲染依赖。

为什么需要它
------------
挖空矩形不是图片的一部分，而是 ``<img kong="...">`` 属性，由**笔记类型的模板**
（正面 HTML 里内联的解密 + 绘制脚本）在复习时渲染出来。

AnkiConnect 直连时卡片写进 Anki 里**已有的**笔记类型，自然能渲染。
但离线 genanki 导出如果自建一个只有 ``{{pic_kong}}`` 的空壳笔记类型，导入后
Anki 会生成一个新的类型（名字冲突时还会自动改名成 ``…sle1_0+``），
没有任何渲染脚本 —— 表现就是"图片在，矩形框不见了"。

因此离线导出必须**复用 Anki 里那个笔记类型的全部定义**：模型 id、字段、
正面/背面模板、CSS。Anki 运行时把它们抓下来存成快照，离线时读快照即可。
快照内容与 Anki 中的完全一致，导入回同一个 Anki 时按 id 命中现有类型，
既不会新建垃圾类型，也不会破坏现有模板。
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QStandardPaths

LOG = logging.getLogger("ankisnip.template_store")

_DIR_NAME = "ankisnip"


def store_dir() -> Path:
    """快照存放目录。

    刻意**不**用 ``AppDataLocation``：Qt 在没有设置 ``applicationName`` 时会把它
    退化成 ``argv[0]``（从 stdin 跑时就是字面量 ``<stdin>``），导致 CLI 与 GUI
    写到两个不同的目录。这里用通用数据目录 + 固定子目录名，路径稳定。
    """
    base = QStandardPaths.writableLocation(QStandardPaths.GenericDataLocation)
    root = Path(base) if base else Path.home() / ".local" / "share"
    path = root / _DIR_NAME / "templates"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_name(model_name: str) -> str:
    slug = re.sub(r"[^\w.\-]+", "_", model_name, flags=re.UNICODE).strip("_")
    return slug or "model"


def snapshot_path(model_name: str) -> Path:
    return store_dir() / f"{_safe_name(model_name)}.json"


def save_snapshot(model_name: str, model_id: int, fields: List[str],
                  templates: Dict[str, Dict[str, str]], css: str) -> Path:
    """写入快照，返回文件路径。"""
    payload: Dict[str, Any] = {
        "model_name": model_name,
        "model_id": int(model_id) if model_id else 0,
        "fields": list(fields),
        "templates": templates,
        "css": css,
        "captured_at": _dt.datetime.now().isoformat(timespec="seconds"),
    }
    path = snapshot_path(model_name)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    LOG.info("模板快照已更新：%s（id=%s, %d 个模板, css %d 字符）",
             path.name, model_id, len(templates), len(css))
    return path


def load_snapshot(model_name: str) -> Optional[Dict[str, Any]]:
    """读取快照；不存在或损坏时返回 ``None``。"""
    path = snapshot_path(model_name)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        LOG.warning("模板快照损坏，忽略：%s（%s）", path, exc)
        return None
    if not data.get("templates"):
        return None
    return data


def capture_from_anki(connect, model_name: str) -> Optional[Dict[str, Any]]:
    """趁 Anki 还开着，把笔记类型的完整定义抓下来存成快照。

    任何一步失败都只记日志、返回 ``None`` —— 抓快照是"顺手做"的事，
    不能因为它失败就让保存卡片失败。
    """
    try:
        model_id = connect.find_model_id(model_name)
        fields = connect.model_field_names(model_name)
        templates = connect.model_templates(model_name)
        css = connect.model_styling(model_name)
    except Exception as exc:  # noqa: BLE001 - 抓快照失败不影响主流程
        LOG.warning("抓取模板快照失败（%s）：%s", model_name, exc)
        return None
    if not templates or not fields:
        LOG.warning("模板快照不完整，跳过：templates=%d fields=%d",
                    len(templates), len(fields))
        return None
    path = save_snapshot(model_name, model_id or 0, fields, templates, css)
    data = load_snapshot(model_name)
    if data is not None:
        data["_path"] = str(path)
    return data
