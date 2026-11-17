"""API keys: how the firm's other systems call the public API (/api/v1).

A key is shown once, when it's created. Only its SHA-256 is stored (like signing tokens), plus
its first characters so people can tell keys apart. A key acts for the person who created it:
files it opens are theirs, and they confirm and send them.
"""

import hashlib
import secrets
from datetime import timedelta
from typing import Any

from pymongo.asynchronous.database import AsyncDatabase

from app.repo import Tenant, find_api_key
from app.services.accounts import CurrentUser
from app.services.errors import Conflict, NotFound
from app.worker.queue import utcnow

PREFIX = "kk_live_"
SHOWN = 12  # characters kept in the clear, e.g. "kk_live_AbCd"
MAX_KEYS = 20
_TOUCH_EVERY = timedelta(minutes=1)


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def public(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(doc["_id"]),
        "name": doc["name"],
        "prefix": doc["prefix"],
        "created_at": doc["created_at"],
        "created_by_name": doc["created_by_name"],
        "last_used_at": doc.get("last_used_at"),
        "revoked_at": doc.get("revoked_at"),
    }


async def create(tenant: Tenant, user: CurrentUser, name: str) -> tuple[str, dict[str, Any]]:
    live = await tenant.find("api_keys", {"revoked_at": None}, limit=MAX_KEYS + 1)
    if len(live) >= MAX_KEYS:
        raise Conflict(f"You can have up to {MAX_KEYS} keys. Revoke one you no longer use.")
    raw = PREFIX + secrets.token_urlsafe(32)
    doc: dict[str, Any] = {
        "name": " ".join(name.split())[:80] or "API key",
        "key_hash": hash_key(raw),
        "prefix": raw[:SHOWN],
        "created_by": user.id,
        "created_by_name": user.name,
        "created_at": utcnow(),
        "last_used_at": None,
        "revoked_at": None,
    }
    doc["_id"] = await tenant.insert("api_keys", doc)
    return raw, doc


async def list_keys(tenant: Tenant) -> list[dict[str, Any]]:
    return await tenant.find("api_keys", sort=[("created_at", -1)], limit=100)


async def revoke(tenant: Tenant, key_id: str) -> dict[str, Any]:
    updated = await tenant.transition(
        "api_keys", key_id, {"revoked_at": None}, {"$set": {"revoked_at": utcnow()}}
    )
    if updated is None:
        if await tenant.get("api_keys", key_id) is None:
            raise NotFound(key_id)
        raise Conflict("This key is already revoked.")
    return updated


async def authenticate(db: AsyncDatabase, raw: str) -> tuple[dict[str, Any], CurrentUser] | None:
    """The key's record and the person it acts for, or None for an unknown or revoked key."""
    if not raw.startswith(PREFIX) or len(raw) > 200:
        return None
    key = await find_api_key(db, hash_key(raw))
    if key is None or key.get("revoked_at") is not None:
        return None
    user = await db.users.find_one({"_id": key["created_by"], "org_id": key["org_id"]})
    org = await db.orgs.find_one({"_id": key["org_id"]}, {"name": 1})
    if user is None or org is None:
        return None
    now = utcnow()
    last = key.get("last_used_at")
    if last is None or now - last > _TOUCH_EVERY:
        await db.api_keys.update_one(
            {"_id": key["_id"], "org_id": key["org_id"]}, {"$set": {"last_used_at": now}}
        )
    return key, CurrentUser(user["_id"], user["org_id"], user["email"], user["name"], org["name"])
