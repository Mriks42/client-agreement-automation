"""Finalize: the signed PDF with its audit certificate, delivered to every party."""

import base64
import hashlib
import re
from typing import Any

import httpx
import pymupdf
import pytest
from bson import ObjectId
from fastapi import FastAPI

from app.repo import Tenant
from app.services import finalize, pdf_engine
from app.services.email import EmailPermanentError, OutgoingEmail, SendResult
from app.worker.handlers import build_registry
from app.worker.queue import PermanentJobError, Worker
from tests.conftest import signup
from tests.test_files import PNG, last_link, sent, sign
from tests.test_templates import worker


def text_of(pdf: bytes, first_page: int = 1) -> str:
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        return "\n".join(doc[i].get_text() for i in range(first_page - 1, doc.page_count))


def page_count(pdf: bytes) -> int:
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        return doc.page_count


async def completed(
    app: FastAPI, client: httpx.AsyncClient, outbox: Any
) -> tuple[dict[str, Any], dict[str, Any], httpx.AsyncClient, list[str]]:
    """A file signed by the client (typed) then the firm (drawn); finalize not yet run."""
    t, f = await sent(app, client)
    w = worker(app)
    signer = httpx.AsyncClient(transport=client._transport, base_url="http://test")
    tokens = []
    for i, r in enumerate(f["recipients"]):
        await w.drain()
        token = last_link(outbox, r["email"])
        tokens.append(token)
        mark = None
        if i == 1:
            mark = {
                "kind": "drawn",
                "png": "data:image/png;base64," + base64.b64encode(PNG).decode(),
            }
        resp = await sign(signer, token, mark)
        assert resp.status_code == 200, resp.text
    f = (await client.get(f"/api/files/{f['id']}")).json()
    assert f["status"] == "completed" and f["finalized_at"] is None
    return t, f, signer, tokens


async def test_signed_pdf_with_certificate_reaches_everyone(
    app: FastAPI, client: httpx.AsyncClient, outbox: Any
) -> None:
    await signup(client)
    _, f, signer, tokens = await completed(app, client, outbox)
    fid = f["id"]
    # Not ready yet: both downloads say so, and the signer's page knows.
    assert (await client.get(f"/api/files/{fid}/signed.pdf")).status_code == 409
    assert (await signer.get(f"/api/sign/{tokens[0]}/signed.pdf")).status_code == 409
    assert (await signer.get(f"/api/sign/{tokens[0]}")).json()["signed_pdf_ready"] is False

    before = len(outbox.outbox)
    await worker(app).drain()  # finalize, then its emails
    f = (await client.get(f"/api/files/{fid}")).json()
    assert f["finalized_at"] and f["final_sha256"] and f["finalize_error"] is None

    # The download is exactly the stored PDF, whose hash is on record.
    r = await client.get(f"/api/files/{fid}/signed.pdf")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert "attachment" in r.headers["content-disposition"]
    assert "signed.pdf" in r.headers["content-disposition"]
    pdf = r.content
    assert hashlib.sha256(pdf).hexdigest() == f["final_sha256"]
    assert (await signer.get(f"/api/sign/{tokens[1]}/signed.pdf")).content == pdf
    assert (await signer.get(f"/api/sign/{tokens[0]}")).json()["signed_pdf_ready"] is True

    # The agreement's pages, then the certificate; no form fields left to change.
    pages = f["template"]["page_count"]
    assert page_count(pdf) > pages
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        assert not any(list(p.widgets() or []) for p in doc)
    body = text_of(pdf, pages + 1)
    assert "Certificate of completion" in body
    db = app.state.db.db
    file_doc = await db.files.find_one({"_id": ObjectId(fid)})
    template_pdf = await (
        await app.state.db.blobs.open_download_stream(file_doc["template"]["pdf_blob_id"])
    ).read()
    flat = body.replace("\n", "")
    for expected in (
        fid,
        f["template"]["name"],
        f"version {f['template']['version']}",
        file_doc["prepared"]["sha256"],
        hashlib.sha256(template_pdf).hexdigest(),
        "Drawn on screen",
        'Typed: "Test ',
        "shasum -a 256",
    ):
        assert expected.replace("\n", "") in flat, expected
    for r_ in f["recipients"]:
        assert r_["name"] in body and r_["email"] in body
    assert "127.0.0.1" in body  # where each signer signed from
    # The signatures are on the agreement's own pages.
    assert f["recipients"][0]["name"] in text_of(pdf)

    # One email per party, each with the signed PDF attached and the hash quoted.
    new = list(outbox.outbox)[before:]
    to = sorted(e.to[0] for e in new)
    assert to == sorted([*(r_["email"] for r_ in f["recipients"]), "owner@example.com"])
    for e in new:
        assert len(e.attachments) == 1 and e.attachments[0].content == pdf
        assert e.attachments[0].filename.endswith("signed.pdf")
        assert f["final_sha256"] in e.text
    owner = next(e for e in new if e.to == ["owner@example.com"])
    assert "Ready to start work" in owner.subject and f"/files/{fid}" in owner.text
    client_copy = next(e for e in new if e.to == [f["recipients"][0]["email"]])
    assert client_copy.from_name == "Acme Test Co" and client_copy.reply_to == "owner@example.com"
    assert "finalized" in [e["type"] for e in f["events"]]

    # Running it again changes nothing: same PDF, no second round of emails or events.
    await finalize.handle_finalize_file(
        _ctx(app), {"org_id": str(file_doc["org_id"]), "file_id": fid}
    )
    await worker(app).drain()
    again = (await client.get(f"/api/files/{fid}")).json()
    assert again["final_sha256"] == f["final_sha256"]
    assert len(outbox.outbox) == before + len(new)
    assert [e["type"] for e in again["events"]].count("finalized") == 1
    await signer.aclose()


