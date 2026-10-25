"""启动准备、在线缓存复用和预热失败的降级契约。"""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
import yaml

from app.container import AppContainer
from app.intent import funnel
from tests.intent_helpers import service
from tests.test_intent_jev import recognizer, response


@pytest.fixture
def hybrid_config():
    config_path = Path(__file__).resolve().parents[2] / "config" / "intent_hybrid.yaml"
    return yaml.safe_load(config_path.read_text(encoding="utf-8"))


@pytest.mark.asyncio
async def test_startup_prepares_samples_once_outside_online_budget(hybrid_config):
    agent, llm = service()
    agent.hybrid_config = hybrid_config
    agent.hybrid_config["embedding_timeout_seconds"] = 0.001

    async def encode_samples(items):
        await asyncio.sleep(0.02)
        return [[1.0, 2.0, 3.0] for _ in items]

    agent.embeddings.aembed_documents.side_effect = encode_samples
    agent.embeddings.aembed_query.return_value = [2.0, 1.0, 1.0]
    profile = await agent.warmup_embeddings()
    assert profile["sample_count"] == 85
    assert profile["embedding_prototypes"] > 1
    await funnel.embedding_decision(agent, "学校转专业流程")
    await funnel.embedding_decision(agent, "校园网认证失败")
    agent.embeddings.aembed_documents.assert_awaited_once()
    assert agent.embeddings.aembed_query.await_count == 3
    llm.ainvoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_startup_timeout_cancels_preparation_and_does_not_query():
    agent, _ = service()
    agent.hybrid_config["embedding_warmup_timeout_seconds"] = 0.01
    cancelled = asyncio.Event()

    async def never_finishes(items):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    agent.embeddings.aembed_documents.side_effect = never_finishes
    with pytest.raises(TimeoutError):
        await agent.warmup_embeddings()
    assert cancelled.is_set()
    assert agent._embedding_warmup_error == "TimeoutError"
    assert agent._example_vectors is None
    agent.embeddings.aembed_query.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_warmup_upgrades_without_repeating_samples(monkeypatch):
    agent, llm, client = recognizer(
        monkeypatch,
        lambda request: httpx.Response(
            200,
            json=response(
                json.loads(request.content)["questions"],
                "learning_plan",
                tasks=("task_learning_plan",),
            ),
        ),
    )
    agent.embeddings = SimpleNamespace(
        aembed_documents=AsyncMock(side_effect=ConnectionError("Ollama unavailable")),
        aembed_query=AsyncMock(),
    )
    with pytest.raises(ConnectionError):
        await agent.warmup_embeddings()
    async with client:
        for _ in range(2):
            result = await agent.recognize("如何规划这个月的学习", [], "")
            assert result.decision_stage == "jev"
            stage = next(item for item in result.stage_trace if item["stage"] == "embedding")
            assert stage["status"] == "skipped"
            assert "启动向量预热失败" in stage["reason"]
            assert result.model_calls["embedding"] == 0
    agent.embeddings.aembed_documents.assert_awaited_once()
    agent.embeddings.aembed_query.assert_not_awaited()
    llm.ainvoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_successful_retry_restores_vector_branch():
    agent, _ = service()
    agent.embeddings.aembed_documents.side_effect = ConnectionError()
    with pytest.raises(ConnectionError):
        await agent.warmup_embeddings()
    agent.embeddings.aembed_documents.side_effect = lambda items: [[1.0, 2.0] for _ in items]
    agent.embeddings.aembed_query.return_value = [1.0, 2.0]
    await agent.warmup_embeddings()
    assert agent._embedding_warmup_error is None
    assert agent._example_vectors is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, True])
async def test_container_initialization_records_warmup_without_aborting(failure):
    container = object.__new__(AppContainer)
    container.users = SimpleNamespace(ensure_workspace_user=AsyncMock(return_value="workspace-id"))
    container.unified_evaluations = SimpleNamespace(recover_interrupted=AsyncMock())
    container.web_cache = SimpleNamespace(sync_sources=AsyncMock())
    container.web_registry = object()
    container.knowledge = SimpleNamespace(load_active_chunks=AsyncMock(return_value=[]))
    container.bm25 = SimpleNamespace(rebuild=Mock())
    warmup = AsyncMock(return_value={"sample_count": 85, "embedding_query": 10.0})
    if failure:
        warmup.side_effect = TimeoutError()
    container.intent_service = SimpleNamespace(warmup_embeddings=warmup)
    await container.initialize()
    assert container.workspace_user_id == "workspace-id"
    container.users.ensure_workspace_user.assert_awaited_once()
    warmup.assert_awaited_once()
    container.bm25.rebuild.assert_called_once_with([])
    assert container.intent_embedding_warmup["duration_ms"] >= 0
    if failure:
        assert container.intent_embedding_warmup["status"] == "degraded"
        assert container.intent_embedding_warmup["error_type"] == "TimeoutError"
    else:
        assert container.intent_embedding_warmup["status"] == "ready"
        assert container.intent_embedding_warmup["sample_count"] == 85
