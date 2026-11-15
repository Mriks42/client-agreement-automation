"""The signer's API. No session: the signing token in the path is the credential (D6), so
paths under /api/sign/ are never logged in full (see main.py)."""

import base64
import binascii
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field, StringConstraints

from app.api.deps import client_info
from app.api.files import render_cached, signed_pdf_response
from app.services import signing as svc
from app.services.errors import Conflict, Invalid

router = APIRouter(prefix="/api/sign", tags=["signing"])

LINK_PROBLEM = (
    "This signing link isn't valid. Use the latest link from your email, "
    "or ask the sender for a new one."
)


class MarkIn(BaseModel):
    kind: Literal["typed", "drawn"]
    text: Annotated[str, StringConstraints(max_length=200)] = ""
    # A data: URL or plain base64 of a PNG.
    png: Annotated[str, StringConstraints(max_length=800_000)] = ""

    def to_mark(self) -> svc.Mark:
        if self.kind == "typed":
            return svc.Mark("typed", text=self.text)
        data = self.png.split(",", 1)[1] if self.png.startswith("data:") else self.png
        try:
            png = base64.b64decode(data, validate=True)
        except (binascii.Error, ValueError):
            png = b""
        return svc.Mark("drawn", png=png)


class SubmitIn(BaseModel):
    values: dict[str, str | bool] = Field(default_factory=dict, max_length=2000)
    signature: MarkIn | None = None
    initials: MarkIn | None = None
    tz: Annotated[str, StringConstraints(max_length=64)] | None = None


class DeclineIn(BaseModel):
    reason: Annotated[str, StringConstraints(max_length=2000)]


async def _signer(request: Request, token: str) -> svc.Signer:
    s = await svc.open_signer(request.app.state.db.db, token)
    if s is None:
        raise HTTPException(status_code=404, detail=LINK_PROBLEM)
    return s


def _errors(exc: Exception) -> HTTPException:
    if isinstance(exc, Conflict):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, Invalid):
        return HTTPException(
            status_code=422, detail={"message": "Almost there:", "problems": exc.problems}
        )
    raise exc


@router.get("/{token}")
async def view(token: str, request: Request) -> dict[str, Any]:
    s = await _signer(request, token)
    return await svc.view(s, client_info(request))


@router.get("/{token}/pages/{page}.png")
async def page_image(token: str, page: int, request: Request) -> Response:
    s = await _signer(request, token)
    f = s.file
    if svc.signer_state(f, s.recipient) not in svc.VIEWABLE or not (
        1 <= page <= f["template"]["page_count"]
    ):
        raise HTTPException(status_code=404, detail="Page not found.")
    pdf = await svc.current_pdf(request.app.state.db.blobs, request.app.state.page_cache, f)
    png = await render_cached(request, ("file", f["_id"], svc.page_rev(f), page), pdf, page)
    return Response(png, media_type="image/png", headers={"cache-control": "private, max-age=3600"})


@router.get("/{token}/signed.pdf")
async def signed_pdf(token: str, request: Request) -> Response:
    s = await _signer(request, token)
    if svc.signer_state(s.file, s.recipient) != "completed":
        raise HTTPException(status_code=404, detail="There's no signed copy for this link.")
    return await signed_pdf_response(request, s.file)


@router.post("/{token}/consent", status_code=204)
async def consent(token: str, request: Request) -> None:
    s = await _signer(request, token)
    try:
        await svc.consent(s, client_info(request))
    except Conflict as exc:
        raise _errors(exc) from exc


@router.post("/{token}/submit")
async def submit(token: str, body: SubmitIn, request: Request) -> dict[str, Any]:
    s = await _signer(request, token)
    sub = svc.Submission(
        values=body.values,
        signature=body.signature.to_mark() if body.signature else None,
        initials=body.initials.to_mark() if body.initials else None,
        tz=body.tz,
    )
    try:
        await svc.submit(s, request.app.state.db.blobs, sub, client_info(request))
    except (Conflict, Invalid) as exc:
        raise _errors(exc) from exc
    return await svc.view(await _signer(request, token), client_info(request))


@router.post("/{token}/decline")
async def decline(token: str, body: DeclineIn, request: Request) -> dict[str, Any]:
    s = await _signer(request, token)
    try:
        await svc.decline(s, request.app.state.settings, body.reason, client_info(request))
    except (Conflict, Invalid) as exc:
        raise _errors(exc) from exc
    return await svc.view(await _signer(request, token), client_info(request))
