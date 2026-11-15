"""Templates: an uploaded agreement, the fields the AI proposed, and a human's approval of them.

Lifecycle (every arrow is one atomic, conditional update):

    upload -> analyzing -> needs_review -> active
                  |              ^  |        |
                  v              |  +--------+  editing an active template: new version,
            analysis_failed -----+              back to needs_review until approved again
              (retry, or place fields by hand)

`revision` goes up on every write. Edits and approval name the revision they were made
against, so two tabs can't silently overwrite each other. `version` is what files will pin to;
it goes up when an approved template is edited (DECISIONS.md "What I'm including", item 14).
"""

import asyncio
import logging
import re
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any

from bson import ObjectId
from gridfs import AsyncGridFSBucket
from pymongo.asynchronous.database import AsyncDatabase

from app.config import Settings
from app.domain.fields import SENDER, SIGNER_ONLY_TYPES, Field, Rect, Role, sender_inputs
from app.repo import Tenant
from app.services import ingest, pdf_engine, storage
from app.services.accounts import CurrentUser
from app.services.detection import heuristics, pipeline
from app.services.detection.schema import slug
from app.services.errors import Conflict, Invalid, NotFound
from app.worker.queue import enqueue, utcnow

log = logging.getLogger(__name__)

__all__ = ["Conflict", "Invalid", "NotFound"]

ANALYZE_TEMPLATE = "analyze_template"
EDITABLE = ("needs_review", "active")
MAX_FIELDS = 2000
MAX_ROLES = 10
MIN_FIELD_SIZE = 4.0  # pt
_ROLE_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_FIELD_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,80}$")
_RESERVED_ROLE_KEYS = frozenset({SENDER, "none"})


# ---- upload ------------------------------------------------------------------------------------
def display_name(filename: str | None) -> str:
    stem = PurePath((filename or "").replace("\\", "/")).stem
    stem = re.sub(r"[\x00-\x1f\x7f]", "", stem)
    stem = re.sub(r"[_\-]+", " ", stem).strip()
    return stem[:120] or "Client agreement"


async def create_from_upload(
    tenant: Tenant,
    bucket: AsyncGridFSBucket,
    settings: Settings,
    user: CurrentUser,
    data: bytes,
    filename: str | None,
    *,
    name: str | None = None,
) -> dict[str, Any]:
    """Validate and normalize the upload (raises ingest.IngestError with a user-facing message),
    store it, and queue the analysis. Detection itself runs in the background job."""
    result = await ingest.ingest(data, settings)
    name = (name or "").strip()[:120] or display_name(filename)
    org = tenant.org_id
    source_id = await storage.put(
        bucket,
        data,
        filename=f"{name}.{result.source_format}",
        org_id=org,
        kind="template_source",
        content_type="application/pdf"
        if result.source_format == "pdf"
        else "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    pdf_id = await storage.put(
        bucket, result.pdf, filename=f"{name}.pdf", org_id=org, kind="template_normalized"
    )
    sizes = await asyncio.to_thread(pdf_engine.page_sizes, result.pdf)
    now = utcnow()
    doc: dict[str, Any] = {
        "name": name,
        "status": "analyzing",
        "version": 1,
        "revision": 1,
        "source": {
            "filename": (filename or "")[:200],
            "format": result.source_format,
            "sha256": result.source_sha256,
            "size": len(data),
            "blob_id": source_id,
            "convert_ms": result.convert_ms,
        },
        "normalized_blob_id": pdf_id,
        # Until analysis finishes, the reviewer sees the normalized pages.
        "pdf_blob_id": pdf_id,
        "page_count": result.page_count,
        "pages": [{"width": w, "height": h} for w, h in sizes],
        "roles": [],
        "fields": [],
        "warnings": [],
        "detection": None,
        "analysis_attempt": 1,
        "analysis_started_at": now,
        "analysis_error": None,
        "created_by": user.id,
        "created_at": now,
        "updated_at": now,
        "approved_at": None,
        "approved_by": None,
    }
    template_id = await tenant.insert("templates", doc)
    await _enqueue_analysis(tenant, template_id, 1)
    doc["_id"] = template_id
    doc["org_id"] = org
    return doc


