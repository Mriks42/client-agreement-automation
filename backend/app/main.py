"""FastAPI app factory. One process serves the API (/api), the built SPA (/), and runs the
durable job worker (DECISIONS.md D8, D9)."""

import asyncio
import logging
import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import auth, files, health, ops, signing, templates, v1
from app.api import settings as settings_api
from app.config import Settings, get_settings
from app.db import connect
from app.logging_setup import configure_logging, request_id_var
from app.services.accounts import LoginThrottle
from app.services.detection.llm import ClaudeLabeller
from app.services.email import EmailSender, build_email_sender
from app.services.storage import BytesLRU
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
        app.state.login_throttle = LoginThrottle()
        app.state.page_cache = BytesLRU(24 * 1024 * 1024)
        labeller = ClaudeLabeller.from_settings(settings)
        # What job handlers get in ctx.services. Tests build a Worker from this same dict.
        app.state.services = {
            "email": app.state.email,
            "blobs": app.state.db.blobs,
            "settings": settings,
            "labeller": labeller,
        }
        app.state.worker = None
        worker_task: asyncio.Task[None] | None = None
        if settings.run_worker:
            worker = Worker(app.state.db.db, build_registry(), services=app.state.services)
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
            if labeller is not None:
                with suppress(Exception):
                    await labeller.client.close()
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
            response: Response
            if _cross_site(request, settings):
                response = JSONResponse(
                    {"detail": "Cross-site request refused.", "request_id": rid}, status_code=403
                )
            else:
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
        path = redact_path(request.url.path)
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
    app.include_router(auth.router)
    app.include_router(templates.router)
    app.include_router(files.router)
    app.include_router(signing.router)
    app.include_router(settings_api.router)
    app.include_router(v1.router)

    _mount_spa(app, settings)
    return app


_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_TOKEN_PATH = re.compile(r"^(/(?:api/)?sign/|/join/|/api/auth/invites/)[^/]+")


def redact_path(path: str) -> str:
    """Signing and team-invitation links carry a credential in the path; never log it."""
    return _TOKEN_PATH.sub(r"\1<token>", path)


def _cross_site(request: Request, settings: Settings) -> bool:
    """Defence in depth next to SameSite=Lax cookies: refuse a state-changing API call whose
    Origin is another site. Server-to-server callers send no Origin and are unaffected."""
    if request.method in _SAFE_METHODS or not request.url.path.startswith("/api/"):
        return False
    origin = request.headers.get("origin")
    if not origin:
        return False
    host = urlsplit(origin).netloc
    allowed = {request.headers.get("host", ""), urlsplit(settings.app_base_url).netloc}
    return host not in allowed


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
        before_send=_scrub_event,
    )


def _scrub_event(event: Any, hint: Any) -> Any:
    request = event.get("request") or {}
    if isinstance(request.get("url"), str):
        parts = urlsplit(request["url"])
        request["url"] = parts._replace(path=redact_path(parts.path)).geturl()
    headers = request.get("headers")
    if isinstance(headers, dict):  # API keys and session cookies, whatever the SDK's defaults
        for name in list(headers):
            if name.lower() in ("authorization", "cookie", "x-kickoff-signature"):
                headers[name] = "[Filtered]"
    return event
