"""Tenant-scoped data access (CLAUDE.md "Tenant isolation").

Every read and write of tenant data goes through `Tenant`, which adds the `org_id` filter
itself. Code never builds a bare `find({"_id": ...})` on a tenant collection, so forgetting the
filter can't leak another company's data: a wrong-org id simply isn't found (and the API answers
404, the same as for an id that doesn't exist).
"""

from typing import Any

from bson import ObjectId
from bson.errors import InvalidId
from pymongo import ReturnDocument
from pymongo.asynchronous.database import AsyncDatabase

TENANT_COLLECTIONS = frozenset(
    {
        "templates",
        "files",
        "recipients",
        "events",
        "api_keys",
        "webhook_endpoints",
        "users",
        "invites",
        "clients",
    }
)


def parse_id(value: str | ObjectId) -> ObjectId | None:
    if isinstance(value, ObjectId):
        return value
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        return None


class Tenant:
    def __init__(self, db: AsyncDatabase, org_id: ObjectId) -> None:
        if not isinstance(org_id, ObjectId):
            raise TypeError("org_id must be an ObjectId")
        self.db = db
        self.org_id = org_id

    def _coll(self, name: str) -> Any:
        if name not in TENANT_COLLECTIONS:
            raise ValueError(f"{name} is not a tenant collection")
        return self.db[name]

    def scope(self, query: dict[str, Any] | None = None) -> dict[str, Any]:
        return {**(query or {}), "org_id": self.org_id}

    async def get(
        self, coll: str, id: str | ObjectId, projection: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        oid = parse_id(id)
        if oid is None:
            return None
        return await self._coll(coll).find_one(self.scope({"_id": oid}), projection)

    async def find(
        self,
        coll: str,
        query: dict[str, Any] | None = None,
        *,
        sort: list[tuple[str, int]] | None = None,
        limit: int = 200,
        projection: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        cursor = self._coll(coll).find(self.scope(query), projection, sort=sort, limit=limit)
        return [doc async for doc in cursor]

    async def insert(self, coll: str, doc: dict[str, Any]) -> ObjectId:
        result = await self._coll(coll).insert_one({**doc, "org_id": self.org_id})
        return result.inserted_id

    async def delete(self, coll: str, id: str | ObjectId, expect: dict[str, Any]) -> bool:
        """Delete only if the document still matches `expect` (e.g. a file that is still a
        draft). Signed records and events are never deleted."""
        oid = parse_id(id)
        if oid is None:
            return False
        result = await self._coll(coll).delete_one(self.scope({**expect, "_id": oid}))
        return result.deleted_count == 1

    async def transition(
        self,
        coll: str,
        id: str | ObjectId,
        expect: dict[str, Any],
        update: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Atomic conditional update: applies `update` only if the document still matches
        `expect` (e.g. the status and revision the caller read). Returns the updated document,
        or None if it doesn't exist in this org or no longer matches."""
        oid = parse_id(id)
        if oid is None:
            return None
        return await self._coll(coll).find_one_and_update(
            self.scope({**expect, "_id": oid}), update, return_document=ReturnDocument.AFTER
        )

    async def upsert(
        self, coll: str, match: dict[str, Any], update: dict[str, Any]
    ) -> dict[str, Any]:
        """Update the one document matching `match` in this org, or create it (the org_id and
        the equality fields of `match` are set on insert). Needs a unique index on `match`."""
        doc: dict[str, Any] = await self._coll(coll).find_one_and_update(
            self.scope(match), update, upsert=True, return_document=ReturnDocument.AFTER
        )
        return doc


async def find_file_by_signing_token(db: AsyncDatabase, token_hash: str) -> dict[str, Any] | None:
    """The one lookup that isn't scoped by org: a signer has no session, and the signing token
    *is* their credential (only its SHA-256 is stored). The caller builds a `Tenant` from the
    returned file's own org_id for everything after this."""
    if len(token_hash) != 64:
        return None
    file: dict[str, Any] | None = await db.files.find_one({"recipients.token_hashes": token_hash})
    return file


async def find_file_by_final_sha256(db: AsyncDatabase, sha256: str) -> dict[str, Any] | None:
    """The public verify page: a signed PDF's own SHA-256 finds its file, whichever company it
    belongs to. Only someone holding that exact PDF knows the hash, and they already see
    everything the page shows (the agreement, the firm, the signers' names, the date)."""
    if len(sha256) != 64:
        return None
    file: dict[str, Any] | None = await db.files.find_one({"final_sha256": sha256})
    return file


async def find_api_key(db: AsyncDatabase, key_hash: str) -> dict[str, Any] | None:
    """Like a signing token, an API key is its own credential, so this lookup isn't scoped by
    org: the key's record says which org it belongs to, and the caller scopes everything after
    this to that org. Only the key's SHA-256 is stored."""
    if len(key_hash) != 64:
        return None
    return await db.api_keys.find_one({"key_hash": key_hash})


async def find_invite(db: AsyncDatabase, token_hash: str) -> dict[str, Any] | None:
    """A team invitation by its link's SHA-256. Like a signing token, the link is the invited
    person's credential, so this isn't scoped by org; the caller scopes everything after it to
    the invitation's own org_id."""
    if len(token_hash) != 64:
        return None
    return await db.invites.find_one({"token_hashes": token_hash})
