"""Team members: more than one person per company, each with their own login.

Anyone in the company can invite a colleague by email (every member is an admin; roles were
left out, DECISIONS.md). The invitation works like a signing link: its token is minted inside the
email job (or by Copy invite link) and only its SHA-256 is stored, so a usable link never sits in
the database. Joining creates the person's login in the inviting company, bound to the invited
email. Removing someone ends their sessions and revokes the API keys they made. Files they sent
keep their name, because the audit trail records who did what.
"""

import hashlib
import secrets
from datetime import timedelta
from typing import Any

from bson import ObjectId
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

from app.config import Settings
from app.repo import Tenant, find_invite, parse_id
from app.services import accounts
from app.services.accounts import CurrentUser
from app.services.email import OutgoingEmail
from app.services.email import messages as mail
from app.services.errors import Conflict, Invalid, NotFound
from app.worker.queue import JobContext, enqueue, utcnow

INVITE_EMAIL = "team_invite_email"
INVITE_TTL = timedelta(days=7)
MAX_PENDING = 50


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _new_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    return token, _hash(token)


def join_url(settings: Settings, token: str) -> str:
    return f"{settings.app_base_url.rstrip('/')}/join/{token}"


def _pending(now: Any) -> dict[str, Any]:
    return {"accepted_at": None, "revoked_at": None, "expires_at": {"$gt": now}}


# ---- for people already in the company -----------------------------------------------------------
async def team(tenant: Tenant, me: CurrentUser) -> dict[str, Any]:
    members = await tenant.find("users", sort=[("created_at", 1)], projection={"password_hash": 0})
    invites = await tenant.find(
        "invites", _pending(utcnow()), sort=[("created_at", -1)], limit=MAX_PENDING
    )
    return {
        "members": [
            {
                "id": str(u["_id"]),
                "name": u["name"],
                "email": u["email"],
                "joined_at": u["created_at"],
                "you": u["_id"] == me.id,
            }
            for u in members
        ],
        "invites": [
            {
                "id": str(i["_id"]),
                "email": i["email"],
                "name": i.get("name"),
                "invited_by_name": i["invited_by_name"],
                "created_at": i["created_at"],
                "expires_at": i["expires_at"],
            }
            for i in invites
        ],
    }


async def invite(tenant: Tenant, me: CurrentUser, email: str, name: str | None) -> dict[str, Any]:
    email = accounts.normalize_email(email)
    if "@" not in email or len(email) > 254:
        raise Invalid(["Enter a valid email address."])
    if await tenant.db.users.find_one({"email": email}, {"org_id": 1}):
        raise Conflict(
            "That email already has a Kickoff account. Each email can belong to one company."
        )
    now = utcnow()
    if await tenant.find("invites", {"email": email, **_pending(now)}, limit=1):
        raise Conflict("That person is already invited. Use Copy invite link, or revoke it.")
    pending = await tenant.find("invites", _pending(now), limit=MAX_PENDING + 1)
    if len(pending) >= MAX_PENDING:
        raise Conflict(f"You can have up to {MAX_PENDING} open invitations. Revoke some first.")
    doc: dict[str, Any] = {
        "email": email,
        "name": " ".join((name or "").split())[:120] or None,
        "invited_by": me.id,
        "invited_by_name": me.name,
        "token_hashes": [],
        "created_at": now,
        "expires_at": now + INVITE_TTL,
        "accepted_at": None,
        "revoked_at": None,
    }
    doc["_id"] = await tenant.insert("invites", doc)
    await enqueue(
        tenant.db,
        INVITE_EMAIL,
        {"org_id": str(tenant.org_id), "invite_id": str(doc["_id"])},
        dedupe_key=f"{INVITE_EMAIL}:{doc['_id']}",
        org_id=str(tenant.org_id),
    )
    return doc


async def _mint(tenant: Tenant, invite_id: str) -> tuple[str, dict[str, Any]]:
    """A fresh link for a pending invitation (only its hash is stored)."""
    token, token_hash = _new_token()
    updated = await tenant.transition(
        "invites",
        invite_id,
        _pending(utcnow()),
        {"$push": {"token_hashes": {"$each": [token_hash], "$slice": -10}}},
    )
    if updated is None:
        if await tenant.get("invites", invite_id) is None:
            raise NotFound(invite_id)
        raise Conflict("This invitation was used, revoked or has expired. Invite them again.")
    return token, updated


async def copy_link(tenant: Tenant, settings: Settings, invite_id: str) -> str:
    token, _ = await _mint(tenant, invite_id)
    return join_url(settings, token)


