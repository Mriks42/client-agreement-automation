"""Companies, users, passwords and sessions (DECISIONS.md D14).

- Passwords are hashed with argon2id, off the event loop (the free tier has 0.1 CPU).
- A session is a random 256-bit token in an httpOnly cookie. The database stores only an HMAC of
  it keyed with SESSION_SECRET, so neither a database leak nor a forged cookie gets anyone in.
- Every member of a company is an admin (roles were left out; DECISIONS.md "Left out").
"""

import asyncio
import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from bson import ObjectId
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

from app.config import Settings
from app.worker.queue import utcnow

SESSION_TTL = timedelta(days=14)
COOKIE_NAME = "kickoff_session"

_hasher = PasswordHasher()
_dummy_hash: str | None = None


class EmailTaken(Exception):
    pass


@dataclass(frozen=True)
class CurrentUser:
    id: ObjectId
    org_id: ObjectId
    email: str
    name: str
    org_name: str

    def public(self) -> dict[str, Any]:
        return {
            "user": {"id": str(self.id), "name": self.name, "email": self.email},
            "org": {"id": str(self.org_id), "name": self.org_name},
        }


def normalize_email(email: str) -> str:
    return email.strip().lower()


async def hash_password(password: str) -> str:
    return await asyncio.to_thread(_hasher.hash, password)


async def verify_password(stored_hash: str | None, password: str) -> bool:
    """Constant work whether or not the account exists, so timing doesn't reveal emails."""
    global _dummy_hash
    if stored_hash is None:
        if _dummy_hash is None:
            _dummy_hash = await hash_password(secrets.token_urlsafe(16))
        stored_hash = _dummy_hash
        await asyncio.to_thread(_safe_verify, stored_hash, password)
        return False
    return await asyncio.to_thread(_safe_verify, stored_hash, password)


def _safe_verify(stored_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(stored_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


async def create_account(
    db: AsyncDatabase, *, org_name: str, name: str, email: str, password: str
) -> CurrentUser:
    email = normalize_email(email)
    if await db.users.find_one({"email": email}, {"_id": 1}):
        raise EmailTaken(email)
    password_hash = await hash_password(password)
    now = utcnow()
    org_id = (await db.orgs.insert_one({"name": org_name, "created_at": now})).inserted_id
    try:
        user_id = (
            await db.users.insert_one(
                {
                    "org_id": org_id,
                    "email": email,
                    "name": name,
                    "password_hash": password_hash,
                    "created_at": now,
                }
            )
        ).inserted_id
    except DuplicateKeyError as exc:  # a concurrent signup with the same email won the race
        await db.orgs.delete_one({"_id": org_id})
        raise EmailTaken(email) from exc
    return CurrentUser(user_id, org_id, email, name, org_name)


async def authenticate(db: AsyncDatabase, email: str, password: str) -> CurrentUser | None:
    user = await db.users.find_one({"email": normalize_email(email)})
    if not await verify_password(user["password_hash"] if user else None, password):
        return None
    assert user is not None
    if _hasher.check_needs_rehash(user["password_hash"]):
        new_hash = await hash_password(password)
        await db.users.update_one({"_id": user["_id"]}, {"$set": {"password_hash": new_hash}})
    org = await db.orgs.find_one({"_id": user["org_id"]}, {"name": 1})
    return CurrentUser(
        user["_id"], user["org_id"], user["email"], user["name"], org["name"] if org else ""
    )


def _session_key(settings: Settings, token: str) -> str:
    secret = settings.session_secret.get_secret_value().encode()
    return hmac.new(secret, token.encode(), hashlib.sha256).hexdigest()


async def create_session(db: AsyncDatabase, settings: Settings, user: CurrentUser) -> str:
    token = secrets.token_urlsafe(32)
    now = utcnow()
    await db.sessions.insert_one(
        {
            "_id": _session_key(settings, token),
            "user_id": user.id,
            "org_id": user.org_id,
            "created_at": now,
            "expires_at": now + SESSION_TTL,
        }
    )
    return token


async def load_session(db: AsyncDatabase, settings: Settings, token: str) -> CurrentUser | None:
    if not token or len(token) > 200:
        return None
    session = await db.sessions.find_one(
        {"_id": _session_key(settings, token), "expires_at": {"$gt": utcnow()}}
    )
    if not session:
        return None
    user = await db.users.find_one({"_id": session["user_id"], "org_id": session["org_id"]})
    if not user:
        return None
    org = await db.orgs.find_one({"_id": user["org_id"]}, {"name": 1})
    if not org:
        return None
    return CurrentUser(user["_id"], user["org_id"], user["email"], user["name"], org["name"])


async def delete_session(db: AsyncDatabase, settings: Settings, token: str) -> None:
    if token:
        await db.sessions.delete_one({"_id": _session_key(settings, token)})


class LoginThrottle:
    """Slows password guessing: after `limit` failures for one email within `window_s`, further
    attempts are refused until the window passes. In-process, which is enough for one instance."""

    def __init__(self, limit: int = 10, window_s: float = 900) -> None:
        self.limit = limit
        self.window_s = window_s
        self._failures: dict[str, list[float]] = {}

    def _recent(self, email: str) -> list[float]:
        cutoff = time.monotonic() - self.window_s
        recent = [t for t in self._failures.get(email, []) if t > cutoff]
        if recent:
            self._failures[email] = recent
        else:
            self._failures.pop(email, None)
        return recent

    def blocked(self, email: str) -> bool:
        return len(self._recent(normalize_email(email))) >= self.limit

    def failed(self, email: str) -> None:
        key = normalize_email(email)
        self._failures.setdefault(key, []).append(time.monotonic())
        if len(self._failures) > 10_000:  # bound memory under a spray of random emails
            self._failures.pop(next(iter(self._failures)))

    def succeeded(self, email: str) -> None:
        self._failures.pop(normalize_email(email), None)
