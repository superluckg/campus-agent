"""Agentic Retrieval 使用的结构化对象。"""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from langchain_core.documents import Document
from pydantic import BaseModel, ConfigDict, Field

from .enums import AgentName, IntentName, QueryStrategy, RetrievalSource


class RetrievalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=4000)
    conversation_id: UUID
    agent_name: AgentName
    intent: IntentName


class QueryPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy: QueryStrategy
    retrieval_query: str = Field(min_length=1, max_length=4000)
    generated_queries: list[str] = Field(default_factory=list, max_length=2)
    hypothetical_document: str | None = Field(default=None, max_length=8000)
    selected_sources: list[RetrievalSource] = Field(default_factory=list)
    web_source_keys: list[str] = Field(default_factory=list)
    reason_summary: str = ""




class RetrievalQueryExecution(BaseModel):
    """实际路线输入及其执行结果，成功结果才可以缓存。"""

    source: str = "local_knowledge"
    route: str
    query: str
    status: Literal["executed", "cached", "failed"]
    result_count: int = 0
    error: str = ""


class RetrievalDiagnostics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rounds: int = Field(ge=0, le=3)
    finished_reason: str
    stop_reason: str = ""
    evidence_sufficient: bool | None
    protocol: str = "agentic"
    total_rounds: int = Field(default=0, ge=0, le=3)
    query_plan: QueryPlan | None = None
    source_counts: dict[str, int] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
    query_executions: list[RetrievalQueryExecution] = Field(default_factory=list)


@dataclass(frozen=True)
class RetrievalExecution:
    documents: list[Document]
    diagnostics: RetrievalDiagnostics
