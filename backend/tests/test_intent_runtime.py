"""正式配置、网页评测和缓存恢复后的四级漏斗追问链路；不调用真实模型。"""

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest
import yaml

from app.cache.recent_messages import RecentMessageCache
from app.graph.builder import GlobalGraphDependencies, build_global_graph
from app.intent.runtime import build_intent_service
from app.intent.service import LLMIntentOutput
from app.routing.router import AgentRouter
from app.schemas.agent import FinalResponse
from app.services.chat_service import ChatRunService
from app.services.context_service import PostgresContextLoader
from tests.test_context_memory import FOLLOWUP, TOPIC, MemoryRedis
from tests.test_intent_jev import response

ROOT = Path(__file__).resolve().parents[2]


def runtime(monkeypatch, strategy="funnel", configured=True):
    if configured:
        monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    else:
        monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    settings = SimpleNamespace(
        config_dir=ROOT / "config",
        load_yaml=lambda name: yaml.safe_load((ROOT / "config" / name).read_text(encoding="utf-8")),
    )
    app_config = settings.load_yaml("app.yaml")
    app_config["intent"]["default_strategy"] = strategy
    structured = SimpleNamespace(
        ainvoke=AsyncMock(
            return_value=LLMIntentOutput(primary_intent="campus_affairs", confidence=0.98)
        )
    )
    model = Mock()
    model.with_structured_output.return_value = structured
    factory = SimpleNamespace(chat_model=Mock(return_value=model))
    embeddings = SimpleNamespace(aembed_documents=AsyncMock(), aembed_query=AsyncMock())
    recognizer = build_intent_service(settings, factory, embeddings, app_config["intent"])
    return recognizer, structured, factory, embeddings, app_config, settings


def test_production_configuration_uses_calibrated_thresholds_and_bounded_llm(monkeypatch):
    service, _, factory, _, _, _ = runtime(monkeypatch)
    assert service.default_strategy == "funnel"
    assert service.hybrid_config["vector_thresholds"]["career_recruitment"]["margin"] == 0.1
    assert service.hybrid_config["jev_thresholds"]["intent_thresholds"]["unclear"] == 0.55
    assert service.jev_client.config["model"] == "jev-1.13.0"
    factory.chat_model.assert_called_once_with(
        "intent_model", max_retries=0, max_output_tokens=1536
    )


def test_default_hybrid_requires_credentials_before_a_request(monkeypatch):
    with pytest.raises(ValueError, match="TYPESAFE_API_KEY"):
        runtime(monkeypatch, configured=False)


@pytest.mark.asyncio
async def test_rules_use_configured_default_without_any_model_call(monkeypatch):
    service, llm, _, embeddings, _, _ = runtime(monkeypatch)
    result = await service.recognize("你好！", [], "")
    assert result.decision_stage == "rule"
    assert result.primary_intent.value == "small_talk"
    llm.ainvoke.assert_not_awaited()
    embeddings.aembed_query.assert_not_awaited()
    assert service.jev_client.client is None
    await service.jev_client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("partial_cache", [False, True])
