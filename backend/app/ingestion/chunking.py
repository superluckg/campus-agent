"""按自然边界切分，并用字符偏移保留跨页来源。"""

from dataclasses import dataclass, field

from langchain_text_splitters import RecursiveCharacterTextSplitter

from .parser import ParsedSection

SEPARATORS = ["\n\n", "\n", "。", "；", "，", " ", ""]


@dataclass(frozen=True)
class ParsedChunk:
    text: str
    page_number: int | None
    section_path: str | None
    metadata: dict = field(default_factory=dict)


def split_sections(
    sections: list[ParsedSection],
    splitter: RecursiveCharacterTextSplitter,
    *,
    cross_pages: bool,
) -> list[ParsedChunk]:
    batches = [sections] if cross_pages else [[section] for section in sections]
    chunks = []
    for batch in batches:
        source = ""
        page_ranges = []
        for section in batch:
            if not section.text.strip():
                continue
            if source:
                source += "\n"
            start = len(source)
            source += section.text
            if section.page_number is not None:
                page_ranges.append((start, len(source), section.page_number))
        for document in splitter.create_documents([source]):
            text = document.page_content
            if not text.strip():
                continue
            start = document.metadata["start_index"]
            end = start + len(text)
            if start < 0 or source[start:end] != text:
                raise ValueError("切分结果无法定位原文，拒绝保存错误页码。")
            spans = [
                {
                    "page_number": page,
                    "start": max(start, page_start) - start,
                    "end": min(end, page_end) - start,
                    "page_start_index": max(start, page_start) - page_start,
                }
                for page_start, page_end, page in page_ranges
                if page_start < end and page_end > start
            ]
            pages = list(dict.fromkeys(span["page_number"] for span in spans))
            chunks.append(
                ParsedChunk(
                    text=text,
                    page_number=pages[0] if pages else None,
                    section_path=None if cross_pages else batch[0].section_path,
                    metadata={
                        "start_index": start,
                        "end_index": end,
                        **({"source_start_index": start} if cross_pages else {}),
                        "page_numbers": pages,
                        "page_spans": spans,
                    },
                )
            )
    return chunks
