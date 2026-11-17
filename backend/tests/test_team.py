"""Team members: invite by email, join with your own login, remove; tokens stored hashed."""

import json
import re
from datetime import timedelta
from typing import Any

import httpx
from fastapi import FastAPI

from app.main import redact_path
from app.worker.queue import utcnow
from tests.conftest import signup
from tests.test_files import approved, people, sender_values
from tests.test_templates import worker

JOIN = re.compile(r"http://localhost:5173/join/([A-Za-z0-9_\-]+)")


def link_in(outbox: Any, to: str) -> str:
    for email in reversed(outbox.outbox):
        if email.to == [to]:
            m = JOIN.search(email.text)
            assert m and m.group(0) in email.html, email.text
            return m.group(1)
    raise AssertionError(f"no email to {to}")


async def invite(
    client: httpx.AsyncClient, email: str, name: str = "Maria Sales"
) -> dict[str, Any]:
    r = await client.post("/api/settings/team/invites", json={"email": email, "name": name})
    assert r.status_code == 201, r.text
    return dict(r.json())


async def test_invite_join_and_work_as_one_company(
    app: FastAPI,
    client: httpx.AsyncClient,
    second_client: httpx.AsyncClient,
    outbox: Any,
    db: Any,
) -> None:
    await signup(client)
    t = await approved(app, client)
    team = await invite(client, "Maria@Example.com")
    [pending] = team["invites"]
    assert pending["email"] == "maria@example.com" and pending["invited_by_name"] == "Test Owner"
    assert [m["you"] for m in team["members"]] == [True]

    await worker(app).drain()
    email = outbox.outbox[-1]
    assert email.subject == "Test Owner invited you to Acme Test Co on Kickoff"
    assert email.reply_to == "owner@example.com"
    token = link_in(outbox, "maria@example.com")
    # Only the link's hash is stored, never the link itself (not in the invite, not in the job).
    stored = json.dumps(
        [d async for d in db.invites.find({})] + [d async for d in db.jobs.find({})], default=str
    )
    assert token not in stored
    assert redact_path(f"/join/{token}") == "/join/<token>"

    maria = second_client
    info = (await maria.get(f"/api/auth/invites/{token}")).json()
    assert info == {
        "org_name": "Acme Test Co",
        "email": "maria@example.com",
        "name": "Maria Sales",
        "invited_by_name": "Test Owner",
    }
    r = await maria.post(
        "/api/auth/join", json={"token": token, "name": "Maria Sales", "password": "maria-secret-1"}
    )
    assert r.status_code == 201, r.text
    me = (await maria.get("/api/auth/me")).json()
    assert me["org"]["name"] == "Acme Test Co" and me["user"]["email"] == "maria@example.com"

    # Same company: Maria sees the agreement, and a file she sends is sent by her.
    assert [x["id"] for x in (await maria.get("/api/templates")).json()] == [t["id"]]
    f = (
        await maria.post(
            "/api/files",
            json={"template_id": t["id"], "values": sender_values(t), "recipients": people(t)},
        )
    ).json()
    sent = await maria.post(f"/api/files/{f['id']}/send", json={"revision": f["revision"]})
    assert sent.status_code == 200
    detail = (await client.get(f"/api/files/{f['id']}")).json()
    assert detail["events"][0]["actor"]["name"] == "Maria Sales"
    await worker(app).drain()
    assert outbox.outbox[-1].reply_to == "maria@example.com"

    # The link is used up; she logs in with her own password from now on.
    again = {"token": token, "name": "Someone", "password": "another-pass-1"}
    assert (await maria.post("/api/auth/join", json=again)).status_code == 404
    assert (await maria.get(f"/api/auth/invites/{token}")).status_code == 404
    login = {"email": "maria@example.com", "password": "maria-secret-1"}
    assert (await maria.post("/api/auth/login", json=login)).status_code == 200
    members = (await client.get("/api/settings/team")).json()
    assert [m["name"] for m in members["members"]] == ["Test Owner", "Maria Sales"]
    assert members["invites"] == []


