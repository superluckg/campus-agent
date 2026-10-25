"""同时写 PostgreSQL Trace 和 Redis SSE 的统一事件出口。"""

from __future__ import annotations

from app.cache.event_bus import RedisEventBus
from app.db.repositories.traces import TraceRepository
from app.core.observability import snapshot


class CompositeTraceSink:
    def __init__(self, repository: TraceRepository, event_bus: RedisEventBus) -> None:
        self.repository = repository
        self.event_bus = event_bus

    async def event(
        self,
        request_id: str,
        event_type: str,
        payload: dict,
    ) -> None:
        payload = snapshot(payload)
        await self.repository.append_event(request_id, event_type, payload)
        await self.event_bus.publish(
            request_id=request_id,
            event_type=event_type,
            data=payload,
        )
