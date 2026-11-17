"""Branding: the firm's logo and colour (Settings), and the public logo the emails and signing
page show."""

from typing import Annotated, Any

from fastapi import APIRouter, File, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, StringConstraints

from app.api.deps import UserDep
from app.services import branding as svc
from app.services.errors import Invalid

router = APIRouter(prefix="/api", tags=["branding"])


class ColorBody(BaseModel):
    color: Annotated[str, StringConstraints(max_length=20)] | None = None


def _invalid(exc: Invalid) -> HTTPException:
    return HTTPException(
        status_code=422, detail={"message": exc.problems[0], "problems": exc.problems}
    )


async def _current(request: Request, user: UserDep) -> dict[str, Any]:
    return svc.public(await svc.load(request.app.state.db.db, user.org_id))


@router.get("/settings/branding")
async def get_branding(request: Request, user: UserDep) -> dict[str, Any]:
    return await _current(request, user)


@router.post("/settings/branding/logo")
async def upload_logo(
    request: Request, user: UserDep, file: Annotated[UploadFile, File()]
) -> dict[str, Any]:
    data = await file.read(svc.MAX_LOGO + 1)
    try:
        await svc.set_logo(request.app.state.db.db, request.app.state.db.blobs, user.org_id, data)
    except Invalid as exc:
        raise _invalid(exc) from exc
    return await _current(request, user)


@router.delete("/settings/branding/logo")
async def remove_logo(request: Request, user: UserDep) -> dict[str, Any]:
    await svc.remove_logo(request.app.state.db.db, request.app.state.db.blobs, user.org_id)
    return await _current(request, user)


@router.put("/settings/branding")
async def set_color(body: ColorBody, request: Request, user: UserDep) -> dict[str, Any]:
    try:
        await svc.set_color(request.app.state.db.db, user.org_id, body.color)
    except Invalid as exc:
        raise _invalid(exc) from exc
    return await _current(request, user)


@router.get("/branding/{org_id}/logo/{blob_id}")
async def logo(org_id: str, blob_id: str, request: Request) -> Response:
    """Public: signers' email clients and the signing page load it. The URL changes with every
    new logo, so it can be cached for a year."""
    found = await svc.logo_file(
        request.app.state.db.db, request.app.state.db.blobs, org_id, blob_id
    )
    if found is None:
        raise HTTPException(status_code=404, detail="Logo not found.")
    data, mime = found
    return Response(
        data,
        media_type=mime,
        headers={
            "cache-control": "public, max-age=31536000, immutable",
            "x-content-type-options": "nosniff",
        },
    )