def _ctx(app: FastAPI) -> Any:
    from app.worker.queue import JobContext

    return JobContext(app.state.db.db, {"_id": ObjectId()}, app.state.services)


async def test_two_runs_at_once_keep_one_signed_pdf(
    app: FastAPI, client: httpx.AsyncClient, outbox: Any
) -> None:
    await signup(client)
    _, f, signer, _ = await completed(app, client, outbox)
    await signer.aclose()
    db = app.state.db.db
    doc = await db.files.find_one({"_id": ObjectId(f["id"])})
    tenant = Tenant(db, doc["org_id"])
    services = app.state.services
    first = await finalize.build(tenant, services["blobs"], services["settings"], doc, "Acme")
    # A second worker that read the file before the first finished.
    second = await finalize.build(tenant, services["blobs"], services["settings"], doc, "Acme")
    assert second["final_blob_id"] == first["final_blob_id"]
    assert second["final_sha256"] == first["final_sha256"]
    finals = await db["blobs.files"].count_documents({"metadata.kind": "file_final"})
    assert finals == 1


async def test_a_failed_finalize_shows_with_try_again(
    app: FastAPI, client: httpx.AsyncClient, outbox: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    await signup(client)
    _, f, signer, tokens = await completed(app, client, outbox)

    def broken(*args: Any, **kwargs: Any) -> bytes:
        raise PermanentJobError("the PDF library gave up")

    monkeypatch.setattr(pdf_engine, "finalize_pdf", broken)
    await worker(app).drain()
    f = (await client.get(f"/api/files/{f['id']}")).json()
    assert f["finalized_at"] is None and "gave up" in f["finalize_error"]
    assert "finalize_failed" in [e["type"] for e in f["events"]]
    assert (await signer.get(f"/api/sign/{tokens[0]}")).json()["signed_pdf_ready"] is False

    monkeypatch.undo()
    r = await client.post(f"/api/files/{f['id']}/finalize")
    assert r.status_code == 200 and r.json()["finalize_error"] is None
    await worker(app).drain()
    f = (await client.get(f"/api/files/{f['id']}")).json()
    assert f["finalized_at"] and "finalize_retried" in [e["type"] for e in f["events"]]
    # Nothing to retry once it's done, and only a completed file can be finalized.
    assert (await client.post(f"/api/files/{f['id']}/finalize")).status_code == 200
    _, other = await sent(app, client)
    assert (await client.post(f"/api/files/{other['id']}/finalize")).status_code == 409
    await signer.aclose()


async def test_a_signed_copy_that_cannot_be_emailed_shows_on_the_file(
    app: FastAPI, client: httpx.AsyncClient, outbox: Any
) -> None:
    class Rejecting:
        name = "rejecting"

        async def send(self, email: OutgoingEmail) -> SendResult:
            if email.attachments:
                raise EmailPermanentError("gmail send HTTP 400: Invalid To header")
            return await outbox.send(email)

        async def check(self) -> None:
            return None

    await signup(client)
    _, f, signer, _ = await completed(app, client, outbox)
    await signer.aclose()
    w = Worker(
        app.state.db.db, build_registry(), services={**app.state.services, "email": Rejecting()}
    )
    await w.drain()
    f = (await client.get(f"/api/files/{f['id']}")).json()
    assert f["finalized_at"]  # the PDF exists and downloads; only the emails failed
    failed = [e for e in f["events"] if e["type"] == "email_failed"]
    assert len(failed) == 3 and {e["data"]["reason"] for e in failed} == {"signed_copy"}


async def test_other_companies_cannot_download_it(
    app: FastAPI, client: httpx.AsyncClient, second_client: httpx.AsyncClient, outbox: Any
) -> None:
    await signup(client)
    _, f, signer, _ = await completed(app, client, outbox)
    await signer.aclose()
    await worker(app).drain()
    await signup(second_client, email="other@example.com", org_name="Other Test Co")
    for method, path in [
        ("GET", f"/api/files/{f['id']}/signed.pdf"),
        ("POST", f"/api/files/{f['id']}/finalize"),
    ]:
        r = await second_client.request(method, path)
        assert r.status_code == 404, (method, path, r.status_code)
    # A signing link that isn't valid gets nothing either.
    assert (await second_client.get("/api/sign/not-a-real-token/signed.pdf")).status_code == 404


# ---- the PDF itself ----------------------------------------------------------------------------
def test_certificate_flows_onto_more_pages_and_wraps_long_values() -> None:
    doc = pymupdf.open()
    doc.new_page(width=612, height=792)
    pdf = doc.tobytes()
    doc.close()
    agent = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) " * 6
    sections = [
        pdf_engine.CertSection("Document", [pdf_engine.CertRow("File ID", "x" * 200, True)])
    ]
    for i in range(12):
        sections.append(
            pdf_engine.CertSection(
                f"{i + 1}. Signer O’Brien – Łukasz",
                [pdf_engine.CertRow("Browser (user agent)", agent)] * 4,
                minor=True,
            )
        )
    out = pdf_engine.finalize_pdf(pdf, sections, title="Certificate", subtitle="s", footer="f")
    with pymupdf.open(stream=out, filetype="pdf") as d:
        assert d.page_count >= 4
        texts = [p.get_text() for p in d]
    every = "\n".join(texts)
    assert "O'Brien - Lukasz" in every and "·" not in every.replace("  ·  ", "")
    assert f"Certificate page 1 of {len(texts) - 1}" in texts[1]
    # A signer's block is never split across two pages.
    for i in range(12):
        heading = re.compile(rf"(?m)^{i + 1}\. Signer")
        holders = [n for n, t in enumerate(texts) if heading.search(t)]
        assert len(holders) == 1


def test_latin1_makes_phone_punctuation_printable() -> None:
    assert pdf_engine.latin1("O’Brien “Jr” – Zöe Łucja …") == ('O\'Brien "Jr" - Zöe Lucja ...')
    assert pdf_engine.latin1("ąę") == "ae"  # accents outside Latin-1 dropped
    assert pdf_engine.latin1("李") == "?"  # other scripts can't be drawn


def test_stamped_values_keep_curly_apostrophes_readable() -> None:
    doc = pymupdf.open()
    doc.new_page(width=612, height=792)
    pdf = doc.tobytes()
    doc.close()
    stamp = pdf_engine.Stamp(1, pdf_engine.Box(72, 90, 400, 110), "text", "Sea’n O’Brien")
    out = pdf_engine.stamp(pdf, [stamp])
    assert "Sea'n O'Brien" in text_of(out)
