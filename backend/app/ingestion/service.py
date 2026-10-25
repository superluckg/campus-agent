"""扫描原始目录、确认文档并入库；扫描不调用生成式模型。"""

from __future__ import annotations

import hashlib
from pathlib import Path

from langchain_core.embeddings import Embeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.db.models import KnowledgeChunk, KnowledgeDocument
from app.db.repositories.ingestion import IngestionRepository
from app.db.repositories.knowledge import PostgresKnowledgeRepository
from app.schemas.ingestion import DocumentMetadata

from .parser import parse_public_document
from .chunking import SEPARATORS, split_sections


class KnowledgeIngestionService:
    ALLOWED_SUFFIXES = {".pdf", ".docx", ".pptx", ".md", ".txt"}

    def __init__(
        self,
        raw_root: Path,
        repository: IngestionRepository,
        knowledge: PostgresKnowledgeRepository,
        embeddings: Embeddings,
        chunk_size: int = 1000,
        chunk_overlap: int = 100,
    ) -> None:
        self.raw_root = raw_root.resolve()
        self.repository = repository
        self.knowledge = knowledge
        self.embeddings = embeddings
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=SEPARATORS,
            add_start_index=True,
        )

    async def scan(self) -> str:
        items = []
        for path in sorted(self.raw_root.rglob("*")):
            if not path.is_file() or path.suffix.casefold() not in self.ALLOWED_SUFFIXES:
                continue
            metadata = DocumentMetadata(title=path.stem)
            items.append(
                {
                    "relative_path": path.relative_to(self.raw_root).as_posix(),
                    "detected_metadata": metadata.model_dump(mode="json"),
                }
            )
        job = await self.repository.create_job(
            items,
            {"raw_root": str(self.raw_root), "allowed_suffixes": sorted(self.ALLOWED_SUFFIXES)},
        )
        return str(job.job_id)

    async def ingest_confirmed(self, job_id: str) -> None:
        items = await self.repository.list_items(job_id)
        for item in items:
            if item.status != "confirmed":
                continue
            try:
                document_id = await self._ingest_one(item.relative_path, item.confirmed_metadata)
                await self.repository.finish_item(str(item.item_id), document_id=document_id)
            except Exception as exc:
                await self.repository.finish_item(str(item.item_id), error=str(exc))

    async def _ingest_one(self, relative_path: str, metadata: dict) -> str:
        path = (self.raw_root / relative_path).resolve()
        path.relative_to(self.raw_root)
        if not path.is_file():
            raise FileNotFoundError("待入库文件不存在。")
        raw = path.read_bytes()
        content_hash = hashlib.sha256(raw).hexdigest()
        sections = parse_public_document(path)
        cross_pages = path.suffix.casefold() == ".pdf"
        split_rows = split_sections(sections, self.splitter, cross_pages=cross_pages)
        if not split_rows:
            raise ValueError("文档没有可入库文本。")
        vectors = await self.embeddings.aembed_documents([row.text for row in split_rows])
        document = KnowledgeDocument(
            source_type="local_file",
            title=metadata["title"],
            filename=path.name,
            relative_path=relative_path,
            content_hash=content_hash,
            metadata_extra={
                "ingestion": "confirmed",
                "chunking": {
                    "strategy": "recursive-character",
                    "chunk_size": self.chunk_size,
                    "chunk_overlap": self.chunk_overlap,
                    "cross_pages": cross_pages,
                    "separators": SEPARATORS,
                },
            },
        )
        chunks = [
            KnowledgeChunk(
                content=row.text,
                embedding=vector,
                page_number=row.page_number,
                section_path=row.section_path,
                chunk_index=index,
                chunk_count=len(split_rows),
                metadata_extra=row.metadata,
            )
            for index, (row, vector) in enumerate(zip(split_rows, vectors, strict=True))
        ]
        return await self.knowledge.replace_document(document, chunks)
