"""跨页自然切分、出处映射、评测锚点和文档替换。"""

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from langchain_core.documents import Document

from app.agents.evidence import EvidenceRegistry, normalize_answer
from app.core.source_pages import page_label, text_on_page
from app.db.models import KnowledgeChunk, KnowledgeDocument
from app.db.repositories.knowledge import PostgresKnowledgeRepository, _to_document
from app.evaluation.retrieval.matching import OriginalEvidenceMatcher
from app.evaluation.retrieval.scoring import serialize_unit
from app.evaluation.sources import preview_sections
from app.ingestion.chunking import split_sections
from app.ingestion.parser import ParsedSection
from app.ingestion.service import KnowledgeIngestionService
from app.schemas.agent import Citation
from app.tools.agentic_retrieval import render_documents
from tests.test_conversation_database import isolated_database


def service(tmp_path, **kwargs):
    return KnowledgeIngestionService(
        tmp_path, SimpleNamespace(), SimpleNamespace(), SimpleNamespace(), **kwargs
    )


def split(rows, tmp_path, **kwargs):
    return split_sections(rows, service(tmp_path, **kwargs).splitter, cross_pages=True)


def documents(chunks):
    return [
        Document(
            id=str(uuid4()),
            page_content=chunk.text,
            metadata={
                **chunk.metadata,
                "chunk_id": str(uuid4()),
                "document_id": "source",
                "filename": "政策.pdf",
                "page_number": chunk.page_number,
                "chunk_index": index,
            },
        )
        for index, chunk in enumerate(chunks)
    ]


def match(docs, text, page):
    corpus = {"units": [serialize_unit(doc) for doc in docs], "web_sources": {}}
    return OriginalEvidenceMatcher(corpus).match(
        {
            "agent_name": "CampusAgent",
            "evidence": [{"text": text, "filename": "政策.pdf", "page_number": page}],
        }
    )


def test_two_short_pdf_pages_merge_and_keep_every_source_character(tmp_path):
    rows = [ParsedSection("甲" * 453, 1), ParsedSection("乙" * 400, 2)]
    chunks = split(rows, tmp_path)
    assert len(chunks) == 1 and len(chunks[0].text) == 854
    assert chunks[0].metadata["page_numbers"] == [1, 2]
    assert chunks[0].page_number == 1
    assert text_on_page(chunks[0].text, chunks[0].metadata, 1) == rows[0].text
    assert text_on_page(chunks[0].text, chunks[0].metadata, 2) == rows[1].text


def test_long_text_without_any_separator_still_obeys_size_and_overlap(tmp_path):
    source = "甲" * 1300 + "乙" * 1100
    chunks = split([ParsedSection(source, 1)], tmp_path)
    assert [len(chunk.text) for chunk in chunks] == [1000, 1000, 600]
    assert [chunk.metadata["start_index"] for chunk in chunks] == [0, 900, 1800]
    assert chunks[0].text[-100:] == chunks[1].text[:100]
    assert source == chunks[0].text + chunks[1].text[100:] + chunks[2].text[100:]


def test_natural_boundaries_keep_real_page_numbers_with_empty_page_gaps(tmp_path):
    chunks = split(
        [ParsedSection("申请条件。" * 90, 2), ParsedSection("办理流程。" * 90, 4)], tmp_path
    )
    assert all(len(chunk.text) <= 1000 for chunk in chunks)
    assert chunks[0].metadata["page_numbers"] == [2, 4]
    assert page_label(chunks[0].metadata) == "第2、4页"
    assert [row["content"] for row in preview_sections(documents(chunks))] == [
        "申请条件。" * 90,
        "办理流程。" * 90,
    ]


def test_exact_page_projection_prevents_false_gold_matches_from_other_page(tmp_path):
    docs = documents(
        split(
            [
                ParsedSection("第一页独有的申请条件。", 1),
                ParsedSection("第二页独有的办理流程。", 2),
            ],
            tmp_path,
        )
    )
    assert match(docs, "第二页独有的办理流程", 2)["complete"]
    assert not match(docs, "第一页独有的申请条件", 2)["complete"]
    assert match(docs, "第一页独有的申请条件", 1)["complete"]


def test_preview_and_gold_use_offsets_even_for_tiny_page_fragment_overlap(tmp_path):
    rows = [
        ParsedSection("甲" * 80 + "\n" + "乙" * 14, 1),
        ParsedSection("丙丁\n戊己庚辛壬癸子丑寅卯辰巳午未申酉戌", 2),
    ]
    chunks = split(rows, tmp_path, chunk_size=100, chunk_overlap=5)
    docs = documents(chunks)
    assert len(chunks) == 2 and text_on_page(chunks[0].text, chunks[0].metadata, 2) == "丙丁"
    preview = preview_sections(docs)
    assert [row["content"] for row in preview] == [row.text for row in rows]
    assert match(docs, rows[1].text, 2)["complete"]