async def _enqueue_analysis(tenant: Tenant, template_id: ObjectId, attempt: int) -> None:
    await enqueue(
        tenant.db,
        ANALYZE_TEMPLATE,
        {"template_id": str(template_id), "org_id": str(tenant.org_id), "attempt": attempt},
        dedupe_key=f"{ANALYZE_TEMPLATE}:{template_id}:{attempt}",
        org_id=str(tenant.org_id),
    )


# ---- analysis job ------------------------------------------------------------------------------
_ID = r"p\d+-[sl]\d+"
# A run of ids, e.g. "p2-s3, p2-s5 and p3-s1" or "p2-s15/16", with an optional noun before it.
_ID_RUN = re.compile(
    rf"(?:\b(?P<noun>slots?|blanks?|fields?|checkbox(?:es)?|lines?)\s+)?\b{_ID}\b"
    rf"(?:\s*(?:,\s*(?:and\s+|or\s+)?|&|\band\b|\bor\b)\s*{_ID}\b|\s*/\s*(?:{_ID}|\d+)\b)*",
    re.IGNORECASE,
)
_ID_PARTS = re.compile(r"\bp(\d+)-([sl])\d+", re.IGNORECASE)


def _describe_ids(m: re.Match[str]) -> str:
    run = m.group(0)
    refs = _ID_PARTS.findall(run)
    count = len(refs) + len(re.findall(r"/\s*\d+\b", run))  # "p2-s15/16" is two blanks
    pages = sorted({int(page) for page, _ in refs})
    where = (
        f"page {pages[0]}"
        if len(pages) == 1
        else "pages " + ", ".join(map(str, pages[:-1])) + f" and {pages[-1]}"
    )
    noun = (m.group("noun") or "").lower()
    if noun and not noun.startswith("slot"):
        return f"{m.group('noun')} on {where}"  # keep the model's word: "Checkboxes on page 2"
    kind = "line" if all(k.lower() == "l" for _, k in refs) else "blank"
    return f"a {kind} on {where}" if count == 1 else f"{kind}s on {where}"


def readable_note(text: str) -> str:
    """The model sometimes cites our internal ids ("p1-s4") in its notes to the reviewer.
    Say where on the page instead."""
    text = _ID_RUN.sub(_describe_ids, text)
    return text[:1].upper() + text[1:]


