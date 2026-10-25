"""对话与消息持久化。"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable, Optional

from sqlalchemy import desc, func, select, update

from app.db.models import Conversation, Message, MessageCitation
from app.db.identifiers import as_uuid
from app.db.session import Database
from app.schemas.agent import Citation


class ConversationRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def create(self, user_id: str, title: str = "新对话") -> Conversation:
        conversation = Conversation(
            user_id=as_uuid(user_id), title=title[:120], status="active",
            title_status="pending" if title == "新对话" else "custom",
        )
        async with self.database.session() as session:
            session.add(conversation)
            await session.flush()
        return conversation

    async def get_owned(self, user_id: str, conversation_id: str) -> Optional[Conversation]:
        async with self.database.session() as session:
            result = await session.execute(
                select(Conversation).where(
                    Conversation.conversation_id == as_uuid(conversation_id),
                    Conversation.user_id == as_uuid(user_id),
                )
            )
            return result.scalar_one_or_none()

    async def list_owned(self, user_id: str, limit: int = 50) -> list[Conversation]:
        async with self.database.session() as session:
            result = await session.execute(
                select(Conversation)
                .where(Conversation.user_id == as_uuid(user_id))
                .order_by(desc(Conversation.updated_at))
                .limit(limit)
            )
            return list(result.scalars())

    async def list_messages(self, conversation_id: str, limit: int = 100) -> list[Message]:
        async with self.database.session() as session:
            result = await session.execute(
                select(Message)
                .where(Message.conversation_id == as_uuid(conversation_id))
                .order_by(desc(Message.created_at))
                .limit(limit)
            )
            return list(reversed(list(result.scalars())))

    async def list_overviews(self, user_id: str, limit: int = 50) -> list[tuple[Conversation, datetime | None]]:
        latest = select(
            Message.conversation_id, func.max(Message.created_at).label("last_user_message_at"),
        ).join(Conversation, Conversation.conversation_id == Message.conversation_id).where(
            Message.role == "user", Conversation.user_id == as_uuid(user_id),
        ).group_by(Message.conversation_id).subquery()
        async with self.database.session() as session:
            rows = await session.execute(select(Conversation, latest.c.last_user_message_at)
                .outerjoin(latest, latest.c.conversation_id == Conversation.conversation_id)
                .where(Conversation.user_id == as_uuid(user_id))
                .order_by(desc(func.coalesce(latest.c.last_user_message_at, Conversation.created_at)),
                          desc(Conversation.conversation_id)).limit(limit))
            return [(item, timestamp) for item, timestamp in rows]

    async def set_generated_title(self, user_id: str, conversation_id: str, title: str) -> None:
        async with self.database.session() as session:
            await session.execute(update(Conversation).where(
                Conversation.conversation_id == as_uuid(conversation_id),
                Conversation.user_id == as_uuid(user_id), Conversation.title_status == "pending",
            ).values(title=title[:32], title_status="generated"))

    async def citations_for_messages(self, message_ids: list[str]) -> dict[str, list[MessageCitation]]:
        if not message_ids:
            return {}
        async with self.database.session() as session:
            result = await session.execute(
                select(MessageCitation)
                .where(MessageCitation.message_id.in_([as_uuid(value) for value in message_ids]))
                .order_by(MessageCitation.message_id, MessageCitation.citation_index)
            )
            grouped: dict[str, list[MessageCitation]] = {}
            for item in result.scalars():
                grouped.setdefault(str(item.message_id), []).append(item)
            return grouped

    async def add_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        status: str = "success",
        agent_names: Optional[list[str]] = None,
        display_metadata: Optional[dict] = None,
        citations: Iterable[Citation] = (),
        request_id: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> Message:
        if (request_id is None) != (trace_id is None):
            raise ValueError("request_id 和 trace_id 必须同时提供。")
        citations = list(citations)
        metadata = dict(display_metadata or {})
        citation_pages = {str(index): citation.page_numbers for index, citation in enumerate(citations, 1)
                          if citation.page_numbers}
        if citation_pages:
            # 利用已有消息JSON列保留页码快照，不依赖以后可能重建的chunk。
            metadata["citation_page_numbers"] = citation_pages
        message = Message(
            conversation_id=as_uuid(conversation_id),
            request_id=as_uuid(request_id) if request_id else None,
            trace_id=as_uuid(trace_id) if trace_id else None,
            role=role,
            content=content,
            status=status,
            agent_names=agent_names or [],
            display_metadata=metadata,
        )
        async with self.database.session() as session:
            session.add(message)
            await session.flush()
            if role == "user":
                fallback = " ".join(content.split())[:24]
                if fallback:
                    await session.execute(update(Conversation).where(
                        Conversation.conversation_id == as_uuid(conversation_id),
                        Conversation.title_status == "pending", Conversation.title == "新对话",
                    ).values(title=fallback))
            for index, citation in enumerate(citations, start=1):
                payload = citation.model_dump(exclude={"citation_index", "page_numbers"})
                session.add(
                    MessageCitation(
                        message_id=message.message_id,
                        citation_index=index,
                        **payload,
                    )
                )
        return message
