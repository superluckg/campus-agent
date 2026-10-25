"""执行业务 Agent 给出的检索计划。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from langchain_core.documents import Document

from app.core.observability import current_observation, span
from app.retrieval.local.service import query_marker
from app.retrieval.rrf import reciprocal_rank_fusion
from app.schemas.enums import QueryStrategy, RetrievalSource
from app.schemas.retrieval import (
    QueryPlan,
    RetrievalDiagnostics,
    RetrievalQueryExecution,
    RetrievalRequest,
)

from app.schemas.retrieval import RetrievalExecution


class InvalidQueryPlan(ValueError):
    """在任何召回执行前拒绝不合法或越权的计划。"""


@dataclass
class RetrievalSession:
    """仅属于一次 Subagent 执行，不能放在应用单例或会话数据库中。"""

    rounds: int = 0
    calls: int = 0
    local_cache: dict[str, list[Document]] = field(default_factory=dict)
    source_cache: dict[str, list[Document]] = field(default_factory=dict)
    documents: list[Document] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class PlannedKnowledgeRetriever:
    def __init__(
        self,
        local_service,
        web_service,
        web_selector,
        memory_retriever=None,
        max_rounds=3,
        source_timeout_seconds=15,
    ):
        if not 1 <= max_rounds <= 3:
            raise ValueError("max_rounds 必须在 1 到 3 之间。")
        self.local = local_service
        self.web = web_service
        self.selector = web_selector
        self.memory = memory_retriever
        self.max_rounds = max_rounds
        self.source_timeout = source_timeout_seconds

    async def retrieve_with_diagnostics(
        self, request: RetrievalRequest, *, plan: QueryPlan, session: RetrievalSession
    ):
        async with session.lock:
            return await self._execute(request, plan, session)

    async def _execute(self, request, plan, session):
        session.calls += 1
        plan = plan.model_copy(deep=True)
        if not plan.retrieval_query.strip():
            raise InvalidQueryPlan("retrieval_query 不能为空。")
        if plan.strategy == QueryStrategy.HYDE and not (plan.hypothetical_document or "").strip():
            raise InvalidQueryPlan("HyDE 必须提供 hypothetical_document。")
        if not plan.selected_sources:
            raise InvalidQueryPlan("必须选择至少一种检索来源。")
        if len(plan.generated_queries) > 2:
            raise InvalidQueryPlan("Multi-query 最多提供两个附加查询，连同原查询最多三个。")
        if any(not item.strip() or len(item) > 4000 for item in plan.generated_queries):
            raise InvalidQueryPlan("附加查询必须为 1 到 4000 个字符。")
        web_keys = []
        if RetrievalSource.FIXED_WEB_SOURCES in plan.selected_sources:
            # 在创建任务、执行任何 I/O 前校验白名单与 Agent 权限。
            try:
                web_keys = self.selector.select(
                    plan.retrieval_query,
                    request.agent_name,
                    requested_keys=plan.web_source_keys or None,
                )
            except ValueError as exc:
                raise InvalidQueryPlan(str(exc)) from exc
        elif plan.web_source_keys:
            raise InvalidQueryPlan("web_source_keys 需要同时选择 fixed_web_sources。")
        local_selected = RetrievalSource.LOCAL_KNOWLEDGE in plan.selected_sources
        source_inputs = [("fixed_web_sources", key, key) for key in web_keys]
        if RetrievalSource.MEMORY in plan.selected_sources:
            if self.memory is None:
                raise InvalidQueryPlan("本次执行没有可用的记忆检索来源。")
            source_inputs.append(
                ("memory", query_marker("memory", plan.retrieval_query), plan.retrieval_query)
            )
        has_new = local_selected and self.local.has_new_queries(
            request.query, plan, session.local_cache
        )
        has_new = has_new or any(key not in session.source_cache for _, key, _ in source_inputs)
        exhausted = has_new and session.rounds >= self.max_rounds
        observation = current_observation()
        if observation:
            await observation.emit(
                "retrieval.plan.selected",
                {
                    "protocol": "agentic",
                    "agent": request.agent_name,
                    "call_number": session.calls,
                    "plan": plan,
                    "total_rounds": session.rounds,
                    "budget_exhausted": exhausted,
                },
            )
        if exhausted:
            return RetrievalExecution(
                list(session.documents),
                RetrievalDiagnostics(
                    rounds=0,
                    total_rounds=session.rounds,
                    protocol="agentic",
                    query_plan=plan,
                    evidence_sufficient=None,
                    stop_reason="max_rounds",
                    finished_reason="三批实际检索预算已用完；请依据已有证据作答并说明缺口。",
                ),
            )
        async with span(
            "retrieval.retrieve",
            "执行检索计划",
            agent=request.agent_name,
            protocol="agentic",
            call_number=session.calls,
            round_number=session.rounds + int(has_new),
            query_plan=plan,
        ) as details:
            if has_new:
                # 在实际执行前记账，失败、超时或取消均不会退还批次。
                session.rounds += 1
                if observation:
                    await observation.emit(
                        "retrieval.round.started",
                        {
                            "protocol": "agentic",
                            "agent": request.agent_name,
                            "span_id": details.get("span_id"),
                            "round_number": session.rounds,
                        },
                    )
            tasks = []
            labels = []
            if local_selected:
                tasks.append(
                    asyncio.wait_for(
                        self.local.retrieve(request.query, plan, session.local_cache),
                        self.source_timeout,
                    )
                )
                labels.append(("local_knowledge", "", plan.retrieval_query))

            async def source_load(kind, key, value):
                if key not in session.source_cache:
                    if kind == "memory":
                        docs = await self.memory.retrieve(value, str(request.conversation_id), 3)
                    elif hasattr(self.web, "retrieve_source"):
                        docs = await self.web.retrieve_source(value)
                    else:
                        docs = await self.web.retrieve_sources([value])
                    session.source_cache[key] = list(docs)
                return session.source_cache[key]

            cached_sources = set(session.source_cache)
            for item in source_inputs:
                tasks.append(asyncio.wait_for(source_load(*item), self.source_timeout))
                labels.append(item)
            outputs = await asyncio.gather(*tasks, return_exceptions=True)
            executions = []
            errors = []
            for (kind, key, value), output in zip(labels, outputs, strict=True):
                if isinstance(output, BaseException):
                    if isinstance(output, asyncio.CancelledError):
                        raise output
                    error = str(output) or type(output).__name__
                    errors.append(f"{kind}: {error}")
                    executions.append(
                        RetrievalQueryExecution(
                            source=kind, route=kind, query=value, status="failed", error=error
                        )
                    )
                elif kind == "local_knowledge":
                    session.local_cache.update(output.query_cache)
                    executions.extend(output.query_executions)
                    errors.extend(
                        f"{item.route}: {item.error}"
                        for item in output.query_executions
                        if item.status == "failed"
                    )
                else:
                    executions.append(
                        RetrievalQueryExecution(
                            source=kind,
                            route=key,
                            query=value,
                            status="cached" if key in cached_sources else "executed",
                            result_count=len(output),
                        )
                    )
            ranked = {**session.local_cache, **session.source_cache}
            top_k = min(self.local.rrf_candidate_k, 20)
            async with span("retrieval.merge", "统一候选融合与去重", top_k=top_k) as merge:
                fused = reciprocal_rank_fusion(ranked.items(), k=self.local.rrf_k, limit=top_k)
                candidates = [
                    Document(
                        id=item.document.id,
                        page_content=item.document.page_content,
                        metadata={
                            **item.document.metadata,
                            "rrf_score": item.rrf_score,
                            "route_ranks": item.route_ranks,
                        },
                    )
                    for item in fused
                ]
                merge["result_count"] = len(candidates)
            session.documents = candidates
            reason = "executed" if has_new else "cached"
            if errors:
                reason = (
                    "retrieval_failed"
                    if all(item.status == "failed" for item in executions)
                    else "partial_failure"
                )
            diagnostics = RetrievalDiagnostics(
                rounds=int(has_new),
                total_rounds=session.rounds,
                protocol="agentic",
                query_plan=plan,
                evidence_sufficient=None,
                stop_reason=reason,
                finished_reason=(
                    "本批检索结束。" if has_new else "复用成功查询缓存，未消耗检索批次。"
                )
                + f"已用 {session.rounds}/{self.max_rounds} 批；请自行判断补查或作答。",
                query_executions=executions,
                errors=errors,
                source_counts={key: len(docs) for key, docs in ranked.items()},
            )
            details.update(
                output=diagnostics,
                result_count=len(session.documents),
                status="degraded" if errors else "success" if has_new else "skipped",
            )
            return RetrievalExecution(list(session.documents), diagnostics)
