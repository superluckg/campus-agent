"""SSE 事件协议。"""

from datetime import datetime, timezone
from typing import Any, Dict, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SSEEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    event_id: int = Field(ge=1)
    event_type: str
    request_id: UUID
    data: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    agent_name: Optional[str] = None

