import email
import json
from email import policy
from typing import Any

import httpx
import pytest

from app.services.email import (
    Attachment,
    EmailPermanentError,
    EmailTransientError,
    GmailApiSender,
    OutgoingEmail,
)
from app.services.email.base import build_mime
from app.worker.handlers import enqueue_email
from app.worker.queue import Worker

MSG = OutgoingEmail(
    to=["jane.client@example.com"],
    subject="Please sign",
    text="Sign here: https://example.test/sign/abc",
    html="<p>Sign</p>",
    from_name="Acme Test Co",
    reply_to="owner@acme.example",
    headers={"X-Agreement-Ref": "file1:rec1"},
)


def test_build_mime_sets_sender_identity_and_correlation_header() -> None:
    msg, message_id = build_mime(
        OutgoingEmail(**{**MSG.__dict__, "attachments": [Attachment("a.pdf", b"%PDF-1.7")]}),
        "sender@gmail.example",
    )
    parsed = email.message_from_bytes(msg.as_bytes(), policy=policy.default)
    assert parsed["From"] == "Acme Test Co <sender@gmail.example>"
    assert parsed["Reply-To"] == "owner@acme.example"
    assert parsed["X-Agreement-Ref"] == "file1:rec1"
    assert parsed["Message-ID"] == message_id
    names = [p.get_filename() for p in parsed.iter_attachments()]
    assert names == ["a.pdf"]


class FakeGoogle:
    """Scriptable stand-in for Google's token and Gmail send endpoints."""

    def __init__(self, send_statuses: list[int], token_status: int = 200) -> None:
        self.send_statuses = send_statuses
        self.token_status = token_status
        self.token_calls = 0
        self.sent: list[bytes] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            self.token_calls += 1
            if self.token_status != 200:
                return httpx.Response(self.token_status, json={"error": "invalid_grant"})
            return httpx.Response(
                200, json={"access_token": f"t{self.token_calls}", "expires_in": 3600}
            )
        status = self.send_statuses.pop(0)
        if status == 200:
            assert request.headers["content-type"] == "message/rfc822"
            self.sent.append(request.content)
            return httpx.Response(200, json={"id": "gm-123"})
        body: dict[str, Any] = {"error": {"message": "nope", "errors": [{"reason": "x"}]}}
        return httpx.Response(status, content=json.dumps(body))


def make_sender(fake: FakeGoogle) -> GmailApiSender:
    return GmailApiSender(
        "cid",
        "secret",
        "refresh",
        "sender@gmail.example",
        http=httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)),
    )


async def test_gmail_send_success_and_token_cached() -> None:
    fake = FakeGoogle([200, 200])
    sender = make_sender(fake)
    r1 = await sender.send(MSG)
    await sender.send(MSG)
    assert r1.provider_message_id == "gm-123"
    assert fake.token_calls == 1
    assert b"Subject: Please sign" in fake.sent[0]


async def test_gmail_refreshes_token_once_on_401() -> None:
    fake = FakeGoogle([401, 200])
    result = await make_sender(fake).send(MSG)
    assert result.provider_message_id == "gm-123"
    assert fake.token_calls == 2


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_gmail_transient_errors(status: int) -> None:
    with pytest.raises(EmailTransientError):
        await make_sender(FakeGoogle([status])).send(MSG)


async def test_gmail_bad_request_is_permanent() -> None:
    with pytest.raises(EmailPermanentError):
        await make_sender(FakeGoogle([400])).send(MSG)


async def test_revoked_refresh_token_is_permanent() -> None:
    with pytest.raises(EmailPermanentError, match="invalid_grant"):
        await make_sender(FakeGoogle([], token_status=400)).check()


async def test_send_email_job_goes_through_queue_with_attachment(app: Any, outbox: Any) -> None:
    db = app.state.db
    blob_id = await db.blobs.upload_from_stream("signed.pdf", b"%PDF-1.7 test")
    job_id = await enqueue_email(
        db.db,
        {
            "to": ["jane.client@example.com"],
            "subject": "Signed",
            "text": "Attached.",
            "attachments": [{"blob_id": str(blob_id), "filename": "signed.pdf"}],
        },
    )
    from app.worker.handlers import build_registry

    worker = Worker(db.db, build_registry(), services={"email": outbox, "blobs": db.blobs})
    await worker.drain()
    job = await db.db.jobs.find_one({"_id": job_id})
    assert job["status"] == "done" and job["result"]["provider"] == "console"
    assert outbox.outbox[-1].attachments[0].content == b"%PDF-1.7 test"

    # Re-running a finished-but-reclaimed job must not send twice.
    await db.db.jobs.update_one({"_id": job_id}, {"$set": {"status": "pending"}})
    await worker.drain()
    assert len(outbox.outbox) == 1
