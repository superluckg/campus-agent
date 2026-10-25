"""本地 BM25、Embedding 召回与 RRF 融合。"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from langchain_core.documents import Document

from app.core.observability import span
from app.retrieval.bm25 import BM25Index
from app.retrieval.rrf import reciprocal_rank_fusion
from app.retrieval.scores import min_max_normalize
from app.schemas.enums import QueryStrategy
from app.schemas.retrieval import QueryPlan, RetrievalQueryExecution


class KnowledgeRepository(Protocol):
    async def dense_search(
        self,
        query: str,
        top_k: int,
    ) -> list[Document]: ...


@dataclass(frozen=True)
class LocalRetrievalOutput:
    documents: list[Document]
    route_counts: dict[str, int]
    query_cache: dict[str, list[Document]] = field(default_factory=dict)
    query_executions: list[RetrievalQueryExecution] = field(default_factory=list)


def query_marker(route: str, query: str) -> str:
    normalized = " ".join(query.split()).casefold()
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"local_knowledge:{route}:{digest}"


class LocalRetrievalService:
    def __init__(
        self,
        repository: KnowledgeRepository,
        bm25_index: BM25Index,
        route_top_k: int = 20,
        rrf_k: int = 60,
        rrf_candidate_k: int = 20,
    ) -> None:
        self.repository = repository
        self.bm25_index = bm25_index
        self.route_top_k = route_top_k
        self.rrf_k = rrf_k
        self.rrf_candidate_k = rrf_candidate_k

    async def retrieve(
        self,
        original_query: str,
        plan: QueryPlan,
        query_cache: dict[str, list[Document]] | None = None,
    ) -> LocalRetrievalOutput:
        async with span(
            "retrieval.local",
            "本地知识库召回",
            source="local_knowledge",
            query=original_query,
            query_strategy=plan.strategy.value,
        ) as details:
            result = await self._retrieve(original_query, plan, query_cache if query_cache is not None else {})
            details.update(result_count=len(result.documents), route_counts=result.route_counts)
            details["query_executions"] = result.query_executions
            if any(item.status == "failed" for item in result.query_executions):
                details["status"] = "degraded"
            elif all(item.status == "cached" for item in result.query_executions):
                details["status"] = "skipped"
            return result

    def has_new_queries(
        self,
        original_query: str,
        plan: QueryPlan,
        query_cache: dict[str, list[Document]],
    ) -> bool:
        return any(
            query_marker(route, query) not in query_cache
            for _, route, query in self.query_inputs(original_query, plan)
        )

    async def _retrieve(
        self,
        original_query: str,
        plan: QueryPlan,
        previous_cache: dict[str, list[Document]],
    ) -> LocalRetrievalOutput:
        # 逐路线保存成功结果，即使后续路线超时也可在同一 Subagent 中复用。
        query_cache = previous_cache
        executions: list[RetrievalQueryExecution] = []
        route_counts: dict[str, int] = {}
        for index, route, query in self.query_inputs(original_query, plan):
            marker = query_marker(route, query)
            node = "retrieval.bm25" if route == "bm25" else "retrieval.dense"
            label = "BM25 词法召回" if route == "bm25" else "向量召回"
            async with span(
                node,
                label,
                query=query,
                query_index=index,
                top_k=self.route_top_k,
            ) as details:
                if marker in query_cache:
                    documents = query_cache[marker]
                    status = "cached"
                    details.update(
                        status="skipped",
                        cache_hit=True,
                        reason_summary="复用已成功执行的相同路线查询。",
                    )
                else:
                    try:
                        if route == "bm25":
                            hits = self.bm25_index.search(query, self.route_top_k)
                            documents = self._annotate(
                                [hit.document for hit in hits],
                                [hit.score for hit in hits],
                                route,
                            )
                        else:
                            documents = await self.repository.dense_search(query, self.route_top_k)
                            documents = self._annotate(
                                documents,
                                [float(doc.metadata.get("score", 0.0)) for doc in documents],
                                route,
                            )
                    except Exception as exc:
                        executions.append(
                            RetrievalQueryExecution(
                                route=route,
                                query=query,
                                status="failed",
                                error=str(exc),
                            )
                        )
                        details.update(
                            status="failed",
                            error_type=type(exc).__name__,
                            error_message=str(exc),
                            result_count=0,
                        )
                        route_counts[f"{route}:q{index}"] = 0
                        continue
                    # 空结果也是成功执行；异常不写缓存，后续轮次仍能重试。
                    query_cache[marker] = documents
                    status = "executed"
                executions.append(
                    RetrievalQueryExecution(
                        route=route,
                        query=query,
                        status=status,
                        result_count=len(documents),
                    )
                )
                details.update(result_count=len(documents), candidate_count=len(documents))
                route_counts[f"{route}:q{index}"] = len(documents)

        # 跨轮次诊断使用成功查询缓存的稳定编号，避免新一轮 q1 覆盖旧一轮 q1。
        route_counts = {}
        route_indexes: dict[str, int] = {}
        for marker, documents in query_cache.items():
            route = marker.split(":", 2)[1]
            route_indexes[route] = route_indexes.get(route, 0) + 1
            route_counts[f"{route}:q{route_indexes[route]}"] = len(documents)
        ranked_lists = list(query_cache.items())

        async with span(
            "retrieval.rrf",
            "RRF 候选融合",
            route_count=len(ranked_lists),
            candidate_count=sum(len(documents) for _, documents in ranked_lists),
            top_k=self.rrf_candidate_k,
        ) as details:
            fused = reciprocal_rank_fusion(
                ranked_lists,
                k=self.rrf_k,
                limit=self.rrf_candidate_k,
            )
            details["result_count"] = len(fused)
        candidates: list[Document] = []
        for item in fused:
            metadata = dict(item.document.metadata)
            metadata["rrf_score"] = item.rrf_score
            metadata["route_ranks"] = item.route_ranks
            candidates.append(
                Document(
                    page_content=item.document.page_content,
                    metadata=metadata,
                    id=item.document.id,
                )
            )
        return LocalRetrievalOutput(
            documents=candidates,
            route_counts=route_counts,
            query_cache=query_cache,
            query_executions=executions,
        )

    @classmethod
    def query_inputs(cls, original_query: str, plan: QueryPlan) -> list[tuple[int, str, str]]:
        values = []
        seen = set()
        for index, query in enumerate(cls._queries(original_query, plan), start=1):
            lexical_query = original_query if plan.strategy == QueryStrategy.HYDE else query
            dense_query = (
                (plan.hypothetical_document or query)
                if plan.strategy == QueryStrategy.HYDE
                else query
            )
            for route, actual_query in (("bm25", lexical_query), ("dense", dense_query)):
                marker = query_marker(route, actual_query)
                if marker not in seen:
                    values.append((index, route, actual_query))
                    seen.add(marker)
        return values

    @staticmethod
    def _queries(original_query: str, plan: QueryPlan) -> list[str]:
        if plan.strategy == QueryStrategy.MULTI_QUERY:
            values = [original_query, *plan.generated_queries]
            unique: list[str] = []
            for value in values:
                cleaned = value.strip()
                if cleaned and query_marker("query", cleaned) not in {
                    query_marker("query", value) for value in unique
                }:
                    unique.append(cleaned)
            return unique[:3]
        return [plan.retrieval_query.strip() or original_query]

    @staticmethod
    def _annotate(
        documents: Sequence[Document],
        raw_scores: Sequence[float],
        route: str,
    ) -> list[Document]:
        normalized = min_max_normalize(raw_scores)
        result = []
        for document, raw_score, normalized_score in zip(
            documents, raw_scores, normalized, strict=True
        ):
            metadata = dict(document.metadata)
            metadata["retrieval_route"] = route
            metadata["route_raw_score"] = float(raw_score)
            metadata["route_normalized_score"] = float(normalized_score)
            result.append(
                Document(
                    page_content=document.page_content,
                    metadata=metadata,
                    id=document.id,
                )
            )
        return result
