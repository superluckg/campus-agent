"""Agent、Router、引用和最终回答结构。"""

from datetime import datetime
from typing import Any, Dict, List, Optional
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .enums import AgentName, RunStatus, ToolName


class Citation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    citation_index: int = Field(ge=1)
    title: str
    document_id: Optional[UUID] = None
    chunk_id: Optional[UUID] = None
    filename: Optional[str] = None
    page_number: Optional[int] = Field(default=None, ge=1)
    page_numbers: list[Annotated[int, Field(ge=1)]] = Field(default_factory=list)
    section_path: Optional[str] = None
    source_url: Optional[str] = None
    quoted_excerpt: Optional[str] = None
    fetched_at: Optional[datetime] = None
    evidence_identity: Optional[str] = Field(default=None, exclude=True)


class ToolRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_name: ToolName
    call_number: int = Field(ge=1)
    success: bool
    duration_ms: float = Field(ge=0.0)
    summary: str


class AgentAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_name: AgentName
    focus: str


class RoutePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    route: str
    assignments: List[AgentAssignment] = Field(default_factory=list, max_length=2)
    clarification_question: Optional[str] = None
    reason_summary: str = ""


class AgentResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_name: AgentName
    answer: str
    citations: List[Citation] = Field(default_factory=list)
    evidence_sufficient: bool = True
    unresolved_points: List[str] = Field(default_factory=list)
    tool_records: List[ToolRecord] = Field(default_factory=list)
    status: RunStatus = RunStatus.SUCCESS
    metadata: Dict[str, Any] = Field(default_factory=dict)
    evidence_answer: Optional[str] = Field(default=None, exclude=True)
    evidence: Dict[str, Citation] = Field(default_factory=dict, exclude=True)


class AgentAnswer(BaseModel):
    """模型只负责正文与可回答判断，来源元数据由工具证据生成。"""

    model_config = ConfigDict(extra="forbid")

    answer: str = Field(description="使用工具提供的 [[e:Agent名:编号]] 标识引用事实，不列参考来源目录。")
    evidence_sufficient: bool
    unresolved_points: List[str] = Field(default_factory=list)
    status: RunStatus = RunStatus.SUCCESS


class ComposedAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(description="保留输入中完整的证据标识，不自行编号或列来源目录。")
    unresolved_points: List[str] = Field(default_factory=list)
    partial: bool = False


class FinalResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str
    agents: List[AgentName] = Field(default_factory=list)
    citations: List[Citation] = Field(default_factory=list)
    partial: bool = False
    unresolved_points: List[str] = Field(default_factory=list)
    status: RunStatus = RunStatus.SUCCESS
    metadata: Dict[str, Any] = Field(default_factory=dict)

