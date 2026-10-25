"""Redis 连接包装；Redis 仅存易失状态，不承担事实主存储。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator
from uuid import uuid4

from redis.asyncio import Redis


class RedisResources:
    def __init__(self, url: str) -> None:
        self.client = Redis.from_url(url, decode_responses=True)

    async def close(self) -> None:
        await self.client.aclose()

    @asynccontextmanager
    async def conversation_lock(
        self,
        conversation_id: str,
        ttl_seconds: int = 300,
    ) -> AsyncIterator[bool]:
        key = "lock:conversation:%s" % conversation_id
        token = str(uuid4())
        acquired = bool(await self.client.set(key, token, ex=ttl_seconds, nx=True))
        try:
            yield acquired
        finally:
            if acquired:
                current = await self.client.get(key)
                if current == token:
                    await self.client.delete(key)
