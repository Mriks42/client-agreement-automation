"""Settings, for people logged in to the app: API keys, and the webhook endpoint with its
delivery log."""

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, StringConstraints

from app.api.deps import TenantDep, UserDep
from app.repo import parse_id
from app.services import apikeys, team, webhooks
from app.services.errors import Conflict, Invalid, NotFound
from app.worker.queue import retry_dead_job

router = APIRouter(prefix="/api/settings", tags=["settings"])


class NewKey(BaseModel):
    name: Annotated[str, StringConstraints(max_length=80)] = ""


class InviteIn(BaseModel):
    email: Annotated[str, StringConstraints(strip_whitespace=True, max_length=254)]
    name: Annotated[str, StringConstraints(max_length=120)] | None = None


class EndpointIn(BaseModel):
    url: Annotated[str, StringConstraints(max_length=600)]
    rotate_secret: bool = False


def _errors(exc: Exception) -> HTTPException:
    if isinstance(exc, NotFound):
        return HTTPException(status_code=404, detail="Not found.")
    if isinstance(exc, Conflict):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, Invalid):
        return HTTPException(
            status_code=422, detail={"message": "Fix this first:", "problems": exc.problems}
        )
    raise exc


# ---- API keys -----------------------------------------------------------------------------------
@router.get("/api-keys")
async def list_keys(tenant: TenantDep) -> list[dict[str, Any]]:
    return [apikeys.public(k) for k in await apikeys.list_keys(tenant)]


@router.post("/api-keys", status_code=201)
async def create_key(body: NewKey, user: UserDep, tenant: TenantDep) -> dict[str, Any]:
    """The key itself is in this response only. It can't be shown again."""
    try:
        raw, doc = await apikeys.create(tenant, user, body.name)
    except Conflict as exc:
        raise _errors(exc) from exc
    return {**apikeys.public(doc), "key": raw}


@router.delete("/api-keys/{key_id}")
async def revoke_key(key_id: str, tenant: TenantDep) -> dict[str, Any]:
    try:
        return apikeys.public(await apikeys.revoke(tenant, key_id))
    except (NotFound, Conflict) as exc:
        raise _errors(exc) from exc


# ---- webhook ------------------------------------------------------------------------------------
@router.get("/webhook")
async def get_webhook(tenant: TenantDep) -> dict[str, Any]:
    return {
        "endpoint": webhooks.endpoint_public(await webhooks.get_endpoint(tenant)),
        "events": sorted(set(webhooks.EVENTS.values())),
    }


@router.put("/webhook")
async def save_webhook(body: EndpointIn, request: Request, tenant: TenantDep) -> dict[str, Any]:
    try:
        doc = await webhooks.save_endpoint(
            tenant, request.app.state.settings, body.url, rotate_secret=body.rotate_secret
        )
    except Invalid as exc:
        raise _errors(exc) from exc
    return {
        "endpoint": webhooks.endpoint_public(doc),
        "events": sorted(set(webhooks.EVENTS.values())),
    }


@router.delete("/webhook", status_code=204)
async def delete_webhook(tenant: TenantDep) -> None:
    await webhooks.delete_endpoint(tenant)


@router.post("/webhook/test", status_code=202)
async def test_webhook(tenant: TenantDep) -> dict[str, Any]:
    try:
        await webhooks.send_test(tenant)
    except Invalid as exc:
        raise _errors(exc) from exc
    return {"queued": True}


@router.get("/webhook/deliveries")
async def list_deliveries(tenant: TenantDep) -> list[dict[str, Any]]:
    return await webhooks.deliveries(tenant)


@router.post("/webhook/deliveries/{job_id}/retry")
async def retry_delivery(job_id: str, tenant: TenantDep) -> dict[str, Any]:
    oid = parse_id(job_id)
    if oid is None or not await retry_dead_job(tenant.db, oid, org_id=str(tenant.org_id)):
        raise HTTPException(status_code=409, detail="Only a delivery that gave up can be retried.")
    return {"queued": True}


# ---- team ---------------------------------------------------------------------------------------
@router.get("/team")
async def get_team(user: UserDep, tenant: TenantDep) -> dict[str, Any]:
    return await team.team(tenant, user)


@router.post("/team/invites", status_code=201)
async def invite(body: InviteIn, user: UserDep, tenant: TenantDep) -> dict[str, Any]:
    """Emails them a link to join. The link isn't returned here: use Copy invite link."""
    try:
        await team.invite(tenant, user, body.email, body.name)
    except (Conflict, Invalid) as exc:
        raise _errors(exc) from exc
    return await team.team(tenant, user)


@router.post("/team/invites/{invite_id}/link")
async def copy_invite_link(invite_id: str, request: Request, tenant: TenantDep) -> dict[str, str]:
    try:
        return {"url": await team.copy_link(tenant, request.app.state.settings, invite_id)}
    except (NotFound, Conflict) as exc:
        raise _errors(exc) from exc


@router.delete("/team/invites/{invite_id}", status_code=204)
async def revoke_invite(invite_id: str, tenant: TenantDep) -> None:
    try:
        await team.revoke(tenant, invite_id)
    except (NotFound, Conflict) as exc:
        raise _errors(exc) from exc


@router.delete("/team/members/{user_id}", status_code=204)
async def remove_member(user_id: str, user: UserDep, tenant: TenantDep) -> None:
    try:
        await team.remove(tenant, user, user_id)
    except (NotFound, Conflict) as exc:
        raise _errors(exc) from exc
