"""Signing: the file state machine, routing, signing tokens, and each step a signer takes.

File status (every arrow is one atomic, conditional update of the file document):

    draft --send--> sent --a signer, not the last--> partially_signed --the last signer--> completed
                      |                                   |
                      +----- declined / expired / voided -+

Recipients are embedded in the file, in signing order, so a signature, the file's new status and
the next signer's activation are a single atomic update. Only the current signer is `active`;
later signers stay `pending` and have no link until their turn.

Signing tokens are 256-bit random strings. Only their SHA-256 is stored. A token is minted when
an email goes out (inside the job, so it never sits in a job document) or when the sender copies
the link, and every token of a recipient works until they sign or decline, or the file closes.
"""

import asyncio
import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from bson import ObjectId
from gridfs import AsyncGridFSBucket
from pymongo.asynchronous.database import AsyncDatabase

from app.config import Settings
from app.domain.fields import SENDER, SIGNER_ONLY_TYPES
from app.repo import Tenant, find_file_by_signing_token
from app.services import branding, events, pdf_engine, storage
from app.services.email import OutgoingEmail
from app.services.email import messages as mail
from app.services.email.outbox import enqueue_email
from app.services.errors import Conflict, Invalid, NotFound
from app.services.events import SYSTEM, ClientInfo
from app.services.storage import BytesLRU
from app.worker.queue import JobContext, enqueue, utcnow

log = logging.getLogger(__name__)

LIVE = ("sent", "partially_signed")
CLOSED = ("completed", "declined", "expired", "voided")
REMINDER_DAYS = (2, 5)
EXPIRY = timedelta(days=30)
VIEW_EVENT_EVERY = timedelta(minutes=30)
MAX_TOKENS = 20
MAX_TEXT = 2000
MAX_MARK_PNG = 512 * 1024
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
ESIGN_DISCLOSURE_VERSION = "2026-11-15"

SIGNING_EMAIL = "signing_email"
EXPIRE_FILE = "expire_file"
FINALIZE_FILE = "finalize_file"

# Stamping is CPU-bound and the free tier has 0.1 CPU: one at a time, and check the cache again
# after waiting, so several page requests for the same new PDF stamp it once.
STAMP_LOCK = asyncio.Lock()


# ---- tokens and links --------------------------------------------------------------------------
def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    return token, hash_token(token)


def sign_url(settings: Settings, token: str) -> str:
    return f"{settings.app_base_url.rstrip('/')}/sign/{token}"


def file_url(settings: Settings, file_id: ObjectId | str) -> str:
    return f"{settings.app_base_url.rstrip('/')}/files/{file_id}"


def verify_url(settings: Settings) -> str:
    return f"{settings.app_base_url.rstrip('/')}/verify"


def push_token(i: int, token_hash: str) -> dict[str, Any]:
    return {f"recipients.{i}.token_hashes": {"$each": [token_hash], "$slice": -MAX_TOKENS}}


# ---- recipients and routing --------------------------------------------------------------------
def recipient_index(file: dict[str, Any], recipient_id: str) -> int:
    for i, r in enumerate(file["recipients"]):
        if r["id"] == recipient_id:
            return i
    raise NotFound(recipient_id)


def is_current(r: dict[str, Any], **conditions: Any) -> dict[str, Any]:
    """Filter: the file is out for signature and `r` is still the one signing.

    Conditions on a recipient go through $elemMatch, never a path like `recipients.0.x`: on such
    a path MongoDB also looks for a field named "0" inside each element, so a null test there
    always matches, and a not-null test never does. Updates still use index paths, because a
    file's recipients never move once it exists."""
    return {
        "status": {"$in": list(LIVE)},
        "recipients": {"$elemMatch": {"id": r["id"], "status": "active", **conditions}},
    }


def activate(i: int, now: datetime) -> dict[str, Any]:
    return {f"recipients.{i}.status": "active", f"recipients.{i}.activated_at": now}


