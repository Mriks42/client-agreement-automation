"""Outbound webhooks: tell the firm's other systems when a file moves on (DECISIONS.md D4).

One endpoint per firm. Each audit event worth knowing about (sent, signed, declined, voided,
expired, and completed once the signed PDF exists) queues one delivery job, so a slow or broken
endpoint never holds up signing. The job POSTs JSON signed with the endpoint's secret:

    X-Kickoff-Signature: t=<unix seconds>,v1=<hex HMAC-SHA256 of "<t>.<raw body>">

and retries with backoff (8 attempts, over about an hour and a half) until the endpoint answers
2xx. `data.file` is the file as it is when the delivery is made (the same shape as
GET /api/v1/files/{id}); `created_at` says when the event happened.

The endpoint must be a public https:// address. Its host is resolved when it's saved and again
before every delivery, and private, loopback and link-local addresses are refused, so the server
can't be pointed at itself or its neighbours. Redirects aren't followed.
"""

import asyncio
import hashlib
import hmac
import ipaddress
import json
import secrets
import socket
import time
from typing import Any
from urllib.parse import urlsplit

import httpx
from bson import ObjectId

from app.config import Settings
from app.repo import Tenant
from app.services.api_view import file_view, iso
from app.services.errors import Invalid
from app.worker.queue import JobContext, PermanentJobError, enqueue, utcnow

DELIVER = "deliver_webhook"
MAX_ATTEMPTS = 8
TIMEOUT_S = 10.0
EVENTS = {  # audit event type -> webhook event type
    "sent": "file.sent",
    "signed": "signer.signed",
    "declined": "file.declined",
    "voided": "file.voided",
    "expired": "file.expired",
    "finalized": "file.completed",
}
PING = "ping"


class WebhookFailed(Exception):
    """The endpoint didn't accept the delivery; the job retries."""


# ---- the endpoint -----------------------------------------------------------------------------
async def _resolve(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


async def check_url(url: str, *, allow_private: bool) -> str:
    """The URL, cleaned, or Invalid with a reason a person can act on."""
    url = url.strip()
    parts = urlsplit(url)
    schemes = ("https", "http") if allow_private else ("https",)
    if parts.scheme not in schemes or not parts.hostname:
        raise Invalid(["Use a full https:// address, like https://example.com/hooks/kickoff."])
    if len(url) > 500:
        raise Invalid(["That address is too long (500 characters at most)."])
    if parts.username or parts.password:
        raise Invalid(["Leave the user name and password out of the address."])
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
        addresses = await _resolve(parts.hostname, port)
    except (OSError, ValueError) as exc:
        raise Invalid([f"We couldn't find {parts.hostname}. Check the address."]) from exc
    if not allow_private:
        for a in addresses:
            if not ipaddress.ip_address(a.split("%")[0]).is_global:
                raise Invalid(
                    ["That address is on a private network. Use one the internet can reach."]
                )
    return url


def new_secret() -> str:
    return "whsec_" + secrets.token_urlsafe(32)


def sign(secret: str, timestamp: str, body: bytes) -> str:
    mac = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256)
    return f"t={timestamp},v1={mac.hexdigest()}"


async def get_endpoint(tenant: Tenant) -> dict[str, Any] | None:
    found = await tenant.find("webhook_endpoints", limit=1)
    return found[0] if found else None


async def save_endpoint(
    tenant: Tenant, settings: Settings, url: str, *, rotate_secret: bool = False
) -> dict[str, Any]:
    url = await check_url(url, allow_private=not settings.is_prod)
    now = utcnow()
    current = await get_endpoint(tenant)
    if current is None:
        doc = {"url": url, "secret": new_secret(), "created_at": now, "updated_at": now}
        doc["_id"] = await tenant.insert("webhook_endpoints", doc)
        doc["org_id"] = tenant.org_id
        return doc
    update: dict[str, Any] = {"url": url, "updated_at": now}
    if rotate_secret:
        update["secret"] = new_secret()
    updated = await tenant.transition("webhook_endpoints", current["_id"], {}, {"$set": update})
    return updated or current


async def delete_endpoint(tenant: Tenant) -> None:
    current = await get_endpoint(tenant)
    if current is not None:
        await tenant.delete("webhook_endpoints", current["_id"], {})


def endpoint_public(doc: dict[str, Any] | None) -> dict[str, Any] | None:
    if doc is None:
        return None
    return {
        "url": doc["url"],
        "secret": doc["secret"],
        "created_at": doc["created_at"],
        "updated_at": doc["updated_at"],
    }


# ---- queueing deliveries ------------------------------------------------------------------------
async def on_event(
    tenant: Tenant, file_id: ObjectId, event_id: ObjectId, event: dict[str, Any]
) -> None:
    """Called for every audit event recorded; queues a delivery if the firm has an endpoint."""
    kind = EVENTS.get(event["type"])
    if kind is None:
        return
    endpoint = await get_endpoint(tenant)
    if endpoint is None:
        return
    body: dict[str, Any] = {
        "id": str(event_id),
        "type": kind,
        "created_at": iso(event["at"]),
        "file_id": str(file_id),
    }
    if event.get("recipient"):
        body["signer"] = event["recipient"]
    if (event.get("data") or {}).get("reason"):
        body["reason"] = event["data"]["reason"]
    await _queue(tenant, endpoint, body, dedupe_key=f"{DELIVER}:{event_id}:{endpoint['_id']}")