async def run_analysis(
    db: AsyncDatabase,
    bucket: AsyncGridFSBucket,
    labeller: pipeline.Labeller | None,
    payload: dict[str, Any],
) -> None:
    """The analyze_template job. Idempotent: a stale or repeated job finds the template no
    longer `analyzing` at its attempt number and does nothing."""
    tenant = Tenant(db, ObjectId(payload["org_id"]))
    attempt = int(payload["attempt"])
    expect = {"status": "analyzing", "analysis_attempt": attempt}
    template = await tenant.get("templates", payload["template_id"])
    if template is None or any(template.get(k) != v for k, v in expect.items()):
        log.info("analysis no longer wanted; skipping", extra={"template": payload["template_id"]})
        return
    org = await db.orgs.find_one({"_id": tenant.org_id}, {"name": 1})
    pdf = await storage.get(bucket, template["normalized_blob_id"])

    started = utcnow()
    analysis = await pipeline.analyze(
        pdf, labeller=labeller, firm_name=org["name"] if org else None
    )
    pdf_id = template["normalized_blob_id"]
    new_blob: ObjectId | None = None
    if analysis.pdf is not pdf:  # a signature page was appended
        new_blob = pdf_id = await storage.put(
            bucket,
            analysis.pdf,
            filename=f"{template['name']}.pdf",
            org_id=tenant.org_id,
            kind="template_pdf",
        )
    sizes = await asyncio.to_thread(pdf_engine.page_sizes, analysis.pdf)
    finished = utcnow()
    detection = {
        "detector": analysis.detector,
        "fallback_reason": analysis.fallback_reason,
        "appended_signature_page": analysis.appended_signature_page,
        "original_page_count": analysis.original_page_count,
        "heuristic_slots": len(analysis.heuristic_slots),
        "slots": len(analysis.extraction.slots),
        "llm": analysis.llm,
        "duration_ms": int((finished - started).total_seconds() * 1000),
        "finished_at": finished,
    }
    updated = await tenant.transition(
        "templates",
        template["_id"],
        expect,
        {
            "$set": {
                "status": "needs_review",
                "pdf_blob_id": pdf_id,
                "page_count": analysis.page_count,
                "pages": [{"width": w, "height": h} for w, h in sizes],
                "roles": [r.model_dump() for r in analysis.roles],
                "fields": [f.model_dump() for f in analysis.fields],
                # The editor explains a fallback itself, with the reason.
                "warnings": [
                    readable_note(w) for w in analysis.warnings if w != heuristics.FALLBACK_WARNING
                ],
                "detection": detection,
                "analysis_error": None,
                "updated_at": finished,
            },
            "$inc": {"revision": 1},
        },
    )
    if updated is None and new_blob is not None:
        await storage.delete(bucket, new_blob)  # another run finished first
    log.info(
        "template analyzed",
        extra={
            "template": str(template["_id"]),
            "detector": analysis.detector,
            "fields": len(analysis.fields),
            "ms": detection["duration_ms"],
        },
    )


async def mark_analysis_failed(db: AsyncDatabase, payload: dict[str, Any], error: str) -> None:
    """on_dead hook for the analyze job: tell the reviewer, and offer the ways out."""
    tenant = Tenant(db, ObjectId(payload["org_id"]))
    await tenant.transition(
        "templates",
        payload["template_id"],
        {"status": "analyzing", "analysis_attempt": int(payload["attempt"])},
        {
            "$set": {
                "status": "analysis_failed",
                "analysis_error": "We couldn't finish reading this document.",
                "detection": {"error_detail": error[:500], "finished_at": utcnow()},
                "updated_at": utcnow(),
            },
            "$inc": {"revision": 1},
        },
    )


async def retry_analysis(tenant: Tenant, template_id: str) -> dict[str, Any]:
    template = await _get(tenant, template_id)
    attempt = int(template.get("analysis_attempt", 1)) + 1
    updated = await tenant.transition(
        "templates",
        template["_id"],
        {"status": "analysis_failed", "revision": template["revision"]},
        {
            "$set": {
                "status": "analyzing",
                "analysis_attempt": attempt,
                "analysis_started_at": utcnow(),
                "analysis_error": None,
                "updated_at": utcnow(),
            },
            "$inc": {"revision": 1},
        },
    )
    if updated is None:
        raise Conflict("Only a document whose analysis failed can be analyzed again.")
    await _enqueue_analysis(tenant, template["_id"], attempt)
    return updated


async def start_manual(tenant: Tenant, template_id: str) -> dict[str, Any]:
    """Skip detection: open the editor with one signer and no fields, to place them by hand."""
    template = await _get(tenant, template_id)
    updated = await tenant.transition(
        "templates",
        template["_id"],
        {"status": "analysis_failed", "revision": template["revision"]},
        {
            "$set": {
                "status": "needs_review",
                "roles": [Role(key="client", label="Client", kind="client", order=1).model_dump()],
                "fields": [],
                "warnings": [
                    "Fields weren't detected automatically. Add each blank and signature by hand."
                ],
                "updated_at": utcnow(),
            },
            "$inc": {"revision": 1},
        },
    )
    if updated is None:
        raise Conflict("This document isn't waiting for manual setup.")
    return updated


