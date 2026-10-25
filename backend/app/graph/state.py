"""全局图在节点间传递的原始状态。"""

from __future__ import annotations

from typing import Dict, List, Optional, TypedDict

from app.schemas.agent import AgentResult, FinalResponse, RoutePlan
from app.schemas.intent import IntentResult
from app.skills.models import SelectedSkillResult


class GlobalState(TypedDict):
    request_id: str
    user_id: str
    conversation_id: str
    query: str
    recent_messages: List[Dict[str, str]]
    conversation_summary: str
    recalled_memories: List[str]
    uploaded_files: List[Dict[str, str]]
    intent_result: Optional[IntentResult]
    route_plan: Optional[RoutePlan]
    selected_skills: Dict[str, SelectedSkillResult]
    agent_results: Dict[str, AgentResult]
    final_response: Optional[FinalResponse]
    citations: List[Dict[str, object]]
    trace_context: Dict[str, object]
    errors: List[str]

