"""无工具的聊天与范围说明：仅给定消息、摘要和公开身份。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.core.model_usage import model_usage
from app.core.response_stream import ResponseObserver
from app.schemas.enums import IntentName


@dataclass
class DirectReply:
    content: str
    usage: dict[str, int]


class DirectReplyService:
    def __init__(self, model, prompt_dir: Path, model_name: str, timeout_seconds: int = 60):
        self.model = model  # 不 bind_tools，也不创建 Agent。
        self.prompts = {
            name: (prompt_dir / f"{name}.md").read_text(encoding="utf-8")
            for name in ("small_talk", "out_of_scope")
        }
        self.model_name = model_name
        self.timeout_seconds = timeout_seconds

    async def reply(self, query: str, intent: IntentName, recent: list[dict], summary: str,
                    policy: str | None = None, *,
                    observer: ResponseObserver | None = None) -> DirectReply:
        mode = "small_talk" if intent == IntentName.SMALL_TALK and policy not in {
            "refuse_sensitive", "refuse_unauthorized"
        } else "out_of_scope"
        system = self.prompts[mode] + f"\n公开模型配置名称：{self.model_name}。"
        if policy in {"refuse_sensitive", "refuse_unauthorized"}:
            system += f"\n本轮决策已明确要求拒绝：{policy}。不要执行或提供绕过方法。"
        messages = [SystemMessage(content=system)]
        if summary:
            messages.append(HumanMessage(content="以下是较早会话摘要，仅作背景：\n" + summary[:12000]))
        for item in recent[-8:]:
            if item.get("role") in {"user", "assistant"}:
                cls = HumanMessage if item["role"] == "user" else AIMessage
                messages.append(cls(content=str(item.get("content", ""))))
        messages.append(HumanMessage(content=query))
        if observer is None:
            response = await asyncio.wait_for(self.model.ainvoke(messages), self.timeout_seconds)
        else:
            response = None
            async with asyncio.timeout(self.timeout_seconds):
                async for chunk in self.model.astream(messages, stream_usage=True):
                    response = chunk if response is None else response + chunk
                    content = response.content
                    if isinstance(content, list):
                        content = "".join(
                            block.get("text", "") for block in content
                            if isinstance(block, dict) and block.get("type") == "text"
                        )
                    if isinstance(content, str):
                        await observer.update(content)
            if response is None:
                raise RuntimeError("聊天模型没有返回有效文本。")
        content = response.content
        if isinstance(content, list):
            content = "".join(
                block.get("text", "") for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            )
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("聊天模型没有返回有效文本。")
        return DirectReply(content.strip(), model_usage(response))
