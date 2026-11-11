"""Send through the Gmail REST API over HTTPS.

Why not SMTP: Render's free tier blocks outbound SMTP ports (see DECISIONS.md D11).
Auth: a long-lived OAuth refresh token for the dedicated sender account, minted once with
`uv run python -m scripts.gmail_auth`. Access tokens (1h) are cached and refreshed on demand.
"""

import asyncio
import logging
import time

import httpx

from app.services.email.base import (
    EmailPermanentError,
    EmailTransientError,
    OutgoingEmail,
    SendResult,
    build_mime,
)

log = logging.getLogger(__name__)

TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 - endpoint, not a secret
SEND_URL = "https://gmail.googleapis.com/upload/gmail/v1/users/me/messages/send"
# The media-upload endpoint takes raw RFC 822 bytes and allows messages up to 35 MB,
# which matters because completed agreements are sent as PDF attachments.
MAX_MESSAGE_BYTES = 35 * 1024 * 1024


class GmailApiSender:
    name = "gmail_api"

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        sender_address: str,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token
        self.sender_address = sender_address
        self._http = http or httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0))
        self._access_token: str | None = None
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    async def _token(self, force: bool = False) -> str:
        async with self._lock:
            if not force and self._access_token and time.monotonic() < self._expires_at - 60:
                return self._access_token
            try:
                resp = await self._http.post(
                    TOKEN_URL,
                    data={
                        "client_id": self._client_id,
                        "client_secret": self._client_secret,
                        "refresh_token": self._refresh_token,
                        "grant_type": "refresh_token",
                    },
                )
            except httpx.HTTPError as exc:
                raise EmailTransientError(f"token refresh network error: {exc}") from exc
            if resp.status_code >= 500 or resp.status_code == 429:
                raise EmailTransientError(f"token refresh failed: HTTP {resp.status_code}")
            body = resp.json() if resp.content else {}
            if resp.status_code != 200:
                # invalid_grant = refresh token revoked/expired. Needs a human; don't retry.
                raise EmailPermanentError(
                    f"token refresh rejected: {body.get('error', resp.status_code)}"
                )
            self._access_token = body["access_token"]
            self._expires_at = time.monotonic() + float(body.get("expires_in", 3600))
            return self._access_token

    async def check(self) -> None:
        await self._token()

    async def send(self, email: OutgoingEmail) -> SendResult:
        mime, message_id = build_mime(email, self.sender_address)
        raw = mime.as_bytes()
        if len(raw) > MAX_MESSAGE_BYTES:
            raise EmailPermanentError(f"message is {len(raw)} bytes; Gmail limit is 35 MB")

        for attempt in (1, 2):
            token = await self._token(force=attempt == 2)
            try:
                resp = await self._http.post(
                    SEND_URL,
                    params={"uploadType": "media"},
                    content=raw,
                    headers={"Authorization": f"Bearer {token}", "Content-Type": "message/rfc822"},
                )
            except httpx.HTTPError as exc:
                raise EmailTransientError(f"send network error: {exc}") from exc
            if resp.status_code == 401 and attempt == 1:
                continue  # access token expired early; refresh once and retry
            break

        if resp.status_code == 200:
            provider_id = resp.json().get("id")
            log.info(
                "email sent", extra={"provider": self.name, "provider_message_id": provider_id}
            )
            return SendResult(self.name, provider_id, message_id)
        detail = _error_reason(resp)
        if resp.status_code == 429 or resp.status_code >= 500:
            raise EmailTransientError(f"gmail send HTTP {resp.status_code}: {detail}")
        if resp.status_code == 403 and "rateLimitExceeded" in detail:
            raise EmailTransientError(f"gmail rate limited: {detail}")
        raise EmailPermanentError(f"gmail send HTTP {resp.status_code}: {detail}")


def _error_reason(resp: httpx.Response) -> str:
    try:
        err = resp.json().get("error", {})
        reasons = ",".join(e.get("reason", "") for e in err.get("errors", []))
        return f"{err.get('message', '')} [{reasons}]"
    except ValueError:
        return resp.text[:200]