async def send_test(tenant: Tenant) -> None:
    endpoint = await get_endpoint(tenant)
    if endpoint is None:
        raise Invalid(["Save an endpoint address first."])
    event_id = ObjectId()
    body = {"id": str(event_id), "type": PING, "created_at": iso(utcnow())}
    await _queue(tenant, endpoint, body, dedupe_key=None)


async def _queue(
    tenant: Tenant, endpoint: dict[str, Any], body: dict[str, Any], *, dedupe_key: str | None
) -> None:
    await enqueue(
        tenant.db,
        DELIVER,
        {"org_id": str(tenant.org_id), "endpoint_id": str(endpoint["_id"]), "event": body},
        dedupe_key=dedupe_key,
        max_attempts=MAX_ATTEMPTS,
        org_id=str(tenant.org_id),
    )


async def deliveries(tenant: Tenant, limit: int = 25) -> list[dict[str, Any]]:
    """The delivery log: the most recent delivery jobs for this firm (jobs carry org_id)."""
    cursor = tenant.db.jobs.find(
        {"type": DELIVER, "org_id": str(tenant.org_id)},
        sort=[("created_at", -1)],
        limit=limit,
    )
    out = []
    async for job in cursor:
        event = job["payload"]["event"]
        out.append(
            {
                "id": str(job["_id"]),
                "event_id": event["id"],
                "event_type": event["type"],
                "file_id": event.get("file_id"),
                "status": job["status"],
                "attempts": job.get("attempts", 0),
                "status_code": (job.get("result") or {}).get("status_code"),
                "last_error": job.get("last_error"),
                "created_at": job["created_at"],
                "updated_at": job["updated_at"],
                "next_attempt_at": job["run_at"] if job["status"] == "pending" else None,
            }
        )
    return out


# ---- the delivery job ---------------------------------------------------------------------------
def _encode(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return iso(value)
    if isinstance(value, ObjectId):
        return str(value)
    raise TypeError(type(value).__name__)


async def handle_deliver(ctx: JobContext, payload: dict[str, Any]) -> None:
    if (ctx.job.get("result") or {}).get("delivered"):
        return  # an earlier run delivered it; don't send it twice
    settings: Settings = ctx.services["settings"]
    tenant = Tenant(ctx.db, ObjectId(payload["org_id"]))
    endpoint = await tenant.get("webhook_endpoints", payload["endpoint_id"])
    if endpoint is None:
        return  # the endpoint was removed (or replaced) since: nothing to deliver to
    event = payload["event"]
    data: dict[str, Any] = {}
    if event["type"] == PING:
        data["message"] = "Test event from Kickoff. Your endpoint is set up."
    else:
        file = await tenant.get("files", event["file_id"])
        if file is None:
            return
        data["file"] = file_view(file, settings.app_base_url)
        for k in ("signer", "reason"):
            if k in event:
                data[k] = event[k]
    body = {"id": event["id"], "type": event["type"], "created_at": event["created_at"]}
    raw = json.dumps({**body, "data": data}, default=_encode, separators=(",", ":")).encode()
    try:
        url = await check_url(endpoint["url"], allow_private=not settings.is_prod)
    except Invalid as exc:
        raise PermanentJobError(" ".join(exc.problems)) from exc
    timestamp = str(int(time.time()))
    headers = {
        "content-type": "application/json",
        "user-agent": "Kickoff-Webhooks/1",
        "x-kickoff-event": event["type"],
        "x-kickoff-delivery": event["id"],
        "x-kickoff-signature": sign(endpoint["secret"], timestamp, raw),
    }
    client: httpx.AsyncClient | None = ctx.services.get("webhook_http")
    try:
        if client is not None:
            resp = await client.post(url, content=raw, headers=headers, timeout=TIMEOUT_S)
        else:
            async with httpx.AsyncClient(follow_redirects=False) as own:
                resp = await own.post(url, content=raw, headers=headers, timeout=TIMEOUT_S)
    except httpx.TimeoutException as exc:
        raise WebhookFailed(f"No answer within {TIMEOUT_S:.0f} s.") from exc
    except httpx.HTTPError as exc:
        raise WebhookFailed(f"Couldn't connect ({type(exc).__name__}).") from exc
    ok = 200 <= resp.status_code < 300
    await ctx.db.jobs.update_one(
        {"_id": ctx.job["_id"]},
        {"$set": {"result": {"status_code": resp.status_code, "delivered": ok}}},
    )
    if not ok:
        raise WebhookFailed(f"The endpoint answered HTTP {resp.status_code}.")
