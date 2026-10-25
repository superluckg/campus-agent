"""意图识别结果。"""

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from .enums import IntentName


class IntentResult(BaseModel):
    """四级漏斗输出的统一决策及阶段诊断。"""

    model_config = ConfigDict(extra="forbid")

    primary_intent: IntentName
    confidence: float = Field(ge=0.0, le=1.0)
    entities: Dict[str, List[str]] = Field(default_factory=dict)
    missing_fields: List[str] = Field(default_factory=list)
    candidate_scores: Dict[str, float] = Field(default_factory=dict)
    clarification_reason: Optional[str] = None
    is_compound: bool = False
    sub_intents: List[IntentName] = Field(default_factory=list)
    source_scores: Dict[str, float] = Field(default_factory=dict)
    signal_errors: Dict[str, str] = Field(default_factory=dict)
    signal_durations_ms: Dict[str, float] = Field(default_factory=dict)
    model_usage: Dict[str, int] = Field(default_factory=dict)
    decision_stage: str | None = None
    stage_trace: list[dict] = Field(default_factory=list)
    model_calls: Dict[str, int] = Field(default_factory=dict)
    decision_details: dict = Field(default_factory=dict)
    policy_decision: Optional[Literal["allow", "refuse_sensitive", "refuse_unauthorized"]] = None

