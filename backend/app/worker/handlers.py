"""Job handlers. Each must be idempotent: a job can run more than once (at-least-once)."""

import logging
from datetime import timedelta
from typing import Any

from bson import ObjectId

from app.services import signing, templates
from app.services.email import Attachment, EmailPermanentError, EmailSender, OutgoingEmail
from app.services.email.outbox import SEND_EMAIL, enqueue_email
from app.worker.queue import JobContext, JobType

log = logging.getLogger(__name__)

__all__ = ["SEND_EMAIL", "build_registry", "enqueue_email"]


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


async def handle_analyze_template(ctx: JobContext, payload: dict[str, Any]) -> None:
    await templates.run_analysis(
        ctx.db, ctx.services["blobs"], ctx.services.get("labeller"), payload
    )


async def on_analyze_dead(ctx: JobContext, payload: dict[str, Any], error: str) -> None:
    await templates.mark_analysis_failed(ctx.db, payload, error)


def build_registry() -> dict[str, JobType]:
    return {
        SEND_EMAIL: JobType(
            handler=handle_send_email,
            max_attempts=8,
            on_dead=on_email_dead,
            permanent_errors=(EmailPermanentError,),
        ),
        signing.SIGNING_EMAIL: JobType(
            handler=signing.handle_signing_email,
            max_attempts=6,
            on_dead=signing.on_signing_email_dead,
            permanent_errors=(EmailPermanentError,),
        ),
        signing.EXPIRE_FILE: JobType(handler=signing.handle_expire_file),
        templates.ANALYZE_TEMPLATE: JobType(
            handler=handle_analyze_template,
            # Detection already falls back to heuristics on any LLM problem, so a failure here
            # is unexpected (a document that breaks extraction). One retry, then tell the user.
            max_attempts=2,
            on_dead=on_analyze_dead,
            # The LLM alone may take up to 330 s (detection/llm.py TOTAL_BUDGET_S).
            lease=timedelta(minutes=10),
        ),
    }
