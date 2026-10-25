"""从独立完整资料匹配原文，绝不读取被测检索输出。"""

from collections import defaultdict
from pathlib import PureWindowsPath
import unicodedata
from app.core.source_pages import page_fragments, page_numbers, text_on_page

from .corpus import eligible_unit
from .scoring import normalize, digest

MATCHING_CONTRACT = "original-evidence"


def anchored(evidence, unit):
    metadata = unit["metadata"]
    filename = evidence.get("filename")
    if (
        filename
        and unicodedata.normalize("NFKC", PureWindowsPath(filename).name).casefold()
        != unicodedata.normalize(
            "NFKC", PureWindowsPath(str(metadata.get("filename") or "")).name
        ).casefold()
    ):
        return False
    if evidence.get("page_number") is not None and evidence["page_number"] not in page_numbers(
        metadata
    ):
        return False
    if evidence.get("source_key") and evidence["source_key"] != metadata.get("source_key"):
        return False
    if evidence.get("source_url") and evidence["source_url"].rstrip("/") != str(
        metadata.get("source_url") or ""
    ).rstrip("/"):
        return False
    return True


def streams(units, normalized=None):
    groups = defaultdict(list)
    for position, unit in enumerate(units):
        metadata = unit["metadata"]
        group = (
            ("local", metadata.get("document_id") or metadata.get("filename") or unit["key"])
            if unit["source_kind"] == "local"
            else ("web", unit["key"])
        )
        groups[group].append((metadata.get("chunk_index", position), unit))
    for rows in groups.values():
        text, spans, previous, source_end = "", [], None, None
        for index, unit in sorted(rows, key=lambda row: row[0]):
            content = (
                normalized[unit["key"]] if normalized is not None else normalize(unit["content"])
            )
            if not content:
                continue
            # 过滤掉中间 chunk 后不能把不相邻的原文拼成证据。
            if previous is not None and index != previous + 1:
                if text:
                    yield text, spans
                text, spans, source_end = "", [], None
            source_start = unit["metadata"].get("source_start_index")
            if source_start is not None and source_end is not None:
                if source_start > source_end:
                    if text:
                        yield text, spans
                    text, spans = "", []
                    overlap = 0
                else:
                    raw_overlap = max(0, source_end - source_start)
                    overlap = len(normalize(unit["content"][:raw_overlap]))
            else:
                overlap = next(
                    (
                        size
                        for size in range(min(len(text), len(content)), 3, -1)
                        if text.endswith(content[:size])
                    ),
                    0,
                )
            start = len(text) - overlap
            text += content[overlap:]
            spans.append((start, len(text), unit["key"]))
            previous = index
            source_end = source_start + len(unit["content"]) if source_start is not None else None
        if text:
            yield text, spans


class OriginalEvidenceMatcher:
    def __init__(self, corpus):
        self.corpus = corpus
        self.normalized = {unit["key"]: normalize(unit["content"]) for unit in corpus["units"]}
        self.stream_cache = {}

    def match(self, case):
        details, relevant = [], set()
        for index, evidence in enumerate(case["evidence"], 1):
            target, matched = normalize(evidence["text"]), set()
            if target:
                cache_key = digest(
                    {
                        "agent": case["agent_name"],
                        "anchors": {
                            key: evidence.get(key)
                            for key in ("filename", "page_number", "source_url", "source_key")
                        },
                    }
                )
                if cache_key not in self.stream_cache:
                    allowed = [
                        unit
                        for unit in self.corpus["units"]
                        if eligible_unit(case, unit, self.corpus) and anchored(evidence, unit)
                    ]
                    if evidence.get("page_number") is not None:
                        # 页码锚点只能匹配该页正文，不能借用同一chunk里另一页的文字。
                        projected = []
                        for unit in allowed:
                            fragments = [
                                fragment
                                for fragment in page_fragments(unit["content"], unit["metadata"])
                                if fragment[0] == evidence["page_number"]
                            ]
                            projected.append(
                                {
                                    **unit,
                                    "content": text_on_page(
                                        unit["content"], unit["metadata"], evidence["page_number"]
                                    ),
                                    "metadata": {
                                        **unit["metadata"],
                                        "source_start_index": fragments[0][2]
                                        if fragments
                                        else None,
                                    },
                                }
                            )
                        allowed = projected
                        self.stream_cache[cache_key] = list(streams(allowed))
                    else:
                        self.stream_cache[cache_key] = list(streams(allowed, self.normalized))
                for text, spans in self.stream_cache[cache_key]:
                    offset = text.find(target)
                    while offset >= 0:
                        matched.update(
                            key
                            for start, end, key in spans
                            if start < offset + len(target) and end > offset
                        )
                        offset = text.find(target, offset + 1)
            relevant.update(matched)
            details.append(
                {
                    "evidence_index": index,
                    "matched": bool(matched),
                    "unit_keys": sorted(matched),
                    "reason": None if matched else "当前资料中未完整定位该原文证据",
                }
            )
        complete = bool(details) and all(item["matched"] for item in details)
        return {
            "contract": MATCHING_CONTRACT,
            "complete": complete,
            "relevant_unit_keys": sorted(relevant),
            "evidence_details": details,
            "reason": None
            if complete
            else "部分标准证据未完整定位；Hit、Precision、Chunk Recall、MRR 为 N/A",
        }
