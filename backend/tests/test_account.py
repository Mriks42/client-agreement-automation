"""Your own account: change your name, change your password (other sessions end)."""

import httpx
from fastapi import FastAPI

from tests.conftest import signup

PASSWORD = "correct horse battery"


async def test_change_your_name(client: httpx.AsyncClient) -> None:
    assert (await client.patch("/api/auth/me", json={"name": "Alex"})).status_code == 401
    await signup(client)
    r = await client.patch("/api/auth/me", json={"name": "  Alex Partner  "})
    assert r.status_code == 200, r.text
    assert r.json()["user"]["name"] == "Alex Partner"
    assert (await client.get("/api/auth/me")).json()["user"]["name"] == "Alex Partner"
    assert (await client.patch("/api/auth/me", json={"name": "   "})).status_code == 422


async def test_change_your_password_ends_other_sessions(
    app: FastAPI, client: httpx.AsyncClient, second_client: httpx.AsyncClient
) -> None:
    await signup(client)
    login = {"email": "owner@example.com", "password": PASSWORD}
    assert (await second_client.post("/api/auth/login", json=login)).status_code == 200

    wrong = {"current_password": "not it", "new_password": "a new password 1"}
    r = await client.post("/api/auth/password", json=wrong)
    assert r.status_code == 400 and "incorrect" in r.text
    short = {"current_password": PASSWORD, "new_password": "short"}
    assert (await client.post("/api/auth/password", json=short)).status_code == 422

    good = {"current_password": PASSWORD, "new_password": "a new password 1"}
    assert (await client.post("/api/auth/password", json=good)).status_code == 204
    # This session stays signed in; the other one ends.
    assert (await client.get("/api/auth/me")).status_code == 200
    assert (await second_client.get("/api/auth/me")).status_code == 401
    # The old password no longer works; the new one does.
    assert (await second_client.post("/api/auth/login", json=login)).status_code == 401
    login["password"] = "a new password 1"
    assert (await second_client.post("/api/auth/login", json=login)).status_code == 200


async def test_repeated_wrong_current_passwords_are_slowed_down(client: httpx.AsyncClient) -> None:
    await signup(client)
    wrong = {"current_password": "not it", "new_password": "a new password 1"}
    codes = [(await client.post("/api/auth/password", json=wrong)).status_code for _ in range(12)]
    assert codes[0] == 400 and codes[-1] == 429