@pytest.mark.parametrize("jev_failure", [False, True])
async def test_cold_or_partial_cache_followup_routes_to_campus_with_jev_or_deepseek(
    monkeypatch, partial_cache, jev_failure
):
    recognizer, llm, _, embeddings, _, _ = runtime(monkeypatch)
    captured = {}

    def handler(request):
        captured.update(json.loads(request.content))
        if jev_failure:
            return httpx.Response(429, json={"error": {"message": "rate limited"}})
        return httpx.Response(
            200,
            json=response(captured["questions"], "campus_affairs", tasks=("task_campus_affairs",)),
        )

    client = httpx.AsyncClient(
        base_url="https://api.typesafe.ai", transport=httpx.MockTransport(handler)
    )
    recognizer.jev_client.client = client
    redis = MemoryRedis()
    cache = RecentMessageCache(SimpleNamespace(client=redis))
    if partial_cache:
        await cache.replace("conversation", [{"role": "assistant", "content": "过期的片段"}])
    records = [SimpleNamespace(message_id=uuid4(), **item) for item in TOPIC]

    async def add_message(conversation_id, role, content, **kwargs):
        record = SimpleNamespace(message_id=uuid4(), role=role, content=content)
        records.append(record)
        return record

    conversations = SimpleNamespace(
        list_messages=AsyncMock(side_effect=lambda conversation_id, limit: records[-limit:]),
        add_message=AsyncMock(side_effect=add_message),
    )
    loader = PostgresContextLoader(
        conversations,
        SimpleNamespace(latest_summary=AsyncMock(return_value="旧的寒暄摘要")),
        SimpleNamespace(list_owned=AsyncMock(return_value=[])),
        cache,
    )
    trace = SimpleNamespace(event=AsyncMock())
    memory = SimpleNamespace(recall=AsyncMock(return_value=[]), maintain_after_turn=AsyncMock())
    skills = SimpleNamespace(select=Mock(return_value=Mock(model_dump=Mock(return_value={}))))
    agent_service = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace()))
    graph = build_global_graph(
        GlobalGraphDependencies(
            loader,
            recognizer,
            AgentRouter(),
            memory,
            skills,
            SimpleNamespace(scores=AsyncMock(return_value={})),
            agent_service,
            SimpleNamespace(
                compose=AsyncMock(
                    return_value=FinalResponse(
                        content="研究生转专业需要按学校研究生管理规定办理。", agents=["CampusAgent"]
                    )
                )
            ),
            trace,
        )
    )

    @asynccontextmanager
    async def lock(*args):
        yield True

    chat = ChatRunService(
        graph,
        conversations,
        SimpleNamespace(
            start=AsyncMock(return_value=SimpleNamespace(trace_id=uuid4())), finish=AsyncMock()
        ),
        trace,
        SimpleNamespace(conversation_lock=lock),
        memory,
        cache,
    )
    async with client:
        await chat.start("user", "conversation", FOLLOWUP)
        while chat.tasks:
            await asyncio.gather(*list(chat.tasks))
    assert captured["state"]["query"] == FOLLOWUP
    assert captured["state"]["recent_messages"] == TOPIC
    events = {call.args[1]: call.args[2] for call in trace.event.await_args_list}
    assert "request.failed" not in events
    assert events["intent.completed"]["decision_stage"] == (
        "llm_fallback" if jev_failure else "jev"
    )
    assert events["route.completed"]["route"] == "single_agent"
    assert agent_service.execute.await_args.kwargs["assignment"].agent_name.value == "CampusAgent"
    assert "response.completed" in events
    if jev_failure:
        assert "转专业" in str(llm.ainvoke.await_args)
        llm.ainvoke.assert_awaited_once()
    else:
        llm.ainvoke.assert_not_awaited()
    embeddings.aembed_query.assert_not_awaited()
    conversations.list_messages.assert_awaited_once_with("conversation", limit=8)


@pytest.mark.asyncio
async def test_container_closes_jev_and_still_closes_storage_on_provider_close_error():
    from app.container import AppContainer

    container = AppContainer.__new__(AppContainer)
    container.chat = SimpleNamespace(tasks=[])
    jev = SimpleNamespace(aclose=AsyncMock(side_effect=RuntimeError("close failed")))
    container.intent_service = SimpleNamespace(jev_client=jev)
    container.redis = SimpleNamespace(close=AsyncMock())
    container.database = SimpleNamespace(close=AsyncMock())
    with pytest.raises(RuntimeError, match="close failed"):
        await container.close()
    container.redis.close.assert_awaited_once()
    container.database.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_container_cancels_chat_and_unified_evaluation_tasks_before_closing():
    from app.container import AppContainer

    pending = asyncio.Event()
    chat_task = asyncio.create_task(pending.wait())
    evaluation_task = asyncio.create_task(pending.wait())
    container = AppContainer.__new__(AppContainer)
    container.chat = SimpleNamespace(tasks={chat_task})
    container.unified_evaluation = SimpleNamespace(tasks={"run": evaluation_task})
    container.intent_service = SimpleNamespace(jev_client=SimpleNamespace(aclose=AsyncMock()))
    container.redis = SimpleNamespace(close=AsyncMock())
    container.database = SimpleNamespace(close=AsyncMock())
    await asyncio.sleep(0)

    await container.close()

    assert chat_task.cancelled() and evaluation_task.cancelled()
    container.intent_service.jev_client.aclose.assert_awaited_once()
    container.redis.close.assert_awaited_once()
    container.database.close.assert_awaited_once()
