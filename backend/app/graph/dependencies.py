"""全局图依赖协议，便于单元测试使用 Fake Service。"""

from __future__ import annotations

from typing import Dict, Protocol

from app.core.response_stream import ResponseObserver
from app.schemas.agent import AgentAssignment, AgentResult, FinalResponse
from app.schemas.enums import AgentName, IntentName
from app.schemas.intent import IntentResult
from app.skills.models import SelectedSkillResult


class ContextLoader(Protocol):
    async def load_recent(self, user_id: str, conversation_id: str) -> list[dict[str, str]]: ...

    async def load_summary(self, conversation_id: str) -> str: ...

    async def load_uploaded_files(
        self, user_id: str, conversation_id: str
    ) -> list[dict[str, str]]: ...


class IntentService(Protocol):
    async def recognize(self, **kwargs) -> IntentResult: ...


class MemoryRecallService(Protocol):
    async def recall(
        self, user_id: str, query: str, agent_names: list[AgentName], top_k: int
    ) -> list[str]: ...


class SkillScoreService(Protocol):
    async def scores(self, query: str, agent: AgentName) -> dict[str, float]: ...


class AgentExecutionService(Protocol):
    async def execute(
        self,
        assignment: AgentAssignment,
        state: dict,
        intent: IntentName,
        skills: SelectedSkillResult,
        observer: ResponseObserver | None = None,
    ) -> AgentResult: ...


class ResponseCompositionService(Protocol):
    async def compose(
        self, results: list[AgentResult], *, observer: ResponseObserver | None = None,
    ) -> FinalResponse: ...


class TraceSink(Protocol):
    async def event(
        self,
        request_id: str,
        event_type: str,
        payload: Dict[str, object],
    ) -> None: ...
