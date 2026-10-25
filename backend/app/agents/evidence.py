"""请求内的证据身份、引用协议与正文/来源的原子编号。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from uuid import UUID

from langchain_core.documents import Document

from app.core.source_pages import page_numbers
from app.schemas.agent import Citation

MARKER = re.compile(r"\[\[e:([^\]\n]+)\]\]")
# 不触碰代码、链接及转义的普通方括号。
PROTECTED = re.compile(
    r"```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]*`|!?\[[^\]\n]*\]\([^\n]*?\)|\\\[[^\]\n]*\]"
)
TAIL = re.compile(r"(?m)^(?:#{1,6}\s*|\*\*)?参考来源(?:\*\*)?\s*[:：]?\s*(?:\n|(?=\[))")
NUMBER = re.compile(r"(?<![\[\\])\[(\d+)\](?![\](])")


def evidence_identity(document: Document) -> str:
    metadata = document.metadata
    if metadata.get("chunk_id"):
        return "chunk:" + str(metadata["chunk_id"])
    # 网页同 URL 的不同片段、上传文件的不同已返回切片均独立编号。
    value = {
        "kind": metadata.get("source_type"),
        "document": metadata.get("document_id"),
        "file": metadata.get("file_id"),
        "url": metadata.get("source_url"),
        "id": document.id,
        "pages": page_numbers(metadata),
        "section": metadata.get("section_path"),
        "offset": metadata.get("slice_start"),
        "content": document.page_content,
    }
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _uuid(value):
    try:
        return UUID(str(value)) if value else None
    except ValueError:
        return None


class EvidenceRegistry:
    def __init__(self, namespace: str):
        self.namespace = namespace
        self.identities: dict[str, str] = {}
        self.citations: dict[str, Citation] = {}

    def register(self, documents: list[Document]) -> list[Document]:
        output = []
        for document in documents:
            identity = evidence_identity(document)
            key = self.identities.get(identity)
            if key is None:
                key = f"{self.namespace}:{len(self.identities) + 1}"
                self.identities[identity] = key
                meta = document.metadata
                pages = page_numbers(meta)
                self.citations[key] = Citation(
                    citation_index=1,
                    title=str(meta.get("title") or meta.get("filename") or "未命名资料"),
                    document_id=_uuid(meta.get("document_id")),
                    chunk_id=_uuid(meta.get("chunk_id")),
                    filename=meta.get("filename"),
                    page_number=pages[0] if len(pages) == 1 else None,
                    page_numbers=pages,
                    section_path=meta.get("section_path"),
                    source_url=meta.get("source_url"),
                    quoted_excerpt=document.page_content[:1000],
                    fetched_at=meta.get("fetched_at"),
                    evidence_identity=identity,
                )
            output.append(
                Document(
                    id=document.id,
                    page_content=document.page_content,
                    metadata={**document.metadata, "evidence_id": key},
                )
            )
        return output


@dataclass
class NormalizedAnswer:
    content: str
    citations: list[Citation]
    metadata: dict
    evidence_answer: str


async def publish_answer(observer, answer):
    if hasattr(observer, "set_citations"):
        await observer.set_citations(answer.metadata, answer.citations)
    await observer.update(answer.content)


def normalize_answer(
    text: str, evidence: dict[str, Citation], *, preview=False
) -> NormalizedAnswer:
    # 清理模型在正文尾部重复生成的来源目录。
    protected_ranges = [(match.start(), match.end()) for match in PROTECTED.finditer(text)]
    for match in TAIL.finditer(text):
        if any(start <= match.start() < end for start, end in protected_ranges):
            continue
        tail = text[match.end() :].strip()
        if not tail or re.match(r"(?:[-*]\s*)?(?:\[\d+\]|\[\[e:)", tail):
            text = text[: match.start()].rstrip()
            break
    if preview:
        # 逐字符截断可能停在 '['、'[[e:'、甚至标识中间，缓冲整个尾部。
        last = text.rfind("[")
        opening = text.rfind("[[")
        if opening >= 0 and "]]" not in text[opening:]:
            text = text[:opening]
        elif last == len(text) - 1:
            text = text[:last]
    citations: list[Citation] = []
    indexes: dict[str, int] = {}
    invalid: list[str] = []
    pieces = []
    raw_pieces = []

    def replace(match):
        key = match.group(1)
        citation = evidence.get(key)
        if citation is None:
            invalid.append(key)
            return ""
        # 跨 Agent 对同一真实片段再次去重；绝不按文件名猜测匹配。
        identity = citation.evidence_identity or (
            str(citation.chunk_id)
            if citation.chunk_id
            else json.dumps(
                citation.model_dump(mode="json", exclude={"citation_index"}), sort_keys=True
            )
        )
        if identity not in indexes:
            indexes[identity] = len(citations) + 1
            citations.append(citation.model_copy(update={"citation_index": indexes[identity]}))
        return f"[{indexes[identity]}]"

    cursor = 0
    for protected in [*PROTECTED.finditer(text), None]:
        end = protected.start() if protected else len(text)
        segment = text[cursor:end]

        # 模型误用数字引用时无法证明其对应关系，清除并记录，禁止悄悄保留错误编号。
        def invalid_number(match):
            invalid.append(match.group(0))
            return ""

        segment = NUMBER.sub(invalid_number, segment)
        raw_segment = MARKER.sub(lambda m: m.group(0) if m.group(1) in evidence else "", segment)
        rendered_segment = MARKER.sub(replace, segment)
        if not preview and "[[e:" in rendered_segment:
            invalid.append("malformed_marker")
            malformed = r"\[\[e:[^\]\n]*(?:\]\]?|(?=\n|$))"
            rendered_segment = re.sub(malformed, "", rendered_segment)
            raw_segment = re.sub(malformed, "", raw_segment)
        raw_pieces.append(raw_segment)
        pieces.append(rendered_segment)
        if protected:
            pieces.append(protected.group(0))
            raw_pieces.append(protected.group(0))
            cursor = protected.end()
    # 未闭合的内部标识不能在最终回答中泄漏。
    content = "".join(pieces)
    raw = "".join(raw_pieces)
    metadata = {"render_citations": True}
    if invalid:
        metadata.update(
            citation_warning="部分引用无法定位，相关内容需核验。",
            invalid_evidence_ids=list(dict.fromkeys(invalid)),
        )
    return NormalizedAnswer(content, citations, metadata, raw)
