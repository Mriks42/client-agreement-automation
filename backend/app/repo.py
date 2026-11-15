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
    {"templates", "files", "recipients", "events", "api_keys", "webhook_endpoints"}
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
