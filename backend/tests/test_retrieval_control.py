"""本地召回的候选上限、成功缓存和失败重试契约。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from langchain_core.documents import Document

from app.retrieval.local.service import LocalRetrievalService
from app.schemas.retrieval import QueryPlan


@pytest.mark.asyncio
async def test_rrf_candidates_and_order_are_stable_when_queries_are_cached():
    documents = [Document(id=str(i), page_content="校园说明", metadata={"chunk_id": str(i)}) for i in range(12)]
    bm25 = SimpleNamespace(search=Mock(return_value=[
        SimpleNamespace(document=document, score=12-i) for i, document in enumerate(documents)
    ]))
    repository = SimpleNamespace(dense_search=AsyncMock(return_value=documents))
    retriever = LocalRetrievalService(repository, bm25, route_top_k=20, rrf_candidate_k=10)
    plan = QueryPlan(strategy="original", retrieval_query="校园手续", selected_sources=["local_knowledge"])
    first = await retriever.retrieve("校园手续", plan)
    second = await retriever.retrieve("校园手续", plan, first.query_cache)
    assert len(first.documents) == 10
    assert [document.id for document in first.documents] == [document.id for document in second.documents]
    assert len({document.id for document in first.documents}) == 10
    assert all(item.status == "cached" for item in second.query_executions)
    assert bm25.search.call_count == 1
    assert repository.dense_search.await_count == 1


@pytest.mark.asyncio
async def test_successful_empty_route_is_cached_while_failed_route_is_retried():
    bm25 = SimpleNamespace(search=Mock(return_value=[]))
    repository = SimpleNamespace(dense_search=AsyncMock(side_effect=[RuntimeError("暂时失败"), []]))
    retriever = LocalRetrievalService(repository, bm25)
    plan = QueryPlan(strategy="original", retrieval_query="校园手续", selected_sources=["local_knowledge"])
    first = await retriever.retrieve("校园手续", plan)
    assert first.documents == []
    assert [item.status for item in first.query_executions] == ["executed", "failed"]
    second = await retriever.retrieve("校园手续", plan, first.query_cache)
    assert second.documents == []
    assert [item.status for item in second.query_executions] == ["cached", "executed"]
    third = await retriever.retrieve("校园手续", plan, second.query_cache)
    assert all(item.status == "cached" for item in third.query_executions)
    assert bm25.search.call_count == 1
    assert repository.dense_search.await_count == 2
