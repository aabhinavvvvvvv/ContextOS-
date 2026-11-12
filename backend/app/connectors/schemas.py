"""API shapes for connectors."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ConnectorCreate(BaseModel):
    kind: Literal["github", "folder"]
    # github: "owner/repo". folder: a path inside FOLDER_ROOT, e.g. "notes".
    name: str = Field(min_length=1, max_length=200)


class ConnectorRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: Literal["github", "folder"]
    name: str
    created_at: datetime
    last_synced_at: datetime | None
    last_status: Literal["never", "ok", "error"]
    last_error: str | None
    documents_changed: int  # by the last successful sync
