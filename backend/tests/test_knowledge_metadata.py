"""验证当前入库字段、跨主题召回和逐文档进度。"""

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from langchain_core.documents import Document
from pydantic import ValidationError
from sqlalchemy import select

from app.api.routes.unified_evaluation import catalog
from app.db.models import KnowledgeDocument
from app.db.repositories.ingestion import IngestionRepository
from app.db.repositories.knowledge import _to_document
from app.ingestion.service import KnowledgeIngestionService
from app.retrieval.bm25 import BM25Index
from app.retrieval.local.service import LocalRetrievalService
from app.schemas.enums import QueryStrategy, RetrievalSource
from app.schemas.ingestion import DocumentMetadata
from app.schemas.retrieval import QueryPlan
from app.tools.agentic_retrieval import AgenticRetrievalToolInput
from tests.test_conversation_database import isolated_database


def test_model_contracts_reject_unused_fields_and_sql_uses_current_documents():
    with pytest.raises(ValidationError):
        AgenticRetrievalToolInput(query="转专业", optional_filters={"domain": "campus"})
    with pytest.raises(ValidationError):
        QueryPlan(strategy="original", retrieval_query="转专业", metadata_filters={"category": "regulation"})
    with pytest.raises(ValidationError):
        DocumentMetadata(title="指南", domain="campus")
    statement = select(KnowledgeDocument)
    sql = str(statement.compile())
    assert "documents.source_status" not in sql
    assert "documents.domain" not in sql and "documents.category" not in sql


@pytest.mark.asyncio
async def test_scan_needs_no_model_or_embedding_and_ingestion_retains_source_locations(tmp_path):
    (tmp_path / "指南.md").write_text("# 校园办事指南\n\n办理校园卡时需要携带学生证。", encoding="utf-8")
    repository = SimpleNamespace(create_job=AsyncMock(return_value=SimpleNamespace(job_id=uuid4())))
    knowledge = SimpleNamespace(replace_document=AsyncMock(return_value=str(uuid4())))
    embeddings = SimpleNamespace(aembed_documents=AsyncMock(side_effect=lambda rows: [[1.0] + [0.0] * 1023 for _ in rows]))
    service = KnowledgeIngestionService(tmp_path, repository, knowledge, embeddings)
    await service.scan()
    items, _ = repository.create_job.await_args.args
    assert items[0]["detected_metadata"] == {"title": "指南"}
    embeddings.aembed_documents.assert_not_awaited()
    assert not hasattr(service, "model")
    await service._ingest_one(items[0]["relative_path"], items[0]["detected_metadata"])
    source, chunks = knowledge.replace_document.await_args.args
    assert source.title == "指南" and source.filename == "指南.md"
    assert not hasattr(source, "domain") and not hasattr(source, "category")
    source.document_id = uuid4()
    for chunk in chunks:
        chunk.chunk_id = uuid4()
        returned = _to_document(chunk, source)
        assert returned.metadata["document_id"] == str(source.document_id)
        assert returned.metadata["filename"] == "指南.md"
        assert not {"domain", "category"} & returned.metadata.keys()
    assert embeddings.aembed_documents.await_count == 1


@pytest.mark.asyncio
async def test_cross_topic_documents_are_not_excluded_from_either_recall_route():
    documents = [
        Document(id="policy", page_content="转专业申请条件与办理流程", metadata={"domain": "learning", "category": "regulation"}),
        Document(id="handbook", page_content="学生手册中的转专业申请流程", metadata={"domain": "campus", "category": "handbook"}),
    ]
    bm25 = BM25Index()
    bm25.rebuild(documents)
    repository = SimpleNamespace(dense_search=AsyncMock(return_value=documents))
    service = LocalRetrievalService(repository, bm25, route_top_k=2, rrf_candidate_k=2)
    plan = QueryPlan(strategy=QueryStrategy.ORIGINAL, retrieval_query="转专业申请流程", selected_sources=[RetrievalSource.LOCAL_KNOWLEDGE])
    result = await service.retrieve("转专业申请流程", plan)
    assert {doc.id for doc in result.documents} == {"policy", "handbook"}
    assert result.route_counts == {"bm25:q1": 2, "dense:q1": 2}
    repository.dense_search.assert_awaited_once_with("转专业申请流程", 2)


@pytest.mark.asyncio
async def test_evaluation_catalog_only_publishes_current_fields():
    value = await catalog(SimpleNamespace(intent_service=SimpleNamespace(catalog={})))
    assert "metadata" not in value
    assert not {"domain", "category"} & set(KnowledgeDocument.__table__.columns.keys())




@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("RUN_UNIFIED_DB_TESTS") != "1", reason="显式启用隔离 PostgreSQL 验收")
async def test_each_document_commits_progress_without_double_counting_or_false_completion():
    async with isolated_database() as (database, _):
        repository = IngestionRepository(database)
        job = await repository.create_job([{"relative_path": f"{index}.md", "detected_metadata": {"title": str(index)}} for index in range(2)], {})
        items = await repository.list_items(str(job.job_id))
        await repository.confirm(str(job.job_id), {str(item.item_id): item.detected_metadata for item in items})
        await repository.mark_running(str(job.job_id))
        with pytest.raises(ValueError, match="重复启动"):
            await repository.mark_running(str(job.job_id))
        await repository.finish_item(str(items[0].item_id))
        progress = await repository.get_job(str(job.job_id))
        assert (progress.completed_items, progress.failed_items, progress.status) == (1, 0, "running")
        await repository.finish_item(str(items[0].item_id))
        assert (await repository.get_job(str(job.job_id))).completed_items == 1
        await repository.finish_item(str(items[1].item_id), error="文档没有可入库文本")
        await repository.finish_job(str(job.job_id))
        final = await repository.get_job(str(job.job_id))
        assert (final.completed_items, final.failed_items, final.status) == (1, 1, "partial")
        success = await repository.create_job([{"relative_path": "成功.md", "detected_metadata": {"title": "成功"}}], {})
        item = (await repository.list_items(str(success.job_id)))[0]
        await repository.confirm(str(success.job_id), {str(item.item_id): item.detected_metadata})
        await repository.mark_running(str(success.job_id))
        await repository.finish_item(str(item.item_id))
        await repository.finish_job(str(success.job_id))
        final = await repository.get_job(str(success.job_id))
        assert (final.completed_items, final.failed_items, final.status) == (1, 0, "completed")
