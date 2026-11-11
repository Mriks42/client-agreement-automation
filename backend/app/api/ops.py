"""Operator diagnostics, enabled only when OPS_TOKEN is set.

Render's free tier has no shell, so this is how we prove email delivery from the deployed
service. It sends synchronously (unlike product code, which always enqueues) because the
point is to see the provider's response immediately.
"""

import hmac
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, EmailStr

from app.services.email import EmailError, OutgoingEmail

router = APIRouter(prefix="/api/ops", tags=["ops"])


def require_ops_token(
    request: Request, x_ops_token: Annotated[str | None, Header()] = None
) -> None:
    expected = request.app.state.settings.ops_token
    if expected is None:
        raise HTTPException(status_code=404)
    if not x_ops_token or not hmac.compare_digest(x_ops_token, expected.get_secret_value()):
        raise HTTPException(status_code=401, detail="bad ops token")


class TestEmailIn(BaseModel):
    to: EmailStr


@router.post("/test-email", dependencies=[Depends(require_ops_token)])
async def send_test_email(body: TestEmailIn, request: Request) -> dict[str, str | None]:
    settings = request.app.state.settings
    email = OutgoingEmail(
        to=[body.to],
        subject=f"{settings.email_product_name} delivery test",
        text=f"Delivery test from {settings.app_base_url} (version {settings.git_sha}).",
        html=f"<p>Delivery test from <b>{settings.app_base_url}</b> "
        f"(version {settings.git_sha}).</p>",
        from_name=settings.email_product_name,
    )
    try:
        result = await request.app.state.email.send(email)
    except EmailError as exc:
        raise HTTPException(status_code=502, detail=f"{type(exc).__name__}: {exc}") from exc
    return {"provider": result.provider, "provider_message_id": result.provider_message_id}
