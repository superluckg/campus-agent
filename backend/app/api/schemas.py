"""Web API 边界对象。"""

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class ConversationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(default="新对话", min_length=1, max_length=120)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=4000)


class ChatAccepted(BaseModel):
    request_id: str
    conversation_id: str


class MemoryUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=4000)


class WorkspaceIdentity(BaseModel):
    user_id: str


class HealthResult(BaseModel):
    status: str
    details: Optional[dict] = None
