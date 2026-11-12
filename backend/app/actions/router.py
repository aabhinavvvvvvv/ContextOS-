"""HTTP routes for the approval flow and the audit log."""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.actions import service
from app.actions.schemas import ActionRead, ActionStatus, AuditEventRead
from app.config import Settings, get_settings
from app.db import SessionDep
from app.embeddings import Embedder, get_embedder

router = APIRouter(tags=["actions"])
SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_github_transport() -> Any:
    """The HTTP transport for GitHub calls: None = the real network. Tests override it."""
    return None


@router.get("/actions")
async def list_actions(
    session: SessionDep,
    status_filter: Annotated[ActionStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[ActionRead]:
    """Proposed and past actions, newest first. `status=proposed` = waiting for you."""
    return [ActionRead.model_validate(a) for a in await service.list_actions(session, status=status_filter, limit=limit)]


@router.post("/actions/{action_id}/approve")
async def approve_action(
    action_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    embedder: Annotated[Embedder, Depends(get_embedder)],
    transport: Annotated[Any, Depends(get_github_transport)],
) -> ActionRead:
    """Approve a proposal and run it now. Returns it as `executed` (with `result`) or
    `failed` (with `error`). 409 if it was already decided, 403 if its kind isn't allowed."""
    await _decide(session, settings, action_id, approve=True)
    action = await service.execute(session, embedder, settings, action_id, transport=transport)
    return ActionRead.model_validate(action)


@router.post("/actions/{action_id}/reject")
async def reject_action(action_id: uuid.UUID, session: SessionDep, settings: SettingsDep) -> ActionRead:
    await _decide(session, settings, action_id, approve=False)
    [action] = await service.get_actions(session, [action_id])
    return ActionRead.model_validate(action)


@router.get("/audit")
async def audit_log(session: SessionDep, limit: Annotated[int, Query(ge=1, le=500)] = 100) -> list[AuditEventRead]:
    """Who did what, newest first: proposals, approvals, executions, memory and connector changes."""
    return [AuditEventRead.model_validate(e) for e in await service.list_audit(session, limit=limit)]


async def _decide(session: SessionDep, settings: Settings, action_id: uuid.UUID, *, approve: bool) -> None:
    try:
        await service.decide(session, settings, action_id, approve=approve)
    except service.ActionNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Action not found")
    except service.ActionConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    except service.ActionError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc))
