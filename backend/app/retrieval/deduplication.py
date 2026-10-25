"""按稳定标识去重 Document，不执行语义去重。"""

from __future__ import annotations

import hashlib
from typing import Iterable

from langchain_core.documents import Document


def document_identity(document: Document) -> str:
    metadata = document.metadata
    for key in ("chunk_id",):
        value = metadata.get(key)
        if value:
            return "%s:%s" % (key, value)
    digest = hashlib.sha256(document.page_content.encode("utf-8")).hexdigest()
    url = metadata.get("source_url") or metadata.get("url")
    title = metadata.get("title") or ""
    if url:
        return "web:%s|%s|%s" % (url, title, digest)
    if metadata.get("document_id") or document.id:
        return "fragment:%s|%s" % (metadata.get("document_id") or document.id, digest)
    return "content:%s" % digest


def deduplicate_documents(documents: Iterable[Document]) -> list[Document]:
    seen: set[str] = set()
    result: list[Document] = []
    for document in documents:
        identity = document_identity(document)
        if identity in seen:
            continue
        seen.add(identity)
        result.append(document)
    return result

