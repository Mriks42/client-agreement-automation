"""Files: one job or case for one client, and the agreement it needs signed.

A file copies the approved template (its version, PDF, roles, and fields with their placeholder
redactions) when it is created. Later edits to the template make a new version and never change
a file that already exists (DECISIONS.md "What I'm including", item 14).

The sender types each value once (fields that share a key share it), names one signer per role,
previews the exact PDF, and confirms. Sending is one atomic `draft -> sent` update that also makes
the first signer current; their email goes out from a job. See services/signing.py for the rest.
"""

import asyncio
import hashlib
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from gridfs import AsyncGridFSBucket

from app.config import Settings
from app.domain.fields import sender_inputs
from app.repo import Tenant
from app.services import events, pdf_engine, signing, storage
from app.services.accounts import CurrentUser, normalize_email
from app.services.errors import Conflict, Invalid, NotFound
from app.services.signing import LIVE, SIGNING_EMAIL
from app.worker.queue import enqueue, utcnow

DRAFT = "draft"
MAX_TITLE = 120
MAX_NAME = 120
RESEND_GAP = timedelta(seconds=60)
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass
class RecipientIn:
    role: str
    name: str
    email: str


@dataclass
class FileIn:
    title: str | None
    values: dict[str, Any]
    recipients: list[RecipientIn]


