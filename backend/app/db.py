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
    log.info("indexes ensured")
