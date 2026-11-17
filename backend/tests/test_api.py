"""The public API (API keys, /api/v1) and outbound webhooks."""

import hashlib
import hmac
import json
from typing import Any

import httpx
import pytest
from bson import ObjectId
from fastapi import FastAPI

from app.services import webhooks
from app.services.errors import Invalid
from app.worker.handlers import build_registry
from app.worker.queue import Worker, utcnow
from tests.conftest import signup
from tests.test_files import approved, last_link, people, sender_values, sign


async def new_key(client: httpx.AsyncClient, name: str = "CRM") -> str:
    r = await client.post("/api/settings/api-keys", json={"name": name})
    assert r.status_code == 201, r.text
    return str(r.json()["key"])


def bearer(key: str) -> dict[str, str]:
    return {"authorization": f"Bearer {key}"}


def file_body(t: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "template_id": t["id"],
        "values": sender_values(t),
        "signers": people(t),
        **extra,
    }


async def test_api_keys_are_shown_once_stored_hashed_and_revocable(
    app: FastAPI, client: httpx.AsyncClient, second_client: httpx.AsyncClient, db: Any
) -> None:
    assert (await second_client.get("/api/settings/api-keys")).status_code == 401
    await signup(client)
    key = await new_key(client)
    assert key.startswith("kk_live_") and len(key) > 40
    [listed] = (await client.get("/api/settings/api-keys")).json()
    assert listed["prefix"] == key[:12] and "key" not in listed and "key_hash" not in listed
    stored = await db.api_keys.find_one({})
    assert stored["key_hash"] == hashlib.sha256(key.encode()).hexdigest()
    assert key not in json.dumps(stored, default=str)

    api = second_client  # no session: the key alone is the credential
    assert (await api.get("/api/v1/templates", headers=bearer(key))).status_code == 200
    assert (await api.get("/api/v1/templates")).status_code == 401
    assert (await api.get("/api/v1/templates", headers=bearer(key + "x"))).status_code == 401
    assert (await client.get("/api/v1/templates")).status_code == 401  # a session isn't enough

    r = await client.delete(f"/api/settings/api-keys/{listed['id']}")
    assert r.status_code == 200 and r.json()["revoked_at"]
    assert (await api.get("/api/v1/templates", headers=bearer(key))).status_code == 401
    assert (await client.delete(f"/api/settings/api-keys/{listed['id']}")).status_code == 409


async def test_a_file_opened_through_the_api_waits_for_a_person_to_send_it(
    app: FastAPI, client: httpx.AsyncClient, second_client: httpx.AsyncClient
) -> None:
    await signup(client)
    t = await approved(app, client)
    key = await new_key(client, "Practice manager")
    api = second_client

    [listed] = (await api.get("/api/v1/templates", headers=bearer(key))).json()
    assert listed["id"] == t["id"]
    assert [s["role"] for s in listed["signers"]] == [p["role"] for p in people(t)]
    assert {f["key"] for f in listed["fields"]} == {i["key"] for i in t["sender_inputs"]}

    headers = {**bearer(key), "idempotency-key": "matter-1042"}
    body = file_body(t, title="Matter 1042", metadata={"matter_id": "M-1042"})
    r = await api.post("/api/v1/files", json=body, headers=headers)
    assert r.status_code == 201, r.text
    f = r.json()
    assert f["status"] == "draft" and f["created_via"] == "api"
    assert f["metadata"] == {"matter_id": "M-1042"} and f["signed_pdf"] is None
    assert f["url"].endswith(f"/files/{f['id']}")
    again = await api.post("/api/v1/files", json=body, headers=headers)
    assert again.status_code == 200 and again.json()["id"] == f["id"]
    assert len((await client.get("/api/files")).json()) == 1

    # The firm sees it in the app, marked as from the API, and sends it after the preview.
    detail = (await client.get(f"/api/files/{f['id']}")).json()
    assert detail["created_via"] == "api" and detail["api"]["key_name"] == "Practice manager"
    sent = await client.post(f"/api/files/{f['id']}/send", json={"revision": detail["revision"]})
    assert sent.status_code == 200, sent.text
    got = (await api.get(f"/api/v1/files/{f['id']}", headers=bearer(key))).json()
    assert got["status"] == "sent" and got["sent_at"]
    pdf = await api.get(f"/api/v1/files/{f['id']}/signed.pdf", headers=bearer(key))
    assert pdf.status_code == 409  # not signed yet

    bad = await api.post("/api/v1/files", json={**body, "signers": []}, headers=bearer(key))
    assert bad.status_code == 422 and bad.json()["detail"]["problems"]
    missing = {**body, "template_id": "6afc10264451c389aa140678"}
    assert (await api.post("/api/v1/files", json=missing, headers=bearer(key))).status_code == 404


