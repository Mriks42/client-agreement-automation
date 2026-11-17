"""Sign now: the firm's own signer, logged in, countersigns from the file page."""

from typing import Any

import httpx
from fastapi import FastAPI

from app.services import pdf_engine
from tests.conftest import signup
from tests.test_files import approved, last_link, sender_values, sign
from tests.test_templates import worker


async def test_the_firms_signer_can_sign_from_the_file_page(
    app: FastAPI, client: httpx.AsyncClient, second_client: httpx.AsyncClient, outbox: Any
) -> None:
    await signup(client)  # owner@example.com, "Test Owner"
    t = await approved(app, client)
    roles = sorted(t["roles"], key=lambda r: r["order"])
    people = [
        {"role": r["key"], "name": "Test Owner", "email": "owner@example.com"}
        if r["kind"] == "firm"
        else {"role": r["key"], "name": "Jane Client", "email": "jane@example.com"}
        for r in roles
    ]
    f = (
        await client.post(
            "/api/files",
            json={"template_id": t["id"], "values": sender_values(t), "recipients": people},
        )
    ).json()
    f = (await client.post(f"/api/files/{f['id']}/send", json={"revision": f["revision"]})).json()
    jane, firm = f["recipients"]
    path = f"/api/files/{f['id']}/recipients"

    # Jane's turn: the button is only for the person named as the signer, and only on their turn.
    r = await client.post(f"{path}/{jane['id']}/sign-now")
    assert r.status_code == 409 and "Only Jane Client can sign here" in r.json()["detail"]
    assert (await client.post(f"{path}/{firm['id']}/sign-now")).status_code == 409
    listed = (await client.get("/api/files")).json()[0]
    assert listed["waiting_for"]["email"] == "jane@example.com"

    await worker(app).drain()
    assert (await sign(second_client, last_link(outbox, "jane@example.com"))).status_code == 200

    # Now it's the firm's turn: the logged-in owner opens their signing page from the file.
    listed = (await client.get("/api/files")).json()[0]
    assert listed["waiting_for"]["email"] == "owner@example.com"
    r = await client.post(f"{path}/{firm['id']}/sign-now")
    assert r.status_code == 200, r.text
    token = r.json()["url"].rsplit("/", 1)[1]
    assert (await sign(client, token)).status_code == 200

    await worker(app).drain()  # the signed PDF and everyone's copy
    detail = (await client.get(f"/api/files/{f['id']}")).json()
    assert detail["status"] == "completed" and detail["finalized_at"]
    [opened] = [e for e in detail["events"] if e["type"] == "signing_opened_in_app"]
    assert opened["recipient"]["email"] == "owner@example.com"
    assert opened["data"] == {"login": "owner@example.com"}
    pdf = (await client.get(f"/api/files/{f['id']}/signed.pdf")).content
    text = " ".join(ln.text for p in pdf_engine.extract(pdf).pages for ln in p.lines)
    assert "opened their signing page from the file, logged in as owner@example.com" in text
    assert "opened it from the file while logged in to their own account" in text


async def test_sign_now_is_not_for_another_company(
    app: FastAPI, client: httpx.AsyncClient, second_client: httpx.AsyncClient
) -> None:
    await signup(client)
    t = await approved(app, client)
    roles = sorted(t["roles"], key=lambda r: r["order"])
    people = [{"role": r["key"], "name": "Same Name", "email": "shared@example.com"} for r in roles]
    f = (
        await client.post(
            "/api/files",
            json={"template_id": t["id"], "values": sender_values(t), "recipients": people},
        )
    ).json()
    f = (await client.post(f"/api/files/{f['id']}/send", json={"revision": f["revision"]})).json()
    await signup(second_client, email="shared@example.com", org_name="Other Test Co")
    rid = f["recipients"][0]["id"]
    r = await second_client.post(f"/api/files/{f['id']}/recipients/{rid}/sign-now")
    assert r.status_code == 404  # the signer's own email, but another company's file