async def schedule_signer(
    tenant: Tenant, file_id: ObjectId, recipient_id: str, activated_at: datetime
) -> None:
    """After a recipient becomes the current signer: their invitation now, and reminders on
    day 2 and day 5. Each job checks they still haven't signed before sending anything."""
    payload = {"org_id": str(tenant.org_id), "file_id": str(file_id), "recipient_id": recipient_id}
    base = f"{SIGNING_EMAIL}:{file_id}:{recipient_id}"
    org = str(tenant.org_id)
    await enqueue(
        tenant.db,
        SIGNING_EMAIL,
        {**payload, "reason": "invite"},
        dedupe_key=f"{base}:invite",
        org_id=org,
    )
    for n, days in enumerate(REMINDER_DAYS, 1):
        await enqueue(
            tenant.db,
            SIGNING_EMAIL,
            {**payload, "reason": "reminder", "n": n},
            run_at=activated_at + timedelta(days=days),
            dedupe_key=f"{base}:reminder{n}",
            org_id=org,
        )


# ---- values -> stamps --------------------------------------------------------------------------
def format_date(value: str) -> str:
    try:
        d = date.fromisoformat(value)
    except ValueError:
        return value
    return f"{d:%B} {d.day}, {d.year}"


def _box(r: dict[str, float]) -> pdf_engine.Box:
    return pdf_engine.Box(r["x0"], r["y0"], r["x1"], r["y1"])


def _value_stamp(f: dict[str, Any], value: Any) -> pdf_engine.Stamp | None:
    rect = _box(f["rect"])
    if f["type"] == "checkbox":
        # Draw the mark over the box; the box glyph itself stays.
        return pdf_engine.Stamp(f["page"], rect, "check") if value is True else None
    text = value.strip() if isinstance(value, str) else ""
    if not text:
        return None
    if f["type"] == "date":
        text = format_date(text)
    return pdf_engine.Stamp(
        f["page"],
        rect,
        "text",
        value=text,
        redact=[_box(r) for r in f.get("redact", [])],
        room=f["filled_by"] == SENDER,
    )


def _mark_stamp(
    f: dict[str, Any], mark: dict[str, Any] | None, images: dict[str, bytes]
) -> pdf_engine.Stamp | None:
    if not mark:
        return None
    redact = [_box(r) for r in f.get("redact", [])]
    rect = _box(f["rect"])
    if mark["kind"] == "drawn":
        image = images.get(str(mark["blob_id"]))
        if image is None:
            return None
        return pdf_engine.Stamp(f["page"], rect, "signature", image=image, redact=redact)
    return pdf_engine.Stamp(f["page"], rect, "signature", value=mark["text"], redact=redact)


def build_stamps(
    fields: list[dict[str, Any]],
    *,
    sender_values: dict[str, Any] | None = None,
    signed: list[dict[str, Any]] | None = None,
    images: dict[str, bytes] | None = None,
) -> list[pdf_engine.Stamp]:
    """What to write: the sender's values (if given) and each signed recipient's values."""
    by_role = {r["role"]: r for r in signed or []}
    images = images or {}
    out: list[pdf_engine.Stamp] = []
    for f in fields:
        stamp: pdf_engine.Stamp | None = None
        if f["filled_by"] == SENDER:
            if sender_values is not None:
                stamp = _value_stamp(f, sender_values.get(f["key"]))
        elif (r := by_role.get(f["filled_by"])) is not None:
            if f["type"] == "signature":
                stamp = _mark_stamp(f, r.get("signature"), images)
            elif f["type"] == "initials":
                stamp = _mark_stamp(f, r.get("initials"), images)
            elif f["type"] == "date_signed":
                stamp = _value_stamp({**f, "type": "text"}, r.get("date_signed"))
            else:
                stamp = _value_stamp(f, r.get("values", {}).get(f["key"]))
        if stamp is not None:
            out.append(stamp)
    return out


