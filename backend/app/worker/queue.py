"""Durable job queue on MongoDB (DECISIONS.md D9).

Guarantees:
- At-least-once execution. A job is claimed atomically with a lease; if the process dies
  mid-job the lease expires and the job is reclaimed. Handlers MUST be idempotent.
- Retries with exponential backoff + jitter, then a terminal `dead` state that is visible
  in the UI and can be retried by a human.
- Nothing is lost while the instance sleeps: jobs carry `run_at`, and overdue jobs run on wake.
"""

import asyncio
import logging
import os
import random
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

log = logging.getLogger(__name__)

LEASE = timedelta(minutes=5)
BACKOFF_BASE_S = 20
BACKOFF_CAP_S = 3600


class PermanentJobError(Exception):
    """Raise from a handler when retrying cannot succeed. The job goes straight to `dead`."""


Handler = Callable[["JobContext", dict[str, Any]], Awaitable[None]]
DeadHook = Callable[["JobContext", dict[str, Any], str], Awaitable[None]]


@dataclass
class JobType:
    handler: Handler
    max_attempts: int = 8
    on_dead: DeadHook | None = None
    # Exceptions that mean "don't retry" in addition to PermanentJobError.
    permanent_errors: tuple[type[BaseException], ...] = ()


@dataclass
class JobContext:
    db: AsyncDatabase
    job: dict[str, Any]
    services: dict[str, Any] = field(default_factory=dict)

    @property
    def attempt(self) -> int:
        return int(self.job.get("attempts", 0))


def utcnow() -> datetime:
    return datetime.now(UTC)


def backoff_delay(attempt: int) -> timedelta:
    """attempt is 1-based (the attempt that just failed)."""
    delay = min(BACKOFF_CAP_S, BACKOFF_BASE_S * 2 ** (attempt - 1))
    return timedelta(seconds=delay * random.uniform(0.8, 1.2))  # noqa: S311 - jitter only


async def enqueue(
    db: AsyncDatabase,
    job_type: str,
    payload: dict[str, Any],
    *,
    run_at: datetime | None = None,
    dedupe_key: str | None = None,
    max_attempts: int | None = None,
    org_id: str | None = None,
) -> ObjectId:
    """Enqueue a job. With `dedupe_key`, enqueueing the same logical job twice is a no-op."""
    now = utcnow()
    doc: dict[str, Any] = {
        "type": job_type,
        "payload": payload,
        "status": "pending",
        "attempts": 0,
        "run_at": run_at or now,
        "created_at": now,
        "updated_at": now,
        "last_error": None,
        "locked_until": None,
        "locked_by": None,
    }
    if max_attempts is not None:
        doc["max_attempts"] = max_attempts
    if org_id is not None:
        doc["org_id"] = org_id
    if dedupe_key is not None:
        doc["dedupe_key"] = dedupe_key
    try:
        result = await db.jobs.insert_one(doc)
        return result.inserted_id
    except DuplicateKeyError:
        existing = await db.jobs.find_one({"dedupe_key": dedupe_key}, {"_id": 1})
        assert existing is not None
        return existing["_id"]


async def retry_dead_job(db: AsyncDatabase, job_id: ObjectId, org_id: str | None = None) -> bool:
    """Human-triggered retry of a dead job. Resets attempts so it gets a full retry budget."""
    query: dict[str, Any] = {"_id": job_id, "status": "dead"}
    if org_id is not None:
        query["org_id"] = org_id
    res = await db.jobs.update_one(
        query,
        {
            "$set": {
                "status": "pending",
                "attempts": 0,
                "run_at": utcnow(),
                "updated_at": utcnow(),
                "retried_manually_at": utcnow(),
            }
        },
    )
    return res.modified_count == 1


