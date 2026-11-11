from datetime import timedelta
from typing import Any

from app.worker.queue import (
    JobContext,
    JobType,
    PermanentJobError,
    Worker,
    enqueue,
    retry_dead_job,
    utcnow,
)


class Flaky(Exception):
    pass


def make_worker(db: Any, **types: JobType) -> Worker:
    return Worker(db, dict(types))


async def test_success_marks_done(db: Any) -> None:
    seen: list[dict[str, Any]] = []

    async def handler(ctx: JobContext, payload: dict[str, Any]) -> None:
        seen.append(payload)

    w = make_worker(db, ok=JobType(handler))
    job_id = await enqueue(db, "ok", {"n": 1})
    assert await w.drain() == 1
    job = await db.jobs.find_one({"_id": job_id})
    assert job["status"] == "done" and job["attempts"] == 1
    assert seen == [{"n": 1}]


async def test_transient_failure_backs_off_then_dies_after_max_attempts(db: Any) -> None:
    dead: list[str] = []

    async def handler(ctx: JobContext, payload: dict[str, Any]) -> None:
        raise Flaky("provider 503")

    async def on_dead(ctx: JobContext, payload: dict[str, Any], error: str) -> None:
        dead.append(error)

    w = make_worker(db, flaky=JobType(handler, max_attempts=3, on_dead=on_dead))
    job_id = await enqueue(db, "flaky", {})

    assert await w.drain() == 1
    job = await db.jobs.find_one({"_id": job_id})
    assert job["status"] == "pending"
    assert job["run_at"] > utcnow() + timedelta(seconds=10)  # backed off, not immediately due
    assert "provider 503" in job["last_error"]

    # Fast-forward through the remaining attempts.
    for _ in range(2):
        await db.jobs.update_one({"_id": job_id}, {"$set": {"run_at": utcnow()}})
        await w.drain()
    job = await db.jobs.find_one({"_id": job_id})
    assert job["status"] == "dead" and job["attempts"] == 3
    assert dead and "provider 503" in dead[0]


async def test_permanent_error_dies_immediately(db: Any) -> None:
    async def handler(ctx: JobContext, payload: dict[str, Any]) -> None:
        raise PermanentJobError("recipient rejected")

    w = make_worker(db, perm=JobType(handler, max_attempts=8))
    job_id = await enqueue(db, "perm", {})
    await w.drain()
    job = await db.jobs.find_one({"_id": job_id})
    assert job["status"] == "dead" and job["attempts"] == 1


async def test_declared_permanent_errors_are_not_retried(db: Any) -> None:
    async def handler(ctx: JobContext, payload: dict[str, Any]) -> None:
        raise ValueError("bad input")

    w = make_worker(db, v=JobType(handler, permanent_errors=(ValueError,)))
    job_id = await enqueue(db, "v", {})
    await w.drain()
    assert (await db.jobs.find_one({"_id": job_id}))["status"] == "dead"


async def test_dedupe_key_makes_enqueue_idempotent(db: Any) -> None:
    a = await enqueue(db, "ok", {"n": 1}, dedupe_key="invite:r1")
    b = await enqueue(db, "ok", {"n": 2}, dedupe_key="invite:r1")
    assert a == b
    assert await db.jobs.count_documents({}) == 1


async def test_jobs_without_dedupe_key_do_not_collide(db: Any) -> None:
    await enqueue(db, "ok", {})
    await enqueue(db, "ok", {})
    assert await db.jobs.count_documents({}) == 2


async def test_future_jobs_are_not_claimed(db: Any) -> None:
    async def handler(ctx: JobContext, payload: dict[str, Any]) -> None:
        raise AssertionError("should not run")

    w = make_worker(db, later=JobType(handler))
    await enqueue(db, "later", {}, run_at=utcnow() + timedelta(hours=1))
    assert await w.drain() == 0


async def test_stale_lease_is_reclaimed(db: Any) -> None:
    """A job whose worker died mid-run (lease expired) gets picked up again."""
    ran: list[int] = []

    async def handler(ctx: JobContext, payload: dict[str, Any]) -> None:
        ran.append(ctx.attempt)

    w = make_worker(db, ok=JobType(handler))
    job_id = await enqueue(db, "ok", {})
    await db.jobs.update_one(
        {"_id": job_id},
        {
            "$set": {
                "status": "running",
                "attempts": 1,
                "locked_by": "dead-worker",
                "locked_until": utcnow() - timedelta(seconds=1),
            }
        },
    )
    assert await w.drain() == 1
    assert ran == [2]
    assert (await db.jobs.find_one({"_id": job_id}))["status"] == "done"


async def test_job_reclaimed_too_often_is_killed_without_running(db: Any) -> None:
    async def handler(ctx: JobContext, payload: dict[str, Any]) -> None:
        raise AssertionError("should not run")

    w = make_worker(db, crashy=JobType(handler, max_attempts=2))
    job_id = await enqueue(db, "crashy", {})
    await db.jobs.update_one(
        {"_id": job_id},
        {
            "$set": {
                "status": "running",
                "attempts": 2,
                "locked_by": "dead-worker",
                "locked_until": utcnow() - timedelta(seconds=1),
            }
        },
    )
    await w.drain()
    job = await db.jobs.find_one({"_id": job_id})
    assert job["status"] == "dead" and "lease" in job["last_error"]


async def test_manual_retry_of_dead_job(db: Any) -> None:
    calls = 0

    async def handler(ctx: JobContext, payload: dict[str, Any]) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise PermanentJobError("boom")

    w = make_worker(db, j=JobType(handler))
    job_id = await enqueue(db, "j", {})
    await w.drain()
    assert (await db.jobs.find_one({"_id": job_id}))["status"] == "dead"
    assert await retry_dead_job(db, job_id)
    await w.drain()
    job = await db.jobs.find_one({"_id": job_id})
    assert job["status"] == "done" and job["attempts"] == 1


async def test_periodic_task_runs_once_per_interval(db: Any) -> None:
    runs = 0

    async def tick(ctx: JobContext) -> None:
        nonlocal runs
        runs += 1

    w = make_worker(db)
    w.every("sweep", 3600, tick)
    await w._run_periodic()
    await w._run_periodic()
    assert runs == 1