# ---- review ------------------------------------------------------------------------------------
@dataclass
class Edit:
    revision: int
    roles: list[Role]
    fields: list[Field]
    name: str | None = None


def check_layout(
    roles: list[Role], fields: list[Field], pages: list[dict[str, float]]
) -> tuple[list[Role], list[Field], list[str]]:
    """Validate an edited layout. Returns cleaned roles (orders renumbered 1..n) and fields
    (rectangles clamped to the page when they overshoot by a hair), plus any problems."""
    problems: list[str] = []
    if len(roles) > MAX_ROLES:
        problems.append(f"A template can have at most {MAX_ROLES} signers.")
    if len(fields) > MAX_FIELDS:
        problems.append(f"A template can have at most {MAX_FIELDS} fields.")
    keys: set[str] = set()
    clean_roles: list[Role] = []
    for i, r in sorted(enumerate(roles), key=lambda p: (p[1].order, p[0])):
        label = r.label.strip()[:80]
        if not _ROLE_KEY_RE.match(r.key) or r.key in _RESERVED_ROLE_KEYS:
            problems.append(f"Signer {i + 1} has an invalid key.")
        elif r.key in keys:
            problems.append(f"Two signers share the key {r.key!r}.")
        if not label:
            problems.append(f"Signer {i + 1} needs a name.")
        keys.add(r.key)
        clean_roles.append(r.model_copy(update={"label": label, "order": len(clean_roles) + 1}))

    labels = {r.key: r.label for r in clean_roles}
    ids: set[str] = set()
    clean_fields: list[Field] = []
    for f in fields:
        name = f.label.strip()[:200] or f.type.replace("_", " ").capitalize()
        where = f"“{name}” on page {f.page}"
        if not _FIELD_ID_RE.match(f.id) or f.id in ids:
            problems.append(f"Field {where} has a missing or duplicate id.")
        ids.add(f.id)
        if f.filled_by != SENDER and f.filled_by not in labels:
            problems.append(f"Field {where} is assigned to a signer that doesn't exist.")
        if f.filled_by == SENDER and f.type in SIGNER_ONLY_TYPES:
            kind = f.type.replace("_", " ")
            problems.append(f"Field {where} is a {kind}, so a signer must fill it.")
        if not 1 <= f.page <= len(pages):
            problems.append(f"Field {where} is on a page that doesn't exist.")
            continue
        rect = _clamp(f.rect, pages[f.page - 1])
        if rect is None:
            problems.append(f"Field {where} is outside the page or too small.")
            continue
        key = slug(f.key, fallback="") or f.id
        clean_fields.append(f.model_copy(update={"label": name, "rect": rect, "key": key}))
    return clean_roles, clean_fields, problems


def _clamp(r: Rect, page: dict[str, float], tol: float = 2.0) -> Rect | None:
    w, h = page["width"], page["height"]
    if r.x0 < -tol or r.y0 < -tol or r.x1 > w + tol or r.y1 > h + tol:
        return None
    out = Rect(x0=max(0.0, r.x0), y0=max(0.0, r.y0), x1=min(w, r.x1), y1=min(h, r.y1))
    if out.width < MIN_FIELD_SIZE or out.height < MIN_FIELD_SIZE:
        return None
    return out


def approval_problems(roles: list[Role], fields: list[Field]) -> list[str]:
    """What stops a reviewed template from being approved."""
    if not roles:
        return ["Add at least one signer."]
    problems = []
    signs = {f.filled_by for f in fields if f.type == "signature"}
    for r in sorted(roles, key=lambda r: r.order):
        if r.key not in signs:
            problems.append(f"{r.label} has nowhere to sign. Add a signature field for them.")
    return problems


