"""Finalizing a completed file: the signed PDF with its audit certificate, sent to everyone.

Runs as the `finalize_file` job, which signing.submit enqueues when the last signer signs. The
job can run more than once, so it has two steps and each is safe to repeat:

1. Build. Stamp every signer's values onto the prepared PDF (the one the sender previewed and
   sent), bake any form fields into the page, append the certificate, and store the result.
   One conditional update (`finalized_at: None`) records it. A rerun, or a second worker that
   lost the race, keeps the first result, so the signed PDF never changes once it exists
   (CLAUDE.md "Signed artifacts are immutable").
2. Deliver. Queue the signed copy to every signer and the sender (one email job per address,
   deduplicated), then record the `finalized` event.

The certificate can't carry the SHA-256 of the PDF it's part of. That hash is stored on the file,
shown on the file page and printed in every delivery email; the certificate says where to find it.
"""

import asyncio
import hashlib
import logging
import re
from datetime import UTC, datetime
from typing import Any

from bson import ObjectId
from gridfs import AsyncGridFSBucket

from app.config import Settings
from app.repo import Tenant
from app.services import branding, events, pdf_engine, signing, storage
from app.services.email import messages as mail
from app.services.email.outbox import enqueue_email
from app.services.errors import Conflict, NotFound
from app.services.events import SYSTEM
from app.services.pdf_engine import CertRow, CertSection
from app.services.signing import FINALIZE_FILE
from app.worker.queue import JobContext, enqueue, retry_dead_job, utcnow

log = logging.getLogger(__name__)

MAX_TRAIL = 300  # events listed on the certificate; a file has a few dozen at most


# ---- the job -----------------------------------------------------------------------------------
async def handle_finalize_file(ctx: JobContext, payload: dict[str, Any]) -> None:
    tenant = Tenant(ctx.db, ObjectId(payload["org_id"]))
    file = await tenant.get("files", payload["file_id"])
    if file is None or file["status"] != "completed":
        return
    settings: Settings = ctx.services["settings"]
    org = await branding.load(ctx.db, tenant.org_id)
    org_name = org["name"] if org else settings.email_product_name
    if not file.get("finalized_at"):
        file = await build(tenant, ctx.services["blobs"], settings, file, org_name)
    await deliver(tenant, settings, file, org_name, branding.public(org, settings.app_base_url))


async def on_finalize_dead(ctx: JobContext, payload: dict[str, Any], error: str) -> None:
    """Show it on the file page, which offers Try again (see `retry`)."""
    tenant = Tenant(ctx.db, ObjectId(payload["org_id"]))
    short = error.split("\n")[0][:300]
    file = await tenant.transition(
        "files",
        payload["file_id"],
        {"status": "completed", "finalized_at": None},
        {"$set": {"finalize_error": {"at": utcnow(), "error": short}}},
    )
    if file is not None:
        await events.record(
            tenant,
            file["_id"],
            "finalize_failed",
            actor=SYSTEM,
            data={"error": short},
            key=f"dead:{ctx.job['_id']}",
        )


async def retry(tenant: Tenant, user_actor: dict[str, Any], file_id: str) -> dict[str, Any]:
    """Try again after the job died: give the same job a fresh set of attempts."""
    file = await tenant.get("files", file_id)
    if file is None:
        raise NotFound(file_id)
    if file["status"] != "completed":
        raise Conflict("Only a file everyone has signed has a signed PDF to make.")
    if file.get("finalized_at"):
        return file
    org = str(tenant.org_id)
    key = f"{FINALIZE_FILE}:{file['_id']}"
    job = await tenant.db.jobs.find_one({"dedupe_key": key, "org_id": org})
    if job is None:
        await enqueue(
            tenant.db,
            FINALIZE_FILE,
            {"org_id": org, "file_id": str(file["_id"])},
            dedupe_key=key,
            org_id=org,
        )
    elif job["status"] == "dead":
        await retry_dead_job(tenant.db, job["_id"], org_id=org)
    updated = await tenant.transition(
        "files",
        file["_id"],
        {"status": "completed", "finalized_at": None},
        {"$unset": {"finalize_error": ""}},
    )
    if updated is not None and file.get("finalize_error"):
        await events.record(tenant, file["_id"], "finalize_retried", actor=user_actor)
    return updated or file


