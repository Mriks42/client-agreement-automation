"""Saved clients: an address book that fills itself in when a file is sent.

When a file is sent, the client signer's name and email, and the values of the agreement's
*client identity* fields (company, contact name, email, phone, address, title), are saved for the
company, keyed by the client's email. On New file, picking a saved client fills the client
signer and those identity fields, and nothing else.

Which fields are identity fields is decided by plain keyword rules on the field's label, not by
AI, so the same label always gives the same answer:
- Job-specific fields (dates, fees, descriptions, deliverables, ...) are never filled: they
  change from file to file.
- Firm-side fields (labels mentioning consultant, provider, firm, vendor, designer, or one of the
  other signers' role names) are never filled: they're the firm's own details.
- For the agreement a client was last sent, the exact values are reused key for key. For another
  agreement, a field is filled when its label falls in the same category.
"""

import re
from typing import Any

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from app.domain.fields import sender_inputs
from app.repo import Tenant, parse_id
from app.services.accounts import normalize_email
from app.services.errors import Conflict, Invalid, NotFound
from app.worker.queue import utcnow

COMPANY = "company"
CONTACT = "contact_name"
EMAIL = "email"
PHONE = "phone"
ADDRESS = "address"
TITLE = "title"
DETAILS = (
    COMPANY,
    ADDRESS,
    PHONE,
    TITLE,
)  # saved from the form; name and email come from the signer

MAX_VALUE = 300
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_FIRM_WORDS = frozenset(
    {
        "consultant",
        "consultants",
        "provider",
        "firm",
        "vendor",
        "designer",
        "contractor",
        "supplier",
    }
)
_JOB_WORDS = frozenset(
    {
        "date", "dates", "dated", "day", "days", "month", "year", "effective", "start", "end",
        "deadline", "term", "renewal", "fee", "fees", "price", "prices", "pricing", "amount",
        "rate", "rates", "cost", "costs", "budget", "deposit", "retainer", "payment", "payments",
        "invoice", "total", "usd", "hours", "quantity", "plan", "subscription", "order",
        "description", "describe", "deliverable", "deliverables", "scope", "project", "projects",
        "matter", "service", "services", "milestone", "milestones", "schedule", "purpose",
    }
)  # fmt: skip
_PHONE_WORDS = frozenset({"phone", "telephone", "tel", "mobile", "cell"})
_TITLE_WORDS = frozenset({"title", "position"})
_CONTACT_WORDS = frozenset(
    {"contact", "representative", "attention", "attn", "dear", "signatory", "signer", "person"}
)
_COMPANY_WORDS = frozenset(
    {"name", "company", "business", "organization", "organisation", "entity", "corporation"}
)


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z]+", text.lower().replace("e-mail", "email")))


