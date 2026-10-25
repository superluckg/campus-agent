"""Reciprocal Rank Fusion。RRF只使用排名，不混合原始量纲。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from langchain_core.documents import Document

from .deduplication import document_identity


@dataclass(frozen=True)
class FusedDocument:
    document: Document
    rrf_score: float
    route_ranks: dict[str, int]


def reciprocal_rank_fusion(
    ranked_lists: Iterable[tuple[str, Sequence[Document]]],
    k: int = 60,
    limit: int = 20,
) -> list[FusedDocument]:
    if k <= 0 or limit <= 0:
        raise ValueError("RRF参数k和limit必须大于0。")
    scores: dict[str, float] = {}
    documents: dict[str, Document] = {}
    route_ranks: dict[str, dict[str, int]] = {}
    for route_name, ranked_documents in ranked_lists:
        route_seen: set[str] = set()
        for rank, document in enumerate(ranked_documents, start=1):
            identity = document_identity(document)
            if identity in route_seen:
                continue
            route_seen.add(identity)
            documents.setdefault(identity, document)
            scores[identity] = scores.get(identity, 0.0) + 1.0 / (k + rank)
            route_ranks.setdefault(identity, {})[route_name] = rank
    ranked = sorted(
        scores,
        key=lambda identity: (
            -scores[identity],
            min(route_ranks[identity].values()),
            identity,
        ),
    )[:limit]
    return [
        FusedDocument(
            document=documents[identity],
            rrf_score=scores[identity],
            route_ranks=route_ranks[identity],
        )
        for identity in ranked
    ]

