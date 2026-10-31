"""AnkiConnect HTTP 客户端（仅用标准库 ``urllib``，减少打包体积与依赖）。

接口文档：https://foosoft.net/projects/anki-connect/
默认地址 ``http://127.0.0.1:8765``，可用配置覆盖。
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

ANKICONNECT_VERSION = 6
DEFAULT_URL = "http://127.0.0.1:8765"


class AnkiConnectError(RuntimeError):
    """AnkiConnect 返回的业务错误（连接 / 协议 / 牌组不存在等）。"""


class AnkiConnect:
    """极薄的 AnkiConnect 封装。"""

    def __init__(self, url: str = DEFAULT_URL, timeout: float = 3.0) -> None:
        self.url = url.rstrip("/") or DEFAULT_URL
        self.timeout = timeout

    # -- 底层 ------------------------------------------------------------- #
    def invoke(self, action: str, **params: Any) -> Any:
        payload = json.dumps(
            {"action": action, "version": ANKICONNECT_VERSION, "params": params}
        ).encode("utf-8")
        request = urllib.request.Request(
            self.url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.URLError as exc:
            raise AnkiConnectError(
                f"无法连接 AnkiConnect（{self.url}）。\n"
                "请确认 Anki 已启动，并已安装插件 AnkiConnect（代码 2055492159），"
                "且未修改其监听端口。\n"
                f"底层错误：{exc}"
            ) from exc
        except TimeoutError as exc:
            raise AnkiConnectError(f"AnkiConnect 响应超时（{self.timeout}s）。") from exc

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AnkiConnectError(f"AnkiConnect 返回了非 JSON 内容：{raw[:200]}") from exc

        if len(data) != 2 or "error" not in data or "result" not in data:
            raise AnkiConnectError(f"AnkiConnect 响应格式异常：{str(data)[:200]}")
        if data["error"] is not None:
            raise AnkiConnectError(f"AnkiConnect 返回错误：{data['error']}")
        return data["result"]

    # -- 探测 ------------------------------------------------------------- #
    def is_available(self) -> bool:
        try:
            self.version()
            return True
        except AnkiConnectError:
            return False

    def version(self) -> int:
        return int(self.invoke("version"))

    # -- 查询 ------------------------------------------------------------- #
    def deck_names(self) -> List[str]:
        return list(self.invoke("deckNames") or [])

    def model_names(self) -> List[str]:
        return list(self.invoke("modelNames") or [])

    def model_field_names(self, model_name: str) -> List[str]:
        return list(self.invoke("modelFieldNames", modelName=model_name) or [])

    def find_model_id(self, model_name: str) -> Optional[int]:
        """笔记类型的内部 id（离线导出 apkg 时用它对齐已有类型）。"""
        found = self.invoke("findModelsByName", modelNames=[model_name]) or []
        if not found:
            return None
        first = found[0]
        return int(first["id"]) if isinstance(first, dict) else int(first)

    def model_templates(self, model_name: str) -> Dict[str, Dict[str, str]]:
        """``{模板名: {"Front": ..., "Back": ...}}``。"""
        result = self.invoke("modelTemplates", modelName=model_name) or {}
        # AnkiConnect 可能返回 {model: {tmpl: {...}}} 或 {tmpl: {...}} 两种形状
        if result and all(isinstance(v, dict) and "Front" in v for v in result.values()):
            return result
        for value in result.values():
            if isinstance(value, dict) and any(
                isinstance(v, dict) and "Front" in v for v in value.values()
            ):
                return value
        return {}

    def model_styling(self, model_name: str) -> str:
        result = self.invoke("modelStyling", modelName=model_name) or {}
        if isinstance(result, dict):
            return str(result.get("css", ""))
        return str(result)

    def create_deck(self, deck: str) -> None:
        self.invoke("createDeck", deck=deck)

    # -- 媒体 ------------------------------------------------------------- #
    def store_media_file(self, filename: str, data: bytes) -> str:
        encoded = base64.b64encode(data).decode("ascii")
        return str(self.invoke("storeMediaFile", filename=filename, data=encoded))

    def delete_media_file(self, filename: str) -> None:
        """尽力而为的清理，失败不影响主流程。"""
        try:
            self.invoke("deleteMediaFile", filename=filename)
        except AnkiConnectError:
            pass

    # -- 卡片 ------------------------------------------------------------- #
    def add_note(self, deck: str, model: str, fields: Dict[str, str],
                 tags: Optional[List[str]] = None, allow_duplicate: bool = True) -> int:
        note = {
            "deckName": deck,
            "modelName": model,
            "fields": fields,
            "options": {
                "allowDuplicate": allow_duplicate,
                "duplicateScope": "deck",
                "duplicateScopeOptions": {"deckName": deck, "checkChildren": False},
            },
            "tags": tags or [],
        }
        return int(self.invoke("addNote", note=note))