async def mark_images(
    bucket: AsyncGridFSBucket, recipients: list[dict[str, Any]]
) -> dict[str, bytes]:
    images: dict[str, bytes] = {}
    for r in recipients:
        for mark in (r.get("signature"), r.get("initials")):
            if mark and mark.get("kind") == "drawn":
                images[str(mark["blob_id"])] = await storage.get(bucket, mark["blob_id"])
    return images


async def stamp_signed(bucket: AsyncGridFSBucket, file: dict[str, Any]) -> bytes:
    """The prepared PDF (the sender's values, as previewed and sent) plus the values of everyone
    who has signed so far. Uncached: callers hold STAMP_LOCK."""
    pdf = await storage.get(bucket, file["prepared"]["blob_id"])
    signed = [r for r in file["recipients"] if r["status"] == "signed"]
    if signed:
        images = await mark_images(bucket, signed)
        stamps = build_stamps(file["template"]["fields"], signed=signed, images=images)
        pdf = await asyncio.to_thread(pdf_engine.stamp, pdf, stamps)
    return pdf


async def current_pdf(bucket: AsyncGridFSBucket, cache: BytesLRU, file: dict[str, Any]) -> bytes:
    """The document as it stands, for page images and "Open as PDF" (cached)."""
    signed = sum(1 for r in file["recipients"] if r["status"] == "signed")
    key = ("file-pdf", file["_id"], file["prepared"]["blob_id"], signed)
    if (hit := cache.get(key)) is not None:
        return hit
    async with STAMP_LOCK:
        if (hit := cache.get(key)) is not None:
            return hit
        pdf = await stamp_signed(bucket, file)
        cache.put(key, pdf)
    return pdf


def page_rev(file: dict[str, Any]) -> str:
    prepared = (file.get("prepared") or {}).get("blob_id", "none")
    signed = sum(1 for r in file["recipients"] if r["status"] == "signed")
    return f"{prepared}-{signed}"


# ---- the signer's side (public, token-authenticated) -------------------------------------------
@dataclass
class Signer:
    tenant: Tenant
    file: dict[str, Any]
    index: int

    @property
    def recipient(self) -> dict[str, Any]:
        return self.file["recipients"][self.index]


async def open_signer(db: AsyncDatabase, token: str) -> Signer | None:
    if not token or len(token) > 100:
        return None
    token_hash = hash_token(token)
    file = await find_file_by_signing_token(db, token_hash)
    if file is None:
        return None
    for i, r in enumerate(file["recipients"]):
        if token_hash in r.get("token_hashes", []):
            return Signer(Tenant(db, file["org_id"]), file, i)
    return None


SignerState = Literal[
    "ready",
    "signed",
    "completed",
    "declined",
    "declined_by_other",
    "voided",
    "expired",
    "unavailable",
]
VIEWABLE = ("ready", "signed", "completed")


def signer_state(file: dict[str, Any], r: dict[str, Any]) -> SignerState:
    status = file["status"]
    if status == "voided":
        return "voided"
    if r["status"] == "signed":
        return "completed" if status == "completed" else "signed"
    if r["status"] == "declined":
        return "declined"
    if status == "declined":
        return "declined_by_other"
    if status == "expired":
        return "expired"
    if r["status"] == "active" and status in LIVE:
        return "ready"
    return "unavailable"


def _public_field(f: dict[str, Any]) -> dict[str, Any]:
    keep = ("id", "key", "label", "type", "page", "rect", "required")
    return {k: f[k] for k in keep}


