import base64
import re
from datetime import timedelta
from typing import Any

import httpx
from fastapi import FastAPI

from app.main import redact_path
from app.services import pdf_engine, signing
from app.services.email import EmailPermanentError, OutgoingEmail, SendResult
from app.worker.handlers import build_registry
from app.worker.queue import Worker, utcnow
from tests.conftest import signup
from tests.test_templates import analyzed, worker

LINK = re.compile(r"http://localhost:5173/sign/([A-Za-z0-9_\-]+)")
# A 1x1 PNG, standing in for a drawn signature.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


async def approved(app: FastAPI, client: httpx.AsyncClient) -> dict[str, Any]:
    t = await analyzed(app, client)
    r = await client.post(f"/api/templates/{t['id']}/approve", json={"revision": t["revision"]})
    assert r.status_code == 200, r.text
    return r.json()


def sender_values(t: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for i in t["sender_inputs"]:
        if i["type"] == "checkbox":
            out[i["key"]] = True
        elif i["type"] == "date":
            out[i["key"]] = "2026-11-20"
        else:
            out[i["key"]] = "Acme Test Co"
    return out


def people(t: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"role": r["key"], "name": f"Test {r['label']}", "email": f"{r['key']}@example.com"}
        for r in sorted(t["roles"], key=lambda r: r["order"])
    ]


async def draft(app: FastAPI, client: httpx.AsyncClient) -> tuple[dict[str, Any], dict[str, Any]]:
    t = await approved(app, client)
    r = await client.post(
        "/api/files",
        json={"template_id": t["id"], "values": sender_values(t), "recipients": people(t)},
    )
    assert r.status_code == 201, r.text
    return t, r.json()


async def sent(app: FastAPI, client: httpx.AsyncClient) -> tuple[dict[str, Any], dict[str, Any]]:
    t, f = await draft(app, client)
    r = await client.post(f"/api/files/{f['id']}/send", json={"revision": f["revision"]})
    assert r.status_code == 200, r.text
    return t, r.json()


def last_link(outbox: Any, to: str) -> str:
    for email in reversed(outbox.outbox):
        if email.to == [to]:
            m = LINK.search(email.text)
            assert m, email.text
            return m.group(1)
    raise AssertionError(f"no email to {to}")


