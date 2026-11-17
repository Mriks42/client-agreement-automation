"""File endpoints: open a file from an approved agreement, preview the exact PDF, send it, and
follow it (copy link, resend, correct an email, void). Side effects run in jobs."""

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field, StringConstraints

from app.api.deps import TenantDep, UserDep
from app.services import events, finalize, pdf_engine, signing, storage
from app.services import files as svc
from app.services.errors import Conflict, Invalid, NotFound
from app.services.storage import BytesLRU

router = APIRouter(prefix="/api/files", tags=["files"])

PAGE_DPI = 110
_render_slots = asyncio.Semaphore(2)

Short = Annotated[str, StringConstraints(max_length=200)]


class RecipientIn(BaseModel):
    role: Annotated[str, StringConstraints(max_length=40)]
    name: Short
    email: Short


class FileBody(BaseModel):
    title: Annotated[str, StringConstraints(max_length=200)] | None = None
    values: dict[str, str | bool] = Field(default_factory=dict, max_length=2000)
    recipients: list[RecipientIn] = Field(max_length=10)


class NewFile(FileBody):
    template_id: Annotated[str, StringConstraints(max_length=40)]


class FileEdit(FileBody):
    revision: int


class Revision(BaseModel):
    revision: int


class RecipientEdit(BaseModel):
    name: Short | None = None
    email: Short | None = None
    resend: bool = False


class VoidBody(BaseModel):
    reason: Annotated[str, StringConstraints(max_length=1000)]


def _errors(exc: Exception) -> HTTPException:
    if isinstance(exc, NotFound):
        return HTTPException(status_code=404, detail="File not found.")
    if isinstance(exc, Conflict):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, Invalid):
        return HTTPException(
            status_code=422, detail={"message": "Fix these first:", "problems": exc.problems}
        )
    raise exc


def _body(b: FileBody) -> svc.FileIn:
    return svc.FileIn(
        title=b.title,
        values=b.values,
        recipients=[svc.RecipientIn(r.role, r.name, r.email) for r in b.recipients],
    )


async def _detail(tenant: TenantDep, f: dict[str, Any]) -> dict[str, Any]:
    return svc.detail(f, await events.for_file(tenant, f["_id"]))


@router.post("", status_code=201)
async def create_file(body: NewFile, user: UserDep, tenant: TenantDep) -> dict[str, Any]:
    try:
        f = await svc.create(tenant, user, body.template_id, _body(body))
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="Agreement not found.") from exc
    except (Conflict, Invalid) as exc:
        raise _errors(exc) from exc
    return await _detail(tenant, f)


@router.get("")
async def list_files(tenant: TenantDep) -> list[dict[str, Any]]:
    docs = await tenant.find(
        "files",
        sort=[("updated_at", -1)],
        projection={"template.fields": 0, "template.pages": 0, "values": 0},
    )
    return [svc.summary(d) for d in docs]


@router.get("/{file_id}")
async def get_file(file_id: str, tenant: TenantDep) -> dict[str, Any]:
    try:
        f = await svc.get(tenant, file_id)
    except NotFound as exc:
        raise _errors(exc) from exc
    return await _detail(tenant, f)


@router.put("/{file_id}")
async def edit_file(file_id: str, body: FileEdit, tenant: TenantDep) -> dict[str, Any]:
    try:
        f = await svc.update(tenant, file_id, body.revision, _body(body))
    except (NotFound, Conflict, Invalid) as exc:
        raise _errors(exc) from exc
    return await _detail(tenant, f)


@router.delete("/{file_id}", status_code=204)
async def delete_file(file_id: str, request: Request, tenant: TenantDep) -> None:
    try:
        await svc.delete_draft(tenant, request.app.state.db.blobs, file_id)
    except (NotFound, Conflict) as exc:
        raise _errors(exc) from exc


@router.post("/{file_id}/send")
async def send_file(
    file_id: str, body: Revision, request: Request, user: UserDep, tenant: TenantDep
) -> dict[str, Any]:
    try:
        f = await svc.send(tenant, request.app.state.db.blobs, user, file_id, body.revision)
    except (NotFound, Conflict) as exc:
        raise _errors(exc) from exc
    return await _detail(tenant, f)


@router.post("/{file_id}/recipients/{recipient_id}/link")
async def copy_link(
    file_id: str, recipient_id: str, request: Request, user: UserDep, tenant: TenantDep
) -> dict[str, str]:
    try:
        url = await svc.copy_link(tenant, request.app.state.settings, user, file_id, recipient_id)
    except (NotFound, Conflict) as exc:
        raise _errors(exc) from exc
    return {"url": url}