async def view(s: Signer, client: ClientInfo) -> dict[str, Any]:
    file, r = s.file, s.recipient
    state = signer_state(file, r)
    if state == "ready":
        await _mark_viewed(s, client)
    org = await branding.load(s.tenant.db, s.tenant.org_id)
    tpl = file["template"]
    viewable = state in VIEWABLE
    return {
        "state": state,
        "org_name": org["name"] if org else "",
        "branding": branding.public(org),
        "agreement_name": tpl["name"],
        "file_title": file["title"],
        "signer": {"name": r["name"], "email": r["email"], "role_label": r["role_label"]},
        "sender": {"name": file["created_by_name"], "email": file["created_by_email"]},
        "consented": bool(r.get("consented_at")),
        "disclosure_version": ESIGN_DISCLOSURE_VERSION,
        "pages": tpl["pages"] if viewable else [],
        "page_rev": page_rev(file),
        "fields": [_public_field(f) for f in tpl["fields"] if f["filled_by"] == r["role"]]
        if state == "ready"
        else [],
        "parties": [
            {
                "role_label": x["role_label"],
                "name": x["name"],
                "status": x["status"],
                "you": i == s.index,
            }
            for i, x in enumerate(file["recipients"])
        ],
        "expires_at": file.get("expires_at"),
        "signed_at": r.get("signed_at"),
        # The signed PDF with its certificate, downloadable by this link once it exists.
        "signed_pdf_ready": state == "completed" and bool(file.get("finalized_at")),
        "decline_reason": r.get("decline_reason"),
        "void_reason": file.get("void_reason") if state == "voided" else None,
    }


async def _mark_viewed(s: Signer, client: ClientInfo) -> None:
    """Record a view the first time, then at most every 30 minutes (not on every page load)."""
    i, r, now = s.index, s.recipient, utcnow()
    update: dict[str, Any] = {f"recipients.{i}.last_viewed_at": now}
    if not r.get("viewed_at"):
        update[f"recipients.{i}.viewed_at"] = now
    stale = [{"last_viewed_at": None}, {"last_viewed_at": {"$lt": now - VIEW_EVENT_EVERY}}]
    updated = await s.tenant.transition(
        "files", s.file["_id"], is_current(r, **{"$or": stale}), {"$set": update}
    )
    if updated is not None:
        await events.record(
            s.tenant,
            s.file["_id"],
            "viewed",
            actor=events.by_recipient(r),
            recipient=r,
            client=client,
        )


_CLOSED_MESSAGES: dict[str, str] = {
    "signed": "You've already signed this agreement.",
    "completed": "This agreement has been signed by everyone.",
    "declined": "You declined this agreement.",
    "declined_by_other": "This agreement was declined, so it can no longer be signed.",
    "voided": "The sender withdrew this agreement.",
    "expired": "This signing link has expired. Ask the sender for a new one.",
    "unavailable": "This agreement isn't ready for your signature.",
}


def _require_ready(s: Signer) -> None:
    state = signer_state(s.file, s.recipient)
    if state != "ready":
        raise Conflict(_CLOSED_MESSAGES[state])


async def consent(s: Signer, client: ClientInfo) -> None:
    _require_ready(s)
    i, r = s.index, s.recipient
    if r.get("consented_at"):
        return
    updated = await s.tenant.transition(
        "files",
        s.file["_id"],
        is_current(r, consented_at=None),
        {"$set": {f"recipients.{i}.consented_at": utcnow()}},
    )
    if updated is not None:
        await events.record(
            s.tenant,
            s.file["_id"],
            "consented",
            actor=events.by_recipient(r),
            recipient=r,
            client=client,
            data={"disclosure_version": ESIGN_DISCLOSURE_VERSION},
        )


@dataclass
class Mark:
    """A signature or initials: typed text, or a drawn PNG."""

    kind: Literal["typed", "drawn"]
    text: str = ""
    png: bytes = b""


@dataclass
class Submission:
    values: dict[str, Any]
    signature: Mark | None
    initials: Mark | None
    tz: str | None = None


def _check_mark(mark: Mark | None, what: str, problems: list[str]) -> Mark | None:
    if mark is None:
        problems.append(f"Add your {what}.")
        return None
    if mark.kind == "typed":
        text = " ".join(mark.text.split())[:120]
        if not text:
            problems.append(f"Type your {what}.")
            return None
        return Mark("typed", text=text)
    if not mark.png.startswith(PNG_MAGIC) or len(mark.png) > MAX_MARK_PNG:
        problems.append(f"Your drawn {what} couldn't be read. Draw it again or type it.")
        return None
    return mark


