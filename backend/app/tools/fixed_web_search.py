"""CareerAgent 可直接调用的固定学校官网查询 Tool。"""

from __future__ import annotations

from typing import List, Optional

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field

from app.retrieval.web.selector import WebSourceSelector
from app.retrieval.web.service import WebSourceRetrievalService
from app.schemas.enums import AgentName
from app.agents.evidence import EvidenceRegistry
from app.tools.agentic_retrieval import render_documents


class FixedWebSearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=2000)
    source_keys: Optional[List[str]] = None
    max_results: int = Field(default=5, ge=1, le=5)
    freshness_required: bool = False


def create_fixed_web_search_tool(
    selector: WebSourceSelector,
    service: WebSourceRetrievalService,
    agent_name: AgentName,
    registry: EvidenceRegistry | None = None,
) -> StructuredTool:
    registry = registry or EvidenceRegistry(agent_name.value)
    async def run(
        query: str,
        source_keys: Optional[List[str]] = None,
        max_results: int = 5,
        freshness_required: bool = False,
    ):
        del freshness_required
        selected = selector.select(query, agent_name, source_keys)
        documents = registry.register((await service.retrieve_sources(selected))[:max_results])
        return render_documents(documents), documents

    return StructuredTool.from_function(
        coroutine=run,
        name="fixed_web_search",
        description=(
            "从预先登记的青岛科技大学招聘会、就业服务等固定官网页面读取近期信息。"
            "这不是通用互联网搜索，不允许访问任意URL。"
        ),
        args_schema=FixedWebSearchInput,
        response_format="content_and_artifact",
    )

