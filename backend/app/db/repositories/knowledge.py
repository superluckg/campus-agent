"""公共知识库查询；向量距离由 pgvector 在 PostgreSQL 内计算。"""

from __future__ import annotations

from typing import Sequence

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from sqlalchemy import delete, select, text

from app.core.observability import span
from app.db.models import KnowledgeChunk, KnowledgeDocument
from app.db.session import Database


def _to_document(chunk: KnowledgeChunk, source: KnowledgeDocument, score: float = 0.0) -> Document:
    return Document(
        id=str(chunk.chunk_id),
        page_content=chunk.content,
        metadata={
            **{key: value for key, value in (getattr(chunk, "metadata_extra", None) or {}).items()
               if key in {"page_numbers", "page_spans", "start_index", "end_index", "source_start_index"}},
            "chunk_id": str(chunk.chunk_id),
            "document_id": str(source.document_id),
            "title": source.title,
            "filename": source.filename,
            "source_url": source.source_url,
            "page_number": chunk.page_number,
            "section_path": chunk.section_path,
            "published_at": source.published_at.isoformat() if source.published_at else None,
            "score": score,
        },
    )


class PostgresKnowledgeRepository:
    def __init__(self, database: Database, embeddings: Embeddings) -> None:
        self.database = database
        self.embeddings = embeddings

    async def dense_search(
        self,
        query: str,
        top_k: int,
    ) -> list[Document]:
        async with span("retrieval.embedding", "查询向量化", query=query) as details:
            vector = await self.embeddings.aembed_query(query)
            details["vector_dimensions"] = len(vector)
        distance = KnowledgeChunk.embedding.cosine_distance(vector)
        statement = select(KnowledgeChunk, KnowledgeDocument, distance.label("distance")).join(
            KnowledgeDocument, KnowledgeDocument.document_id == KnowledgeChunk.document_id
        )
        statement = statement.order_by(distance).limit(top_k)
        async with span(
            "retrieval.vector_search", "向量数据库查询", top_k=top_k,
        ) as details:
            async with self.database.session() as session:
                rows = (await session.execute(statement)).tuples()
                documents = [
                    _to_document(chunk, source, score=max(0.0, 1.0 - float(distance_value)))
                    for chunk, source, distance_value in rows
                ]
            details["result_count"] = len(documents)
            return documents

    async def load_active_chunks(self) -> list[Document]:
        statement = select(KnowledgeChunk, KnowledgeDocument).join(
            KnowledgeDocument, KnowledgeDocument.document_id == KnowledgeChunk.document_id
        )
        statement = statement.order_by(
            KnowledgeDocument.document_id, KnowledgeChunk.chunk_index
        )
        async with self.database.session() as session:
            rows = (await session.execute(statement)).tuples()
            return [_to_document(chunk, source) for chunk, source in rows]

    async def load_evaluation_chunks(self) -> list[Document]:
        """评测原文拼接需要顺序；不改变生产检索的 metadata。"""
        statement = select(KnowledgeChunk, KnowledgeDocument).join(
            KnowledgeDocument, KnowledgeDocument.document_id == KnowledgeChunk.document_id
        ).order_by(KnowledgeDocument.document_id, KnowledgeChunk.chunk_index)
        async with self.database.session() as session:
            rows = (await session.execute(statement)).tuples()
            documents = []
            for chunk, source in rows:
                document = _to_document(chunk, source)
                document.metadata["chunk_index"] = chunk.chunk_index
                documents.append(document)
            return documents

    async def replace_document(
        self,
        document: KnowledgeDocument,
        chunks: Sequence[KnowledgeChunk],
    ) -> str:
        async with self.database.session() as session:
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": "knowledge:" + document.source_type + ":" + document.relative_path},
            )
            result = await session.execute(
                select(KnowledgeDocument)
                .where(
                    KnowledgeDocument.source_type == document.source_type,
                    KnowledgeDocument.relative_path == document.relative_path,
                )
                .with_for_update()
            )
            existing = result.scalar_one_or_none()
            if existing is not None:
                if (existing.content_hash == document.content_hash
                    and (existing.metadata_extra or {}).get("chunking")
                    == (document.metadata_extra or {}).get("chunking")):
                    existing.title = document.title
                    return str(existing.document_id)
                for field in (
                    "title", "filename", "source_url", "published_at", "content_hash", "metadata_extra"
                ):
                    setattr(existing, field, getattr(document, field))
                await session.execute(
                    delete(KnowledgeChunk).where(KnowledgeChunk.document_id == existing.document_id)
                )
                current = existing
            else:
                session.add(document)
                current = document
            await session.flush()
            for chunk in chunks:
                chunk.document_id = current.document_id
                session.add(chunk)
            identifier = str(current.document_id)
        return identifier