def signing_date(now: datetime, tz: str | None) -> str:
    """The date shown in 'date signed' fields: the signer's own calendar date."""
    zone: ZoneInfo | None = None
    if tz and len(tz) <= 64:
        try:
            zone = ZoneInfo(tz)
        except (ValueError, KeyError, OSError):
            zone = None
    local = now.astimezone(zone) if zone else now
    return f"{local:%B} {local.day}, {local.year}"


async def submit(
    s: Signer, bucket: AsyncGridFSBucket, sub: Submission, client: ClientInfo
) -> dict[str, Any]:
    _require_ready(s)
    file, i, r = s.file, s.index, s.recipient
    if not r.get("consented_at"):
        raise Conflict("Agree to sign electronically before signing.")
    mine = [f for f in file["template"]["fields"] if f["filled_by"] == r["role"]]
    problems: list[str] = []
    values: dict[str, Any] = {}
    seen: set[str] = set()
    for f in mine:
        if f["type"] in SIGNER_ONLY_TYPES or f["key"] in seen:
            continue
        seen.add(f["key"])
        raw = sub.values.get(f["key"])
        if f["type"] == "checkbox":
            # Never required: detection marks option boxes required too, and a signer must be
            # free to leave the options they don't choose unticked.
            values[f["key"]] = raw is True
            continue
        text = raw.strip() if isinstance(raw, str) else ""
        if len(text) > MAX_TEXT:
            problems.append(f"“{f['label']}” is too long.")
        elif f["type"] == "date" and text:
            try:
                date.fromisoformat(text)
            except ValueError:
                problems.append(f"“{f['label']}” isn't a valid date.")
        elif not text and f["required"]:
            problems.append(f"Fill in “{f['label']}”.")
        if text:
            values[f["key"]] = text
    signature = initials = None
    if any(f["type"] == "signature" for f in mine):
        signature = _check_mark(sub.signature, "signature", problems)
    if any(f["type"] == "initials" and f["required"] for f in mine) or (
        sub.initials and any(f["type"] == "initials" for f in mine)
    ):
        initials = _check_mark(sub.initials, "initials", problems)
    if problems:
        raise Invalid(problems)

    stored: list[ObjectId] = []

    async def keep(mark: Mark | None, kind: str) -> dict[str, Any] | None:
        if mark is None:
            return None
        if mark.kind == "typed":
            return {"kind": "typed", "text": mark.text}
        blob = await storage.put(
            bucket,
            mark.png,
            filename=f"{kind}.png",
            org_id=s.tenant.org_id,
            kind=f"signer_{kind}",
            content_type="image/png",
        )
        stored.append(blob)
        return {"kind": "drawn", "blob_id": blob}

    now = utcnow()
    nxt = next(
        (
            j
            for j in range(i + 1, len(file["recipients"]))
            if file["recipients"][j]["status"] == "pending"
        ),
        None,
    )
    update: dict[str, Any] = {
        f"recipients.{i}.status": "signed",
        f"recipients.{i}.signed_at": now,
        f"recipients.{i}.values": values,
        f"recipients.{i}.signature": await keep(signature, "signature"),
        f"recipients.{i}.initials": await keep(initials, "initials"),
        f"recipients.{i}.date_signed": signing_date(now, sub.tz),
        f"recipients.{i}.signed_client": {"ip": client.ip, "user_agent": client.user_agent},
        "updated_at": now,
    }
    if nxt is not None:
        update |= {"status": "partially_signed", **activate(nxt, now)}
    else:
        update |= {"status": "completed", "completed_at": now}
    expect = is_current(r, consented_at={"$type": "date"})
    updated = await s.tenant.transition(
        "files", file["_id"], expect, {"$set": update, "$inc": {"revision": 1}}
    )
    if updated is None:
        for blob in stored:
            await storage.delete(bucket, blob)
        raise Conflict("This agreement changed while you were signing. Reload the page.")

    await events.record(
        s.tenant,
        file["_id"],
        "signed",
        actor=events.by_recipient(r),
        recipient=r,
        client=client,
        data={"signature": signature.kind if signature else None},
    )
    if nxt is not None:
        await schedule_signer(s.tenant, file["_id"], file["recipients"][nxt]["id"], now)
    else:
        await events.record(s.tenant, file["_id"], "completed", actor=SYSTEM)
        # The signed PDF, its certificate and the copy to every party: services/finalize.py.
        await enqueue(
            s.tenant.db,
            FINALIZE_FILE,
            {"org_id": str(s.tenant.org_id), "file_id": str(file["_id"])},
            dedupe_key=f"{FINALIZE_FILE}:{file['_id']}",
            org_id=str(s.tenant.org_id),
        )
    return updated


