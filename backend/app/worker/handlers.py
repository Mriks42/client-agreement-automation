"""Job handlers. Each must be idempotent: a job can run more than once (at-least-once)."""

import logging
from typing import Any

from bson import ObjectId

from app.services.email import Attachment, EmailPermanentError, EmailSender, OutgoingEmail
from app.worker.queue import JobContext, JobType, enqueue

log = logging.getLogger(__name__)

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


async def handle_send_email(ctx: JobContext, payload: dict[str, Any]) -> None:
    # Idempotency: a job that crashed after the provider accepted the message would resend it.
    # We record the provider result on the job before marking done; if it's already there, skip.
    if ctx.job.get("result"):
        log.info("email already sent for this job; skipping", extra={"job_id": str(ctx.job["_id"])})
        return
    sender: EmailSender = ctx.services["email"]
    attachments = []
    for att in payload.get("attachments", []):
        stream = await ctx.services["blobs"].open_download_stream(ObjectId(att["blob_id"]))
        attachments.append(
            Attachment(
                att["filename"], await stream.read(), att.get("mime_type", "application/pdf")
            )
        )
    email = OutgoingEmail(
        to=payload["to"],
        subject=payload["subject"],
        text=payload["text"],
        html=payload.get("html"),
        from_name=payload.get("from_name"),
        reply_to=payload.get("reply_to"),
        headers=payload.get("headers", {}),
        attachments=attachments,
    )
    result = await sender.send(email)
    await ctx.db.jobs.update_one(
        {"_id": ctx.job["_id"]},
        {
            "$set": {
                "result": {
                    "provider": result.provider,
                    "provider_message_id": result.provider_message_id,
                    "message_id": result.message_id,
                }
            }
        },
    )


async def on_email_dead(ctx: JobContext, payload: dict[str, Any], error: str) -> None:
    # Feature code attaches context (file/recipient) via payload["ref"]; surfacing the failure
    # on the file timeline is wired up with the files feature.
    log.error("email permanently failed", extra={"ref": payload.get("ref"), "error": error})


def build_registry() -> dict[str, JobType]:
    return {
        SEND_EMAIL: JobType(
            handler=handle_send_email,
            max_attempts=8,
            on_dead=on_email_dead,
            permanent_errors=(EmailPermanentError,),
        ),
    }