# ---- step 1: the signed PDF --------------------------------------------------------------------
async def build(
    tenant: Tenant,
    bucket: AsyncGridFSBucket,
    settings: Settings,
    file: dict[str, Any],
    org_name: str,
) -> dict[str, Any]:
    tpl = file["template"]
    template_pdf = await storage.get(bucket, tpl["pdf_blob_id"])
    source = await tenant.get("templates", tpl["id"], {"source.sha256": 1, "source.format": 1})
    evts = await events.for_file(tenant, file["_id"])
    sections = certificate(
        file,
        evts,
        org_name=org_name,
        product=settings.email_product_name,
        template_sha256=hashlib.sha256(template_pdf).hexdigest(),
        source=(source or {}).get("source"),
    )
    async with signing.STAMP_LOCK:
        signed = await signing.stamp_signed(bucket, file)
        pdf = await asyncio.to_thread(
            pdf_engine.finalize_pdf,
            signed,
            sections,
            title="Certificate of completion",
            subtitle=f"{tpl['name']}  ·  {file['title']}  ·  {org_name}",
            footer=f"{settings.email_product_name}  ·  File {file['_id']}",
        )
    digest = hashlib.sha256(pdf).hexdigest()
    blob = await storage.put(
        bucket, pdf, filename=download_name(file), org_id=tenant.org_id, kind="file_final"
    )
    now = utcnow()
    updated = await tenant.transition(
        "files",
        file["_id"],
        {"status": "completed", "finalized_at": None},
        {
            "$set": {
                "final_blob_id": blob,
                "final_sha256": digest,
                "finalized_at": now,
                "updated_at": now,
            },
            "$unset": {"finalize_error": ""},
        },
    )
    if updated is None:
        # Another run finished first. Its PDF is the one on record; drop ours.
        await storage.delete(bucket, blob)
        again = await tenant.get("files", file["_id"])
        if again is None or not again.get("finalized_at"):
            raise Conflict("The file changed while its signed PDF was being made.")
        return again
    log.info("file finalized", extra={"file_id": str(file["_id"]), "bytes": len(pdf)})
    return updated


def download_name(file: dict[str, Any], suffix: str = "signed") -> str:
    base = f"{file['template']['name']} - {file['title']}"
    clean = re.sub(r"[^A-Za-z0-9 ._-]", "", base).strip() or "agreement"
    return f"{clean} - {suffix}.pdf" if suffix else f"{clean}.pdf"


# ---- the certificate ---------------------------------------------------------------------------
def when(d: datetime | None) -> str:
    if d is None:
        return "-"
    d = d.astimezone(UTC) if d.tzinfo else d.replace(tzinfo=UTC)
    return f"{d:%b} {d.day}, {d.year}, {d:%H:%M:%S} UTC"


def _mark(mark: dict[str, Any] | None) -> str:
    if not mark:
        return "-"
    if mark.get("kind") == "drawn":
        return "Drawn on screen"
    return f'Typed: "{mark.get("text", "")}"'


def _describe(e: dict[str, Any]) -> str:
    who = (e.get("recipient") or {}).get("name") or "The signer"
    by = e["actor"].get("name") or "Someone"
    d = e.get("data") or {}
    match e["type"]:
        case "created":
            return f"{by} opened the file (version {d.get('version', '?')} of the agreement)"
        case "sent":
            return f"{by} sent it for signature"
        case "email_sent":
            again = " again" if d.get("reason") == "resend" else ""
            return f"Signing email sent{again} to {who} ({d.get('to', '')})"
        case "reminded":
            return f"Reminder emailed to {who} ({d.get('to', '')})"
        case "email_failed":
            return f"Email to {d.get('to', '')} failed"
        case "viewed":
            return f"{who} opened the agreement"
        case "consented":
            return f"{who} agreed to sign electronically"
        case "signed":
            return f"{who} signed"
        case "completed":
            return "Everyone had signed"
        case "link_copied":
            return f"{by} copied the signing link for {who}, to share it directly"
        case "signing_opened_in_app":
            return f"{who} opened their signing page from the file, logged in as {d.get('login')}"
        case "email_corrected":
            return f"{by} changed {who}'s email from {d.get('from')} to {d.get('to')}"
        case "name_corrected":
            return f"{by} changed a signer's name from {d.get('from')} to {d.get('to')}"
        case "resend_requested":
            return f"{by} resent the signing email to {who}"
        case _:
            return str(e["type"]).replace("_", " ").capitalize()


