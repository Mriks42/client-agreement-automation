"""Template endpoints: upload, list, review (pages, fields, roles), approve.

Handlers stay thin: the work is in services/templates.py, and detection runs in the
analyze_template job, never in a request.
"""

import asyncio
import re
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, StringConstraints

from app.api.deps import TenantDep, UserDep
from app.domain.fields import Field, Role
from app.services import ingest, pdf_engine, storage
from app.services import templates as svc
from app.services.storage import BytesLRU

router = APIRouter(prefix="/api/templates", tags=["templates"])

PAGE_DPI = 110
_render_slots = asyncio.Semaphore(2)  # 0.1 CPU: rendering many pages at once only queues


class TemplateEdit(BaseModel):
    revision: int
    roles: list[Role]
    fields: list[Field]
    name: Annotated[str, StringConstraints(max_length=120)] | None = None


class Approval(BaseModel):
    revision: int


def _errors(exc: Exception) -> HTTPException:
    if isinstance(exc, svc.NotFound):
        return HTTPException(status_code=404, detail="Template not found.")
    if isinstance(exc, svc.Conflict):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, svc.Invalid):
        return HTTPException(
            status_code=422, detail={"message": "Fix these first:", "problems": exc.problems}
        )
    raise exc


@router.post("", status_code=201)
async def upload(
    request: Request,
    user: UserDep,
    tenant: TenantDep,
    file: Annotated[UploadFile, File()],
    name: Annotated[str | None, Form(max_length=120)] = None,
) -> dict[str, Any]:
    settings = request.app.state.settings
    limit = settings.max_upload_mb * 1024 * 1024
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(
            status_code=413,
            detail=f"The file is larger than {settings.max_upload_mb} MB. Upload a smaller one.",
        )
    try:
        doc = await svc.create_from_upload(
            tenant, request.app.state.db.blobs, settings, user, data, file.filename, name=name
        )
    except ingest.IngestError as exc:
        raise HTTPException(status_code=422, detail=exc.message) from exc
    return svc.detail(doc)


@router.get("")
async def list_templates(tenant: TenantDep) -> list[dict[str, Any]]:
    docs = await tenant.find(
        "templates",
        sort=[("updated_at", -1)],
        projection={"roles": 0, "warnings": 0, "detection": 0},
    )
    return [svc.summary(d) for d in docs]


@router.get("/{template_id}")
async def get_template(template_id: str, tenant: TenantDep) -> dict[str, Any]:
    doc = await tenant.get("templates", template_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Template not found.")
    return svc.detail(doc)


@router.put("/{template_id}")
async def save_template(template_id: str, body: TemplateEdit, tenant: TenantDep) -> dict[str, Any]:
    try:
        doc = await svc.save(
            tenant,
            template_id,
            svc.Edit(revision=body.revision, roles=body.roles, fields=body.fields, name=body.name),
        )
    except (svc.NotFound, svc.Conflict, svc.Invalid) as exc:
        raise _errors(exc) from exc
    return svc.detail(doc)


@router.post("/{template_id}/approve")
async def approve_template(
    template_id: str, body: Approval, user: UserDep, tenant: TenantDep
) -> dict[str, Any]:
    try:
        doc = await svc.approve(tenant, template_id, body.revision, user)
    except (svc.NotFound, svc.Conflict, svc.Invalid) as exc:
        raise _errors(exc) from exc
    return svc.detail(doc)


@router.post("/{template_id}/retry-analysis")
async def retry_analysis(template_id: str, tenant: TenantDep) -> dict[str, Any]:
    try:
        doc = await svc.retry_analysis(tenant, template_id)
    except (svc.NotFound, svc.Conflict) as exc:
        raise _errors(exc) from exc
    return svc.detail(doc)


@router.post("/{template_id}/manual")
async def place_manually(template_id: str, tenant: TenantDep) -> dict[str, Any]:
    try:
        doc = await svc.start_manual(tenant, template_id)
    except (svc.NotFound, svc.Conflict) as exc:
        raise _errors(exc) from exc
    return svc.detail(doc)


async def _template_pdf(request: Request, doc: dict[str, Any]) -> bytes:
    cache: BytesLRU = request.app.state.page_cache
    key = ("pdf", doc["pdf_blob_id"])
    pdf = cache.get(key)
    if pdf is None:
        pdf = await storage.get(request.app.state.db.blobs, doc["pdf_blob_id"])
        cache.put(key, pdf)
    return pdf


@router.get("/{template_id}/pages/{page}.png")
async def page_image(template_id: str, page: int, request: Request, tenant: TenantDep) -> Response:
    doc = await tenant.get("templates", template_id, {"pdf_blob_id": 1, "page_count": 1})
    if doc is None or not 1 <= page <= doc["page_count"]:
        raise HTTPException(status_code=404, detail="Page not found.")
    cache: BytesLRU = request.app.state.page_cache
    key = ("png", doc["pdf_blob_id"], page, PAGE_DPI)
    png = cache.get(key)
    if png is None:
        pdf = await _template_pdf(request, doc)
        async with _render_slots:
            png = await asyncio.to_thread(pdf_engine.render_page_png, pdf, page, dpi=PAGE_DPI)
        cache.put(key, png)
    # The URL carries ?v=<pdf blob id>, and a blob never changes, so the browser can keep it.
    return Response(
        png, media_type="image/png", headers={"cache-control": "private, max-age=86400"}
    )


@router.get("/{template_id}/pdf")
async def template_pdf(template_id: str, request: Request, tenant: TenantDep) -> Response:
    doc = await tenant.get("templates", template_id, {"pdf_blob_id": 1, "name": 1})
    if doc is None:
        raise HTTPException(status_code=404, detail="Template not found.")
    pdf = await _template_pdf(request, doc)
    filename = (re.sub(r"[^A-Za-z0-9 ._-]", "", doc["name"]).strip() or "agreement") + ".pdf"
    return Response(
        pdf,
        media_type="application/pdf",
        headers={
            "content-disposition": f'inline; filename="{filename}"',
            "cache-control": "private, no-cache",
        },
    )