# ---- validation --------------------------------------------------------------------------------
def clean_values(
    inputs: list[dict[str, Any]], values: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    by_key = {i["key"]: i for i in inputs}
    clean: dict[str, Any] = {}
    problems: list[str] = []
    for key, value in values.items():
        spec = by_key.get(key)
        if spec is None:
            continue  # not a value this agreement asks for
        if spec["type"] == "checkbox":
            if value is True:
                clean[key] = True
            continue
        if not isinstance(value, str):
            problems.append(f"“{spec['label']}” must be text.")
            continue
        text = value.strip()
        if not text:
            continue
        if len(text) > signing.MAX_TEXT:
            problems.append(f"“{spec['label']}” is too long.")
            continue
        if spec["type"] == "date":
            try:
                date.fromisoformat(text)
            except ValueError:
                problems.append(f"“{spec['label']}” isn't a valid date.")
                continue
        clean[key] = text
    return clean, problems


def clean_recipients(
    roles: list[dict[str, Any]], given: list[RecipientIn]
) -> tuple[list[dict[str, Any]], list[str]]:
    by_role = {r.role: r for r in given}
    problems: list[str] = []
    out: list[dict[str, Any]] = []
    for role in sorted(roles, key=lambda r: r["order"]):
        r = by_role.get(role["key"])
        name = " ".join((r.name if r else "").split())[:MAX_NAME]
        email = normalize_email(r.email if r else "")
        if not name:
            problems.append(f"Enter the name of the {role['label']} signer.")
        if not _EMAIL_RE.match(email):
            problems.append(f"Enter a valid email for the {role['label']} signer.")
        out.append(
            {
                "id": f"r{role['order']}",
                "role": role["key"],
                "role_label": role["label"],
                "kind": role["kind"],
                "order": role["order"],
                "name": name,
                "email": email,
                "status": "pending",
                "token_hashes": [],
                "emails_sent": 0,
                "resends": 0,
            }
        )
    return out, problems


def default_title(recipients: list[dict[str, Any]]) -> str:
    client = next((r for r in recipients if r["kind"] == "client"), None)
    return (client or recipients[0])["name"] if recipients else "Untitled file"


def missing_values(file: dict[str, Any]) -> list[str]:
    """Required blanks the sender left empty. Shown in the preview; they don't block sending,
    because the human looking at the preview decides (many templates mark optional rows
    required)."""
    values = file["values"]
    return [
        i["label"]
        for i in sender_inputs(file["template"]["fields"])
        if i["required"] and i["type"] != "checkbox" and not values.get(i["key"])
    ]


# ---- create and edit ---------------------------------------------------------------------------
async def create(
    tenant: Tenant, user: CurrentUser, template_id: str, body: FileIn
) -> dict[str, Any]:
    tpl = await tenant.get("templates", template_id)
    if tpl is None:
        raise NotFound(template_id)
    if tpl["status"] != "active":
        raise Conflict("Approve this agreement's fields before opening a file with it.")
    snapshot = {
        "id": tpl["_id"],
        "version": tpl["version"],
        "name": tpl["name"],
        "pdf_blob_id": tpl["pdf_blob_id"],
        "page_count": tpl["page_count"],
        "pages": tpl["pages"],
        "roles": tpl["roles"],
        "fields": tpl["fields"],
    }
    values, recipients, title = _check(snapshot, body)
    now = utcnow()
    doc: dict[str, Any] = {
        "title": title,
        "status": DRAFT,
        "revision": 1,
        "template": snapshot,
        "values": values,
        "recipients": recipients,
        "prepared": None,
        "created_by": user.id,
        "created_by_name": user.name,
        "created_by_email": user.email,
        "created_at": now,
        "updated_at": now,
    }
    file_id = await tenant.insert("files", doc)
    await events.record(
        tenant,
        file_id,
        "created",
        actor=events.by_user(user),
        data={"agreement": tpl["name"], "version": tpl["version"]},
    )
    doc["_id"] = file_id
    doc["org_id"] = tenant.org_id
    return doc


def _check(
    snapshot: dict[str, Any], body: FileIn
) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    values, p1 = clean_values(sender_inputs(snapshot["fields"]), body.values)
    recipients, p2 = clean_recipients(snapshot["roles"], body.recipients)
    if p1 or p2:
        raise Invalid(p1 + p2)
    title = " ".join((body.title or "").split())[:MAX_TITLE] or default_title(recipients)
    return values, recipients, title


async def update(tenant: Tenant, file_id: str, revision: int, body: FileIn) -> dict[str, Any]:
    file = await get(tenant, file_id)
    if file["status"] != DRAFT:
        raise Conflict("This file has been sent, so its details can't be changed.")
    if file["revision"] != revision:
        raise Conflict("This file changed since you opened it (another tab?). Reload it.")
    values, recipients, title = _check(file["template"], body)
    updated = await tenant.transition(
        "files",
        file["_id"],
        {"status": DRAFT, "revision": revision},
        {
            "$set": {
                "title": title,
                "values": values,
                "recipients": recipients,
                "updated_at": utcnow(),
            },
            "$inc": {"revision": 1},
        },
    )
    if updated is None:
        raise Conflict("This file changed while saving. Reload it.")
    return updated


async def delete_draft(tenant: Tenant, bucket: AsyncGridFSBucket, file_id: str) -> None:
    file = await get(tenant, file_id)
    if not await tenant.delete("files", file["_id"], {"status": DRAFT}):
        raise Conflict("Only a draft can be deleted. Void a file that has been sent.")
    if file.get("prepared"):
        await storage.delete(bucket, file["prepared"]["blob_id"])


async def get(tenant: Tenant, file_id: str) -> dict[str, Any]:
    file = await tenant.get("files", file_id)
    if file is None:
        raise NotFound(file_id)
    return file


# ---- preview and send --------------------------------------------------------------------------
async def prepare(
    tenant: Tenant, bucket: AsyncGridFSBucket, file: dict[str, Any]
) -> dict[str, Any]:
    """Make sure the draft has its prepared PDF (the agreement with the sender's values written
    in) for its current revision. That PDF is the preview, and exactly what gets sent."""
    p = file.get("prepared")
    if (p and p["revision"] == file["revision"]) or file["status"] != DRAFT:
        return file
    async with signing.STAMP_LOCK:
        file = await get(tenant, str(file["_id"]))  # another request may have just done it
        p = file.get("prepared")
        if (p and p["revision"] == file["revision"]) or file["status"] != DRAFT:
            return file
        pdf = await storage.get(bucket, file["template"]["pdf_blob_id"])
        stamps = signing.build_stamps(file["template"]["fields"], sender_values=file["values"])
        if stamps:
            pdf = await asyncio.to_thread(pdf_engine.stamp, pdf, stamps)
        blob = await storage.put(
            bucket,
            pdf,
            filename=f"{file['title']}.pdf",
            org_id=tenant.org_id,
            kind="file_prepared",
        )
        updated = await tenant.transition(
            "files",
            file["_id"],
            {"status": DRAFT, "revision": file["revision"]},
            {
                "$set": {
                    "prepared": {
                        "blob_id": blob,
                        "revision": file["revision"],
                        "sha256": hashlib.sha256(pdf).hexdigest(),
                    }
                }
            },
        )
    if updated is None:
        await storage.delete(bucket, blob)
        raise Conflict("This file changed while preparing the preview. Reload it.")
    if p:
        await storage.delete(bucket, p["blob_id"])  # the preview of an earlier revision
    return updated


async def send(
    tenant: Tenant, bucket: AsyncGridFSBucket, user: CurrentUser, file_id: str, revision: int
) -> dict[str, Any]:
    file = await get(tenant, file_id)
    if file["status"] != DRAFT:
        raise Conflict("This file has already been sent.")
    if file["revision"] != revision:
        raise Conflict("The details changed since you previewed them. Check the preview again.")
    file = await prepare(tenant, bucket, file)
    if file["revision"] != revision:
        raise Conflict("The details changed since you previewed them. Check the preview again.")
    now = utcnow()
    first = file["recipients"][0]
    updated = await tenant.transition(
        "files",
        file["_id"],
        {"status": DRAFT, "revision": revision},
        {
            "$set": {
                "status": "sent",
                "sent_at": now,
                "sent_by": user.id,
                "expires_at": now + signing.EXPIRY,
                "updated_at": now,
                **signing.activate(0, now),
            },
            "$inc": {"revision": 1},
        },
    )
    if updated is None:
        raise Conflict("This file changed while sending. Reload it.")
    await events.record(
        tenant,
        file["_id"],
        "sent",
        actor=events.by_user(user),
        data={"pdf_sha256": file["prepared"]["sha256"]},
    )
    await signing.schedule_signer(tenant, file["_id"], first["id"], now)
    await enqueue(
        tenant.db,
        signing.EXPIRE_FILE,
        {"org_id": str(tenant.org_id), "file_id": str(file["_id"])},
        run_at=updated["expires_at"],
        dedupe_key=f"{signing.EXPIRE_FILE}:{file['_id']}",
        org_id=str(tenant.org_id),
    )
    return updated


# ---- while it's out for signature --------------------------------------------------------------
def _current(file: dict[str, Any], recipient_id: str) -> tuple[int, dict[str, Any]]:
    i = signing.recipient_index(file, recipient_id)
    r = file["recipients"][i]
    if file["status"] not in LIVE or r["status"] != "active":
        raise Conflict("Only the person whose turn it is to sign has a signing link.")
    return i, r


async def copy_link(
    tenant: Tenant, settings: Settings, user: CurrentUser, file_id: str, recipient_id: str
) -> str:
    """A fresh link for the current signer, for when the email is slow or lands in spam (D11).
    Recorded in the audit trail as shared by the sender."""
    file = await get(tenant, file_id)
    i, r = _current(file, recipient_id)
    token, token_hash = signing.new_token()
    updated = await tenant.transition(
        "files", file["_id"], signing.is_current(r), {"$push": signing.push_token(i, token_hash)}
    )
    if updated is None:
        raise Conflict("This file changed. Reload it.")
    await events.record(tenant, file["_id"], "link_copied", actor=events.by_user(user), recipient=r)
    return signing.sign_url(settings, token)


async def update_recipient(
    tenant: Tenant,
    user: CurrentUser,
    file_id: str,
    recipient_id: str,
    *,
    name: str | None = None,
    email: str | None = None,
    resend: bool = False,
) -> dict[str, Any]:
    """Correct a signer's name or email, and/or send their invitation again. Correcting the
    current signer's email retires their earlier links and sends a new one."""
    file = await get(tenant, file_id)
    i = signing.recipient_index(file, recipient_id)
    r = file["recipients"][i]
    if file["status"] not in LIVE:
        raise Conflict("This file isn't out for signature.")
    if r["status"] not in ("active", "pending"):
        raise Conflict(f"{r['name']} has already finished.")
    now = utcnow()
    changes: dict[str, Any] = {}
    new_email = normalize_email(email) if email is not None else r["email"]
    new_name = " ".join(name.split())[:MAX_NAME] if name is not None else r["name"]
    if not _EMAIL_RE.match(new_email):
        raise Invalid(["Enter a valid email address."])
    if not new_name:
        raise Invalid(["Enter their name."])
    if new_email != r["email"]:
        changes[f"recipients.{i}.email"] = new_email
        if r["status"] == "active":
            changes[f"recipients.{i}.token_hashes"] = []  # the old address's links stop working
            resend = True
    if new_name != r["name"]:
        changes[f"recipients.{i}.name"] = new_name
    resend = resend and r["status"] == "active"
    if not changes and not resend:
        return file
    match: dict[str, Any] = {"id": r["id"], "status": r["status"]}
    inc: dict[str, int] = {"revision": 1}
    if resend:
        last = r.get("last_resend_at")
        if last and now - last < RESEND_GAP:
            raise Conflict("You just sent it. Wait a minute before sending it again.")
        changes[f"recipients.{i}.last_resend_at"] = now
        inc[f"recipients.{i}.resends"] = 1
        match["last_resend_at"] = last  # so two quick clicks send once
    updated = await tenant.transition(
        "files",
        file["_id"],
        {"status": {"$in": list(LIVE)}, "recipients": {"$elemMatch": match}},
        {"$set": {**changes, "updated_at": now}, "$inc": inc},
    )
    if updated is None:
        raise Conflict("This file changed. Reload it.")
    actor = events.by_user(user)
    if new_email != r["email"]:
        await events.record(
            tenant,
            file["_id"],
            "email_corrected",
            actor=actor,
            recipient=r,
            data={"from": r["email"], "to": new_email},
        )
    if new_name != r["name"]:
        await events.record(
            tenant,
            file["_id"],
            "name_corrected",
            actor=actor,
            recipient=r,
            data={"from": r["name"], "to": new_name},
        )
    if resend:
        n = updated["recipients"][i]["resends"]
        await events.record(
            tenant, file["_id"], "resend_requested", actor=actor, recipient=updated["recipients"][i]
        )
        await enqueue(
            tenant.db,
            SIGNING_EMAIL,
            {
                "org_id": str(tenant.org_id),
                "file_id": str(file["_id"]),
                "recipient_id": r["id"],
                "reason": "resend",
            },
            dedupe_key=f"{SIGNING_EMAIL}:{file['_id']}:{r['id']}:resend{n}",
            org_id=str(tenant.org_id),
        )
    return updated


async def void(tenant: Tenant, user: CurrentUser, file_id: str, reason: str) -> dict[str, Any]:
    file = await get(tenant, file_id)
    reason = reason.strip()[:500]
    if not reason:
        raise Invalid(["Say why you're voiding it. Signers who open their link will see this."])
    now = utcnow()
    updated = await tenant.transition(
        "files",
        file["_id"],
        {"status": {"$in": list(LIVE)}},
        {
            "$set": {
                "status": "voided",
                "voided_at": now,
                "voided_by": user.id,
                "void_reason": reason,
                "updated_at": now,
            },
            "$inc": {"revision": 1},
        },
    )
    if updated is None:
        raise Conflict("Only a file that is out for signature can be voided.")
    await events.record(
        tenant, file["_id"], "voided", actor=events.by_user(user), data={"reason": reason}
    )
    return updated


# ---- output ------------------------------------------------------------------------------------
def _waiting_for(file: dict[str, Any]) -> dict[str, Any] | None:
    r = next((r for r in file["recipients"] if r["status"] == "active"), None)
    return (
        {"name": r["name"], "role_label": r["role_label"]} if r and file["status"] in LIVE else None
    )


def summary(f: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(f["_id"]),
        "title": f["title"],
        "status": f["status"],
        "agreement_name": f["template"]["name"],
        "template_id": str(f["template"]["id"]),
        "signer_count": len(f["recipients"]),
        "signed_count": sum(1 for r in f["recipients"] if r["status"] == "signed"),
        "waiting_for": _waiting_for(f),
        "created_at": f["created_at"],
        "updated_at": f["updated_at"],
        "sent_at": f.get("sent_at"),
        "completed_at": f.get("completed_at"),
    }


_RECIPIENT_KEYS = (
    "id",
    "role",
    "role_label",
    "order",
    "name",
    "email",
    "status",
    "activated_at",
    "viewed_at",
    "consented_at",
    "signed_at",
    "declined_at",
    "decline_reason",
    "emails_sent",
    "resends",
)


def detail(f: dict[str, Any], evts: list[dict[str, Any]]) -> dict[str, Any]:
    tpl = f["template"]
    recipients = []
    for r in f["recipients"]:
        out = {k: r.get(k) for k in _RECIPIENT_KEYS}
        last = r.get("last_email")
        out["last_email"] = (
            {k: last.get(k) for k in ("status", "at", "to", "reason", "error")} if last else None
        )
        recipients.append(out)
    return {
        **summary(f),
        "revision": f["revision"],
        "template": {
            "id": str(tpl["id"]),
            "name": tpl["name"],
            "version": tpl["version"],
            "page_count": tpl["page_count"],
            "pages": tpl["pages"],
            "roles": tpl["roles"],
        },
        "sender_inputs": sender_inputs(tpl["fields"]),
        "values": f["values"],
        "recipients": recipients,
        "missing": missing_values(f) if f["status"] == DRAFT else [],
        "page_rev": f"{f['revision']}" if f["status"] == DRAFT else signing.page_rev(f),
        "prepared_sha256": (f.get("prepared") or {}).get("sha256"),
        "expires_at": f.get("expires_at"),
        "declined_at": f.get("declined_at"),
        "voided_at": f.get("voided_at"),
        "void_reason": f.get("void_reason"),
        "finalized_at": f.get("finalized_at"),
        "final_sha256": f.get("final_sha256"),
        "finalize_error": (f.get("finalize_error") or {}).get("error"),
        "events": [events.public(e) for e in evts],
    }
