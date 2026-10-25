"""单页与跨页资料的物理页码和片段出处。"""

from collections.abc import Mapping
from typing import Any


def page_numbers(metadata: Mapping[str, Any]) -> list[int]:
    values = metadata.get("page_numbers") or [metadata.get("page_number") or metadata.get("page")]
    return sorted({value for value in values if isinstance(value, int) and value > 0})


def page_label(metadata: Mapping[str, Any]) -> str:
    pages = page_numbers(metadata)
    if not pages:
        return ""
    groups = []
    first = previous = pages[0]
    for page in pages[1:]:
        if page == previous + 1:
            previous = page
            continue
        groups.append(str(first) if first == previous else f"{first}–{previous}")
        first = previous = page
    groups.append(str(first) if first == previous else f"{first}–{previous}")
    return "第" + "、".join(groups) + "页"


def page_fragments(
    content: str, metadata: Mapping[str, Any]
) -> list[tuple[int | None, str, int | None]]:
    """返回页码、该页原文及页内起始偏移，不把跨页正文归给第一页。"""
    spans = metadata.get("page_spans")
    if spans:
        return [
            (
                span["page_number"],
                content[span["start"] : span["end"]],
                span.get("page_start_index"),
            )
            for span in spans
        ]
    return [(metadata.get("page_number") or metadata.get("page"), content, None)]


def text_on_page(content: str, metadata: Mapping[str, Any], page: int) -> str:
    return "\n".join(
        text for number, text, _ in page_fragments(content, metadata) if number == page
    )
