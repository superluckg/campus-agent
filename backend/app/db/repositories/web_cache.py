"""固定官网 Source 配置与抓取快照。"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from langchain_core.documents import Document
from sqlalchemy import delete, select, text

from app.db.models import WebSnapshot, WebSource
from app.db.session import Database
from app.retrieval.web.registry import WebSourceRegistry


class WebCacheRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def sync_sources(self, registry: WebSourceRegistry) -> None:
        async with self.database.session() as session:
            for definition in registry.sources:
                item = await session.get(WebSource, definition.source_key)
                payload = {
                    "display_name": definition.display_name,
                    "url": str(definition.url),
                    "adapter": definition.adapter,
                    "cache_ttl_seconds": definition.cache_ttl_seconds,
                    "enabled": True,
                    "config_snapshot": definition.model_dump(mode="json"),
                }
                if item is None:
                    session.add(WebSource(source_key=definition.source_key, **payload))
                else:
                    for key, value in payload.items():
                        setattr(item, key, value)

    async def fresh(self, source_key: str) -> list[Document]:
        return await self._latest(source_key, require_fresh=True)

    async def latest_success(self, source_key: str) -> list[Document]:
        return await self._latest(source_key, require_fresh=False)

    async def _latest(self, source_key: str, require_fresh: bool) -> list[Document]:
        statement = select(WebSnapshot).where(
            WebSnapshot.source_key == source_key,
            WebSnapshot.status == "success",
        )
        if require_fresh:
            statement = statement.where(WebSnapshot.expires_at > datetime.now(timezone.utc))
        async with self.database.session() as session:
            snapshots = list((await session.execute(statement)).scalars())
        return [
            Document(
                id="web:%s" % item.snapshot_id,
                page_content=item.normalized_text,
                metadata={
                    "source_type": "web",
                    "source_key": item.source_key,
                    "title": item.title,
                    "source_url": item.source_url,
                    "fetched_at": item.fetched_at.isoformat(),
                    "content_hash": item.content_hash,
                    "cache": "fresh" if require_fresh else "stale_fallback",
                },
            )
            for item in snapshots
        ]

    async def save(self, source_key: str, documents: list[Document], ttl_seconds: int) -> None:
        if not documents:
            return
        fetched_at = datetime.now(timezone.utc)
        expires_at = fetched_at + timedelta(seconds=ttl_seconds)
        async with self.database.session() as session:
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": "web-cache:" + source_key},
            )
            await session.execute(delete(WebSnapshot).where(WebSnapshot.source_key == source_key))
            for document in documents:
                metadata = document.metadata
                content = document.page_content
                session.add(
                    WebSnapshot(
                        source_key=source_key,
                        title=str(metadata.get("title") or "未命名网页"),
                        source_url=str(metadata.get("source_url") or ""),
                        normalized_text=content,
                        content_hash=str(
                            metadata.get("content_hash")
                            or hashlib.sha256(content.encode("utf-8")).hexdigest()
                        ),
                        fetched_at=fetched_at,
                        expires_at=expires_at,
                        status="success",
                    )
                )