def answers(view: dict[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for f in view["fields"]:
        if f["type"] in ("signature", "initials", "date_signed"):
            continue
        values[f["key"]] = (
            True if f["type"] == "checkbox" else "2026-11-21" if f["type"] == "date" else "Jane Q"
        )
    return values


async def sign(
    client: httpx.AsyncClient, token: str, signature: dict[str, Any] | None = None
) -> httpx.Response:
    view = (await client.get(f"/api/sign/{token}")).json()
    assert view["state"] == "ready", view
    assert (await client.post(f"/api/sign/{token}/consent")).status_code == 204
    body = {
        "values": answers(view),
        "signature": signature or {"kind": "typed", "text": view["signer"]["name"]},
        "initials": {"kind": "typed", "text": "TQ"},
        "tz": "America/Phoenix",
    }
    return await client.post(f"/api/sign/{token}/submit", json=body)


async def test_open_preview_send_and_sign_in_order(
    app: FastAPI, client: httpx.AsyncClient, outbox: Any
) -> None:
    await signup(client)
    t, f = await draft(app, client)
    assert len(t["roles"]) == 2, "fixture has a client and a firm signer"
    assert f["status"] == "draft" and f["template"]["version"] == 1
    assert [r["status"] for r in f["recipients"]] == ["pending", "pending"]
    assert f["title"]  # defaults to the client's name
    assert f["missing"] == []

    # The preview is the agreement with the sender's values written in.
    pdf = await client.get(f"/api/files/{f['id']}/preview.pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    text = " ".join(ln.text for p in pdf_engine.extract(pdf.content).pages for ln in p.lines)
    assert "Acme Test Co" in text
    png = await client.get(f"/api/files/{f['id']}/pages/1.png")
    assert png.status_code == 200 and png.content.startswith(b"\x89PNG")

    r = await client.post(f"/api/files/{f['id']}/send", json={"revision": f["revision"]})
    assert r.status_code == 200, r.text
    f = r.json()
    assert f["status"] == "sent" and f["waiting_for"]["name"] == "Test " + t["roles"][0]["label"]
    first, second = f["recipients"]
    assert (first["status"], second["status"]) == ("active", "pending")
    again = await client.post(f"/api/files/{f['id']}/send", json={"revision": f["revision"]})
    assert again.status_code == 409

    w = worker(app)
    await w.drain()  # the invitation (reminders are due later)
    assert [e.to for e in outbox.outbox] == [[first["email"]]]
    email = outbox.outbox[-1]
    assert email.html and "Review and sign" in email.html and email.from_name == "Acme Test Co"
    assert email.reply_to == "owner@example.com"
    token = last_link(outbox, first["email"])

    # The second signer has no link until the first has signed.
    assert all(second["email"] not in e.to for e in outbox.outbox)

    signer = httpx.AsyncClient(transport=client._transport, base_url="http://test")
    view = (await signer.get(f"/api/sign/{token}")).json()
    assert view["state"] == "ready" and view["org_name"] == "Acme Test Co"
    assert view["fields"] and all(x["key"] for x in view["fields"])
    assert {p["status"] for p in view["parties"]} == {"active", "pending"}
    page = await signer.get(f"/api/sign/{token}/pages/1.png")
    assert page.status_code == 200

    # Can't sign before agreeing to sign electronically.
    early = await signer.post(f"/api/sign/{token}/submit", json={"values": answers(view)})
    assert early.status_code == 409
    r = await sign(signer, token)
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "signed"

    f = (await client.get(f"/api/files/{f['id']}")).json()
    assert f["status"] == "partially_signed" and f["signed_count"] == 1
    assert [r["status"] for r in f["recipients"]] == ["signed", "active"]

    await w.drain()
    second_token = last_link(outbox, second["email"])
    assert "has already signed" in outbox.outbox[-1].text
    drawn = "data:image/png;base64," + base64.b64encode(PNG).decode()
    r = await sign(signer, second_token, {"kind": "drawn", "png": drawn})
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "completed"

    f = (await client.get(f"/api/files/{f['id']}")).json()
    assert f["status"] == "completed" and f["completed_at"]
    types = [e["type"] for e in f["events"]]
    for expected in ("created", "sent", "email_sent", "viewed", "consented", "signed", "completed"):
        assert expected in types, types
    signed_events = [e for e in f["events"] if e["type"] == "signed"]
    assert len(signed_events) == 2 and signed_events[0]["ip"]
    finalize = await app.state.db.db.jobs.find_one({"type": signing.FINALIZE_FILE})
    assert finalize and finalize["payload"]["file_id"] == f["id"]

    # The document as it stands now carries both signatures' values.
    doc = await client.get(f"/api/files/{f['id']}/preview.pdf")
    text = " ".join(ln.text for p in pdf_engine.extract(doc.content).pages for ln in p.lines)
    assert first["name"] in text

    # Nothing can be signed twice, and the raw tokens are stored nowhere.
    assert (await sign_again(signer, token)).status_code == 409
    db = app.state.db.db
    for coll in ("files", "jobs", "events"):
        async for d in db[coll].find():
            assert token not in str(d) and second_token not in str(d)
    await signer.aclose()


async def sign_again(client: httpx.AsyncClient, token: str) -> httpx.Response:
    return await client.post(
        f"/api/sign/{token}/submit",
        json={"values": {}, "signature": {"kind": "typed", "text": "X"}},
    )


async def test_other_companies_cannot_see_or_touch_a_file(
    app: FastAPI, client: httpx.AsyncClient, second_client: httpx.AsyncClient
) -> None:
    await signup(client)
    t, f = await draft(app, client)
    await signup(second_client, email="other@example.com", org_name="Other Test Co")
    fid, rid = f["id"], f["recipients"][0]["id"]
    assert (await second_client.get("/api/files")).json() == []
    for method, path, body in [
        ("GET", f"/api/files/{fid}", None),
        ("GET", f"/api/files/{fid}/preview.pdf", None),
        ("GET", f"/api/files/{fid}/pages/1.png", None),
        ("PUT", f"/api/files/{fid}", {"revision": f["revision"], "recipients": []}),
        ("POST", f"/api/files/{fid}/send", {"revision": f["revision"]}),
        ("POST", f"/api/files/{fid}/recipients/{rid}/link", None),
        ("PATCH", f"/api/files/{fid}/recipients/{rid}", {"resend": True}),
        ("POST", f"/api/files/{fid}/void", {"reason": "x"}),
        ("DELETE", f"/api/files/{fid}", None),
    ]:
        r = await second_client.request(method, path, json=body)
        assert r.status_code == 404, (method, path, r.status_code)
    # Nor open a file from another company's agreement.
    r = await second_client.post(
        "/api/files", json={"template_id": t["id"], "values": {}, "recipients": people(t)}
    )
    assert r.status_code == 404


async def test_draft_rules(app: FastAPI, client: httpx.AsyncClient) -> None:
    await signup(client)
    t, f = await draft(app, client)
    # Bad signer details are listed, not guessed.
    bad = people(t)
    bad[0]["email"] = "not-an-email"
    bad[1]["name"] = " "
    r = await client.put(
        f"/api/files/{f['id']}", json={"revision": f["revision"], "values": {}, "recipients": bad}
    )
    assert r.status_code == 422 and len(r.json()["detail"]["problems"]) == 2

    # Emptying the values shows them as missing in the preview.
    r = await client.put(
        f"/api/files/{f['id']}",
        json={"revision": f["revision"], "values": {}, "recipients": people(t), "title": "Job 7"},
    )
    assert r.status_code == 200, r.text
    edited = r.json()
    assert edited["title"] == "Job 7" and edited["missing"]
    # A stale tab can't overwrite it or send what it didn't preview.
    stale = await client.put(
        f"/api/files/{f['id']}",
        json={"revision": f["revision"], "values": {}, "recipients": people(t)},
    )
    assert stale.status_code == 409
    stale_send = await client.post(f"/api/files/{f['id']}/send", json={"revision": f["revision"]})
    assert stale_send.status_code == 409

    # Only an approved agreement can be used.
    r = await client.put(f"/api/templates/{t['id']}", json={**t_body(t), "name": "Edited"})
    assert r.status_code == 200 and r.json()["status"] == "needs_review"
    r = await client.post(
        "/api/files", json={"template_id": t["id"], "values": {}, "recipients": people(t)}
    )
    assert r.status_code == 409
    # The existing draft keeps its own copy of version 1.
    assert (await client.get(f"/api/files/{f['id']}")).json()["template"]["version"] == 1

    assert (await client.delete(f"/api/files/{f['id']}")).status_code == 204
    assert (await client.get(f"/api/files/{f['id']}")).status_code == 404


def t_body(t: dict[str, Any]) -> dict[str, Any]:
    return {"revision": t["revision"], "roles": t["roles"], "fields": t["fields"]}


async def test_copy_link_resend_and_correct_email(
    app: FastAPI, client: httpx.AsyncClient, outbox: Any
) -> None:
    await signup(client)
    _, f = await sent(app, client)
    w = worker(app)
    await w.drain()
    rid = f["recipients"][0]["id"]
    emailed = last_link(outbox, f["recipients"][0]["email"])

    r = await client.post(f"/api/files/{f['id']}/recipients/{rid}/link")
    assert r.status_code == 200
    copied = LINK.search(r.json()["url"])
    assert copied and copied.group(1) != emailed
    for token in (emailed, copied.group(1)):
        assert (await client.get(f"/api/sign/{token}")).json()["state"] == "ready"
    # The second signer isn't up yet, so has no link to copy.
    later = f["recipients"][1]["id"]
    assert (await client.post(f"/api/files/{f['id']}/recipients/{later}/link")).status_code == 409

    # Correcting the address retires the old links and sends a new one there.
    r = await client.patch(
        f"/api/files/{f['id']}/recipients/{rid}", json={"email": "Fixed.Client@Example.com"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["recipients"][0]["email"] == "fixed.client@example.com"
    assert (await client.get(f"/api/sign/{emailed}")).status_code == 404
    await w.drain()
    fresh = last_link(outbox, "fixed.client@example.com")
    assert (await client.get(f"/api/sign/{fresh}")).json()["state"] == "ready"
    # Resending again straight away is refused.
    r = await client.patch(f"/api/files/{f['id']}/recipients/{rid}", json={"resend": True})
    assert r.status_code == 409

    f = (await client.get(f"/api/files/{f['id']}")).json()
    types = [e["type"] for e in f["events"]]
    assert "link_copied" in types and "email_corrected" in types
    assert f["recipients"][0]["last_email"]["status"] == "sent"


async def test_decline(app: FastAPI, client: httpx.AsyncClient, outbox: Any) -> None:
    await signup(client)
    _, f = await sent(app, client)
    w = worker(app)
    await w.drain()
    token = last_link(outbox, f["recipients"][0]["email"])
    r = await client.post(f"/api/sign/{token}/decline", json={"reason": " "})
    assert r.status_code == 422
    r = await client.post(f"/api/sign/{token}/decline", json={"reason": "The fee is wrong."})
    assert r.status_code == 200 and r.json()["state"] == "declined"
    f = (await client.get(f"/api/files/{f['id']}")).json()
    assert f["status"] == "declined" and f["recipients"][0]["decline_reason"] == "The fee is wrong."
    await w.drain()
    notice = outbox.outbox[-1]
    assert notice.to == ["owner@example.com"] and "The fee is wrong." in notice.text
    assert (await sign_again(client, token)).status_code == 409


async def test_void(app: FastAPI, client: httpx.AsyncClient, outbox: Any) -> None:
    await signup(client)
    _, f = await sent(app, client)
    await worker(app).drain()
    token = last_link(outbox, f["recipients"][0]["email"])
    r = await client.post(f"/api/files/{f['id']}/void", json={"reason": "Wrong client."})
    assert r.status_code == 200 and r.json()["status"] == "voided"
    view = (await client.get(f"/api/sign/{token}")).json()
    assert (
        view["state"] == "voided" and view["void_reason"] == "Wrong client." and not view["fields"]
    )
    assert (await client.get(f"/api/sign/{token}/pages/1.png")).status_code == 404
    assert (
        await client.post(f"/api/files/{f['id']}/void", json={"reason": "x"})
    ).status_code == 409


async def test_reminders_stop_once_signed(
    app: FastAPI, client: httpx.AsyncClient, outbox: Any
) -> None:
    await signup(client)
    _, f = await sent(app, client)
    w = worker(app)
    await w.drain()
    db = app.state.db.db
    reminders = await db.jobs.find({"payload.reason": "reminder"}).to_list()
    assert len(reminders) == 2
    days = sorted(round((j["run_at"] - utcnow()).total_seconds() / 86400) for j in reminders)
    assert days == [2, 5]

    # Day 2 arrives.
    await db.jobs.update_one({"_id": reminders[0]["_id"]}, {"$set": {"run_at": utcnow()}})
    await w.drain()
    assert outbox.outbox[-1].subject.startswith("Reminder:")
    token = last_link(outbox, f["recipients"][0]["email"])
    assert (await sign(client, token)).status_code == 200

    # Day 5 arrives after they signed: nothing is sent to them.
    count = len(outbox.outbox)
    await db.jobs.update_many({"payload.reason": "reminder"}, {"$set": {"run_at": utcnow()}})
    await w.drain()
    assert all(e.to != [f["recipients"][0]["email"]] for e in list(outbox.outbox)[count:])
    f = (await client.get(f"/api/files/{f['id']}")).json()
    first = f["recipients"][0]["email"]
    reminded = [e for e in f["events"] if e["type"] == "reminded"]
    assert [e["recipient"]["email"] for e in reminded].count(first) == 1


async def test_expiry(app: FastAPI, client: httpx.AsyncClient, outbox: Any) -> None:
    await signup(client)
    _, f = await sent(app, client)
    db = app.state.db.db
    past = utcnow() - timedelta(minutes=1)
    await db.files.update_one({}, {"$set": {"expires_at": past}})
    await db.jobs.update_one({"type": signing.EXPIRE_FILE}, {"$set": {"run_at": past}})
    w = worker(app)
    await w.drain()
    f = (await client.get(f"/api/files/{f['id']}")).json()
    assert f["status"] == "expired" and "expired" in [e["type"] for e in f["events"]]
    assert outbox.outbox[-1].to == ["owner@example.com"]
    assert "expired" in outbox.outbox[-1].subject


async def test_signing_validation(app: FastAPI, client: httpx.AsyncClient, outbox: Any) -> None:
    await signup(client)
    _, f = await sent(app, client)
    await worker(app).drain()
    token = last_link(outbox, f["recipients"][0]["email"])
    assert (await client.post(f"/api/sign/{token}/consent")).status_code == 204
    view = (await client.get(f"/api/sign/{token}")).json()
    assert view["consented"]
    r = await client.post(f"/api/sign/{token}/submit", json={"values": answers(view)})
    assert r.status_code == 422 and "Add your signature." in r.json()["detail"]["problems"]
    bad = {"kind": "drawn", "png": base64.b64encode(b"GIF89a not a png").decode()}
    r = await client.post(
        f"/api/sign/{token}/submit", json={"values": answers(view), "signature": bad}
    )
    assert r.status_code == 422
    assert (await client.get("/api/sign/not-a-real-token")).status_code == 404


async def test_failed_email_shows_on_the_file(app: FastAPI, client: httpx.AsyncClient) -> None:
    class Rejecting:
        name = "rejecting"

        async def send(self, email: OutgoingEmail) -> SendResult:
            raise EmailPermanentError("gmail send HTTP 400: Invalid To header")

        async def check(self) -> None:
            return None

    await signup(client)
    _, f = await sent(app, client)
    w = Worker(
        app.state.db.db, build_registry(), services={**app.state.services, "email": Rejecting()}
    )
    await w.drain()
    f = (await client.get(f"/api/files/{f['id']}")).json()
    last = f["recipients"][0]["last_email"]
    assert last["status"] == "failed" and "Invalid To" in last["error"]
    assert "email_failed" in [e["type"] for e in f["events"]]


def test_signing_tokens_never_reach_the_logs() -> None:
    assert redact_path("/sign/abcDEF_123") == "/sign/<token>"
    assert redact_path("/api/sign/abc/pages/2.png") == "/api/sign/<token>/pages/2.png"
    assert redact_path("/api/files/123") == "/api/files/123"


def test_signing_date_uses_the_signers_calendar() -> None:
    from datetime import UTC, datetime

    late = datetime(2026, 11, 16, 3, 0, tzinfo=UTC)  # still Nov 15 in Phoenix
    assert signing.signing_date(late, "America/Phoenix") == "November 15, 2026"
    assert signing.signing_date(late, "Not/AZone") == "November 16, 2026"
    assert signing.signing_date(late, None) == "November 16, 2026"
