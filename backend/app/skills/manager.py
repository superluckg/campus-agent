"""不调用 LLM 的 SkillManager。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Mapping, Optional

from app.schemas.enums import AgentName, IntentName

from .loader import load_all_skills
from .models import SelectedSkill, SelectedSkillResult, SkillCandidate, SkillDefinition


logger = logging.getLogger(__name__)


class SkillManager:
    def __init__(
        self,
        root: Path,
        optional_threshold: float = 0.55,
        max_selected: int = 3,
        token_budget: int = 2000,
        character_limit: int = 6000,
    ) -> None:
        self.root = root
        self.optional_threshold = optional_threshold
        self.max_selected = max_selected
        self.token_budget = token_budget
        self.character_limit = character_limit
        self._skills = load_all_skills(root)
        self._snapshot = self._mtime_snapshot()

    @property
    def skills(self) -> Mapping[str, SkillDefinition]:
        return dict(self._skills)

    def reload_if_changed(self) -> bool:
        snapshot = self._mtime_snapshot()
        if snapshot == self._snapshot:
            return False
        try:
            updated = load_all_skills(self.root)
        except Exception:
            logger.exception("Skill 热加载失败，继续使用当前已加载配置。")
            return False
        self._skills = updated
        self._snapshot = snapshot
        return True

    def select(
        self,
        query: str,
        agent: AgentName,
        intent: IntentName,
        embedding_scores: Optional[Mapping[str, float]] = None,
    ) -> SelectedSkillResult:
        self.reload_if_changed()
        normalized = query.casefold()
        embedding_scores = embedding_scores or {}
        candidates: list[tuple[SkillDefinition, float, bool, SkillCandidate]] = []
        for skill in self._skills.values():
            if not skill.enabled or agent not in skill.allowed_agents:
                continue
            mandatory = intent in skill.mandatory_for_intents
            intent_score = 1.0 if intent in skill.intents else 0.0
            hits = sum(keyword.casefold() in normalized for keyword in skill.keywords)
            keyword_score = min(1.0, hits / max(1, min(3, len(skill.keywords))))
            embedding_score = max(0.0, min(1.0, float(embedding_scores.get(skill.name, 0.0))))
            score = 0.50 * intent_score + 0.35 * embedding_score + 0.15 * keyword_score
            mapped = intent_score == 1.0
            selected_by_score = score >= self.optional_threshold
            chosen = mandatory or mapped or selected_by_score
            reasons = []
            if mandatory:
                reasons.append("强制Policy规则")
            if mapped:
                reasons.append("Intent直接映射")
            if keyword_score:
                reasons.append("关键词命中")
            if embedding_score:
                reasons.append("Embedding相似")
            candidate = SkillCandidate(
                name=skill.name,
                score=round(score, 6),
                intent_score=intent_score,
                embedding_score=embedding_score,
                keyword_score=keyword_score,
                selected=chosen,
                reason="、".join(reasons) if reasons else "未达到选择条件",
            )
            candidates.append((skill, score, mandatory, candidate))

        candidates.sort(
            key=lambda item: (
                not item[2],
                -item[0].priority,
                -item[1],
                item[0].name,
            )
        )
        selected: list[SelectedSkill] = []
        total_characters = 0
        for skill, score, mandatory, candidate in candidates:
            if not candidate.selected:
                continue
            content_length = len(skill.content)
            would_exceed_count = len(selected) >= self.max_selected and not mandatory
            would_exceed_chars = total_characters + content_length > self.character_limit
            approximate_tokens = (total_characters + content_length + 1) // 2
            would_exceed_tokens = approximate_tokens > self.token_budget
            if would_exceed_count or would_exceed_chars or would_exceed_tokens:
                candidate.selected = False
                candidate.reason += "；因数量或Prompt预算未注入"
                continue
            selected.append(
                SelectedSkill(
                    name=skill.name,
                    display_name=skill.display_name,
                    content_hash=skill.content_hash,
                    score=round(score, 6),
                    reasons=candidate.reason.split("、"),
                    content=skill.content,
                )
            )
            total_characters += content_length
        return SelectedSkillResult(
            selected=selected,
            candidates=[item[3] for item in candidates],
            approximate_tokens=(total_characters + 1) // 2,
            characters=total_characters,
        )

    def _mtime_snapshot(self) -> Dict[str, int]:
        return {
            str(path): path.stat().st_mtime_ns
            for path in sorted(self.root.rglob("SKILL.md"))
        }