def certificate(
    file: dict[str, Any],
    evts: list[dict[str, Any]],
    *,
    org_name: str,
    product: str,
    template_sha256: str,
    source: dict[str, Any] | None,
) -> list[CertSection]:
    tpl = file["template"]
    pages = tpl["page_count"]
    document = CertSection(
        "Document",
        [
            CertRow("Agreement", f"{tpl['name']} (version {tpl['version']})"),
            CertRow("File", file["title"]),
            CertRow("File ID", str(file["_id"]), mono=True),
            CertRow("From", org_name),
            CertRow("Sent by", f"{file['created_by_name']} ({file['created_by_email']})"),
            CertRow("Sent for signature", when(file.get("sent_at"))),
            CertRow("Signed by everyone", when(file.get("completed_at"))),
            CertRow(
                "Pages",
                f"{pages} page{'s' if pages != 1 else ''} of agreement, followed by this "
                "certificate",
            ),
        ],
    )

    hashes = [
        CertRow("Agreement as approved", template_sha256, mono=True),
        CertRow("As sent for signature", (file.get("prepared") or {}).get("sha256", ""), mono=True),
    ]
    if source and source.get("sha256"):
        fmt = "Word" if source.get("format") == "docx" else "PDF"
        hashes.insert(0, CertRow(f"Uploaded original ({fmt})", source["sha256"], mono=True))
    fingerprints = CertSection(
        "Document fingerprints (SHA-256)",
        hashes,
        [
            "A SHA-256 hash is a fingerprint of a file: changing even one character of the file "
            "gives a completely different value.",
            f"Agreement as approved: the agreement {org_name} uploaded, as a PDF, with the blanks "
            f"and signature places a person at {org_name} approved. As sent for signature: that "
            "agreement with this file's details filled in, exactly as previewed by the sender. "
            "This is the document every signer reviewed and signed.",
        ],
    )

    copied = {
        (e.get("recipient") or {}).get("id")
        for e in evts
        if e["type"] == "link_copied" and e.get("recipient")
    }
    in_app = {
        (e.get("recipient") or {}).get("id")
        for e in evts
        if e["type"] == "signing_opened_in_app" and e.get("recipient")
    }
    consent_version = {
        (e.get("recipient") or {}).get("id"): (e.get("data") or {}).get("disclosure_version")
        for e in evts
        if e["type"] == "consented"
    }
    signers = [CertSection("Signers", paras=[_signing_order(file)])]
    for r in file["recipients"]:
        client = r.get("signed_client") or {}
        link = f"A personal link emailed to {r['email']}"
        if r["id"] in copied:
            link += f". {file['created_by_name']} also copied a link to share with them directly"
        if r["id"] in in_app:
            link += ". They also opened it from the file while logged in to their own account"
        version = consent_version.get(r["id"]) or signing.ESIGN_DISCLOSURE_VERSION
        rows = [
            CertRow("Email", r["email"]),
            CertRow("Role", r["role_label"]),
            CertRow("Signing link", link),
            CertRow("Viewed", when(r.get("viewed_at"))),
            CertRow(
                "Agreed to sign electronically",
                f"{when(r.get('consented_at'))} (disclosure version {version})",
            ),
            CertRow("Signed", when(r.get("signed_at"))),
            CertRow("Signature", _mark(r.get("signature"))),
        ]
        if r.get("initials"):
            rows.append(CertRow("Initials", _mark(r.get("initials"))))
        rows += [
            CertRow("IP address", client.get("ip") or "-"),
            CertRow("Browser (user agent)", client.get("user_agent") or "-"),
        ]
        signers.append(CertSection(f"{r['order']}. {r['name']}", rows, minor=True))

    trail = CertSection(
        "Audit trail",
        [
            CertRow(
                when(e["at"]),
                _describe(e)
                + (
                    f" (IP {e['client']['ip']})"
                    if e.get("client", {}).get("ip") and e["actor"]["kind"] == "recipient"
                    else ""
                ),
            )
            for e in evts[:MAX_TRAIL]
        ],
    )

    verify = CertSection(
        "How to check this document",
        paras=[
            f"The SHA-256 of this complete PDF, including this certificate, is recorded by "
            f"{product} with file {file['_id']}. It is printed in the email that delivered the "
            f"signed copy, and shown on the file's page in {product}.",
            "To check a copy, compute its SHA-256 and compare the two values. On a Mac or Linux, "
            'run: shasum -a 256 "file.pdf". On Windows, run: certutil -hashfile "file.pdf" '
            "SHA256. If the values match, the copy is exactly the signed document. If they "
            "differ, the copy has been changed since it was signed.",
            "All times are in UTC. Each signer agreed to use electronic records and signatures "
            "before signing, and could ask for a paper copy or decline instead.",
        ],
    )
    return [document, fingerprints, *signers, trail, verify]


