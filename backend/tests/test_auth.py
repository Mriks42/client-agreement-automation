from typing import Any

import httpx

from app.services import accounts
from tests.conftest import signup


async def test_signup_logs_in_and_me_returns_user_and_org(client: httpx.AsyncClient) -> None:
    body = await signup(client, email="Owner@Example.com")
    assert body["user"]["email"] == "owner@example.com"  # type: ignore[index]
    me = await client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["org"]["name"] == "Acme Test Co"


async def test_me_requires_login(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/auth/me")
    assert r.status_code == 401


async def test_duplicate_email_is_refused(
    client: httpx.AsyncClient, second_client: httpx.AsyncClient
) -> None:
    await signup(client)
    r = await second_client.post(
        "/api/auth/signup",
        json={
            "org_name": "Other Test Co",
            "name": "Someone",
            "email": "OWNER@example.com",
            "password": "another password",
        },
    )
    assert r.status_code == 409


async def test_short_password_is_refused(client: httpx.AsyncClient) -> None:
    r = await client.post(
        "/api/auth/signup",
        json={"org_name": "Acme", "name": "A", "email": "a@example.com", "password": "short"},
    )
    assert r.status_code == 422


async def test_login_logout(client: httpx.AsyncClient, second_client: httpx.AsyncClient) -> None:
    await signup(client)
    bad = await second_client.post(
        "/api/auth/login", json={"email": "owner@example.com", "password": "wrong password"}
    )
    assert bad.status_code == 401
    unknown = await second_client.post(
        "/api/auth/login", json={"email": "nobody@example.com", "password": "wrong password"}
    )
    assert unknown.status_code == 401 and unknown.json()["detail"] == bad.json()["detail"]

    ok = await second_client.post(
        "/api/auth/login", json={"email": "owner@example.com", "password": "correct horse battery"}
    )
    assert ok.status_code == 200
    assert (await second_client.get("/api/auth/me")).status_code == 200

    # Logging out ends the session on the server, so replaying the old cookie doesn't work.
    token = second_client.cookies.get(accounts.COOKIE_NAME)
    assert (await second_client.post("/api/auth/logout")).status_code == 204
    second_client.cookies.set(accounts.COOKIE_NAME, token or "")
    assert (await second_client.get("/api/auth/me")).status_code == 401


async def test_secrets_are_stored_hashed(client: httpx.AsyncClient, db: Any) -> None:
    await signup(client)
    user = await db.users.find_one({"email": "owner@example.com"})
    assert user["password_hash"].startswith("$argon2id$")
    token = client.cookies.get(accounts.COOKIE_NAME)
    assert token and await db.sessions.count_documents({"_id": token}) == 0
    assert await db.sessions.count_documents({"user_id": user["_id"]}) == 1


async def test_forged_cookie_is_rejected(client: httpx.AsyncClient) -> None:
    client.cookies.set(accounts.COOKIE_NAME, "x" * 43)
    r = await client.get("/api/auth/me")
    assert r.status_code == 401


async def test_repeated_failures_are_throttled(client: httpx.AsyncClient) -> None:
    await signup(client)
    for _ in range(10):
        await client.post(
            "/api/auth/login", json={"email": "owner@example.com", "password": "wrong password"}
        )
    r = await client.post(
        "/api/auth/login", json={"email": "owner@example.com", "password": "correct horse battery"}
    )
    assert r.status_code == 429


async def test_cross_site_post_is_refused(client: httpx.AsyncClient) -> None:
    await signup(client)
    r = await client.post("/api/auth/logout", headers={"origin": "https://evil.example"})
    assert r.status_code == 403
    same = await client.post("/api/auth/logout", headers={"origin": "http://test"})
    assert same.status_code == 204
