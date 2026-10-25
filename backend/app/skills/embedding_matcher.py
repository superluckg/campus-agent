"""仅在通过权限过滤的 Skill 候选中计算语义相似度。"""

from __future__ import annotations

import math

from langchain_core.embeddings import Embeddings

from app.schemas.enums import AgentName

from .manager import SkillManager


class SkillEmbeddingMatcher:
    def __init__(self, manager: SkillManager, embeddings: Embeddings) -> None:
        self.manager = manager
        self.embeddings = embeddings
        self._vectors: dict[str, list[float]] = {}
        self._hashes: dict[str, str] = {}

    async def scores(self, query: str, agent: AgentName) -> dict[str, float]:
        query_vector = await self.embeddings.aembed_query(query)
        candidates = [
            skill
            for skill in self.manager.skills.values()
            if skill.enabled and agent in skill.allowed_agents
        ]
        missing = [
            skill
            for skill in candidates
            if self._hashes.get(skill.name) != skill.content_hash
        ]
        if missing:
            descriptions = [
                "%s。%s。关键词：%s" % (
                    skill.display_name,
                    skill.content,
                    "、".join(skill.keywords),
                )
                for skill in missing
            ]
            vectors = await self.embeddings.aembed_documents(descriptions)
            for skill, vector in zip(missing, vectors):
                self._vectors[skill.name] = list(vector)
                self._hashes[skill.name] = skill.content_hash
        return {
            skill.name: self._cosine(query_vector, self._vectors[skill.name])
            for skill in candidates
        }

    @staticmethod
    def _cosine(left: list[float], right: list[float]) -> float:
        dot = sum(a * b for a, b in zip(left, right))
        norms = math.sqrt(sum(value * value for value in left)) * math.sqrt(
            sum(value * value for value in right)
        )
        return max(0.0, min(1.0, dot / norms)) if norms else 0.0
