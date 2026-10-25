"""长期记忆召回，以及对话结束后的自动摘要与记忆抽取。"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import BaseModel, ConfigDict, Field

from app.db.repositories.conversations import ConversationRepository
from app.db.repositories.memory import MemoryRepository
from app.schemas.enums import AgentName


class MemoryCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memory_type: str
    content: str
    importance: float = Field(default=0.5, ge=0.0, le=1.0)


class MemoryExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memories: list[MemoryCandidate] = Field(default_factory=list, max_length=3)


class SummaryOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str


class MemoryService:
    def __init__(
        self,
        repository: MemoryRepository,
        conversations: ConversationRepository,
        model: BaseChatModel,
        prompt_dir: Path,
        summarize_after_messages: int = 20,
        keep_recent: int = 8,
    ) -> None:
        self.repository = repository
        self.conversations = conversations
        self.summary_model = model.with_structured_output(
            SummaryOutput,
            method="function_calling",
        )
        self.extractor_model = model.with_structured_output(
            MemoryExtraction,
            method="function_calling",
        )
        self.summary_prompt = (prompt_dir / "conversation_summary.md").read_text(encoding="utf-8")
        self.extractor_prompt = (prompt_dir / "memory_extractor.md").read_text(encoding="utf-8")
        self.summarize_after_messages = summarize_after_messages
        self.keep_recent = keep_recent

    async def recall(
        self,
        user_id: str,
        query: str,
        agent_names: list[AgentName],
        top_k: int,
    ) -> list[str]:
        del agent_names
        items = await self.repository.recall(user_id, query, top_k)
        return [item.content for item in items]

    async def maintain_after_turn(
        self,
        user_id: str,
        conversation_id: str,
        message_ids: Iterable[str],
    ) -> None:
        messages = await self.conversations.list_messages(conversation_id, limit=200)
        if len(messages) >= self.summarize_after_messages:
            covered = messages[:-self.keep_recent]
            if covered:
                transcript = "\n".join("%s: %s" % (item.role, item.content) for item in covered)
                result = await self.summary_model.ainvoke(
                    "%s\n\n待压缩对话：\n%s" % (self.summary_prompt, transcript)
                )
                await self.repository.add_summary(
                    conversation_id=conversation_id,
                    content=result.summary,
                    message_count=len(covered),
                    covered_until_message_id=str(covered[-1].message_id),
                )
        latest = messages[-2:]
        if not latest:
            return
        turn = "\n".join("%s: %s" % (item.role, item.content) for item in latest)
        extraction = await self.extractor_model.ainvoke(
            "%s\n\n本轮对话：\n%s" % (self.extractor_prompt, turn)
        )
        for candidate in extraction.memories:
            await self.repository.create(
                user_id=user_id,
                memory_type=candidate.memory_type,
                content=candidate.content,
                source_conversation_id=conversation_id,
                source_message_ids=list(message_ids),
                importance=candidate.importance,
            )
