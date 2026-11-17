"""The public API: the firm's own systems (a CRM, practice management, Zapier) open files and check
on them with an API key (DECISIONS.md D4). Webhooks tell them what happens next.

A file opened here is a draft. A person at the firm checks its preview and sends it, as for any
file (CLAUDE.md "Humans approve AI output"); the response's `url` is where they do that.
Interactive reference: /api/docs.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field, StringConstraints
from pymongo.errors import DuplicateKeyError

from app.api.deps import ApiCallerDep
from app.api.files import signed_pdf_response
from app.domain.fields import sender_inputs
from app.services import files as svc
from app.services.api_view import file_view
from app.services.errors import Conflict, Invalid, NotFound

router = APIRouter(prefix="/api/v1", tags=["public API"])

Short = Annotated[str, StringConstraints(max_length=200)]
MAX_METADATA = 20


class SignerIn(BaseModel):
    role: Annotated[str, StringConstraints(max_length=40)] = Field(
        description="The signer's role key, from GET /api/v1/templates"
    )
    name: Short
    email: Short


class NewFileIn(BaseModel):
    template_id: Annotated[str, StringConstraints(max_length=40)] = Field(
        description="An approved agreement, from GET /api/v1/templates"
    )
    title: Annotated[str, StringConstraints(max_length=200)] | None = None
    values: dict[str, str | bool] = Field(
        default_factory=dict,
        max_length=2000,
        description="The details the firm fills in, by field key (dates as YYYY-MM-DD)",
    )
    signers: list[SignerIn] = Field(max_length=10, description="One per role")
    metadata: dict[str, Annotated[str, StringConstraints(max_length=500)]] = Field(
        default_factory=dict,
        description="Your own references (e.g. a matter ID), returned in every webhook",
    )


def _base(request: Request) -> str:
    return str(request.app.state.settings.app_base_url)


@router.get("/templates")
async def list_agreements(caller: ApiCallerDep) -> list[dict[str, Any]]:
    """Approved agreements, with the signer roles and the fields a new file can fill in."""
    docs = await caller.tenant.find(
        "templates", {"status": "active"}, sort=[("updated_at", -1)], limit=100
    )
    return [
        {
            "id": str(t["_id"]),
            "name": t["name"],
            "version": t["version"],
            "signers": [
                {"role": r["key"], "label": r["label"], "order": r["order"]}
                for r in sorted(t["roles"], key=lambda r: r["order"])
            ],
            "fields": [
                {k: i[k] for k in ("key", "label", "type", "required")}
                for i in sender_inputs(t["fields"])
            ],
        }
        for t in docs
    ]


@router.post("/files", status_code=201)
async def open_file(
    body: NewFileIn,
    caller: ApiCallerDep,
    request: Request,
    response: Response,
    idempotency_key: Annotated[str | None, Header(max_length=200)] = None,
) -> dict[str, Any]:
    """Open a file as a draft for a person at the firm to check and send. Send an
    Idempotency-Key header to make retries safe: the same key returns the same file."""
    tenant = caller.tenant
    if idempotency_key:
        found = await tenant.find("files", {"api.idempotency_key": idempotency_key}, limit=1)
        if found:
            response.status_code = 200
            return file_view(found[0], _base(request))
    if len(body.metadata) > MAX_METADATA or any(len(k) > 40 for k in body.metadata):
        raise HTTPException(
            status_code=422,
            detail=f"metadata takes up to {MAX_METADATA} keys of 40 characters at most.",
        )
    api: dict[str, Any] = {
        "key_id": caller.key_id,
        "key_name": caller.key_name,
        "metadata": body.metadata,
    }
    if idempotency_key:
        api["idempotency_key"] = idempotency_key
    file_in = svc.FileIn(
        title=body.title,
        values=body.values,
        recipients=[svc.RecipientIn(s.role, s.name, s.email) for s in body.signers],
    )
    try:
        f = await svc.create(tenant, caller.user, body.template_id, file_in, api=api)
    except DuplicateKeyError:
        # The same Idempotency-Key, sent twice at once: return the file the other request made.
        found = await tenant.find("files", {"api.idempotency_key": idempotency_key}, limit=1)
        if not found:
            raise
        response.status_code = 200
        return file_view(found[0], _base(request))
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="Agreement not found.") from exc
    except Conflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Invalid as exc:
        raise HTTPException(
            status_code=422, detail={"message": "Fix these first:", "problems": exc.problems}
        ) from exc
    return file_view(f, _base(request))


STATUSES = ("draft", "sent", "partially_signed", "completed", "declined", "expired", "voided")


@router.get("/files")
async def list_files(
    caller: ApiCallerDep,
    request: Request,
    status: Annotated[
        str | None,
        Query(
            max_length=200,
            description="Comma-separated statuses, e.g. sent,partially_signed for files "
            "waiting on a signature",
        ),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> list[dict[str, Any]]:
    """Files, most recently updated first."""
    query: dict[str, Any] = {}
    if status:
        wanted = [s.strip() for s in status.split(",") if s.strip()]
        unknown = [s for s in wanted if s not in STATUSES]
        if unknown:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown status {', '.join(unknown)}. Use: {', '.join(STATUSES)}.",
            )
        query["status"] = {"$in": wanted}
    docs = await caller.tenant.find("files", query, sort=[("updated_at", -1)], limit=limit)
    return [file_view(f, _base(request)) for f in docs]


@router.get("/files/{file_id}")
async def get_file(file_id: str, caller: ApiCallerDep, request: Request) -> dict[str, Any]:
    f = await caller.tenant.get("files", file_id)
    if f is None:
        raise HTTPException(status_code=404, detail="File not found.")
    return file_view(f, _base(request))


@router.get("/files/{file_id}/signed.pdf")
async def get_signed_pdf(file_id: str, caller: ApiCallerDep, request: Request) -> Response:
    """The signed agreement with its audit certificate. 409 until it exists: wait for the
    file.completed webhook."""
    f = await caller.tenant.get("files", file_id)
    if f is None:
        raise HTTPException(status_code=404, detail="File not found.")
    return await signed_pdf_response(request, f)
