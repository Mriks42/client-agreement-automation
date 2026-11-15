"""Queueing an email. Product code never sends directly: the send_email job does (D9)."""

from typing import Any

from bson import ObjectId

from app.worker.queue import enqueue

SEND_EMAIL = "send_email"


async def enqueue_email(
    db: Any,
    email: dict[str, Any],
    *,
    dedupe_key: str | None = None,
    org_id: str | None = None,
) -> ObjectId:
    """email: OutgoingEmail fields; attachments as [{blob_id, filename, mime_type}] (GridFS ids),
    so large PDFs never sit inside job documents."""
    return await enqueue(db, SEND_EMAIL, email, dedupe_key=dedupe_key, org_id=org_id)