async def decline(s: Signer, settings: Settings, reason: str, client: ClientInfo) -> dict[str, Any]:
    _require_ready(s)
    file, i, r = s.file, s.index, s.recipient
    reason = reason.strip()[:1000]
    if not reason:
        raise Invalid(["Tell the sender why you're declining."])
    now = utcnow()
    updated = await s.tenant.transition(
        "files",
        file["_id"],
        is_current(r),
        {
            "$set": {
                f"recipients.{i}.status": "declined",
                f"recipients.{i}.declined_at": now,
                f"recipients.{i}.decline_reason": reason,
                "status": "declined",
                "declined_at": now,
                "updated_at": now,
            },
            "$inc": {"revision": 1},
        },
    )
    if updated is None:
        raise Conflict("This agreement changed. Reload the page.")
    await events.record(
        s.tenant,
        file["_id"],
        "declined",
        actor=events.by_recipient(r),
        recipient=r,
        client=client,
        data={"reason": reason},
    )
    org = await s.tenant.db.orgs.find_one({"_id": s.tenant.org_id}, {"name": 1})
    org_name = org["name"] if org else ""
    msg = mail.declined_notice(
        org_name=org_name,
        signer_name=r["name"],
        role_label=r["role_label"],
        agreement_name=file["template"]["name"],
        file_title=file["title"],
        reason=reason,
        file_url=file_url(settings, file["_id"]),
        product=settings.email_product_name,
    )
    await enqueue_email(
        s.tenant.db,
        {
            "to": [file["created_by_email"]],
            "subject": msg.subject,
            "text": msg.text,
            "html": msg.html,
            "from_name": settings.email_product_name,
            "ref": str(file["_id"]),
        },
        dedupe_key=f"declined:{file['_id']}",
        org_id=str(s.tenant.org_id),
    )
    return updated


# ---- jobs --------------------------------------------------------------------------------------
async def handle_signing_email(ctx: JobContext, payload: dict[str, Any]) -> None:
    """Invitation, reminder or resend for the current signer. Mints a fresh token here, so the
    link exists only in the email itself. Does nothing if they've signed or the file closed."""
    if ctx.job.get("result"):
        return  # sent on an earlier run that crashed before it was marked done
    tenant = Tenant(ctx.db, ObjectId(payload["org_id"]))
    file = await tenant.get("files", payload["file_id"])
    if file is None or file["status"] not in LIVE:
        return
    try:
        i = recipient_index(file, payload["recipient_id"])
    except NotFound:
        return
    if file["recipients"][i]["status"] != "active":
        return
    token, token_hash = new_token()
    updated = await tenant.transition(
        "files",
        file["_id"],
        is_current(file["recipients"][i]),
        {"$push": push_token(i, token_hash)},
    )
    if updated is None:
        return
    r = updated["recipients"][i]
    settings: Settings = ctx.services["settings"]
    org = await branding.load(ctx.db, tenant.org_id)
    org_name = org["name"] if org else settings.email_product_name
    reason = payload["reason"]
    msg = mail.signing_request(
        org_name=org_name,
        signer_name=r["name"],
        agreement_name=file["template"]["name"],
        url=sign_url(settings, token),
        expires_at=file["expires_at"],
        sender_name=file["created_by_name"],
        reason=reason,
        signed_before=[
            f"{x['name']} ({x['role_label']})"
            for x in updated["recipients"]
            if x["status"] == "signed"
        ],
        product=settings.email_product_name,
        brand=branding.public(org, settings.app_base_url),
    )
    result = await ctx.services["email"].send(
        OutgoingEmail(
            to=[r["email"]],
            subject=msg.subject,
            text=msg.text,
            html=msg.html,
            from_name=org_name,
            reply_to=file["created_by_email"],
            headers={"X-Kickoff-Ref": f"{file['_id']}:{r['id']}"},
        )
    )
    now = utcnow()
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
    await tenant.transition(
        "files",
        file["_id"],
        {f"recipients.{i}.id": r["id"]},
        {
            "$set": {
                f"recipients.{i}.last_email": {
                    "status": "sent",
                    "at": now,
                    "to": r["email"],
                    "reason": reason,
                }
            },
            "$inc": {f"recipients.{i}.emails_sent": 1},
        },
    )
    await events.record(
        tenant,
        file["_id"],
        "reminded" if reason == "reminder" else "email_sent",
        actor=SYSTEM,
        recipient=r,
        data={"reason": reason, "to": r["email"]},
        key=f"job:{ctx.job['_id']}",
    )


