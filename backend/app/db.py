"""MongoDB connection, GridFS bucket and index management.

One `Database` instance lives on `app.state.db` for the lifetime of the process.
"""

import logging
from dataclasses import dataclass

from gridfs import AsyncGridFSBucket
from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase

from app.config import Settings

log = logging.getLogger(__name__)


@dataclass
class Database:
    client: AsyncMongoClient
    db: AsyncDatabase
    blobs: AsyncGridFSBucket

    async def ping(self) -> None:
        await self.db.command("ping")

    async def close(self) -> None:
        await self.client.close()


async def connect(settings: Settings) -> Database:
    client: AsyncMongoClient = AsyncMongoClient(
        settings.mongodb_uri,
        serverSelectionTimeoutMS=5000,
        appname="agreement-backend",
        tz_aware=True,
    )
    db = client[settings.mongodb_db]
    database = Database(client=client, db=db, blobs=AsyncGridFSBucket(db, bucket_name="blobs"))
    await ensure_indexes(database)
    return database


async def ensure_indexes(database: Database) -> None:
    """Idempotent; safe to run on every boot. Collections get indexes as features land."""
    db = database.db
    await db.jobs.create_index([("status", 1), ("run_at", 1)])
    await db.jobs.create_index(
        "dedupe_key", unique=True, partialFilterExpression={"dedupe_key": {"$type": "string"}}
    )
    await db.worker_heartbeats.create_index("updated_at", expireAfterSeconds=3600)
    await db.users.create_index("email", unique=True)
    await db.sessions.create_index("expires_at", expireAfterSeconds=0)
    await db.templates.create_index([("org_id", 1), ("updated_at", -1)])
    await db.files.create_index([("org_id", 1), ("updated_at", -1)])
    await db.files.create_index("recipients.token_hashes")
    await db.events.create_index([("org_id", 1), ("file_id", 1), ("at", 1)])
    await db.events.create_index(
        "key", unique=True, partialFilterExpression={"key": {"$type": "string"}}
    )
    await db.api_keys.create_index("key_hash", unique=True)
    await db.api_keys.create_index([("org_id", 1), ("created_at", -1)])
    await db.webhook_endpoints.create_index("org_id", unique=True)
    # Idempotency-Key on POST /api/v1/files: a retried request gets the file it already made.
    await db.files.create_index(
        [("org_id", 1), ("api.idempotency_key", 1)],
        unique=True,
        partialFilterExpression={"api.idempotency_key": {"$type": "string"}},
    )
    await db.jobs.create_index([("org_id", 1), ("type", 1), ("created_at", -1)])
    log.info("indexes ensured")
