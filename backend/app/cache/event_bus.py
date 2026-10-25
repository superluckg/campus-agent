"""Redis Streams：原子编号、按游标读取和短期 SSE 重放。"""

from __future__ import annotations

import json
from typing import AsyncIterator
from uuid import UUID

from app.schemas.events import SSEEvent

from .redis_client import RedisResources


_PUBLISH = """
local terminal = ARGV[2] == 'response.completed' or ARGV[2] == 'request.failed'
local previous = redis.call('GET', KEYS[3])
if terminal and previous then
    local rows = redis.call('XRANGE', KEYS[1], previous .. '-0', previous .. '-0')
    if #rows > 0 then return {previous, rows[1][2][2]} end
end
local seq = tostring(redis.call('INCR', KEYS[2]))
redis.call('XADD', KEYS[1], seq .. '-0', 'payload', ARGV[1])
if terminal then redis.call('SET', KEYS[3], seq) end
redis.call('EXPIRE', KEYS[1], ARGV[3])
redis.call('EXPIRE', KEYS[2], ARGV[3])
redis.call('EXPIRE', KEYS[3], ARGV[3])
return {seq, ARGV[1]}
"""


class RedisEventBus:
    def __init__(self, redis: RedisResources, ttl_seconds: int = 3600) -> None:
        self.redis = redis
        self.ttl_seconds = ttl_seconds

    @staticmethod
    def _events_key(request_id: str) -> str:
        return "sse:{%s}:events" % request_id

    @staticmethod
    def _counter_key(request_id: str) -> str:
        return "sse:{%s}:counter" % request_id

    async def publish(
        self, request_id: str, event_type: str, data: dict,
        agent_name: str | None = None,
    ) -> SSEEvent:
        event = SSEEvent(
            event_id=1, event_type=event_type, request_id=UUID(request_id),
            data=data, agent_name=agent_name,
        )
        sequence, payload = await self.redis.client.eval(
            _PUBLISH, 3, self._events_key(request_id), self._counter_key(request_id),
            "sse:{%s}:terminal" % request_id,
            event.model_dump_json(), event_type, self.ttl_seconds,
        )
        return SSEEvent.model_validate_json(payload).model_copy(
            update={"event_id": int(sequence)},
        )

    @staticmethod
    def _decode(entry) -> SSEEvent:
        stream_id, fields = entry
        return SSEEvent.model_validate_json(fields["payload"]).model_copy(
            update={"event_id": int(stream_id.split("-")[0])},
        )

    async def available(self, request_id: str) -> bool:
        return bool(await self.redis.client.exists(self._events_key(request_id)))

    async def latest(self, request_id: str) -> SSEEvent | None:
        rows = await self.redis.client.xrevrange(self._events_key(request_id), count=1)
        return self._decode(rows[0]) if rows else None

    async def terminal(self, request_id: str) -> SSEEvent | None:
        sequence = await self.redis.client.get("sse:{%s}:terminal" % request_id)
        if sequence is None:
            return None
        stream_id = "%s-0" % sequence
        rows = await self.redis.client.xrange(self._events_key(request_id), stream_id, stream_id)
        return self._decode(rows[0]) if rows else None

    async def replay(
        self, request_id: str, after_event_id: int = 0, *, block_ms: int | None = None,
    ) -> list[SSEEvent]:
        rows = await self.redis.client.xread(
            {self._events_key(request_id): "%d-0" % max(0, after_event_id)},
            count=200, block=block_ms,
        )
        return [
            self._decode(entry) for _, entries in rows for entry in entries
        ]

    async def subscribe(
        self, request_id: str, after_event_id: int = 0,
    ) -> AsyncIterator[SSEEvent]:
        cursor = after_event_id
        while await self.available(request_id):
            for event in await self.replay(request_id, cursor, block_ms=1000):
                cursor = event.event_id
                yield event
                if event.event_type in {"response.completed", "request.failed"}:
                    return


def format_sse(event: SSEEvent) -> str:
    data = json.dumps(event.data, ensure_ascii=False, default=str)
    return "id: %d\nevent: %s\ndata: %s\n\n" % (
        event.event_id, event.event_type, data,
    )
