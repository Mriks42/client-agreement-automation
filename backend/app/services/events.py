"""The audit trail: append-only events on a file (CLAUDE.md "Signed artifacts are immutable").

Events are only ever inserted. A correction is a new event, never an edit. Events written by a
job carry a `key` (unique), so a job that runs twice doesn't record the same thing twice.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from app.repo import Tenant
from app.services import webhooks
from app.services.accounts import CurrentUser
from app.worker.queue import utcnow

log = logging.getLogger(__name__)

SYSTEM: dict[str, Any] = {"kind": "system", "name": "Kickoff"}


@dataclass(frozen=True)
class ClientInfo:
    """Where a signer's request came from, for the audit trail (D6)."""

    ip: str | None
    user_agent: str | None
    forwarded_for: str | None = None


def by_user(user: CurrentUser) -> dict[str, Any]:
    return {"kind": "user", "id": user.id, "name": user.name, "email": user.email}


def by_recipient(r: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "recipient", "id": r["id"], "name": r["name"], "email": r["email"]}


async def record(
    tenant: Tenant,
    file_id: ObjectId,
    type: str,
    *,
    actor: dict[str, Any],
    recipient: dict[str, Any] | None = None,
    client: ClientInfo | None = None,
    data: dict[str, Any] | None = None,
    key: str | None = None,
    at: datetime | None = None,
) -> None:
    doc: dict[str, Any] = {
        "file_id": file_id,
        "type": type,
        "at": at or utcnow(),
        "actor": actor,
        "data": data or {},
    }
    if recipient is not None:
        doc["recipient"] = {
            "id": recipient["id"],
            "name": recipient["name"],
            "email": recipient["email"],
            "role_label": recipient["role_label"],
        }
    if client is not None:
        doc["client"] = {
            "ip": client.ip,
            "user_agent": (client.user_agent or "")[:400],
            "forwarded_for": (client.forwarded_for or "")[:200] or None,
        }
    if key is not None:
        doc["key"] = key
    try:
        event_id = await tenant.insert("events", doc)
    except DuplicateKeyError:
        if key is None:
            raise
        return  # a job ran twice: recorded (and announced) the first time
    try:
        await webhooks.on_event(tenant, file_id, event_id, doc)
    except Exception:
        # Telling the firm's other systems must never undo or block what just happened here.
        log.exception("couldn't queue a webhook delivery", extra={"event_type": type})


async def for_file(tenant: Tenant, file_id: ObjectId) -> list[dict[str, Any]]:
    return await tenant.find("events", {"file_id": file_id}, sort=[("at", 1)], limit=1000)


def public(e: dict[str, Any]) -> dict[str, Any]:
    recipient = e.get("recipient") or {}
    client = e.get("client") or {}
    return {
        "type": e["type"],
        "at": e["at"],
        "actor": {"kind": e["actor"]["kind"], "name": e["actor"].get("name")},
        "recipient": {
            "name": recipient.get("name"),
            "email": recipient.get("email"),
            "role_label": recipient.get("role_label"),
        }
        if recipient
        else None,
        "ip": client.get("ip"),
        "data": e.get("data", {}),
    }