class Worker:
    def __init__(
        self,
        db: AsyncDatabase,
        registry: dict[str, JobType],
        services: dict[str, Any] | None = None,
        concurrency: int = 4,
        poll_interval: float = 1.0,
    ) -> None:
        self.db = db
        self.registry = registry
        self.services = services or {}
        self.concurrency = concurrency
        self.poll_interval = poll_interval
        self.worker_id = f"{socket.gethostname()}:{os.getpid()}:{ObjectId()}"
        self._stop = asyncio.Event()
        self._sem = asyncio.Semaphore(concurrency)
        self._inflight: set[asyncio.Task[None]] = set()
        self._periodic: list[tuple[str, int, Callable[[JobContext], Awaitable[None]]]] = []

    # ---- periodic tasks (reminder sweeps, expiry, bounce polling) -------------------------
    def every(self, name: str, seconds: int, fn: Callable[[JobContext], Awaitable[None]]) -> None:
        self._periodic.append((name, seconds, fn))

    async def _run_periodic(self) -> None:
        now = utcnow()
        for name, seconds, fn in self._periodic:
            try:
                await self.db.scheduler.update_one(
                    {"_id": name}, {"$setOnInsert": {"next_run_at": now}}, upsert=True
                )
                # Atomic claim: only one process runs each tick even with several instances.
                claimed = await self.db.scheduler.find_one_and_update(
                    {"_id": name, "next_run_at": {"$lte": now}},
                    {
                        "$set": {
                            "next_run_at": now + timedelta(seconds=seconds),
                            "last_run_at": now,
                            "last_run_by": self.worker_id,
                        }
                    },
                )
                if claimed:
                    await fn(JobContext(self.db, {"type": f"periodic:{name}"}, self.services))
            except Exception:
                log.exception("periodic task failed", extra={"task": name})

    # ---- main loop ------------------------------------------------------------------------
    async def claim(self) -> dict[str, Any] | None:
        now = utcnow()
        return await self.db.jobs.find_one_and_update(
            {
                "type": {"$in": list(self.registry)},
                "$or": [
                    {"status": "pending", "run_at": {"$lte": now}},
                    {"status": "running", "locked_until": {"$lt": now}},  # stale lease
                ],
            },
            {
                "$set": {
                    "status": "running",
                    "locked_until": now + LEASE,
                    "locked_by": self.worker_id,
                    "updated_at": now,
                },
                "$inc": {"attempts": 1},
            },
            sort=[("run_at", 1)],
            return_document=ReturnDocument.AFTER,
        )

    async def run_one(self, job: dict[str, Any]) -> None:
        jt = self.registry[job["type"]]
        ctx = JobContext(self.db, job, self.services)
        max_attempts = int(job.get("max_attempts", jt.max_attempts))
        extra = {"job_id": str(job["_id"]), "job_type": job["type"], "attempt": job["attempts"]}
        if job["attempts"] > max_attempts:
            # Reclaimed after a lease expiry too many times (e.g. the process keeps crashing
            # on this job). Stop before running it again.
            error = "exceeded max attempts (lease expired repeatedly)"
            await self._mark(job, {"status": "dead", "last_error": error, "finished_at": utcnow()})
            log.error("job dead", extra={**extra, "error": error})
            if jt.on_dead:
                await jt.on_dead(ctx, job["payload"], error)
            return
        try:
            await jt.handler(ctx, job["payload"])
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"[:2000]
            permanent = isinstance(exc, (PermanentJobError, *jt.permanent_errors))
            if permanent or job["attempts"] >= max_attempts:
                await self._mark(
                    job, {"status": "dead", "last_error": error, "finished_at": utcnow()}
                )
                log.error("job dead", extra={**extra, "error": error}, exc_info=not permanent)
                _report(exc)
                if jt.on_dead:
                    try:
                        await jt.on_dead(ctx, job["payload"], error)
                    except Exception:
                        log.exception("on_dead hook failed", extra=extra)
            else:
                run_at = utcnow() + backoff_delay(job["attempts"])
                await self._mark(job, {"status": "pending", "last_error": error, "run_at": run_at})
                log.warning(
                    "job failed, will retry",
                    extra={**extra, "error": error, "retry_at": run_at.isoformat()},
                )
            return
        await self._mark(job, {"status": "done", "finished_at": utcnow(), "last_error": None})
        log.info("job done", extra=extra)

    async def _mark(self, job: dict[str, Any], fields: dict[str, Any]) -> None:
        fields |= {"updated_at": utcnow(), "locked_until": None}
        # Only the lease holder may finish the job.
        await self.db.jobs.update_one(
            {"_id": job["_id"], "locked_by": self.worker_id}, {"$set": fields}
        )

    async def drain(self, max_jobs: int = 1000) -> int:
        """Run due jobs sequentially until none are left. Used by tests and scripts."""
        count = 0
        while count < max_jobs and (job := await self.claim()):
            await self.run_one(job)
            count += 1
        return count

    async def run_forever(self) -> None:
        log.info("worker started", extra={"worker_id": self.worker_id})
        last_beat = 0.0
        loop = asyncio.get_running_loop()
        while not self._stop.is_set():
            try:
                if loop.time() - last_beat > 30:
                    await self.db.worker_heartbeats.update_one(
                        {"_id": self.worker_id}, {"$set": {"updated_at": utcnow()}}, upsert=True
                    )
                    await self._run_periodic()
                    last_beat = loop.time()
                claimed_any = False
                while not self._sem.locked() and (job := await self.claim()):
                    claimed_any = True
                    await self._sem.acquire()
                    task = asyncio.create_task(self._guarded(job))
                    self._inflight.add(task)
                    task.add_done_callback(self._inflight.discard)
                if not claimed_any:
                    await asyncio.wait_for(self._stop.wait(), timeout=self.poll_interval)
            except TimeoutError:
                continue
            except Exception:
                log.exception("worker loop error; backing off")
                await asyncio.sleep(5)
        if self._inflight:
            await asyncio.wait(self._inflight, timeout=10)
        log.info("worker stopped")

    async def _guarded(self, job: dict[str, Any]) -> None:
        try:
            await self.run_one(job)
        except Exception:
            log.exception("unexpected error finishing job", extra={"job_id": str(job["_id"])})
        finally:
            self._sem.release()

    def stop(self) -> None:
        self._stop.set()


def _report(exc: BaseException) -> None:
    try:
        import sentry_sdk

        sentry_sdk.capture_exception(exc)
    except Exception:  # noqa: S110 - reporting must never break the worker
        pass