def client_role(roles: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The signer a saved client is: the first client-kind role, else the first that isn't the
    firm."""
    ordered = sorted(roles, key=lambda r: r["order"])
    return next((r for r in ordered if r["kind"] == "client"), None) or next(
        (r for r in ordered if r["kind"] != "firm"), None
    )


def category(label: str, *, other_party: set[str], client_words: set[str]) -> str | None:
    """Which identity detail a sender field holds, or None if it must never be filled."""
    w = _words(label)
    if not w or w & _JOB_WORDS or w & _FIRM_WORDS or w & other_party:
        return None
    if EMAIL in w:
        return EMAIL
    if w & _PHONE_WORDS:
        return PHONE
    if ADDRESS in w:
        return ADDRESS
    if w & _TITLE_WORDS:
        return TITLE
    if w & _CONTACT_WORDS:
        return CONTACT
    if w & _COMPANY_WORDS or w <= ({"client", "customer", "the"} | client_words):
        return COMPANY
    return None


def identity_inputs(
    fields: list[dict[str, Any]], roles: list[dict[str, Any]]
) -> list[tuple[dict[str, Any], str]]:
    """The new-file inputs that are client identity details, each with its category."""
    client = client_role(roles)
    if client is None:
        return []
    client_words = _words(f"{client['label']} {client['key']}")
    other_party: set[str] = set()
    for r in roles:
        if r["key"] != client["key"]:
            other_party |= _words(f"{r['label']} {r['key'].replace('_', ' ')}")
    other_party -= client_words  # "Party A" and "Party B" share "party"
    out = []
    for i in sender_inputs(fields):
        if i["type"] in ("date", "checkbox"):
            continue
        cat = category(i["label"], other_party=other_party, client_words=client_words)
        if cat is not None:
            out.append((i, cat))
    return out


# ---- saving, when a file is sent ---------------------------------------------------------------
async def remember(tenant: Tenant, file: dict[str, Any]) -> ObjectId | None:
    """Save or update the file's client. Called after a send; the caller never lets a failure
    here affect the send."""
    tpl = file["template"]
    role = client_role(tpl["roles"])
    r = next((r for r in file["recipients"] if role and r["role"] == role["key"]), None)
    if r is None or not r.get("email"):
        return None
    exact: dict[str, str] = {}
    details: dict[str, str] = {}
    for i, cat in identity_inputs(tpl["fields"], tpl["roles"]):
        v = file["values"].get(i["key"])
        if not isinstance(v, str) or not v.strip():
            continue
        v = v.strip()[:MAX_VALUE]
        exact[i["key"]] = v
        if cat in DETAILS:
            details.setdefault(cat, v)
    now = utcnow()
    doc = await tenant.upsert(
        "clients",
        {"email": normalize_email(r["email"])},
        {
            "$set": {
                "contact_name": r["name"],
                **details,
                f"templates.{tpl['id']}": exact,
                "last_used_at": now,
                "updated_at": now,
            },
            "$setOnInsert": {"created_at": now},
            "$inc": {"files_sent": 1},
        },
    )
    return doc["_id"]


# ---- the picker on New file --------------------------------------------------------------------
def public(c: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(c["_id"]),
        "company": c.get(COMPANY) or "",
        "contact_name": c.get(CONTACT) or "",
        "email": c["email"],
        "phone": c.get(PHONE) or "",
        "address": c.get(ADDRESS) or "",
        "title": c.get(TITLE) or "",
        "files_sent": c.get("files_sent", 0),
        "last_used_at": c.get("last_used_at"),
    }


async def search(tenant: Tenant, q: str, limit: int = 8) -> list[dict[str, Any]]:
    q = " ".join(q.split())[:100]
    query: dict[str, Any] = {}
    if q:
        pattern = {"$regex": re.escape(q), "$options": "i"}
        query = {"$or": [{COMPANY: pattern}, {CONTACT: pattern}, {EMAIL: pattern}]}
    docs = await tenant.find(
        "clients", query, sort=[("last_used_at", -1)], limit=limit, projection={"templates": 0}
    )
    return [public(d) for d in docs]


async def get(tenant: Tenant, client_id: str) -> dict[str, Any]:
    c = await tenant.get("clients", client_id)
    if c is None:
        raise NotFound(client_id)
    return c


async def fill(tenant: Tenant, client_id: str, template_id: str) -> dict[str, Any]:
    """What picking this client fills on a new file for this agreement: the client signer's
    name and email, and the agreement's client identity fields. Never job-specific or firm-side
    fields."""
    c = await get(tenant, client_id)
    tpl = await tenant.get("templates", template_id)
    if tpl is None:
        raise NotFound(template_id)
    role = client_role(tpl["roles"])
    exact = (c.get("templates") or {}).get(str(tpl["_id"]), {})
    saved = {**{k: c.get(k) for k in DETAILS}, CONTACT: c.get(CONTACT), EMAIL: c["email"]}
    values: dict[str, str] = {}
    for i, cat in identity_inputs(tpl["fields"], tpl["roles"]):
        v = exact.get(i["key"]) or saved.get(cat)
        if isinstance(v, str) and v:
            values[i["key"]] = v
    return {
        "client": public(c),
        "recipient": {"role": role["key"], "name": c.get(CONTACT) or "", "email": c["email"]}
        if role
        else None,
        "values": values,
    }


async def update(tenant: Tenant, client_id: str, changes: dict[str, str]) -> dict[str, Any]:
    """Correct a saved client from the Clients list. The values saved key for key from earlier
    files are dropped, so the next file fills from these corrected details everywhere."""
    c = await get(tenant, client_id)
    clean: dict[str, str] = {}
    for k in (COMPANY, CONTACT, PHONE, ADDRESS, TITLE, EMAIL):
        if k in changes:
            clean[k] = " ".join(changes[k].split())[:MAX_VALUE]
    if EMAIL in clean:
        clean[EMAIL] = normalize_email(clean[EMAIL])
        if not _EMAIL_RE.match(clean[EMAIL]):
            raise Invalid(["Enter a valid email address."])
    if CONTACT in clean and not clean[CONTACT] and not clean.get(COMPANY, c.get(COMPANY)):
        raise Invalid(["Enter a company or a contact name."])
    try:
        updated = await tenant.transition(
            "clients",
            c["_id"],
            {},
            {"$set": {**clean, "updated_at": utcnow()}, "$unset": {"templates": ""}},
        )
    except DuplicateKeyError as exc:
        raise Conflict("Another saved client already has that email.") from exc
    if updated is None:
        raise NotFound(client_id)
    return public(updated)


async def delete(tenant: Tenant, client_id: str) -> None:
    oid = parse_id(client_id)
    if oid is None or not await tenant.delete("clients", oid, {}):
        raise NotFound(client_id)