def _signing_order(file: dict[str, Any]) -> str:
    names = [f"{r['name']} ({r['role_label']})" for r in file["recipients"]]
    if len(names) == 1:
        return f"One signer: {names[0]}."
    return (
        "In signing order: " + ", then ".join(names) + ". Each was emailed when it was their turn."
    )


# ---- step 2: everyone's copy -------------------------------------------------------------------
async def deliver(
    tenant: Tenant,
    settings: Settings,
    file: dict[str, Any],
    org_name: str,
    brand: dict[str, Any] | None = None,
) -> None:
    file_id, org = str(file["_id"]), str(tenant.org_id)
    attachment = {
        "blob_id": str(file["final_blob_id"]),
        "filename": download_name(file),
        "mime_type": "application/pdf",
    }
    parties = [f"{r['name']} ({r['role_label']})" for r in file["recipients"]]
    sender_email = file["created_by_email"].lower()
    product = settings.email_product_name
    base: dict[str, Any] = {
        "attachments": [attachment],
        "ref": file_id,
        "ref_kind": "signed_copy",
        "org_id": org,
    }
    emailed: list[str] = []
    for r in file["recipients"]:
        to = r["email"].lower()
        if to == sender_email or to in emailed:
            continue  # the sender gets their own message below; nobody gets two
        msg = mail.signed_copy(
            org_name=org_name,
            signer_name=r["name"],
            agreement_name=file["template"]["name"],
            sender_name=file["created_by_name"],
            parties=parties,
            sha256=file["final_sha256"],
            verify_url=signing.verify_url(settings),
            product=product,
            brand=brand,
        )
        await enqueue_email(
            tenant.db,
            {
                **base,
                "to": [r["email"]],
                "subject": msg.subject,
                "text": msg.text,
                "html": msg.html,
                "from_name": org_name,
                "reply_to": file["created_by_email"],
            },
            dedupe_key=f"signed_copy:{file_id}:{to}",
            org_id=org,
        )
        emailed.append(to)
    msg = mail.completed_notice(
        org_name=org_name,
        agreement_name=file["template"]["name"],
        file_title=file["title"],
        parties=parties,
        sha256=file["final_sha256"],
        file_url=signing.file_url(settings, file_id),
        verify_url=signing.verify_url(settings),
        product=product,
    )
    await enqueue_email(
        tenant.db,
        {
            **base,
            "to": [file["created_by_email"]],
            "subject": msg.subject,
            "text": msg.text,
            "html": msg.html,
            "from_name": product,
        },
        dedupe_key=f"signed_copy:{file_id}:{sender_email}",
        org_id=org,
    )
    emailed.append(sender_email)
    await events.record(
        tenant,
        file["_id"],
        "finalized",
        actor=SYSTEM,
        data={"sha256": file["final_sha256"], "emailed_to": emailed},
        key=f"finalized:{file_id}",
    )