@router.post("/{file_id}/recipients/{recipient_id}/sign-now")
async def sign_now(
    file_id: str, recipient_id: str, request: Request, user: UserDep, tenant: TenantDep
) -> dict[str, str]:
    """For the logged-in person who is this file's current signer: their signing page, without
    going through their email."""
    try:
        url = await svc.sign_now(tenant, request.app.state.settings, user, file_id, recipient_id)
    except (NotFound, Conflict) as exc:
        raise _errors(exc) from exc
    return {"url": url}


@router.patch("/{file_id}/recipients/{recipient_id}")
async def edit_recipient(
    file_id: str, recipient_id: str, body: RecipientEdit, user: UserDep, tenant: TenantDep
) -> dict[str, Any]:
    try:
        f = await svc.update_recipient(
            tenant,
            user,
            file_id,
            recipient_id,
            name=body.name,
            email=body.email,
            resend=body.resend,
        )
    except (NotFound, Conflict, Invalid) as exc:
        raise _errors(exc) from exc
    return await _detail(tenant, f)


@router.post("/{file_id}/void")
async def void_file(
    file_id: str, body: VoidBody, user: UserDep, tenant: TenantDep
) -> dict[str, Any]:
    try:
        f = await svc.void(tenant, user, file_id, body.reason)
    except (NotFound, Conflict, Invalid) as exc:
        raise _errors(exc) from exc
    return await _detail(tenant, f)


async def _pdf(request: Request, tenant: TenantDep, file_id: str) -> tuple[dict[str, Any], bytes]:
    """The draft's preview (exactly what will be sent), or the document as it stands."""
    bucket = request.app.state.db.blobs
    try:
        f = await svc.get(tenant, file_id)
        f = await svc.prepare(tenant, bucket, f)
    except (NotFound, Conflict) as exc:
        raise _errors(exc) from exc
    return f, await signing.current_pdf(bucket, request.app.state.page_cache, f)


@router.get("/{file_id}/preview.pdf")
async def preview_pdf(file_id: str, request: Request, tenant: TenantDep) -> Response:
    f, pdf = await _pdf(request, tenant, file_id)
    return Response(
        pdf,
        media_type="application/pdf",
        headers={
            "content-disposition": f'inline; filename="{finalize.download_name(f, "")}"',
            "cache-control": "private, no-cache",
        },
    )


async def signed_pdf_response(request: Request, f: dict[str, Any]) -> Response:
    """The signed PDF with its certificate, exactly as stored (its SHA-256 is on record)."""
    if not f.get("final_blob_id"):
        raise HTTPException(status_code=409, detail="The signed PDF isn't ready yet.")
    pdf = await storage.get(request.app.state.db.blobs, f["final_blob_id"])
    return Response(
        pdf,
        media_type="application/pdf",
        headers={
            "content-disposition": f'attachment; filename="{finalize.download_name(f)}"',
            "cache-control": "private, max-age=3600",
        },
    )


@router.get("/{file_id}/signed.pdf")
async def signed_pdf(file_id: str, request: Request, tenant: TenantDep) -> Response:
    try:
        f = await svc.get(tenant, file_id)
    except NotFound as exc:
        raise _errors(exc) from exc
    return await signed_pdf_response(request, f)


@router.post("/{file_id}/finalize")
async def retry_finalize(file_id: str, user: UserDep, tenant: TenantDep) -> dict[str, Any]:
    """Try again, after making the signed PDF failed."""
    try:
        f = await finalize.retry(tenant, events.by_user(user), file_id)
    except (NotFound, Conflict) as exc:
        raise _errors(exc) from exc
    return await _detail(tenant, f)


@router.get("/{file_id}/pages/{page}.png")
async def page_image(file_id: str, page: int, request: Request, tenant: TenantDep) -> Response:
    f, pdf = await _pdf(request, tenant, file_id)
    if not 1 <= page <= f["template"]["page_count"]:
        raise HTTPException(status_code=404, detail="Page not found.")
    png = await render_cached(request, ("file", f["_id"], signing.page_rev(f), page), pdf, page)
    # The URL carries ?v=<page_rev>, which changes whenever the document does.
    return Response(png, media_type="image/png", headers={"cache-control": "private, max-age=3600"})


async def render_cached(request: Request, key: tuple[object, ...], pdf: bytes, page: int) -> bytes:
    cache: BytesLRU = request.app.state.page_cache
    png = cache.get(("png", *key))
    if png is None:
        async with _render_slots:
            png = await asyncio.to_thread(pdf_engine.render_page_png, pdf, page, dpi=PAGE_DPI)
        cache.put(("png", *key), png)
    return png
