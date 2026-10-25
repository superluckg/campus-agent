"""公共分类信号和 DeepSeek 单信号决策转换，不融合不同来源的分数。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from app.schemas.enums import IntentName
from app.schemas.intent import IntentResult


@dataclass(frozen=True)
class IntentSignal:
    source: str
    scores: Mapping[IntentName, float]
    entities: Mapping[str, list[str]]
    usage: dict[str, int] = field(default_factory=dict)
    policy_decision: str | None = None
    primary_intent: IntentName | None = None
    sub_intents: tuple[IntentName, ...] = ()


class LLMDecisionConverter:
    def __init__(self, clarification_threshold=0.55, ambiguity_gap=0.10):
        self.clarification_threshold = clarification_threshold
        self.ambiguity_gap = ambiguity_gap

    def convert(self, signal: IntentSignal) -> IntentResult:
        scores = {
            intent: max(0.0, min(1.0, float(signal.scores.get(intent, 0)))) for intent in IntentName
        }
        parts = [
            intent
            for intent in dict.fromkeys(signal.sub_intents)
            if intent.value.startswith(("career_", "campus_", "learning_"))
        ]
        parts = (
            parts
            if signal.primary_intent == IntentName.COMPOUND
            and len({intent.value.split("_", 1)[0] for intent in parts}) >= 2
            else []
        )
        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0].value))
        decision_ranked = [item for item in ranked if item[0] not in parts]
        primary, confidence = decision_ranked[0]
        second = decision_ranked[1][1] if len(decision_ranked) > 1 else 0.0
        reason = None
        if confidence < self.clarification_threshold:
            primary, reason = IntentName.UNCLEAR, "主意图置信度低于澄清阈值。"
        elif confidence - second < self.ambiguity_gap and primary not in {
            IntentName.COMPOUND,
            IntentName.OUT_OF_SCOPE,
            IntentName.SMALL_TALK,
        }:
            primary, reason = IntentName.UNCLEAR, "前两个候选意图得分过于接近。"
        if (
            primary == IntentName.COMPOUND
            and signal.primary_intent == IntentName.COMPOUND
            and not parts
        ):
            primary, reason = IntentName.UNCLEAR, "复合任务未确定两个不同领域的子意图。"
        return IntentResult(
            primary_intent=primary,
            confidence=confidence,
            entities={key: list(dict.fromkeys(values)) for key, values in signal.entities.items()},
            candidate_scores={intent.value: round(score, 6) for intent, score in ranked},
            clarification_reason=reason,
            is_compound=primary == IntentName.COMPOUND,
            sub_intents=parts if primary == IntentName.COMPOUND else [],
            source_scores={signal.source: max(scores.values())},
        )
