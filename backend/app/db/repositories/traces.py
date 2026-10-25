"""请求 Trace 与节点事件持久化。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import desc, func, select

from app.db.models import Trace, TraceEvent, Message
from app.db.identifiers import as_uuid
from app.db.session import Database


class TraceRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def start(self, request_id: str, user_id: str, conversation_id: str,
                    request_source: str = "chat", query: str | None = None) -> Trace:
        trace = Trace(
            request_id=UUID(request_id),
            user_id=UUID(user_id),
            conversation_id=UUID(conversation_id),
            status="running",
            started_at=datetime.now(timezone.utc),
            request_source=request_source,
            query_snapshot=query,
        )
        async with self.database.session() as session:
            session.add(trace)
            await session.flush()
        return trace

    async def get_request(self, request_id: str) -> Optional[Trace]:
        async with self.database.session() as session:
            result = await session.execute(
                select(Trace).where(Trace.request_id == UUID(request_id))
            )
            return result.scalar_one_or_none()

    async def append_event(
        self,
        request_id: str,
        event_type: str,
        payload: dict,
        status: str = "success",
    ) -> Optional[TraceEvent]:
        async with self.database.session() as session:
            trace_result = await session.execute(
                select(Trace)
                .where(Trace.request_id == UUID(request_id))
                .with_for_update()
            )
            trace = trace_result.scalar_one_or_none()
            if trace is None:
                return None
            sequence_result = await session.execute(
                select(func.coalesce(func.max(TraceEvent.sequence_number), 0)).where(
                    TraceEvent.trace_id == trace.trace_id
                )
            )
            item = TraceEvent(
                trace_id=trace.trace_id,
                sequence_number=int(sequence_result.scalar_one()) + 1,
                node_name=str(payload.get("node", event_type.split(".")[0])),
                event_type=event_type,
                status="failed" if event_type.endswith(".failed") else payload.get("status", status),
                payload=payload,
                started_at=datetime.fromisoformat(payload["started_at"]) if payload.get("span_id") and payload.get("started_at") else datetime.now(timezone.utc),
                finished_at=datetime.fromisoformat(payload["finished_at"]) if payload.get("span_id") and payload.get("finished_at") else None,
                duration_ms=payload.get("duration_ms"),
            )
            usage = payload.get("usage", {})
            if event_type == "request.input":
                trace.query_snapshot = payload.get("query")
            if event_type == "intent.completed":
                trace.primary_intent = payload.get("primary_intent")
            if event_type == "route.completed":
                trace.route = payload.get("route")
                trace.selected_agents = [row["agent_name"] for row in payload.get("assignments", [])]
            if event_type.endswith(".failed"):
                trace.error_code = payload.get("code") or payload.get("error_type")
            for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                if isinstance(usage.get(key), int):
                    setattr(trace, key, (getattr(trace, key) or 0) + usage[key])
            session.add(item)
            await session.flush()
            return item

    async def finish(self, request_id: str, status: str, state: dict) -> None:
        now = datetime.now(timezone.utc)
        async with self.database.session() as session:
            result = await session.execute(
                select(Trace).where(Trace.request_id == UUID(request_id))
            )
            trace = result.scalar_one_or_none()
            if trace is None:
                return
            trace.status = status
            trace.finished_at = now
            trace.duration_ms = (now - trace.started_at).total_seconds() * 1000
            intent = state.get("intent_result")
            plan = state.get("route_plan")
            skills = state.get("selected_skills", {})
            trace.primary_intent = intent.primary_intent.value if intent else trace.primary_intent
            trace.route = plan.route if plan else trace.route
            trace.selected_agents = (
                [item.agent_name.value for item in plan.assignments] if plan else trace.selected_agents
            )
            trace.selected_skills = [
                {
                    "agent": agent,
                    "skills": [item.name for item in result.selected],
                }
                for agent, result in skills.items()
            ]
            agent_results = state.get("agent_results", {})
            trace.tool_call_count = sum(
                len(result.tool_records) for result in agent_results.values()
            )
            # Agent中途失败时仍保留已经发生的调用，不能只依赖最终AgentResult。
            calls = await session.scalar(select(func.count(TraceEvent.event_id)).where(
                TraceEvent.trace_id == trace.trace_id, TraceEvent.event_type == "tool.started"))
            spans = await session.scalar(select(func.count(TraceEvent.event_id)).where(
                TraceEvent.trace_id == trace.trace_id, TraceEvent.event_type == "phase.started",
                TraceEvent.payload["node"].astext == "tool"))
            if calls or spans:
                trace.tool_call_count = calls or spans
            trace.retrieval_rounds = await session.scalar(select(func.count(TraceEvent.event_id)).where(
                TraceEvent.trace_id == trace.trace_id,
                TraceEvent.event_type == "retrieval.round.started"))

    async def list_recent(self, limit: int = 100, source: str | None = None) -> list[Trace]:
        async with self.database.session() as session:
            statement = select(Trace).order_by(desc(Trace.started_at)).limit(limit)
            if source:
                statement = statement.where(Trace.request_source == source)
            result = await session.execute(statement)
            return list(result.scalars())

    async def list_views(self, limit: int = 100, source: str = "chat") -> list[dict]:
        query = select(Message.content).where(Message.trace_id == Trace.trace_id, Message.role == "user").order_by(Message.created_at.desc()).limit(1).scalar_subquery()
        route = select(TraceEvent.payload["route"].astext).where(TraceEvent.trace_id == Trace.trace_id,
            TraceEvent.event_type == "route.completed").order_by(TraceEvent.sequence_number.desc()).limit(1).scalar_subquery()
        async with self.database.session() as session:
            rows = (await session.execute(select(Trace, query, route).where(Trace.request_source == source)
                    .order_by(Trace.started_at.desc()).limit(limit))).all()
            return [self.view(row, fallback) | {"route": row.route or actual_route} for row, fallback, actual_route in rows]

    @staticmethod
    def view(row, fallback=None):
        return {column.name: (str(getattr(row, column.name)) if isinstance(getattr(row, column.name), UUID)
                              else getattr(row, column.name)) for column in row.__table__.columns} | {"query": row.query_snapshot or fallback}

    async def detail(self, trace_id: str):
        async with self.database.session() as session:
            row = await session.get(Trace, as_uuid(trace_id))
            if row is None:
                raise LookupError("Trace不存在。")
            query = await session.scalar(select(Message.content).where(Message.trace_id == row.trace_id,
                Message.role == "user").order_by(Message.created_at.desc()).limit(1))
            header = self.view(row, query)
        events = await self.events(trace_id)
        values = [{column.name: (str(getattr(item, column.name)) if isinstance(getattr(item, column.name), UUID)
                               else getattr(item, column.name)) for column in item.__table__.columns} for item in events]
        stages = {}
        for event in values:
            payload = event["payload"]
            if event["event_type"] == "route.completed" and not header.get("route"):
                header["route"] = payload.get("route")
            if event["event_type"].startswith("phase.") and payload.get("span_id"):
                stages[payload["span_id"]] = {**payload, "status": "running" if event["event_type"] == "phase.started" else payload.get("status", "success")}
        return {"trace": header, "events": values, "stages": list(stages.values())}

    async def events(self, trace_id: str) -> list[TraceEvent]:
        async with self.database.session() as session:
            result = await session.execute(
                select(TraceEvent)
                .where(TraceEvent.trace_id == as_uuid(trace_id))
                .order_by(TraceEvent.sequence_number)
            )
            return list(result.scalars())
