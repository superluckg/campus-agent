"""近期消息的 Redis 镜像；PostgreSQL 始终是主存储。"""

from __future__ import annotations

import json

from .redis_client import RedisResources


class RecentMessageCache:
    def __init__(
        self,
        redis: RedisResources,
        keep_count: int = 8,
        ttl_seconds: int = 86400,
    ) -> None:
        self.redis = redis
        self.keep_count = keep_count
        self.ttl_seconds = ttl_seconds

    @staticmethod
    def _key(conversation_id: str) -> str:
        return "recent_messages:%s" % conversation_id

    async def get(self, conversation_id: str) -> list[dict[str, str]]:
        values = await self.redis.client.lrange(self._key(conversation_id), 0, -1)
        return [json.loads(value) for value in values]

    async def replace(self, conversation_id: str, messages: list[dict[str, str]]) -> None:
        key = self._key(conversation_id)
        pipeline = self.redis.client.pipeline()
        pipeline.delete(key)
        if messages:
            pipeline.rpush(
                key,
                *(json.dumps(item, ensure_ascii=False) for item in messages[-self.keep_count :]),
            )
        pipeline.expire(key, self.ttl_seconds)
        await pipeline.execute()

    async def append(self, conversation_id: str, role: str, content: str) -> None:
        key = self._key(conversation_id)
        pipeline = self.redis.client.pipeline()
        # 仅追加已有镜像。缓存失效时保持为空，由加载器从 PostgreSQL 恢复完整窗口。
        # RPUSHX 与裁剪、续期在同一事务内执行，避免把孤立新消息误当成历史。
        pipeline.rpushx(key, json.dumps({"role": role, "content": content}, ensure_ascii=False))
        pipeline.ltrim(key, -self.keep_count, -1)
        pipeline.expire(key, self.ttl_seconds)
        await pipeline.execute()