def test_docx_sections_and_ppt_slides_keep_existing_boundaries(tmp_path):
    rows = [
        ParsedSection("第一节内容", section_path="第一节"),
        ParsedSection("第二节内容", section_path="第二节"),
    ]
    chunks = split_sections(rows, service(tmp_path).splitter, cross_pages=False)
    assert [(chunk.text, chunk.section_path) for chunk in chunks] == [
        (row.text, row.section_path) for row in rows
    ]


@pytest.mark.asyncio
async def test_pdf_ingestion_passes_cross_page_text_to_embedding_and_persists_strategy(tmp_path):
    path = tmp_path / "政策.pdf"
    path.write_bytes(b"fixture")
    knowledge = SimpleNamespace(replace_document=AsyncMock(return_value=str(uuid4())))
    embeddings = SimpleNamespace(
        aembed_documents=AsyncMock(side_effect=lambda rows: [[0.0] * 1024 for _ in rows])
    )
    ingestion = KnowledgeIngestionService(tmp_path, SimpleNamespace(), knowledge, embeddings)
    rows = [ParsedSection("申请条件。" * 75, 1), ParsedSection("办理流程。" * 75, 2)]
    with patch("app.ingestion.service.parse_public_document", return_value=rows):
        await ingestion._ingest_one("政策.pdf", {"title": "政策"})
    source, chunks = knowledge.replace_document.await_args.args
    assert len(chunks) == 1 and chunks[0].metadata_extra["page_numbers"] == [1, 2]
    assert source.metadata_extra["chunking"]["chunk_size"] == 1000
    assert source.metadata_extra["chunking"]["chunk_overlap"] == 100
    assert source.metadata_extra["chunking"]["cross_pages"] is True
    source.document_id, chunks[0].chunk_id = uuid4(), uuid4()
    returned = _to_document(chunks[0], source)
    assert "第1–2页" in render_documents([returned])
    registry = EvidenceRegistry("CampusAgent")
    registered = registry.register([returned])
    result = normalize_answer(
        "依据资料[[e:" + registered[0].metadata["evidence_id"] + "]]", registry.citations
    )
    assert result.content == "依据资料[1]"
    assert result.citations[0].page_numbers == [1, 2]
    assert result.citations[0].page_number is None
    assert result.citations[0].chunk_id == chunks[0].chunk_id
    assert embeddings.aembed_documents.await_args.args[0] == [chunks[0].content]


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("RUN_UNIFIED_DB_TESTS") != "1", reason="显式启用隔离 PostgreSQL 验收")
async def test_changed_chunking_replaces_current_chunks_and_unchanged_input_is_idempotent():
    from sqlalchemy import select

    async with isolated_database() as (database, _):
        repository = PostgresKnowledgeRepository(database, SimpleNamespace())

        def source(strategy):
            return KnowledgeDocument(
                source_type="local_file",
                title="政策",
                filename="政策.pdf",
                relative_path="政策.pdf",
                content_hash="a" * 64,
                metadata_extra={"chunking": strategy} if strategy else {},
            )

        def chunk(text):
            return KnowledgeChunk(
                content=text, embedding=[0.0] * 1024, chunk_index=0, chunk_count=1
            )

        old_id = await repository.replace_document(source(None), [chunk("旧切分")])
        strategy = {"chunk_size": 1000, "chunk_overlap": 100, "cross_pages": True}
        new_id = await repository.replace_document(source(strategy), [chunk("跨页新切分")])
        same_id = await repository.replace_document(source(strategy), [chunk("不应重复保存")])
        assert old_id == new_id == same_id
        async with database.session() as session:
            sources = list((await session.execute(select(KnowledgeDocument))).scalars())
            chunks = list((await session.execute(select(KnowledgeChunk))).scalars())
        assert len(sources) == 1 and str(sources[0].document_id) == old_id
        assert sources[0].relative_path == "政策.pdf"
        assert {row.content for row in chunks} == {"跨页新切分"}
        from app.api.routes.conversations import list_messages
        from app.db.models import User
        from app.db.repositories.conversations import ConversationRepository

        async with database.session() as session:
            user = User(username="cross-page-test", password_hash="unused", role="developer")
            session.add(user)
            await session.flush()
            user_id = user.user_id
        conversations = ConversationRepository(database)
        conversation = await conversations.create(str(user_id))
        await conversations.add_message(
            str(conversation.conversation_id),
            "assistant",
            "跨页来源",
            display_metadata={"keep": True},
            citations=[Citation(citation_index=1, title="政策", page_numbers=[2, 3])],
        )
        restored = await list_messages(
            conversation.conversation_id,
            SimpleNamespace(user_id=user_id),
            SimpleNamespace(conversations=conversations),
        )
        assert restored[0]["citations"][0]["page_numbers"] == [2, 3]
        assert restored[0]["citations"][0]["page_number"] is None
        assert restored[0]["display_metadata"]["keep"] is True
