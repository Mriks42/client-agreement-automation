"""FastAPI app factory. One process serves the API (/api), the built SPA (/), and runs the
durable job worker (DECISIONS.md D8, D9)."""

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import health, ops
from app.config import Settings, get_settings
from app.db import connect
from app.logging_setup import configure_logging, request_id_var
from app.services.email import EmailSender, build_email_sender
from app.worker.handlers import build_registry
from app.worker.queue import Worker

log = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, email: EmailSender | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(json_logs=settings.is_prod)
    _init_sentry(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.db = await connect(settings)
        app.state.email = email or build_email_sender(settings)
        app.state.email_check_cache = {}
        app.state.worker = None
        worker_task: asyncio.Task[None] | None = None
        if settings.run_worker:
            worker = Worker(
                app.state.db.db,
                build_registry(),
                services={
                    "email": app.state.email,
                    "blobs": app.state.db.blobs,
                    "settings": settings,
                },
            )
            app.state.worker = worker
            worker_task = asyncio.create_task(worker.run_forever())
        log.info("startup complete", extra={"env": settings.app_env, "version": settings.git_sha})
        try:
            yield
        finally:
            if app.state.worker and worker_task:
                app.state.worker.stop()
                with suppress(Exception):
                    await asyncio.wait_for(worker_task, timeout=15)
            await app.state.db.close()

    app = FastAPI(
        title="Agreement automation",
        version=settings.git_sha,
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )
    app.state.settings = settings

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        token = request_id_var.set(rid)
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            log.exception("unhandled error", extra={"path": request.url.path})
            response = JSONResponse(
                {
                    "detail": "Something went wrong on our side. It has been logged.",
                    "request_id": rid,
                },
                status_code=500,
            )
        finally:
            request_id_var.reset(token)
        response.headers["x-request-id"] = rid
        response.headers.setdefault("x-content-type-options", "nosniff")
        response.headers.setdefault("referrer-policy", "same-origin")
        if request.url.path.startswith("/assets/"):
            response.headers["cache-control"] = "public, max-age=31536000, immutable"
        path = request.url.path
        if path not in ("/healthz",):
            log.info(
                "request",
                extra={
                    "method": request.method,
                    "path": path,
                    "status": response.status_code,
                    "ms": round((time.perf_counter() - start) * 1000, 1),
                    "request_id": rid,
                },
            )
        return response

    app.include_router(health.router)
    app.include_router(ops.router)

    _mount_spa(app, settings)
    return app


def _mount_spa(app: FastAPI, settings: Settings) -> None:
    dist = Path(settings.frontend_dist).resolve()
    index = dist / "index.html"
    if not index.exists():
        log.info("no frontend build found; serving API only", extra={"dist": str(dist)})
        return
    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str) -> FileResponse:
        if path.startswith("api/"):
            raise HTTPException(status_code=404)
        candidate = (dist / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(dist):
            return FileResponse(candidate)
        return FileResponse(index, headers={"cache-control": "no-cache"})


def _init_sentry(settings: Settings) -> None:
    if not settings.sentry_dsn:
        return
    import sentry_sdk

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.app_env,
        release=settings.git_sha,
        send_default_pii=False,
        traces_sample_rate=0.0,
    )
