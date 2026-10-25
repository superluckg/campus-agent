"""中文分词、词法召回与可原子更新的 BM25 内存索引。"""

from __future__ import annotations

import logging
import re
import threading
import unicodedata
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

import jieba
from langchain_core.documents import Document
from rank_bm25 import BM25Okapi


jieba.setLogLevel(logging.WARNING)

_CHINESE_BLOCK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
_TOKEN_BLOCK = re.compile(
    r"[\u3400-\u4dbf\u4e00-\u9fff]+|"
    r"[a-zA-Z]+(?:[-_.][a-zA-Z0-9]+)*|"
    r"\d+(?:[-_/.:]\d+)+|"
    r"\d+(?:\.\d+)?(?:%|％)?"
)


def tokenize_for_bm25(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    tokens: list[str] = []
    for block in _TOKEN_BLOCK.findall(normalized):
        if _CHINESE_BLOCK.fullmatch(block):
            tokens.extend(token.strip() for token in jieba.lcut(block, cut_all=False) if token.strip())
            tokens.extend(block[index : index + 2] for index in range(max(0, len(block) - 1)))
        else:
            tokens.append(block)
    return tokens


@dataclass(frozen=True)
class BM25Hit:
    document: Document
    rank: int
    score: float


class BM25Index:
    """单进程内存索引；入库完成后原子替换索引快照。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._documents: tuple[Document, ...] = ()
        self._tokenized_documents: tuple[tuple[str, ...], ...] = ()
        self._model: Optional[BM25Okapi] = None

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._documents)

    def rebuild(self, documents: Iterable[Document]) -> int:
        snapshot = tuple(documents)
        tokenized = tuple(
            tuple(tokenize_for_bm25(document.page_content)) for document in snapshot
        )
        model = BM25Okapi(tokenized) if snapshot and any(tokenized) else None
        with self._lock:
            self._documents = snapshot
            self._tokenized_documents = tokenized
            self._model = model
        return len(snapshot)

    def search(self, query: str, top_k: int = 20) -> list[BM25Hit]:
        if top_k <= 0:
            raise ValueError("top_k必须大于0。")
        tokens = tokenize_for_bm25(query)
        if not tokens:
            return []
        with self._lock:
            documents: Sequence[Document] = self._documents
            tokenized_documents = self._tokenized_documents
            model = self._model
            if model is None:
                return []
            scores = model.get_scores(tokens)

        query_tokens = set(tokens)
        overlaps = [
            len(query_tokens.intersection(document_tokens))
            for document_tokens in tokenized_documents
        ]
        candidates = [index for index, overlap in enumerate(overlaps) if overlap > 0]
        indices = sorted(
            candidates,
            key=lambda index: (-float(scores[index]), -overlaps[index], index),
        )[:top_k]
        hits: list[BM25Hit] = []
        for rank, index in enumerate(indices, start=1):
            score = float(scores[index])
            hits.append(BM25Hit(document=documents[index], rank=rank, score=score))
        return hits