async def test_invitation_rules(app: FastAPI, client: httpx.AsyncClient, db: Any) -> None:
    await signup(client)
    me = await client.post("/api/settings/team/invites", json={"email": "owner@example.com"})
    assert me.status_code == 409 and "already has a Kickoff account" in me.text
    bad = await client.post("/api/settings/team/invites", json={"email": "not-an-email"})
    assert bad.status_code == 422
    team = await invite(client, "tom@example.com")
    assert (
        await client.post("/api/settings/team/invites", json={"email": "tom@example.com"})
    ).status_code == 409

    # Copy invite link works until it's revoked; a revoked or expired link explains itself.
    inv = team["invites"][0]["id"]
    url = (await client.post(f"/api/settings/team/invites/{inv}/link")).json()["url"]
    token = url.rsplit("/", 1)[1]
    assert (await client.get(f"/api/auth/invites/{token}")).status_code == 200
    assert (await client.delete(f"/api/settings/team/invites/{inv}")).status_code == 204
    gone = await client.get(f"/api/auth/invites/{token}")
    assert gone.status_code == 404 and "isn't valid" in gone.json()["detail"]
    assert (await client.post(f"/api/settings/team/invites/{inv}/link")).status_code == 409

    team = await invite(client, "ana@example.com")
    inv = team["invites"][0]["id"]
    url = (await client.post(f"/api/settings/team/invites/{inv}/link")).json()["url"]
    await db.invites.update_many({}, {"$set": {"expires_at": utcnow() - timedelta(minutes=1)}})
    assert (await client.get(f"/api/auth/invites/{url.rsplit('/', 1)[1]}")).status_code == 404


async def test_removing_a_member_ends_their_access(
    app: FastAPI, client: httpx.AsyncClient, second_client: httpx.AsyncClient, outbox: Any
) -> None:
    await signup(client)
    await invite(client, "tom@example.com", "Tom Ops")
    await worker(app).drain()
    tom = second_client
    token = link_in(outbox, "tom@example.com")
    body = {"token": token, "name": "Tom Ops", "password": "tom-secret-12"}
    assert (await tom.post("/api/auth/join", json=body)).status_code == 201
    key = (await tom.post("/api/settings/api-keys", json={"name": "Tom's script"})).json()["key"]
    assert (
        await tom.get("/api/v1/templates", headers={"authorization": f"Bearer {key}"})
    ).status_code == 200

    members = (await client.get("/api/settings/team")).json()["members"]
    me_id = next(m["id"] for m in members if m["you"])
    tom_id = next(m["id"] for m in members if not m["you"])
    assert (await client.delete(f"/api/settings/team/members/{me_id}")).status_code == 409
    assert (await client.delete(f"/api/settings/team/members/{tom_id}")).status_code == 204
    assert (await tom.get("/api/files")).status_code == 401  # his session is gone
    assert (
        await tom.get("/api/v1/templates", headers={"authorization": f"Bearer {key}"})
    ).status_code == 401
    login = {"email": "tom@example.com", "password": "tom-secret-12"}
    assert (await tom.post("/api/auth/login", json=login)).status_code == 401


async def test_another_company_cannot_touch_this_team(
    app: FastAPI, client: httpx.AsyncClient, second_client: httpx.AsyncClient
) -> None:
    await signup(client)
    team = await invite(client, "tom@example.com")
    inv = team["invites"][0]["id"]
    owner_id = team["members"][0]["id"]
    await signup(second_client, email="other@example.com", org_name="Other Test Co")
    other = (await second_client.get("/api/settings/team")).json()
    assert [m["email"] for m in other["members"]] == ["other@example.com"] and other[
        "invites"
    ] == []
    assert (await second_client.post(f"/api/settings/team/invites/{inv}/link")).status_code == 404
    assert (await second_client.delete(f"/api/settings/team/invites/{inv}")).status_code == 404
    assert (await second_client.delete(f"/api/settings/team/members/{owner_id}")).status_code == 404
    assert (await client.get("/api/settings/team")).json()["invites"][0]["id"] == inv
