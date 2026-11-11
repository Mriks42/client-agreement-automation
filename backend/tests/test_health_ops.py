from collections.abc import AsyncIterator

import httpx
import pytest_asyncio
from asgi_lifespan import LifespanManager

from app.main import create_app
from app.services.email import ConsoleEmailSender
from tests.conftest import make_settings


async def test_liveness(client: httpx.AsyncClient) -> None:
    r = await client.get("/healthz")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


async def test_deep_health_reports_components(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/health")
    body = r.json()
    assert body["checks"]["database"]["ok"] is True
    assert body["checks"]["email"] == {"ok": True, "backend": "console"}
    assert "x-request-id" in r.headers


async def test_ops_disabled_without_token(client: httpx.AsyncClient) -> None:
    r = await client.post("/api/ops/test-email", json={"to": "jane.client@example.com"})
    assert r.status_code == 404


@pytest_asyncio.fixture
async def ops_client() -> AsyncIterator[tuple[httpx.AsyncClient, ConsoleEmailSender]]:
    settings = make_settings(ops_token="s3cret")
    outbox = ConsoleEmailSender()
    app = create_app(settings, email=outbox)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c, outbox
        await app.state.db.client.drop_database(settings.mongodb_db)


async def test_ops_test_email_requires_correct_token(
    ops_client: tuple[httpx.AsyncClient, ConsoleEmailSender],
) -> None:
    client, outbox = ops_client
    bad = await client.post(
        "/api/ops/test-email",
        json={"to": "jane.client@example.com"},
        headers={"x-ops-token": "wrong"},
    )
    assert bad.status_code == 401
    ok = await client.post(
        "/api/ops/test-email",
        json={"to": "jane.client@example.com"},
        headers={"x-ops-token": "s3cret"},
    )
    assert ok.status_code == 200 and ok.json()["provider"] == "console"
    assert outbox.outbox[-1].to == ["jane.client@example.com"]


def test_blank_optional_settings_are_treated_as_unset() -> None:
    s = make_settings(anthropic_api_key="", sentry_dsn="  ", ops_token="")
    assert s.anthropic_api_key is None and s.sentry_dsn is None and s.ops_token is None
