"""纯评分函数；排名来自同一次返回，标准标签与被测结果分离。"""

import hashlib
import json
import unicodedata
from difflib import SequenceMatcher



def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def normalize(text: str) -> str:
    return "".join(char for char in unicodedata.normalize("NFKC", text).casefold() if char.isalnum())


def coverage(evidence: str, contents: list[str]) -> float | None:
    target = normalize(evidence)
    if not target:
        return None
    positions = bytearray(len(target))
    for content in contents:
        for block in SequenceMatcher(None, target, normalize(content), autojunk=False).get_matching_blocks():
            if block.size >= 4:
                positions[block.a:block.a + block.size] = b"\x01" * block.size
    return sum(positions) / len(target)


def unit_key(document) -> str:
    metadata = document.metadata
    if metadata.get("chunk_id"):
        return "local:" + str(metadata["chunk_id"])
    url = metadata.get("source_url") or metadata.get("url")
    if url:
        content_hash = hashlib.sha256(document.page_content.encode("utf-8")).hexdigest()
        return "web:%s:%s:%s" % (metadata.get("source_key", ""), url, content_hash)
    return "unassigned:" + digest({"id": document.id, "text": document.page_content})


def serialize_unit(document, *, include_scores=False) -> dict:
    metadata = {key: value for key, value in document.metadata.items() if include_scores or key != "score"}
    return {"key": unit_key(document), "id": document.id, "content": document.page_content,
            "metadata": metadata, "source_kind": "local" if metadata.get("chunk_id") else "web"}


def local_fingerprint(units: list[dict]) -> str:
    return digest(sorted((unit for unit in units if unit["source_kind"] == "local"),
                         key=lambda item: item["key"]))
