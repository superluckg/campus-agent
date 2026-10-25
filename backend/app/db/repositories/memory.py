"""摘要和长期记忆仓库。"""

from __future__ import annotations

from typing import Optional

from langchain_core.embeddings import Embeddings
from sqlalchemy import desc, func, select
from sqlalchemy.dialects.postgresql import insert

from app.db.models import ConversationSummary, LongTermMemory
from app.db.identifiers import as_uuid
from app.db.session import Database


class MemoryRepository:
    def __init__(self, database: Database, embeddings: Embeddings) -> None:
        self.database = database
        self.embeddings = embeddings

    async def latest_summary(self, conversation_id: str) -> str:
        async with self.database.session() as session:
            result = await session.execute(
                select(ConversationSummary)
                .where(ConversationSummary.conversation_id == as_uuid(conversation_id))
            )
            item = result.scalar_one_or_none()
            return item.content if item else ""

    async def add_summary(
        self,
        conversation_id: str,
        content: str,
        message_count: int,
        covered_until_message_id: Optional[str],
    ) -> ConversationSummary:
        async with self.database.session() as session:
            values = {
                "conversation_id": as_uuid(conversation_id),
                "content": content,
                "message_count": message_count,
                "covered_until_message_id": (
                    as_uuid(covered_until_message_id) if covered_until_message_id else None
                ),
            }
            statement = insert(ConversationSummary).values(**values)
            statement = statement.on_conflict_do_update(
                index_elements=[ConversationSummary.conversation_id],
                set_={
                    key: value for key, value in values.items() if key != "conversation_id"
                } | {"updated_at": func.now()},
            ).returning(ConversationSummary)
            item = (await session.execute(statement)).scalar_one()
        return item

    async def recall(self, user_id: str, query: str, top_k: int = 3) -> list[LongTermMemory]:
        vector = await self.embeddings.aembed_query(query)
        distance = LongTermMemory.embedding.cosine_distance(vector)
        statement = (
            select(LongTermMemory)
            .where(LongTermMemory.user_id == as_uuid(user_id))
            .order_by(distance, LongTermMemory.importance.desc())
            .limit(top_k)
        )
        async with self.database.session() as session:
            return list((await session.execute(statement)).scalars())

    async def list_owned(self, user_id: str) -> list[LongTermMemory]:
        async with self.database.session() as session:
            result = await session.execute(
                select(LongTermMemory)
                .where(LongTermMemory.user_id == as_uuid(user_id))
                .order_by(desc(LongTermMemory.updated_at))
            )
            return list(result.scalars())

    async def create(
        self,
        user_id: str,
        memory_type: str,
        content: str,
        source_conversation_id: Optional[str] = None,
        source_message_ids: Optional[list[str]] = None,
        importance: float = 0.5,
    ) -> LongTermMemory:
        normalized_user_id = as_uuid(user_id)
        async with self.database.session() as session:
            existing_result = await session.execute(
                select(LongTermMemory).where(
                    LongTermMemory.user_id == normalized_user_id,
                    LongTermMemory.content == content,
                )
            )
            existing = existing_result.scalar_one_or_none()
            if existing is not None:
                existing.importance = max(existing.importance, importance)
                return existing
            item = LongTermMemory(
                user_id=normalized_user_id,
                memory_type=memory_type,
                content=content,
                embedding=await self.embeddings.aembed_query(content),
                importance=importance,
                source_conversation_id=(
                    as_uuid(source_conversation_id) if source_conversation_id else None
                ),
                source_message_ids=source_message_ids or [],
            )
            session.add(item)
            await session.flush()
        return item

    async def update_owned(self, user_id: str, memory_id: str, content: str) -> bool:
        async with self.database.session() as session:
            item = await session.get(LongTermMemory, as_uuid(memory_id))
            if item is None or str(item.user_id) != user_id:
                return False
            item.content = content
            item.embedding = await self.embeddings.aembed_query(content)
            return True

    async def delete_owned(self, user_id: str, memory_id: str) -> bool:
        async with self.database.session() as session:
            item = await session.get(LongTermMemory, as_uuid(memory_id))
            if item is None or str(item.user_id) != user_id:
                return False
            await session.delete(item)
            return True