async def revoke(tenant: Tenant, invite_id: str) -> None:
    updated = await tenant.transition(
        "invites",
        invite_id,
        {"accepted_at": None, "revoked_at": None},
        {"$set": {"revoked_at": utcnow()}},
    )
    if updated is None:
        if await tenant.get("invites", invite_id) is None:
            raise NotFound(invite_id)
        raise Conflict("This invitation was already used or revoked.")


async def remove(tenant: Tenant, me: CurrentUser, user_id: str) -> None:
    uid = parse_id(user_id)
    if uid is None or await tenant.get("users", uid, {"_id": 1}) is None:
        raise NotFound(user_id)
    if uid == me.id:
        raise Conflict("You can't remove yourself. Ask a colleague to remove you.")
    if not await tenant.delete("users", uid, {}):
        raise NotFound(user_id)
    db = tenant.db
    await db.sessions.delete_many({"user_id": uid, "org_id": tenant.org_id})
    await db.api_keys.update_many(
        {"org_id": tenant.org_id, "created_by": uid, "revoked_at": None},
        {"$set": {"revoked_at": utcnow()}},
    )


# ---- the invitation email (a job) ----------------------------------------------------------------
async def handle_invite_email(ctx: JobContext, payload: dict[str, Any]) -> None:
    if ctx.job.get("result"):
        return  # sent on an earlier run that crashed before it was marked done
    tenant = Tenant(ctx.db, ObjectId(payload["org_id"]))
    try:
        token, inv = await _mint(tenant, payload["invite_id"])
    except (NotFound, Conflict):
        return  # revoked or used before the email went out: nothing to send
    settings: Settings = ctx.services["settings"]
    org = await ctx.db.orgs.find_one({"_id": tenant.org_id}, {"name": 1})
    org_name = org["name"] if org else settings.email_product_name
    inviter = await ctx.db.users.find_one(
        {"_id": inv["invited_by"], "org_id": tenant.org_id}, {"email": 1}
    )
    msg = mail.team_invite(
        org_name=org_name,
        invitee_name=inv.get("name"),
        inviter_name=inv["invited_by_name"],
        url=join_url(settings, token),
        expires_at=inv["expires_at"],
        product=settings.email_product_name,
    )
    result = await ctx.services["email"].send(
        OutgoingEmail(
            to=[inv["email"]],
            subject=msg.subject,
            text=msg.text,
            html=msg.html,
            from_name=settings.email_product_name,
            reply_to=inviter["email"] if inviter else None,
        )
    )
    await ctx.db.jobs.update_one(
        {"_id": ctx.job["_id"]},
        {"$set": {"result": {"provider": result.provider, "message_id": result.message_id}}},
    )


# ---- for the person invited (no session: the link is the credential) ----------------------------
async def _open(db: AsyncDatabase, token: str) -> tuple[dict[str, Any], dict[str, Any]]:
    if not token or len(token) > 200:
        raise NotFound("invite")
    inv = await find_invite(db, _hash(token))
    now = utcnow()
    if (
        inv is None
        or inv.get("accepted_at") is not None
        or inv.get("revoked_at") is not None
        or inv["expires_at"] <= now
    ):
        raise NotFound("invite")
    org = await db.orgs.find_one({"_id": inv["org_id"]}, {"name": 1})
    if org is None:
        raise NotFound("invite")
    return inv, org


async def preview(db: AsyncDatabase, token: str) -> dict[str, Any]:
    inv, org = await _open(db, token)
    return {
        "org_name": org["name"],
        "email": inv["email"],
        "name": inv.get("name"),
        "invited_by_name": inv["invited_by_name"],
    }


async def join(db: AsyncDatabase, token: str, name: str, password: str) -> CurrentUser:
    inv, org = await _open(db, token)
    tenant = Tenant(db, inv["org_id"])
    password_hash = await accounts.hash_password(password)
    now = utcnow()
    try:
        user_id = await tenant.insert(
            "users",
            {
                "email": inv["email"],
                "name": name,
                "password_hash": password_hash,
                "created_at": now,
                "invited_by": inv["invited_by"],
            },
        )
    except DuplicateKeyError as exc:
        raise Conflict("That email already has a Kickoff account. Log in instead.") from exc
    accepted = await tenant.transition(
        "invites",
        inv["_id"],
        {"accepted_at": None, "revoked_at": None},
        {"$set": {"accepted_at": now, "accepted_by": user_id}},
    )
    if accepted is None:  # revoked or used at the same moment: undo the login just made
        await tenant.delete("users", user_id, {})
        raise NotFound("invite")
    return CurrentUser(user_id, inv["org_id"], inv["email"], name, org["name"])
