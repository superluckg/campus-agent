"""异步调用作用域内的真实跨度、快照和工具产物；不改变业务返回值。"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from time import perf_counter
from typing import Any
from uuid import uuid4

from app.core.logging import redact


def snapshot(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return snapshot(value.model_dump(mode="json"))
    if isinstance(value, dict):
        return redact({str(key): snapshot(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return [snapshot(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "value"):
        return value.value
    if value is None or isinstance(value, (str, int, float, bool)):
        return redact(value)
    return str(value)


@dataclass
class Observation:
    request_id: str
    sink: Any
    state: dict = field(default_factory=dict)
    tool_calls: list[dict] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)

    async def emit(self, event_type: str, payload: dict):
        data = snapshot(payload)
        self.events.append({"event_type": event_type, "payload": data})
        # 完整输入、产物和诊断只写开发者 Trace，不向学生 SSE 暴露。
        await self.sink.event(self.request_id, event_type, data)


_observation: ContextVar[Observation | None] = ContextVar("observation", default=None)
_parent: ContextVar[str | None] = ContextVar("span_parent", default=None)


def current_observation() -> Observation | None:
    return _observation.get()


@asynccontextmanager
async def observation_scope(request_id: str, sink):
    observer = Observation(request_id, sink)
    token, parent_token = _observation.set(observer), _parent.set(None)
    try:
        yield observer
    finally:
        _observation.reset(token)
        _parent.reset(parent_token)


@asynccontextmanager
async def span(node: str, label: str, **details):
    observer = current_observation()
    data = {"node": node, "label": label, **details}
    if observer is None:
        yield data
        return
    data.update(
        span_id=str(uuid4()),
        parent_span_id=_parent.get(),
        started_at=datetime.now(timezone.utc).isoformat(),
    )
    started = perf_counter()
    await observer.emit("phase.started", data)
    token = _parent.set(data["span_id"])
    kind = "phase.completed"
    try:
        yield data
        data.setdefault("status", "success")
    except BaseException as exc:
        kind = "phase.cancelled" if isinstance(exc, asyncio.CancelledError) else "phase.failed"
        data.update(
            status="cancelled" if kind.endswith("cancelled") else "failed",
            error_type=type(exc).__name__,
            error_message=str(exc) or "执行被中止。",
        )
        diagnostics = getattr(exc, "intent_diagnostics", None)
        if diagnostics:
            data["intent_diagnostics"] = diagnostics
        raise
    finally:
        data.update(
            finished_at=datetime.now(timezone.utc).isoformat(),
            duration_ms=(perf_counter() - started) * 1000,
        )
        _parent.reset(token)
        await observer.emit(kind, data)


@asynccontextmanager
async def observed_semaphore(semaphore):
    async with span("queue", "等待并发资源"):
        await semaphore.acquire()
    try:
        yield
    finally:
        semaphore.release()


def observed_tools(tools, agent: str):
    """保留工具的名字、权限、参数 Schema 和 content_and_artifact 返回协议。"""
    result = []
    for tool in tools:
        coroutine = tool.coroutine

        async def invoke(_coroutine=coroutine, _name=tool.name, **kwargs):
            observer = current_observation()
            async with span(
                "tool",
                "工具：" + _name,
                agent=agent,
                tool=_name,
                call_id=str(uuid4()),
                inputs=kwargs,
            ) as data:
                try:
                    returned = await _coroutine(**kwargs)
                except BaseException as exc:
                    if observer is not None:
                        observer.tool_calls.append(
                            {
                                "agent": agent,
                                "tool": _name,
                                "call_id": data["call_id"],
                                "inputs": snapshot(kwargs),
                                "artifact": None,
                                "content": "",
                                "status": "execution_failed",
                                "error_message": str(exc) or "工具执行中止。",
                                "duration_ms": (perf_counter() - _tool_started.get()) * 1000
                                if _tool_started.get() is not None
                                else None,
                            }
                        )
                    raise
                data["output"] = returned
                if observer is not None:
                    observer.tool_calls.append(
                        {
                            "agent": agent,
                            "tool": _name,
                            "call_id": data["call_id"],
                            "inputs": snapshot(kwargs),
                            "status": "completed",
                            "artifact": returned[1] if isinstance(returned, tuple) else None,
                            "content": str(
                                returned[0] if isinstance(returned, tuple) else returned
                            ),
                            "duration_ms": (perf_counter() - _tool_started.get()) * 1000
                            if _tool_started.get() is not None
                            else None,
                        }
                    )
                return returned

        # 参数 Schema 已显式指定；闭包内部参数不进入工具 Schema。
        async def timed(_invoke=invoke, **kwargs):
            token = _tool_started.set(perf_counter())
            try:
                return await _invoke(**kwargs)
            finally:
                _tool_started.reset(token)

        result.append(tool.model_copy(update={"coroutine": timed}))
    return result


_tool_started: ContextVar[float | None] = ContextVar("tool_started", default=None)
