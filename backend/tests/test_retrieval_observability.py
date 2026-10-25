"""验证真实检索边界的跨度、结果兼容性及失败/取消时的诊断保留。"""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from langchain_core.documents import Document

from app.core.observability import observation_scope, span
from app.db.repositories.knowledge import PostgresKnowledgeRepository
from app.retrieval.local.service import LocalRetrievalService
from app.schemas.enums import QueryStrategy, RetrievalSource
from app.schemas.retrieval import (
    QueryPlan,
)


class MemoryDatabase:
    def __init__(self, results):
        self.execute = AsyncMock(side_effect=[SimpleNamespace(tuples=lambda r=r: r) for r in results])

    @asynccontextmanager
    async def session(self):
        yield SimpleNamespace(execute=self.execute)


def setup_retrieval(query_count=1):
    chunk = SimpleNamespace(chunk_id="chunk", content="校园网充值指南", page_number=1,
                            section_path="充值", version=1)
    source = SimpleNamespace(document_id="source", title="指南", filename="指南.md",
                             source_url=None, published_at=None)
    results = [[(chunk, source, .1)]] * query_count
    database = MemoryDatabase(results)
    embeddings = SimpleNamespace(aembed_query=AsyncMock(return_value=[1., 0.]))
    repository = PostgresKnowledgeRepository(database, embeddings)
    document = Document(id="chunk", page_content=chunk.content,
                        metadata={"chunk_id": "chunk", "document_id": "source"})
    bm25 = SimpleNamespace(search=Mock(return_value=[SimpleNamespace(document=document, score=1.)]))
    return LocalRetrievalService(repository, bm25), database, embeddings


def plan(multi=False):
    return QueryPlan(strategy=QueryStrategy.MULTI_QUERY if multi else QueryStrategy.ORIGINAL,
                     retrieval_query="校园网充值", generated_queries=["充值方式"] if multi else [],
                     selected_sources=[RetrievalSource.LOCAL_KNOWLEDGE])


def phases(observer, event="phase.completed"):
    return [item["payload"] for item in observer.events if item["event_type"] == event]


@pytest.mark.asyncio
async def test_multi_query_traces_match_unobserved_results_and_keep_query_hierarchy():
    baseline, *_ = setup_retrieval(2)
    expected = await baseline.retrieve("校园网充值", plan(multi=True))
    service, _, embeddings = setup_retrieval(2)
    sink = SimpleNamespace(event=AsyncMock())
    async with observation_scope("request", sink) as observer:
        async with span("retrieval.retrieve", "检索召回", round_number=2) as parent:
            result = await service.retrieve("校园网充值", plan(multi=True))
    assert result == expected
    completed = phases(observer)
    local = next(p for p in completed if p["node"] == "retrieval.local")
    assert local["parent_span_id"] == parent["span_id"]
    assert local["route_counts"] == result.route_counts
    dense = [p for p in completed if p["node"] == "retrieval.dense"]
    assert [p["query_index"] for p in dense] == [1, 2]
    for parent_query in dense:
        children = [p for p in completed if p["parent_span_id"] == parent_query["span_id"]]
        assert [p["node"] for p in children] == ["retrieval.embedding", "retrieval.vector_search"]
        assert children[0]["vector_dimensions"] == 2
        assert children[1]["result_count"] == 1
    assert {p["node"] for p in completed} >= {
        "retrieval.bm25", "retrieval.rrf",
    }
    assert all(p["duration_ms"] >= 0 and p["finished_at"] for p in completed)
    assert embeddings.aembed_query.await_count == 2


@pytest.mark.asyncio
async def test_cancel_during_embedding_marks_deepest_step_and_retains_completed_bm25():
    service, database, embeddings = setup_retrieval()
    entered = asyncio.Event()

    async def blocked(query):
        entered.set()
        await asyncio.Event().wait()

    embeddings.aembed_query.side_effect = blocked
    async with observation_scope("request", SimpleNamespace(event=AsyncMock())) as observer:
        async with span("retrieval.retrieve", "检索召回") as parent:
            task = asyncio.create_task(service.retrieve("校园网充值", plan()))
            await asyncio.wait_for(entered.wait(), 1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            async with span("probe", "取消后的同级步骤") as probe:
                assert probe["parent_span_id"] == parent["span_id"]
    cancelled = phases(observer, "phase.cancelled")
    assert [p["node"] for p in cancelled] == [
        "retrieval.embedding", "retrieval.dense", "retrieval.local",
    ]
    assert all(p["status"] == "cancelled" and p["duration_ms"] >= 0 for p in cancelled)
    assert any(p["node"] == "retrieval.bm25" for p in phases(observer))
    database.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_database_failure_keeps_bm25_and_embedding_success_without_caching_failed_route():
    service, database, _ = setup_retrieval()
    error = OSError("数据库查询失败")
    database.execute.side_effect = error
    async with observation_scope("request", SimpleNamespace(event=AsyncMock())) as observer:
        result = await service.retrieve("校园网充值", plan())
    assert result.documents and len(result.query_cache) == 1
    assert result.query_executions[-1].status == "failed"
    assert result.query_executions[-1].error == str(error)
    assert any(p["node"] == "retrieval.embedding" for p in phases(observer))
    failed = phases(observer, "phase.failed")
    assert [p["node"] for p in failed] == ["retrieval.vector_search"]
    assert all(p["error_type"] == "OSError" for p in failed)
    assert next(p for p in phases(observer) if p["node"] == "retrieval.dense")["status"] == "failed"
    assert next(p for p in phases(observer) if p["node"] == "retrieval.local")["status"] == "degraded"
