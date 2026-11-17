"""Request dependencies shared by the routers."""

from dataclasses import dataclass
from typing import Annotated

from bson import ObjectId
from fastapi import Depends, HTTPException, Request

from app.repo import Tenant
from app.services import accounts, apikeys
from app.services.accounts import CurrentUser
from app.services.events import ClientInfo


async def require_user(request: Request) -> CurrentUser:
    token = request.cookies.get(accounts.COOKIE_NAME, "")
    user = await accounts.load_session(request.app.state.db.db, request.app.state.settings, token)
    if user is None:
        raise HTTPException(status_code=401, detail="Please log in.")
    return user


UserDep = Annotated[CurrentUser, Depends(require_user)]


def tenant_for(request: Request, user: UserDep) -> Tenant:
    return Tenant(request.app.state.db.db, user.org_id)


TenantDep = Annotated[Tenant, Depends(tenant_for)]


def client_info(request: Request) -> ClientInfo:
    """The signer's address for the audit trail. uvicorn runs with --proxy-headers, so
    request.client is the address Render's proxy saw; the raw X-Forwarded-For is kept too."""
    return ClientInfo(
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        forwarded_for=request.headers.get("x-forwarded-for"),
    )


@dataclass(frozen=True)
class ApiCaller:
    """A request to the public API: the key used, and the person it acts for."""

    key_id: ObjectId
    key_name: str
    user: CurrentUser
    tenant: Tenant


async def require_api_key(request: Request) -> ApiCaller:
    scheme, _, raw = request.headers.get("authorization", "").partition(" ")
    found = None
    if scheme.lower() == "bearer" and raw.strip():
        found = await apikeys.authenticate(request.app.state.db.db, raw.strip())
    if found is None:
        raise HTTPException(
            status_code=401,
            detail="Send a valid API key as 'Authorization: Bearer kk_live_...'. "
            "Create one in Settings.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    key, user = found
    return ApiCaller(key["_id"], key["name"], user, Tenant(request.app.state.db.db, user.org_id))


ApiCallerDep = Annotated[ApiCaller, Depends(require_api_key)]
