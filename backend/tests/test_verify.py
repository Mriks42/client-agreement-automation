"""The public verify page: a signed PDF's SHA-256 finds what it is; anything else doesn't."""

import hashlib
from typing import Any

import httpx
from fastapi import FastAPI

from tests.conftest import signup
from tests.test_finalize import completed
from tests.test_templates import worker


async def test_verify_a_signed_pdf_by_its_fingerprint(
    app: FastAPI, client: httpx.AsyncClient, outbox: Any
) -> None:
    await signup(client)
    t, f, _, _ = await completed(app, client, outbox)
    public = httpx.AsyncClient(transport=client._transport, base_url="http://test")  # no login

    # Signed but not yet finalized: nothing to match.
    prepared = f["prepared_sha256"]
    assert (await public.get(f"/api/verify/{prepared}")).json() == {"match": False}

    await worker(app).drain()
    pdf = (await client.get(f"/api/files/{f['id']}/signed.pdf")).content
    digest = hashlib.sha256(pdf).hexdigest()
    r = await public.get(f"/api/verify/{digest.upper()}")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    body = r.json()
    assert body["match"] is True
    assert body["firm"] == "Acme Test Co"
    assert body["agreement"] == t["name"] and body["title"] == f["title"]
    assert [s["name"] for s in body["signers"]] == [r["name"] for r in f["recipients"]]
    assert body["completed_at"]
    assert "email" not in str(body)  # names only, never addresses

    # One changed byte, the PDF as sent (before signing), or junk: not a match.
    changed = hashlib.sha256(pdf + b"\n").hexdigest()
    assert (await public.get(f"/api/verify/{changed}")).json() == {"match": False}
    assert (await public.get(f"/api/verify/{prepared}")).json() == {"match": False}
    assert (await public.get("/api/verify/not-a-hash")).status_code == 422

    # The delivery emails link to the verify page.
    copies = [m for m in outbox.outbox if m.subject.startswith("Signed:")]
    assert copies and all("http://localhost:5173/verify" in m.text for m in copies)
    assert all('href="http://localhost:5173/verify"' in m.html for m in copies)