async def test_another_companys_key_sees_nothing(
    app: FastAPI, client: httpx.AsyncClient, second_client: httpx.AsyncClient
) -> None:
    await signup(client)
    t = await approved(app, client)
    key_a = await new_key(client)
    f = (await client.post("/api/v1/files", json=file_body(t), headers=bearer(key_a))).json()
    await signup(second_client, email="other@example.com", org_name="Other Test Co")
    key_b = await new_key(second_client)
    assert (await second_client.get("/api/v1/templates", headers=bearer(key_b))).json() == []
    for path in (f"/api/v1/files/{f['id']}", f"/api/v1/files/{f['id']}/signed.pdf"):
        assert (await second_client.get(path, headers=bearer(key_b))).status_code == 404
    r = await second_client.post("/api/v1/files", json=file_body(t), headers=bearer(key_b))
    assert r.status_code == 404


class Receiver:
    """A stand-in for the firm's webhook endpoint: records requests, answers with `codes`."""

    def __init__(self, *codes: int) -> None:
        self.codes = list(codes)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.codes.pop(0) if self.codes else 200)

    def events(self) -> list[dict[str, Any]]:
        return [json.loads(r.content) for r in self.requests]


def hook_worker(app: FastAPI, receiver: Receiver) -> Worker:
    http = httpx.AsyncClient(transport=httpx.MockTransport(receiver))
    services = {**app.state.services, "webhook_http": http}
    return Worker(app.state.db.db, build_registry(), services=services)


@pytest.fixture
def public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    async def resolve(host: str, port: int) -> list[str]:
        return ["93.184.215.14"]

    monkeypatch.setattr(webhooks, "_resolve", resolve)


