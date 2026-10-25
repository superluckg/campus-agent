"""确定性 Orchestrator：Context → Intent → Router → Skills → Agents → Composer。"""

from __future__ import annotations

import asyncio
import logging
import time
import inspect
from dataclasses import dataclass
from typing import Dict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from app.core.response_stream import ChatRunContext

from app.routing.router import AgentRouter
from app.schemas.agent import FinalResponse
from app.schemas.enums import RunStatus
from app.skills.manager import SkillManager
from app.services.direct_reply import DirectReplyService
from app.core.observability import span, current_observation

from .dependencies import (
    AgentExecutionService,
    ContextLoader,
    IntentService,
    MemoryRecallService,
    ResponseCompositionService,
    SkillScoreService,
    TraceSink,
)
from .state import GlobalState


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GlobalGraphDependencies:
    context_loader: ContextLoader
    intent_service: IntentService
    router: AgentRouter
    memory_service: MemoryRecallService
    skill_manager: SkillManager
    skill_score_service: SkillScoreService
    agent_service: AgentExecutionService
    composer: ResponseCompositionService
    trace: TraceSink
    direct_reply: DirectReplyService | None = None
    single_agent_timeout_seconds: int = 60
    multi_agent_timeout_seconds: int = 90


def build_global_graph(dependencies: GlobalGraphDependencies):
    async def load_context(state: GlobalState):
        recent, summary, files = await asyncio.gather(
            dependencies.context_loader.load_recent(state["user_id"], state["conversation_id"]),
            dependencies.context_loader.load_summary(state["conversation_id"]),
            dependencies.context_loader.load_uploaded_files(
                state["user_id"], state["conversation_id"]
            ),
        )
        if (
            recent
            and recent[-1].get("role") == "user"
            and recent[-1].get("content") == state["query"]
        ):
            recent = recent[:-1]
        await dependencies.trace.event(
            state["request_id"],
            "context.loaded",
            {"recent_count": len(recent[-8:]), "file_count": len(files)},
        )
        return {
            "recent_messages": recent[-8:],
            "conversation_summary": summary,
            "uploaded_files": files,
        }

    async def recognize_intent(state: GlobalState):
        started = time.perf_counter()
        result = await dependencies.intent_service.recognize(
            query=state["query"],
            recent_messages=state["recent_messages"],
            conversation_summary=state["conversation_summary"],
            uploaded_file_ids=[item["file_id"] for item in state["uploaded_files"]],
            uploaded_filenames=[item["filename"] for item in state["uploaded_files"]],
        )
        await dependencies.trace.event(
            state["request_id"],
            "intent.completed",
            {**result.model_dump(mode="json"), "duration_ms": (time.perf_counter() - started) * 1000,
             "usage": result.model_usage},
        )
        return {"intent_result": result}

    async def route_request(state: GlobalState):
        result = state["intent_result"]
        if result is None:
            raise RuntimeError("意图识别结果为空。")
        plan = dependencies.router.route(result)
        await dependencies.trace.event(
            state["request_id"],
            "route.completed",
            plan.model_dump(mode="json"),
        )
        return {"route_plan": plan}

    def after_route(state: GlobalState) -> str:
        plan = state["route_plan"]
        if plan is None:
            return "direct"
        if plan.route == "clarify":
            return "clarify"
        if plan.route == "direct_response":
            return "direct"
        return "agents"

    async def clarify(state: GlobalState, runtime: Runtime[ChatRunContext]):
        plan = state["route_plan"]
        content = plan.clarification_question if plan else "请补充更多信息。"
        if runtime.context and runtime.context.observer:
            await runtime.context.observer.update(content or "请补充更多信息。")
        return {
            "final_response": FinalResponse(
                content=content or "请补充更多信息。",
                status=RunStatus.SUCCESS,
            )
        }

    async def direct_response(state: GlobalState, runtime: Runtime[ChatRunContext]):
        started = time.perf_counter()
        intent = state["intent_result"]
        try:
            if dependencies.direct_reply is None or intent is None:
                raise RuntimeError("聊天回复服务未配置。")
            stream_options = (
                {"observer": runtime.context.observer}
                if runtime.context and runtime.context.observer else {}
            )
            observation = current_observation()
            if observation is not None:
                await observation.emit("context.injected", {"agent": "direct_response",
                    "recent_messages": state["recent_messages"][-8:],
                    "conversation_summary": state["conversation_summary"][:12000], "long_term_memories": [], "skills": []})
            reply = await dependencies.direct_reply.reply(
                state["query"], intent.primary_intent, state["recent_messages"],
                state["conversation_summary"], intent.policy_decision, **stream_options,
            )
            await dependencies.trace.event(state["request_id"], "direct_reply.completed", {
                "intent": intent.primary_intent.value, "policy_decision": intent.policy_decision,
                "duration_ms": (time.perf_counter() - started) * 1000, "usage": reply.usage,
            })
            return {"final_response": FinalResponse(content=reply.content, status=RunStatus.SUCCESS)}
        except Exception as exc:
            # 已展示正文的流式请求由聊天服务发布失败终止，保留原草稿。
            if runtime.context and runtime.context.observer:
                raise
            logger.exception("Direct reply generation failed")
            await dependencies.trace.event(state["request_id"], "direct_reply.failed", {
                "duration_ms": (time.perf_counter() - started) * 1000,
                "code": "DIRECT_REPLY_FAILED", "message": "回复生成暂时失败。",
                "error_type": type(exc).__name__, "error_message": str(exc),
            })
            return {"final_response": FinalResponse(
                content="回复生成暂时失败，请稍后重试。我目前可以帮助处理求职、校园事务和学习相关问题。",
                status=RunStatus.FAILED,
            )}

    async def recall_memory(state: GlobalState):
        plan = state["route_plan"]
        agent_names = [assignment.agent_name for assignment in plan.assignments] if plan else []
        memories = await dependencies.memory_service.recall(
            state["user_id"], state["query"], agent_names, 3
        )
        return {"recalled_memories": memories}

    async def select_skills(state: GlobalState):
        intent_result = state["intent_result"]
        plan = state["route_plan"]
        if intent_result is None or plan is None:
            raise RuntimeError("路由或意图状态缺失。")
        scores = await asyncio.gather(
            *(
                dependencies.skill_score_service.scores(
                    state["query"], assignment.agent_name
                )
                for assignment in plan.assignments
            )
        )
        selected = {
            assignment.agent_name.value: dependencies.skill_manager.select(
                query=state["query"],
                agent=assignment.agent_name,
                intent=intent_result.primary_intent,
                embedding_scores=embedding_scores,
            )
            for assignment, embedding_scores in zip(plan.assignments, scores)
        }
        await dependencies.trace.event(
            state["request_id"],
            "skills.selected",
            {
                name: result.model_dump(mode="json", exclude={"selected": {"__all__": {"content"}}})
                for name, result in selected.items()
            },
        )
        return {"selected_skills": selected}

    async def execute_agents(state: GlobalState, runtime: Runtime[ChatRunContext]):
        plan = state["route_plan"]
        intent_result = state["intent_result"]
        if plan is None or intent_result is None:
            raise RuntimeError("执行Agent前缺少路由状态。")

        stream_options = (
            {"observer": runtime.context.observer}
            if runtime.context and runtime.context.observer and len(plan.assignments) == 1
            else {}
        )

        async def execute_one(assignment):
            return await asyncio.wait_for(
                dependencies.agent_service.execute(
                    assignment=assignment,
                    state=dict(state),
                    intent=intent_result.primary_intent,
                    skills=state["selected_skills"][assignment.agent_name.value],
                    **stream_options,
                ),
                timeout=dependencies.single_agent_timeout_seconds,
            )

        results = await asyncio.wait_for(
            asyncio.gather(
                *(execute_one(assignment) for assignment in plan.assignments),
                return_exceptions=True,
            ),
            timeout=(
                dependencies.multi_agent_timeout_seconds
                if len(plan.assignments) > 1
                else dependencies.single_agent_timeout_seconds + 1
            ),
        )
        mapped: Dict[str, object] = {}
        errors = list(state["errors"])
        for assignment, result in zip(plan.assignments, results):
            if isinstance(result, Exception):
                logger.error(
                    "%s execution failed",
                    assignment.agent_name.value,
                    exc_info=(type(result), result, result.__traceback__),
                )
                await dependencies.trace.event(
                    state["request_id"],
                    "agent.failed",
                    {
                        "agent": assignment.agent_name.value,
                        "error_type": type(result).__name__,
                        "message": str(result),
                    },
                )
                errors.append("%s: %s" % (assignment.agent_name.value, result))
                continue
            mapped[assignment.agent_name.value] = result
        return {"agent_results": mapped, "errors": errors}

    async def compose(state: GlobalState, runtime: Runtime[ChatRunContext]):
        results = list(state["agent_results"].values())
        if not results:
            if runtime.context and runtime.context.observer:
                raise RuntimeError("本次请求未能生成有效回答，请稍后重试。")
            return {
                "final_response": FinalResponse(
                    content="本次请求未能生成有效回答，请稍后重试。",
                    partial=True,
                    status=RunStatus.FAILED,
                )
            }
        options = (
            {"observer": runtime.context.observer}
            if runtime.context and runtime.context.observer else {}
        )
        response = await dependencies.composer.compose(results, **options)
        if state["errors"]:
            response.partial = True
            response.status = RunStatus.PARTIAL
            response.unresolved_points.append("部分 Agent 未完成，本次仅提供已完成的结果。")
        if runtime.context and runtime.context.observer:
            await runtime.context.observer.update(response.content)
        return {
            "final_response": response,
            "citations": [item.model_dump(mode="json") for item in response.citations],
        }

    graph = StateGraph(GlobalState, context_schema=ChatRunContext)
    def observed_node(name, label, function):
        async def run(state: GlobalState, runtime: Runtime[ChatRunContext]):
            async with span(name, label) as details:
                result = await function(state, runtime) if "runtime" in inspect.signature(function).parameters else await function(state)
                details["output"] = result
                observer = current_observation()
                if observer is not None:
                    observer.state.update(result)
                intent = result.get("intent_result")
                final = result.get("final_response")
                if intent and intent.signal_errors:
                    details["status"] = "degraded"
                if final:
                    details["status"] = final.status.value
                if result.get("errors"):
                    details["status"] = "partial"
                if name == "compose" and observer is not None and any(
                        event["event_type"] == "compose.degraded" for event in observer.events):
                    details["status"] = "degraded"
                return result
        return run
    for name, label, function in (
        ("load_context", "加载上下文", load_context),
        ("recognize_intent", "意图识别", recognize_intent),
        ("route_request", "路由决策", route_request),
        ("clarify", "追问回复", clarify),
        ("direct_response", "直接回复", direct_response),
        ("recall_memory", "长期记忆注入", recall_memory),
        ("select_skills", "Skills选择", select_skills),
        ("execute_agents", "Agent执行阶段", execute_agents),
        ("compose", "汇总回答", compose),
    ):
        graph.add_node(name, observed_node(name, label, function))

    graph.add_edge(START, "load_context")
    graph.add_edge("load_context", "recognize_intent")
    graph.add_edge("recognize_intent", "route_request")
    graph.add_conditional_edges(
        "route_request",
        after_route,
        {"clarify": "clarify", "direct": "direct_response", "agents": "recall_memory"},
    )
    graph.add_edge("recall_memory", "select_skills")
    graph.add_edge("select_skills", "execute_agents")
    graph.add_edge("execute_agents", "compose")
    graph.add_edge("clarify", END)
    graph.add_edge("direct_response", END)
    graph.add_edge("compose", END)
    return graph.compile()
