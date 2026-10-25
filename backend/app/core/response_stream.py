"""请求级正文观察器与结构化工具参数的增量提取。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from langchain_core.messages import AIMessageChunk
from langchain_core.utils.json import parse_partial_json


class ResponseObserver(Protocol):
    async def update(self, content: str) -> None: ...


@dataclass(frozen=True)
class ChatRunContext:
    observer: ResponseObserver | None = None


class StructuredTextStream:
    """按模型消息与工具 index 累积，只提取指定最终结构的正文字段。"""

    def __init__(self, schema_name: str, text_field: str) -> None:
        self.schema_name = schema_name
        self.text_field = text_field
        self.calls: dict[tuple[str, int], dict[str, str]] = {}

    def feed(self, message: Any) -> str | None:
        if not isinstance(message, AIMessageChunk):
            return None
        text = None
        for chunk in message.tool_call_chunks:
            key = (message.id or "", chunk.get("index") or 0)
            call = self.calls.setdefault(key, {"name": "", "args": ""})
            name = chunk.get("name")
            if name and name != call["name"]:
                call["name"] += name
            call["args"] += chunk.get("args") or ""
            if call["name"] != self.schema_name:
                continue
            try:
                parsed = parse_partial_json(call["args"])
            except (ValueError, TypeError):
                continue
            if isinstance(parsed, dict) and isinstance(parsed.get(self.text_field), str):
                value = parsed[self.text_field]
                # 半个 JSON Unicode 代理对不能传给 UTF-8/Redis；等待下一片补齐。
                if value and 0xD800 <= ord(value[-1]) <= 0xDBFF:
                    value = value[:-1]
                if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
                    raise ValueError("结构化正文包含无效 Unicode。")
                text = value
        return text