async def test_webhooks_are_signed_retried_and_logged(
    app: FastAPI,
    client: httpx.AsyncClient,
    second_client: httpx.AsyncClient,
    outbox: Any,
    db: Any,
    public_dns: None,
) -> None:
    await signup(client)
    t = await approved(app, client)
    key = await new_key(client)
    r = await client.put("/api/settings/webhook", json={"url": "https://hooks.example.com/k"})
    assert r.status_code == 200, r.text
    secret = r.json()["endpoint"]["secret"]
    assert secret.startswith("whsec_") and "file.completed" in r.json()["events"]

    f = (
        await second_client.post(
            "/api/v1/files",
            json=file_body(t, metadata={"matter_id": "M-7"}),
            headers=bearer(key),
        )
    ).json()
    receiver = Receiver(500)  # the first delivery fails, then the endpoint recovers
    w = hook_worker(app, receiver)
    await w.drain()
    assert receiver.requests == []  # opening a draft announces nothing

    detail = (await client.get(f"/api/files/{f['id']}")).json()
    await client.post(f"/api/files/{f['id']}/send", json={"revision": detail["revision"]})
    await w.drain()
    [failed] = (await client.get("/api/settings/webhook/deliveries")).json()
    assert failed["event_type"] == "file.sent" and failed["status"] == "pending"
    assert failed["status_code"] == 500
    assert failed["last_error"] == "The endpoint answered HTTP 500."
    await db.jobs.update_many({"type": webhooks.DELIVER}, {"$set": {"run_at": utcnow()}})
    await w.drain()  # the retry
    assert len(receiver.requests) == 2

    for recipient in detail["recipients"]:
        r = await sign(client, last_link(outbox, recipient["email"]))
        assert r.status_code == 200, r.text
        await w.drain()  # the next signer's email, then (after the last) the signed PDF

    kinds = [e["type"] for e in receiver.events()]
    assert kinds == ["file.sent", "file.sent", "signer.signed", "signer.signed", "file.completed"]
    for request in receiver.requests:
        parts = dict(p.split("=", 1) for p in request.headers["x-kickoff-signature"].split(","))
        stamp, sig = parts["t"], parts["v1"]
        expected = hmac.new(
            secret.encode(), stamp.encode() + b"." + request.content, hashlib.sha256
        ).hexdigest()
        assert hmac.compare_digest(sig, expected)
        assert request.headers["x-kickoff-event"] == json.loads(request.content)["type"]
    first_signed = receiver.events()[2]
    assert first_signed["data"]["signer"]["email"] == detail["recipients"][0]["email"]
    done = receiver.events()[-1]["data"]["file"]
    assert done["status"] == "completed" and done["metadata"] == {"matter_id": "M-7"}
    assert done["signed_pdf"]["url"].endswith(f"/api/v1/files/{f['id']}/signed.pdf")

    pdf = await second_client.get(f"/api/v1/files/{f['id']}/signed.pdf", headers=bearer(key))
    assert pdf.status_code == 200
    assert hashlib.sha256(pdf.content).hexdigest() == done["signed_pdf"]["sha256"]

    log = (await client.get("/api/settings/webhook/deliveries")).json()
    assert len(log) == 4 and all(d["status"] == "done" for d in log)
    retry = await client.post(f"/api/settings/webhook/deliveries/{log[0]['id']}/retry")
    assert retry.status_code == 409  # only a delivery that gave up can be retried

    # Test event; and a delivery that gave up comes back with Retry.
    assert (await client.post("/api/settings/webhook/test")).status_code == 202
    await w.drain()
    assert receiver.events()[-1]["type"] == "ping"
    newest = (await client.get("/api/settings/webhook/deliveries")).json()[0]
    await db.jobs.update_one({"_id": ObjectId(newest["id"])}, {"$set": {"status": "dead"}})
    retry = await client.post(f"/api/settings/webhook/deliveries/{newest['id']}/retry")
    assert retry.status_code == 200
    await w.drain()
    assert receiver.events()[-1]["type"] == "ping"


async def test_no_endpoint_means_no_deliveries(
    app: FastAPI, client: httpx.AsyncClient, db: Any
) -> None:
    await signup(client)
    t = await approved(app, client)
    r = await client.post(
        "/api/files",
        json={"template_id": t["id"], "values": sender_values(t), "recipients": people(t)},
    )
    f = r.json()
    await client.post(f"/api/files/{f['id']}/send", json={"revision": f["revision"]})
    assert await db.jobs.count_documents({"type": webhooks.DELIVER}) == 0
    assert (await client.post("/api/settings/webhook/test")).status_code == 422


async def test_webhook_address_must_be_public(monkeypatch: pytest.MonkeyPatch) -> None:
    answers = {"hooks.example.com": ["93.184.215.14"], "localhost": ["127.0.0.1"]}

    async def resolve(host: str, port: int) -> list[str]:
        return answers.get(host, [host])

    monkeypatch.setattr(webhooks, "_resolve", resolve)
    ok = await webhooks.check_url(" https://hooks.example.com/k ", allow_private=False)
    assert ok == "https://hooks.example.com/k"
    for bad in (
        "http://hooks.example.com/k",  # not https
        "https://localhost/k",
        "https://10.0.0.5/k",
        "https://169.254.169.254/latest/meta-data",  # cloud metadata
        "https://user:pw@hooks.example.com/k",
        "hooks.example.com/k",
    ):
        with pytest.raises(Invalid):
            await webhooks.check_url(bad, allow_private=False)
    # Outside production, a local receiver is allowed (for development).
    assert await webhooks.check_url("http://localhost:9000/k", allow_private=True)
