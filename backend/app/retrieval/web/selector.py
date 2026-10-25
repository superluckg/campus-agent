"""固定白名单内的 Source Selector。"""

from __future__ import annotations

from typing import List, Optional

from app.schemas.enums import AgentName

from .registry import WebSourceRegistry


class WebSourceSelector:
    def __init__(self, registry: WebSourceRegistry) -> None:
        self.registry = registry

    def select(
        self,
        query: str,
        agent_name: AgentName,
        requested_keys: Optional[List[str]] = None,
    ) -> list[str]:
        allowed = {source.source_key: source for source in self.registry.allowed_for(agent_name)}
        if requested_keys:
            unknown = [key for key in requested_keys if key not in allowed]
            if unknown:
                raise ValueError("Agent无权使用或Source未注册：%s" % "、".join(unknown))
            return list(dict.fromkeys(requested_keys))
        normalized = query.casefold()
        ranked = []
        for source in allowed.values():
            score = sum(keyword.casefold() in normalized for keyword in source.keywords)
            if score:
                ranked.append((score, source.source_key))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return [source_key for _, source_key in ranked]

