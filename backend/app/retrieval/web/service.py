"""并行调用固定 Source Adapter，并将单源失败隔离。"""

from __future__ import annotations

import asyncio
import logging
from typing import Dict, Optional, Protocol

from langchain_core.documents import Document

from app.retrieval.deduplication import deduplicate_documents

from .adapters import DynamicListAdapter, StaticHtmlAdapter
from .registry import WebSourceRegistry


logger = logging.getLogger(__name__)


class WebCache(Protocol):
    async def fresh(self, source_key: str) -> list[Document]: ...
    async def latest_success(self, source_key: str) -> list[Document]: ...
    async def save(self, source_key: str, documents: list[Document], ttl_seconds: int) -> None: ...


class WebSourceRetrievalService:
    def __init__(self, registry: WebSourceRegistry, cache: Optional[WebCache] = None) -> None:
        self.registry = registry
        self.cache = cache
        self.adapters: Dict[str, object] = {
            "static_html": StaticHtmlAdapter(),
            "dynamic_list": DynamicListAdapter(),
        }

    async def retrieve_source(self, source_key: str) -> list[Document]:
        """单源失败且无成功快照时抛错，避免把失败记为成功空缓存。"""
        source = self.registry.get(source_key)
        if self.cache is not None:
            cached = await self.cache.fresh(source_key)
            if cached:
                return cached
        adapter = self.adapters.get(source.adapter)
        if adapter is None:
            raise ValueError("未知Web Adapter：%s" % source.adapter)
        try:
            documents = await adapter.load(source)
            if documents and self.cache is not None:
                await self.cache.save(source_key, documents, source.cache_ttl_seconds)
            return documents
        except Exception:
            if self.cache is not None:
                cached = await self.cache.latest_success(source_key)
                if cached:
                    return cached
            raise

    async def retrieve_sources(self, source_keys: list[str]) -> list[Document]:
        async def load_one(key):
            try:
                return await self.retrieve_source(key)
            except Exception as exc:
                logger.warning("固定官网来源%s加载失败：%s", key, exc)
                return []
        results = await asyncio.gather(*(load_one(key) for key in dict.fromkeys(source_keys)))
        return deduplicate_documents(document for group in results for document in group)