async def save(tenant: Tenant, template_id: str, edit: Edit) -> dict[str, Any]:
    template = await _get(tenant, template_id)
    if template["status"] not in EDITABLE:
        raise Conflict("This document can't be edited until its analysis has finished.")
    if template["revision"] != edit.revision:
        raise Conflict(
            "This template changed since you opened it (another tab?). Reload to get the latest."
        )
    roles, fields, problems = check_layout(edit.roles, edit.fields, template["pages"])
    if problems:
        raise Invalid(problems)
    # Placeholder text to remove is ours (measured at detection), never taken from the client.
    redact = {f["id"]: f.get("redact", []) for f in template["fields"]}
    stored = [{**f.model_dump(), "redact": redact.get(f.id, [])} for f in fields]
    update: dict[str, Any] = {
        "roles": [r.model_dump() for r in roles],
        "fields": stored,
        "updated_at": utcnow(),
    }
    if edit.name is not None and edit.name.strip():
        update["name"] = edit.name.strip()[:120]
    inc: dict[str, int] = {"revision": 1}
    if template["status"] == "active":
        # Files already sent keep their own snapshot; the edit is a new version to approve.
        update |= {"status": "needs_review", "approved_at": None, "approved_by": None}
        inc["version"] = 1
    updated = await tenant.transition(
        "templates",
        template["_id"],
        {"status": template["status"], "revision": edit.revision},
        {"$set": update, "$inc": inc},
    )
    if updated is None:
        raise Conflict("This template changed while saving. Reload to get the latest.")
    return updated


async def approve(
    tenant: Tenant, template_id: str, revision: int, user: CurrentUser
) -> dict[str, Any]:
    template = await _get(tenant, template_id)
    if template["status"] != "needs_review":
        raise Conflict("Only a template waiting for review can be approved.")
    if template["revision"] != revision:
        raise Conflict("This template changed since you reviewed it. Reload and check it again.")
    problems = approval_problems(
        [Role(**r) for r in template["roles"]], [Field(**f) for f in template["fields"]]
    )
    if problems:
        raise Invalid(problems)
    updated = await tenant.transition(
        "templates",
        template["_id"],
        {"status": "needs_review", "revision": revision},
        {
            "$set": {"status": "active", "approved_at": utcnow(), "approved_by": user.id},
            "$inc": {"revision": 1},
        },
    )
    if updated is None:
        raise Conflict("This template changed while approving. Reload and check it again.")
    return updated


async def _get(tenant: Tenant, template_id: str) -> dict[str, Any]:
    template = await tenant.get("templates", template_id)
    if template is None:
        raise NotFound(template_id)
    return template


# ---- output ------------------------------------------------------------------------------------
def summary(t: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(t["_id"]),
        "name": t["name"],
        "status": t["status"],
        "version": t["version"],
        "page_count": t["page_count"],
        "field_count": len(t.get("fields", [])),
        "created_at": t["created_at"],
        "updated_at": t["updated_at"],
        "approved_at": t.get("approved_at"),
    }


def detail(t: dict[str, Any]) -> dict[str, Any]:
    detection = t.get("detection") or {}
    roles = [Role(**r) for r in t.get("roles", [])]
    fields = [Field(**f) for f in t.get("fields", [])]
    return {
        **summary(t),
        "revision": t["revision"],
        "source_format": t["source"]["format"],
        "filename": t["source"]["filename"],
        "pdf_rev": str(t["pdf_blob_id"]),
        "pages": t["pages"],
        "roles": [r.model_dump() for r in roles],
        "fields": [f.model_dump(exclude={"redact"}) for f in fields],
        "warnings": t.get("warnings", []),
        "detector": detection.get("detector"),
        "fallback_reason": detection.get("fallback_reason"),
        "appended_signature_page": bool(detection.get("appended_signature_page")),
        "error": t.get("analysis_error"),
        "problems": approval_problems(roles, fields) if t["status"] == "needs_review" else [],
        "sender_inputs": sender_inputs(t.get("fields", [])),
        "analysis_started_at": t.get("analysis_started_at"),
    }
