"""一次聊天请求的事务边界、并发限制和最终持久化。"""

from __future__ import annotations

import asyncio
import logging
from uuid import uuid4

from app.cache.redis_client import RedisResources
from app.cache.recent_messages import RecentMessageCache
from app.core.response_stream import ChatRunContext
from app.db.repositories.conversations import ConversationRepository
from app.db.repositories.traces import TraceRepository
from app.schemas.agent import FinalResponse
from app.schemas.enums import RunStatus

from .memory_service import MemoryService
from .conversation_activity import ConversationActivity, ConversationBusyError
from .conversation_title import ConversationTitleService
from .response_stream import ResponseStreamPublisher
from .trace_service import CompositeTraceSink
from app.core.observability import observation_scope, current_observation, observed_semaphore, span


logger = logging.getLogger(__name__)


class ChatRunService:
    def __init__(
        self, graph, conversations: ConversationRepository, traces: TraceRepository,
        trace_sink: CompositeTraceSink, redis: RedisResources, memory: MemoryService,
        recent_cache: RecentMessageCache, max_active_requests: int = 2,
        lock_ttl_seconds: int = 300,
        activity: ConversationActivity | None = None,
        title_service: ConversationTitleService | None = None,
    ) -> None:
        self.graph = graph
        self.conversations = conversations
        self.traces = traces
        self.trace_sink = trace_sink
        self.event_bus = getattr(trace_sink, "event_bus", None)
        self.redis = redis
        self.memory = memory
        self.recent_cache = recent_cache
        self.semaphore = asyncio.Semaphore(max_active_requests)
        self.lock_ttl_seconds = lock_ttl_seconds
        self.tasks: set[asyncio.Task] = set()
        self.activity = activity or ConversationActivity()
        self.title_service = title_service

    def _track(self, coroutine, conversation_id: str) -> None:
        release = None
        try:
            release = self.activity.reserve(conversation_id)
            task = asyncio.create_task(coroutine)
        except BaseException:
            coroutine.close()
            if release is not None:
                release()
            raise
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        task.add_done_callback(lambda _: release())

    async def start(self, user_id: str, conversation_id: str, query: str) -> str:
        # 在第一次 await 前登记，排队中的请求也阻止删除。
        with self.activity.operation(conversation_id):
            return await self._start(user_id, conversation_id, query)

    async def _start(self, user_id: str, conversation_id: str, query: str) -> str:
        request_id = str(uuid4())
        trace = await self.traces.start(request_id, user_id, conversation_id)
        if self.event_bus is not None:
            try:
                # POST 返回前创建事件流，排队中的请求也能立即订阅。
                await self.event_bus.publish(request_id, "request.accepted", {})
            except Exception:
                await self.traces.finish(request_id, RunStatus.FAILED.value, {})
                raise
        self._track(self._run(request_id, user_id, conversation_id, query, str(trace.trace_id)),
                    conversation_id)
        return request_id

    async def _run(
        self, request_id: str, user_id: str, conversation_id: str, query: str, trace_id: str,
    ) -> None:
        async with observation_scope(request_id, self.trace_sink) as observation:
            await observation.emit("request.input", {"query": query})
            try:
                await self._run_observed(request_id, user_id, conversation_id, query, trace_id)
            except asyncio.CancelledError:
                await self.traces.finish(request_id, RunStatus.CANCELLED.value, observation.state)
                await observation.emit("request.cancelled", {"code": "RUN_CANCELLED", "message": "请求被中止。"})
                raise

    async def _run_observed(
        self, request_id: str, user_id: str, conversation_id: str, query: str, trace_id: str,
    ) -> None:
        observer = (
            ResponseStreamPublisher(self.event_bus, request_id)
            if self.event_bus is not None else None
        )
        state = {
            "request_id": request_id, "user_id": user_id,
            "conversation_id": conversation_id, "query": query,
            "recent_messages": [], "conversation_summary": "", "recalled_memories": [],
            "uploaded_files": [], "intent_result": None, "route_plan": None,
            "selected_skills": {}, "agent_results": {}, "final_response": None,
            "citations": [], "trace_context": {}, "errors": [],
        }
        try:
            async with observed_semaphore(self.semaphore):
                async with self.redis.conversation_lock(
                    conversation_id, self.lock_ttl_seconds
                ) as acquired:
                    if not acquired:
                        raise ConversationBusyError("该对话已有请求正在执行。")
                    user_message = await self.conversations.add_message(
                        conversation_id, "user", query,
                        request_id=request_id, trace_id=trace_id,
                    )
                    await self.recent_cache.append(conversation_id, "user", query)
                    await self.trace_sink.event(
                        request_id, "request.started",
                        {"message": "正在分析问题。", "message_id": str(user_message.message_id)},
                    )
                    if observer is not None:
                        await observer.start()
                        result = await self.graph.ainvoke(
                            state, context=ChatRunContext(observer=observer),
                        )
                    else:
                        result = await self.graph.ainvoke(state)
                    final: FinalResponse = result["final_response"]
                    state = result
                    if observer is not None:
                        await observer.update(final.content)
                        await observer.flush()
                        await observer.close()
                        await self.trace_sink.event(
                            request_id, "response.stream", observer.metrics(),
                        )
                    assistant_message = await self.conversations.add_message(
                        conversation_id=conversation_id, role="assistant",
                        content=final.content, status=final.status.value,
                        agent_names=[agent.value for agent in final.agents],
                        display_metadata={
                            **final.metadata,
                            "partial": final.partial,
                            "unresolved_points": final.unresolved_points,
                        },
                        citations=final.citations, request_id=request_id, trace_id=trace_id,
                    )
                    if final.status != RunStatus.FAILED:
                        await self.recent_cache.append(conversation_id, "assistant", final.content)
                    await self.traces.finish(request_id, final.status.value, result)
                    await self.trace_sink.event(
                        request_id, "response.completed",
                        {
                            "response_id": request_id,
                            "message_id": str(assistant_message.message_id),
                            "content": final.content,
                            "agents": [agent.value for agent in final.agents],
                            "citations": [item.model_dump(mode="json") for item in final.citations],
                            "partial": final.partial,
                            "unresolved_points": final.unresolved_points,
                            "status": final.status.value,
                            "display_metadata": {
                                **final.metadata, "partial": final.partial,
                                "unresolved_points": final.unresolved_points,
                            },
                        },
                    )
                    if final.status != RunStatus.FAILED:
                        self._track(self._maintain_memory(
                            request_id, user_id, conversation_id,
                            [str(user_message.message_id), str(assistant_message.message_id)],
                        ), conversation_id)
                        if self.title_service is not None:
                            self._track(self.title_service.generate(user_id, conversation_id),
                                        conversation_id)
        except Exception as exc:
            logger.exception("Chat request %s failed", request_id)
            payload = {
                "response_id": request_id,
                "code": "CONVERSATION_BUSY" if isinstance(exc, ConversationBusyError) else "RUN_FAILED",
                "message": str(exc) if isinstance(exc, ConversationBusyError)
                else "本次回答未能完成，请稍后重试。",
            }
            try:
                observation = current_observation()
                await self.traces.finish(request_id, RunStatus.FAILED.value, {**state, **(observation.state if observation else {})})
                await self.trace_sink.event(
                    request_id, "request.failed", payload,
                )
            except Exception:
                logger.exception("Failed to persist chat failure %s", request_id)
                if self.event_bus is not None:
                    await self.event_bus.publish(
                        request_id, "request.failed", payload,
                    )
        finally:
            if observer is not None:
                await observer.close()

    async def _maintain_memory(
        self, request_id: str, user_id: str, conversation_id: str, message_ids: list[str],
    ) -> None:
        try:
            async with observation_scope(request_id, self.trace_sink):
                async with span("memory.maintenance", "回答后的记忆维护", background=True):
                    await self.memory.maintain_after_turn(user_id, conversation_id, message_ids)
        except Exception:
            await self.trace_sink.event(
                request_id, "memory.maintenance_failed",
                {"message": "回答已完成，但记忆维护失败。"},
            )
