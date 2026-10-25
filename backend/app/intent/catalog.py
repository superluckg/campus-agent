"""读取意图配置和样例。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from app.schemas.enums import AgentName, IntentName


@dataclass(frozen=True)
class IntentDefinition:
    name: IntentName
    display_name: str
    description: str
    agent: Optional[AgentName]
    required_entities: tuple[str, ...]
    examples: tuple[str, ...]


def load_intent_catalog(path: Path) -> Dict[IntentName, IntentDefinition]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw_intents = payload.get("intents")
    if not isinstance(raw_intents, dict):
        raise ValueError("intents.yaml 必须包含 intents 对象。")
    catalog: Dict[IntentName, IntentDefinition] = {}
    for raw_name, raw_definition in raw_intents.items():
        intent = IntentName(raw_name)
        if not isinstance(raw_definition, dict):
            raise ValueError("意图 %s 的配置必须是对象。" % raw_name)
        raw_agent = raw_definition.get("agent")
        examples: List[str] = list(raw_definition.get("examples") or [])
        if len(examples) < 5:
            raise ValueError("意图 %s 至少需要5条样例。" % raw_name)
        catalog[intent] = IntentDefinition(
            name=intent,
            display_name=str(raw_definition.get("display_name", raw_name)),
            description=str(raw_definition.get("description", "")),
            agent=AgentName(raw_agent) if raw_agent else None,
            required_entities=tuple(raw_definition.get("required_entities") or ()),
            examples=tuple(examples),
        )
    missing = set(IntentName) - set(catalog)
    if missing:
        raise ValueError("意图配置不完整：%s" % sorted(item.value for item in missing))
    return catalog

