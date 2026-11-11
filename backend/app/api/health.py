"""Liveness (`/healthz`, for Render) and a deep component check (`/api/health`, for monitoring).

`/healthz` has no dependencies on purpose: if Atlas has a blip, restarting the web service
won't fix it, so Render's health check shouldn't fail because of it.
"""

import asyncio
import time
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.worker.queue import utcnow

router = APIRouter()

_EMAIL_CHECK_TTL_S = 900


@router.get("/healthz", include_in_schema=False)
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/api/health")
async def deep_health(request: Request) -> JSONResponse:
    state = request.app.state
    checks: dict[str, Any] = {}

    try:
        await asyncio.wait_for(state.db.ping(), timeout=3)
        checks["database"] = {"ok": True}
    except Exception as exc:
        checks["database"] = {"ok": False, "error": type(exc).__name__}

    checks["email"] = await _check_email(state)
    if state.settings.is_prod and checks["email"].get("backend") == "console":
        checks["email"] = {
            "ok": False,
            "backend": "console",
            "error": "console backend in prod: emails are logged, not delivered",
        }

    if state.settings.run_worker and checks["database"]["ok"]:
        beat = await state.db.db.worker_heartbeats.find_one(sort=[("updated_at", -1)])
        alive = bool(beat and beat["updated_at"] > utcnow() - timedelta(minutes=2))
        dead_jobs = await state.db.db.jobs.count_documents({"status": "dead"})
        checks["worker"] = {"ok": alive, "dead_jobs": dead_jobs}

    checks["llm"] = {"ok": state.settings.anthropic_api_key is not None, "configured_only": True}

    ok = all(c["ok"] for c in checks.values())
    body = {
        "status": "ok" if ok else "degraded",
        "version": state.settings.git_sha,
        "checks": checks,
    }
    return JSONResponse(body, status_code=200 if ok else 503)


async def _check_email(state: Any) -> dict[str, Any]:
    # Cached: verifying Gmail credentials refreshes an OAuth token; no need to do that per ping.
    sender = state.email
    cache: dict[str, Any] = state.email_check_cache
    now = time.monotonic()
    if cache.get("result") and now - cache["at"] < _EMAIL_CHECK_TTL_S:
        return cache["result"]
    try:
        await asyncio.wait_for(sender.check(), timeout=10)
        result: dict[str, Any] = {"ok": True, "backend": sender.name}
    except Exception as exc:
        result = {"ok": False, "backend": sender.name, "error": str(exc)[:200]}
    cache.update(at=now, result=result)
    return result
