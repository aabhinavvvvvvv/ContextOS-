"""API shapes for actions and the audit log."""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

ActionStatus = Literal["proposed", "approved", "rejected", "executed", "failed"]


class ActionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: Literal["create_task", "create_github_issue"]
    status: ActionStatus
    summary: str
    payload: dict[str, Any]  # exactly what will run on approval
    conversation_id: uuid.UUID | None
    message_id: uuid.UUID | None
    created_at: datetime
    decided_at: datetime | None
    executed_at: datetime | None
    result: dict[str, Any] | None
    error: str | None


class AuditEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    at: datetime
    actor: str
    event: str
    target_type: str
    target_id: str
    detail: dict[str, Any]
