"""Saved clients: search them, and get what picking one fills on a new file. They're saved
automatically when a file is sent (services/clients.py)."""

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import TenantDep
from app.services import clients as svc
from app.services.errors import NotFound

router = APIRouter(prefix="/api/clients", tags=["clients"])


@router.get("")
async def list_clients(
    tenant: TenantDep,
    q: Annotated[str, Query(max_length=100)] = "",
    limit: Annotated[int, Query(ge=1, le=50)] = 8,
) -> list[dict[str, Any]]:
    return await svc.search(tenant, q, limit)


@router.get("/{client_id}/fill")
async def fill(
    client_id: str, tenant: TenantDep, template_id: Annotated[str, Query(max_length=40)]
) -> dict[str, Any]:
    try:
        return await svc.fill(tenant, client_id, template_id)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="Client not found.") from exc


@router.delete("/{client_id}", status_code=204)
async def delete_client(client_id: str, tenant: TenantDep) -> None:
    try:
        await svc.delete(tenant, client_id)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="Client not found.") from exc
