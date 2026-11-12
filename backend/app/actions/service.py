"""Action lifecycle and the audit log.

    agent:  propose()                → status "proposed"   (nothing happens in the world)
    user:   decide(approve=True)     → "approved", then execute() → "executed" | "failed"
    user:   decide(approve=False)    → "rejected"

Safety properties:
- Only the user's approve request runs an action; the agent can only create proposals.
- Each transition is a guarded UPDATE (... WHERE status = 'proposed'): two approvals
  arriving at once (double click, two tabs) can't both win, so an action runs at most once.
- Every step is written to audit_log.
"""

import uuid
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.config import Settings
from app.connectors.sources import github_client
from app.embeddings import Embedder
from app.models import Action, AuditEvent, Connector, Memory


class ActionError(Exception):
    """An action can't be proposed or run (not allowed, missing configuration, API refusal)."""


class ActionNotFoundError(Exception):
    pass


class ActionConflictError(Exception):
    """The action isn't in the state this request expects (e.g. already approved)."""


def audit(
    session: AsyncSession, actor: str, event: str, target_type: str, target_id: Any, detail: dict | None = None
) -> None:
    """Record an event in the caller's transaction: it's saved if (and only if) the change is."""
    session.add(
        AuditEvent(actor=actor, event=event, target_type=target_type, target_id=str(target_id), detail=detail or {})
    )


async def propose(
    session: AsyncSession, settings: Settings, kind: str, payload: dict, summary: str
) -> Action:
    """Called by the agent. Stores a proposal; never executes anything."""
    if kind not in settings.allowed_actions:
        raise ActionError(f"Action {kind!r} is not allowed (ALLOWED_ACTIONS)")
    action = Action(id=uuid.uuid4(), kind=kind, payload=payload, summary=summary, status="proposed")
    session.add(action)
    audit(session, "agent", "action.proposed", "action", action.id, {"kind": kind, "summary": summary})
    await session.flush()
    return action


async def latest_github_repo(session: AsyncSession) -> str:
    repo = await session.scalar(
        select(Connector.name).where(Connector.kind == "github").order_by(Connector.created_at.desc())
    )
    if repo is None:
        raise ActionError("No GitHub repository is connected")
    return repo


async def list_actions(session: AsyncSession, *, status: str | None, limit: int) -> list[Action]:
    query = select(Action).order_by(Action.created_at.desc()).limit(limit)
    if status is not None:
        query = query.where(Action.status == status)
    return list(await session.scalars(query))


async def get_actions(session: AsyncSession, ids: Sequence[uuid.UUID]) -> list[Action]:
    return list(await session.scalars(select(Action).where(Action.id.in_(ids)).order_by(Action.created_at)))


async def decide(session: AsyncSession, settings: Settings, action_id: uuid.UUID, *, approve: bool) -> None:
    """Approve or reject a proposal (the user's decision). Raises ActionNotFoundError,
    ActionConflictError (already decided) or ActionError (kind not allowed)."""
    action = await session.get(Action, action_id)
    if action is None:
        raise ActionNotFoundError(action_id)
    if approve and action.kind not in settings.allowed_actions:
        raise ActionError(f"Action {action.kind!r} is not allowed (ALLOWED_ACTIONS)")
    new_status = "approved" if approve else "rejected"
    result = await session.execute(
        update(Action)
        .where(Action.id == action_id, Action.status == "proposed")  # the guard
        .values(status=new_status, decided_at=func.now())
    )
    if result.rowcount == 0:
        raise ActionConflictError(f"Action is already {action.status}")
    audit(session, "user", f"action.{new_status}", "action", action_id)
    await session.commit()


# --- Executors: what an approved action does ------------------------------------------

Executor = Callable[[AsyncSession, Embedder, Settings, Action, Any], Awaitable[dict]]


async def _create_task(session: AsyncSession, embedder: Embedder, settings: Settings, action: Action, transport: Any) -> dict:
    if action.message_id is None:
        raise ActionError("The proposal has no source message")
    content = action.payload["content"]
    [vector] = await run_in_threadpool(embedder.embed_passages, [content])
    memory = Memory(id=uuid.uuid4(), kind="task", content=content, embedding=vector, source_message_id=action.message_id)
    session.add(memory)
    return {"memory_id": str(memory.id)}


async def _create_github_issue(session: AsyncSession, embedder: Embedder, settings: Settings, action: Action, transport: Any) -> dict:
    if not (settings.github_token and settings.github_token.get_secret_value()):
        raise ActionError("GITHUB_TOKEN is not configured")
    payload = action.payload
    async with github_client(settings, transport) as client:
        response = await client.post(
            f"/repos/{payload['repo']}/issues", json={"title": payload["title"], "body": payload.get("body", "")}
        )
    if response.status_code != 201:
        raise ActionError(f"GitHub refused to create the issue ({response.status_code})")
    issue = response.json()
    return {"issue_url": issue["html_url"], "number": issue["number"]}


EXECUTORS: dict[str, Executor] = {
    "create_task": _create_task,
    "create_github_issue": _create_github_issue,
}


async def execute(
    session: AsyncSession, embedder: Embedder, settings: Settings, action_id: uuid.UUID, *, transport: Any = None
) -> Action:
    """Run an approved action exactly once; record executed/failed with the result or error."""
    # Lock the row: the approve step already lets only one request through, and the lock
    # keeps a second execute() (e.g. a retry) waiting until this one has recorded its result.
    action = await session.scalar(
        select(Action).where(Action.id == action_id, Action.status == "approved").with_for_update()
    )
    if action is None:
        raise ActionConflictError("Action is not approved")
    try:
        result = await EXECUTORS[action.kind](session, embedder, settings, action, transport)
    except Exception as exc:
        await session.rollback()
        await session.execute(
            update(Action).where(Action.id == action_id).values(status="failed", error=str(exc)[:500], executed_at=func.now())
        )
        audit(session, "system", "action.failed", "action", action_id, {"error": str(exc)[:500]})
    else:
        action.status, action.result, action.executed_at = "executed", result, func.now()
        audit(session, "system", "action.executed", "action", action_id, result)
    await session.commit()
    refreshed = await session.get(Action, action_id, populate_existing=True)
    assert refreshed is not None
    return refreshed


async def list_audit(session: AsyncSession, *, limit: int) -> list[AuditEvent]:
    return list(await session.scalars(select(AuditEvent).order_by(AuditEvent.at.desc(), AuditEvent.id).limit(limit)))
