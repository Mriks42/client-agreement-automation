import os
import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from fastapi import FastAPI

from app.config import Settings
from app.main import create_app
from app.services.email import ConsoleEmailSender

MONGODB_URI = os.environ.get("TEST_MONGODB_URI", "mongodb://localhost:27017")


def make_settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "app_env": "test",
        "mongodb_uri": MONGODB_URI,
        "mongodb_db": f"test_{uuid.uuid4().hex[:10]}",
        "run_worker": False,  # tests drive the worker explicitly via Worker.drain()
        "email_backend": "console",
        "frontend_dist": "/nonexistent",
        # Never call the real Claude API from tests, even if the shell exports a key.
        "anthropic_api_key": None,
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)  # type: ignore[call-arg]


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
def outbox() -> ConsoleEmailSender:
    return ConsoleEmailSender()


@pytest_asyncio.fixture
async def app(settings: Settings, outbox: ConsoleEmailSender) -> AsyncIterator[FastAPI]:
    application = create_app(settings, email=outbox)
    async with LifespanManager(application):
        yield application
        await application.state.db.client.drop_database(settings.mongodb_db)


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def db(app: FastAPI):  # type: ignore[no-untyped-def]
    return app.state.db.db


async def signup(
    client: httpx.AsyncClient,
    email: str = "owner@example.com",
    org_name: str = "Acme Test Co",
    password: str = "correct horse battery",
) -> dict[str, object]:
    r = await client.post(
        "/api/auth/signup",
        json={"org_name": org_name, "name": "Test Owner", "email": email, "password": password},
    )
    assert r.status_code == 201, r.text
    return r.json()


@pytest_asyncio.fixture
async def second_client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """Another browser: its own cookie jar, so it can be logged in to a different company."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
