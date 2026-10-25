"""正文增量只写 Redis；每个请求持有独立缓冲器。"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

from app.cache.event_bus import RedisEventBus


class ResponseStreamPublisher:
    def __init__(
        self, event_bus: RedisEventBus, request_id: str,
        interval_seconds: float = 0.05, max_characters: int = 256,
    ) -> None:
        self.event_bus = event_bus
        self.request_id = request_id
        self.interval_seconds = interval_seconds
        self.max_characters = max_characters
        self.content = ""
        self.sent = ""
        self.started_at = time.perf_counter()
        self.first_text_ms: float | None = None
        self.event_count = 0
        self._lock = asyncio.Lock()
        self._timer: asyncio.Task | None = None
        self._failure: Exception | None = None
        self._closed = False
        self.display_metadata: dict = {}
        self.citations: list = []

    async def set_citations(self, metadata: dict, citations: list) -> None:
        async with self._lock:
            self.display_metadata = metadata
            self.citations = [item.model_dump(mode="json") for item in citations]

    async def start(self) -> None:
        await self.event_bus.publish(
            self.request_id, "response.started",
            {"response_id": self.request_id},
        )

    async def update(self, content: str) -> None:
        async with self._lock:
            if self._failure:
                raise self._failure
            if self._closed:
                raise RuntimeError("回答流已关闭。")
            self.content = content
            if content == self.sent:
                return
            if (
                not self.sent
                or not content.startswith(self.sent)
                or len(content) - len(self.sent) >= self.max_characters
            ):
                await self._flush_locked()
            elif self._timer is None or self._timer.done():
                self._timer = asyncio.create_task(self._flush_later())

    async def _flush_later(self) -> None:
        try:
            await asyncio.sleep(self.interval_seconds)
            await self.flush()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._failure = exc

    async def _flush_locked(self) -> None:
        if self.content == self.sent:
            return
        replacing = not self.content.startswith(self.sent)
        event_type = "response.replace" if replacing else "response.delta"
        field = "content" if replacing else "delta"
        value = self.content if replacing else self.content[len(self.sent):]
        await self.event_bus.publish(
            self.request_id, event_type,
            {
                "response_id": self.request_id, field: value,
                "display_metadata": self.display_metadata,
                "citations": self.citations,
                "server_emitted_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        self.sent = self.content
        self.event_count += 1
        if self.sent and self.first_text_ms is None:
            self.first_text_ms = (time.perf_counter() - self.started_at) * 1000

    async def flush(self) -> None:
        async with self._lock:
            if self._failure:
                raise self._failure
            if not self._closed:
                await self._flush_locked()

    async def close(self) -> None:
        async with self._lock:
            self._closed = True
        if self._timer is not None and self._timer is not asyncio.current_task():
            self._timer.cancel()
            await asyncio.gather(self._timer, return_exceptions=True)

    def metrics(self) -> dict:
        return {
            "first_text_ms": self.first_text_ms,
            "text_event_count": self.event_count,
            "characters": len(self.sent),
        }
