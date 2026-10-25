"""可选的真实 Redis 验证；只使用独立 UUID 键并让其自行过期。"""

import asyncio
import os
from uuid import uuid4

import pytest

from app.cache.event_bus import RedisEventBus
from app.cache.redis_client import RedisResources


@pytest.fixture
async def event_bus():
    url = os.getenv("STREAM_TEST_REDIS_URL")
    if not url:
        pytest.skip("未配置独立的 STREAM_TEST_REDIS_URL")
    resources = RedisResources(url)
    bus = RedisEventBus(resources, ttl_seconds=2)
    try:
        await resources.client.ping()
        yield bus
    finally:
        await resources.close()


@pytest.mark.asyncio
async def test_concurrent_publication_has_atomic_ids_and_preserves_json_arrays(event_bus):
    request_id = str(uuid4())
    events = await asyncio.gather(*[
        event_bus.publish(request_id, "response.delta", {"delta": str(index), "citations": []})
        for index in range(20)
    ])
    assert sorted(event.event_id for event in events) == list(range(1, 21))
    replayed = await event_bus.replay(request_id, 10)
    assert [event.event_id for event in replayed] == list(range(11, 21))
    assert all(event.data["citations"] == [] for event in replayed)


@pytest.mark.asyncio
async def test_blocking_read_wakes_on_new_delta_without_history_polling(event_bus):
    request_id = str(uuid4())
    await event_bus.publish(request_id, "response.started", {})
    task = asyncio.create_task(event_bus.replay(request_id, 1, block_ms=1000))
    await asyncio.sleep(.02)
    await event_bus.publish(request_id, "response.delta", {"delta": "中文"})
    result = await asyncio.wait_for(task, 1)
    assert len(result) == 1 and result[0].data["delta"] == "中文"


@pytest.mark.asyncio
async def test_terminal_is_idempotent_even_with_later_memory_event(event_bus):
    request_id = str(uuid4())
    settled = await asyncio.gather(
        event_bus.publish(request_id, "response.completed", {"content": "答案", "citations": []}),
        event_bus.publish(request_id, "request.failed", {"message": "失败"}),
    )
    assert settled[0].event_id == settled[1].event_id
    assert settled[0].event_type == settled[1].event_type
    await event_bus.publish(request_id, "memory.maintenance_failed", {"message": "后台记录"})
    assert (await event_bus.latest(request_id)).event_type == "memory.maintenance_failed"
    assert (await event_bus.terminal(request_id)).event_id == settled[0].event_id
    replayed = await event_bus.replay(request_id)
    assert sum(item.event_type in {"response.completed", "request.failed"} for item in replayed) == 1


@pytest.mark.asyncio
async def test_event_stream_and_counter_expire_together(event_bus):
    request_id = str(uuid4())
    await event_bus.publish(request_id, "response.completed", {"content": "答案"})
    await asyncio.sleep(2.1)
    assert not await event_bus.available(request_id)
    assert await event_bus.terminal(request_id) is None
    assert not await event_bus.redis.client.exists(event_bus._counter_key(request_id))
