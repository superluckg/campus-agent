"""Agentic Retrieval 的当前会话上下文来源。"""

from __future__ import annotations

from langchain_core.documents import Document

from app.db.repositories.conversations import ConversationRepository
from app.db.repositories.memory import MemoryRepository
from app.retrieval.bm25 import tokenize_for_bm25


class ConversationMemoryRetriever:
    def __init__(
        self,
        conversations: ConversationRepository,
        memories: MemoryRepository,
    ) -> None:
        self.conversations = conversations
        self.memories = memories

    async def retrieve(
        self,
        query: str,
        conversation_id: str,
        top_k: int,
    ) -> list[Document]:
        query_tokens = set(tokenize_for_bm25(query))
        messages = await self.conversations.list_messages(conversation_id, limit=30)
        candidates = []
        for message in messages:
            tokens = set(tokenize_for_bm25(message.content))
            score = len(query_tokens & tokens) / max(1, len(query_tokens))
            candidates.append((score, message))
        candidates.sort(key=lambda item: (-item[0], item[1].created_at))
        documents = [
            Document(
                id="message:%s" % message.message_id,
                page_content=message.content,
                metadata={
                    "source_type": "conversation_memory",
                    "title": "当前会话历史",
                    "message_id": str(message.message_id),
                    "role": message.role,
                },
            )
            for _, message in candidates[:top_k]
        ]
        summary = await self.memories.latest_summary(conversation_id)
        if summary:
            documents.append(
                Document(
                    id="summary:%s" % conversation_id,
                    page_content=summary,
                    metadata={
                        "source_type": "conversation_summary",
                        "title": "会话长期摘要",
                    },
                )
            )
        return documents[:top_k]
