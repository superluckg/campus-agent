"""从白名单配置读取固定官网来源。"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List

import yaml
from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from app.schemas.enums import AgentName


class WebSourceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_key: str
    display_name: str
    url: HttpUrl
    adapter: str
    allowed_agents: List[AgentName]
    keywords: List[str] = Field(default_factory=list)
    cache_ttl_seconds: int = Field(ge=60)
    first_page_only: bool = True
    follow_detail_pages: bool = False


class WebSourceRegistry:
    def __init__(self, sources: Dict[str, WebSourceConfig]) -> None:
        self._sources = dict(sources)

    @classmethod
    def from_yaml(cls, path: Path) -> "WebSourceRegistry":
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        raw_sources = payload.get("sources")
        if not isinstance(raw_sources, dict):
            raise ValueError("web_sources.yaml 必须包含 sources 对象。")
        sources = {
            source_key: WebSourceConfig(source_key=source_key, **definition)
            for source_key, definition in raw_sources.items()
        }
        return cls(sources)

    def get(self, source_key: str) -> WebSourceConfig:
        try:
            return self._sources[source_key]
        except KeyError as exc:
            raise ValueError("未注册的固定官网来源：%s" % source_key) from exc

    def allowed_for(self, agent_name: AgentName) -> list[WebSourceConfig]:
        return [source for source in self._sources.values() if agent_name in source.allowed_agents]

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(sorted(self._sources))

    @property
    def sources(self) -> tuple[WebSourceConfig, ...]:
        return tuple(self._sources[key] for key in sorted(self._sources))
