"""Skill 文件和选择结果。"""

from __future__ import annotations

from pathlib import Path
from typing import List

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.enums import AgentName, IntentName


class SkillDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    name: str
    display_name: str
    category: str
    allowed_agents: List[AgentName]
    intents: List[IntentName] = Field(default_factory=list)
    keywords: List[str] = Field(default_factory=list)
    priority: int = 0
    enabled: bool = True
    mandatory_for_intents: List[IntentName] = Field(default_factory=list)
    content: str
    source_path: Path
    content_hash: str


class SelectedSkill(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    display_name: str
    content_hash: str
    score: float
    reasons: List[str] = Field(default_factory=list)
    content: str


class SkillCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    score: float
    intent_score: float
    embedding_score: float
    keyword_score: float
    selected: bool
    reason: str


class SelectedSkillResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selected: List[SelectedSkill] = Field(default_factory=list)
    candidates: List[SkillCandidate] = Field(default_factory=list)
    approximate_tokens: int = 0
    characters: int = 0