async def on_signing_email_dead(ctx: JobContext, payload: dict[str, Any], error: str) -> None:
    """Show the failure on the file, where the sender can copy the link or fix the address."""
    tenant = Tenant(ctx.db, ObjectId(payload["org_id"]))
    file = await tenant.get("files", payload["file_id"])
    if file is None:
        return
    try:
        i = recipient_index(file, payload["recipient_id"])
    except NotFound:
        return
    r = file["recipients"][i]
    short = error.split("\n")[0][:300]
    await tenant.transition(
        "files",
        file["_id"],
        {f"recipients.{i}.id": r["id"]},
        {
            "$set": {
                f"recipients.{i}.last_email": {
                    "status": "failed",
                    "at": utcnow(),
                    "to": r["email"],
                    "reason": payload["reason"],
                    "error": short,
                }
            }
        },
    )
    await events.record(
        tenant,
        file["_id"],
        "email_failed",
        actor=SYSTEM,
        recipient=r,
        data={"reason": payload["reason"], "to": r["email"], "error": short},
        key=f"dead:{ctx.job['_id']}",
    )


async def handle_expire_file(ctx: JobContext, payload: dict[str, Any]) -> None:
    tenant = Tenant(ctx.db, ObjectId(payload["org_id"]))
    now = utcnow()
    file = await tenant.transition(
        "files",
        payload["file_id"],
        {"status": {"$in": list(LIVE)}, "expires_at": {"$lte": now}},
        {
            "$set": {"status": "expired", "expired_at": now, "updated_at": now},
            "$inc": {"revision": 1},
        },
    )
    # A rerun after a crash finds it already expired: finish the (idempotent) follow-ups.
    file = file or await tenant.get("files", payload["file_id"])
    if file is None or file["status"] != "expired":
        return
    await events.record(tenant, file["_id"], "expired", actor=SYSTEM, key=f"expired:{file['_id']}")
    settings: Settings = ctx.services["settings"]
    org = await ctx.db.orgs.find_one({"_id": tenant.org_id}, {"name": 1})
    waiting = next((r for r in file["recipients"] if r["status"] == "active"), None)
    msg = mail.expired_notice(
        org_name=org["name"] if org else "",
        agreement_name=file["template"]["name"],
        file_title=file["title"],
        waiting_for=waiting["name"] if waiting else "everyone",
        file_url=file_url(settings, file["_id"]),
        product=settings.email_product_name,
    )
    await enqueue_email(
        ctx.db,
        {
            "to": [file["created_by_email"]],
            "subject": msg.subject,
            "text": msg.text,
            "html": msg.html,
            "from_name": settings.email_product_name,
            "ref": str(file["_id"]),
        },
        dedupe_key=f"expired:{file['_id']}",
        org_id=str(tenant.org_id),
    )
