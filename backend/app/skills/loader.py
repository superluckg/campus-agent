"""解析 YAML Frontmatter + Markdown 的 SKILL.md。"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Dict

import yaml

from app.schemas.enums import AgentName, IntentName

from .models import SkillDefinition


_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", re.DOTALL)


def load_skill(path: Path) -> SkillDefinition:
    raw = path.read_text(encoding="utf-8")
    match = _FRONTMATTER.match(raw)
    if not match:
        raise ValueError("SKILL.md 缺少合法 YAML Frontmatter：%s" % path)
    metadata = yaml.safe_load(match.group(1)) or {}
    if not isinstance(metadata, dict):
        raise ValueError("Skill Frontmatter 必须是对象：%s" % path)
    body = match.group(2).strip()
    if not body:
        raise ValueError("Skill 正文不能为空：%s" % path)
    required = {"name", "display_name", "category", "allowed_agents"}
    missing = sorted(required - metadata.keys())
    if missing:
        raise ValueError("Skill 缺少字段 %s：%s" % (missing, path))

    def intents(name: str) -> list[IntentName]:
        return [IntentName(value) for value in metadata.get(name, [])]

    normalized: Dict[str, Any] = {
        "name": str(metadata["name"]),
        "display_name": str(metadata["display_name"]),
        "category": str(metadata["category"]),
        "allowed_agents": [AgentName(value) for value in metadata["allowed_agents"]],
        "intents": intents("intents"),
        "keywords": [str(value) for value in metadata.get("keywords", [])],
        "priority": int(metadata.get("priority", 0)),
        "enabled": bool(metadata.get("enabled", True)),
        "mandatory_for_intents": intents("mandatory_for_intents"),
        "content": body,
        "source_path": path,
        "content_hash": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
    }
    return SkillDefinition(**normalized)


def load_all_skills(root: Path) -> Dict[str, SkillDefinition]:
    loaded: Dict[str, SkillDefinition] = {}
    for path in sorted(root.rglob("SKILL.md")):
        skill = load_skill(path)
        if skill.name in loaded:
            raise ValueError("Skill 名称重复：%s" % skill.name)
        loaded[skill.name] = skill
    return loaded

