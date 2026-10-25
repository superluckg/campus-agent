"""把意图结果映射为澄清、直接回答、单Agent或双Agent计划。"""

from __future__ import annotations

from typing import Dict, List, Tuple

from app.schemas.agent import AgentAssignment, RoutePlan
from app.schemas.enums import AgentName, IntentName
from app.schemas.intent import IntentResult


_INTENT_TO_AGENT: Dict[IntentName, AgentName] = {
    IntentName.CAREER_RESUME: AgentName.CAREER,
    IntentName.CAREER_RECRUITMENT: AgentName.CAREER,
    IntentName.CAMPUS_NETWORK: AgentName.CAMPUS,
    IntentName.CAMPUS_AFFAIRS: AgentName.CAMPUS,
    IntentName.LEARNING_DOCUMENT: AgentName.LEARNING,
    IntentName.LEARNING_PLAN: AgentName.LEARNING,
    IntentName.LEARNING_GPA: AgentName.LEARNING,
}


class AgentRouter:
    def route(self, result: IntentResult) -> RoutePlan:
        if result.policy_decision in {"refuse_sensitive", "refuse_unauthorized"}:
            return RoutePlan(route="direct_response", reason_summary="拒绝敏感信息或越权请求。")
        if result.missing_fields:
            missing = "、".join(result.missing_fields)
            return RoutePlan(
                route="clarify",
                clarification_question="继续处理前还需要：%s。请补充后再试。" % missing,
                reason_summary="缺少代码确定的必要字段。",
            )
        if result.primary_intent == IntentName.UNCLEAR:
            return RoutePlan(
                route="clarify",
                clarification_question="你希望我处理求职、校园事务，还是学习相关的问题？",
                reason_summary=result.clarification_reason or "意图不清。",
            )
        if result.primary_intent in {IntentName.OUT_OF_SCOPE, IntentName.SMALL_TALK}:
            return RoutePlan(
                route="direct_response",
                reason_summary="使用对应提示词生成聊天或范围说明回复。",
            )
        if result.primary_intent == IntentName.COMPOUND or result.is_compound:
            scores = {
                intent.value: result.candidate_scores.get(intent.value, result.confidence) or result.confidence
                for intent in result.sub_intents
            }
            assignments = self._compound_assignments(scores)
            if len(assignments) < 2:
                return RoutePlan(
                    route="clarify",
                    clarification_question="这个问题包含多个任务，请说明最希望我先处理哪两部分。",
                    reason_summary="复合意图未能稳定确定两个不同领域。",
                )
            return RoutePlan(
                route="multi_agent",
                assignments=assignments,
                reason_summary="根据复合任务的子意图选择两个不同领域Agent。",
            )
        agent = _INTENT_TO_AGENT[result.primary_intent]
        return RoutePlan(
            route="single_agent",
            assignments=[
                AgentAssignment(
                    agent_name=agent,
                    focus="处理 %s 对应的任务。" % result.primary_intent.value,
                )
            ],
            reason_summary="主意图直接映射到业务Agent。",
        )

    @staticmethod
    def _compound_assignments(scores: Dict[str, float]) -> List[AgentAssignment]:
        best_by_agent: Dict[AgentName, Tuple[IntentName, float]] = {}
        for raw_intent, score in scores.items():
            if score <= 0:
                continue
            try:
                intent = IntentName(raw_intent)
            except ValueError:
                continue
            agent = _INTENT_TO_AGENT.get(intent)
            if agent is None:
                continue
            current = best_by_agent.get(agent)
            if current is None or score > current[1]:
                best_by_agent[agent] = (intent, score)
        ranked = sorted(best_by_agent.items(), key=lambda item: -item[1][1])[:2]
        return [
            AgentAssignment(
                agent_name=agent,
                focus="只处理 %s 部分。" % intent.value,
            )
            for agent, (intent, _) in ranked
        ]

