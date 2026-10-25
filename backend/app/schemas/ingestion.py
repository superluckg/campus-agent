"""公共知识库扫描、确认和入库对象。"""

from typing import List
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class DocumentMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=500)


class ConfirmedItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: UUID
    metadata: DocumentMetadata


class ConfirmIngestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: List[ConfirmedItem] = Field(min_length=1)
