"""The Kickoff MCP server's tools, against a fake Kickoff API (httpx.MockTransport)."""

import hashlib
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from mcp.server.fastmcp.exceptions import ToolError

from kickoff_mcp import server

PDF = b"%PDF-1.7 signed agreement with its certificate"
FILE: dict[str, Any] = {
    "id": "f1",
    "title": "Acme Test Co",
    "status": "sent",
    "agreement": {"id": "t1", "name": "Client agreement", "version": 1},
    "signers": [
        {
            "role": "client",
            "role_label": "Client",
            "order": 1,
            "name": "Jane Client",
            "email": "jane@example.com",
            "status": "active",
            "signed_at": None,
            "declined_at": None,
        },
        {
            "role": "firm",
            "role_label": "Provider",
            "order": 2,
            "name": "Sam Provider",
            "email": "sam@example.com",
            "status": "pending",
            "signed_at": None,
            "declined_at": None,
        },
    ],
    "metadata": {"reference": "deal-7"},
    "created_via": "api",
    "created_at": "2026-11-17T12:00:00Z",
    "sent_at": "2026-11-17T12:01:00Z",
    "completed_at": None,
    "signed_pdf": None,
    "url": "https://kickoff.example/files/f1",
}
DONE = {
    **FILE,
    "status": "completed",
    "completed_at": "2026-11-17T13:00:00Z",
    "signed_pdf": {"sha256": hashlib.sha256(PDF).hexdigest(), "url": "https://k/x.pdf"},
}


class FakeKickoff:
    def __init__(self, file: dict[str, Any] = FILE, status: int = 200) -> None:
        self.file, self.status = file, status
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status != 200:
            return httpx.Response(self.status, json={"detail": "Agreement not found."})
        path = request.url.path
        if path == "/api/v1/templates":
            return httpx.Response(200, json=[{"id": "t1", "name": "Client agreement"}])
        if path == "/api/v1/files" and request.method == "POST":
            return httpx.Response(201, json={**self.file, "status": "draft"})
        if path == "/api/v1/files":
            return httpx.Response(200, json=[self.file])
        if path == "/api/v1/files/f1":
            return httpx.Response(200, json=self.file)
        if path == "/api/v1/files/f1/signed.pdf":
            return httpx.Response(200, content=PDF)
        return httpx.Response(404, json={"detail": "File not found."})


@pytest.fixture
def kickoff(monkeypatch: pytest.MonkeyPatch) -> FakeKickoff:
    fake = FakeKickoff()
    monkeypatch.setenv("KICKOFF_API_KEY", "kk_live_test")
    monkeypatch.setattr(server, "_transport", httpx.MockTransport(fake))
    return fake


async def test_the_tools_are_registered_with_their_inputs() -> None:
    tools = {t.name: t for t in await server.mcp.list_tools()}
    assert set(tools) == {
        "list_agreements",
        "open_file",
        "list_files",
        "get_file",
        "download_signed_agreement",
    }
    schema = tools["open_file"].inputSchema
    assert set(schema["required"]) == {"agreement_id", "signers"}
    assert tools["list_agreements"].annotations.readOnlyHint is True
    assert "DRAFT" in (tools["open_file"].description or "")


async def test_open_file_prepares_a_draft_and_says_how_to_send_it(kickoff: FakeKickoff) -> None:
    out = await server.open_file(
        agreement_id="t1",
        signers=[server.Signer(role="client", name="Jane Client", email="jane@example.com")],
        values={"client_name": "Acme Test Co", "start_date": "2026-12-01"},
        title="Acme Test Co",
        reference="deal-7",
    )
    request = kickoff.requests[-1]
    body = json.loads(request.content)
    assert request.headers["authorization"] == "Bearer kk_live_test"
    assert request.headers["idempotency-key"] == "mcp:deal-7"  # a retry can't make a duplicate
    assert body["template_id"] == "t1" and body["metadata"] == {"reference": "deal-7"}
    assert body["signers"] == [
        {"role": "client", "name": "Jane Client", "email": "jane@example.com"}
    ]
    assert out["status"] == "draft" and "Confirm and send" in out["next_step"]
    assert out["url"] in out["next_step"]


async def test_list_files_waiting_means_sent_and_not_signed_by_everyone(
    kickoff: FakeKickoff,
) -> None:
    [f] = await server.list_files()
    assert kickoff.requests[-1].url.params["status"] == "sent,partially_signed"
    assert f["waiting_for"] == {"name": "Jane Client", "email": "jane@example.com"}
    await server.list_files(status="all", limit=5)
    assert "status" not in kickoff.requests[-1].url.params
    assert kickoff.requests[-1].url.params["limit"] == "5"


async def test_download_saves_the_signed_pdf_and_checks_its_fingerprint(
    kickoff: FakeKickoff, tmp_path: Path
) -> None:
    with pytest.raises(ToolError, match="isn't signed by everyone"):
        await server.download_signed_agreement(file_id="f1", folder=str(tmp_path))
    kickoff.file = DONE
    out = await server.download_signed_agreement(file_id="f1", folder=str(tmp_path))
    assert Path(out["saved_to"]).read_bytes() == PDF
    assert out["sha256"] == DONE["signed_pdf"]["sha256"]
    kickoff.file = {**DONE, "signed_pdf": {"sha256": "0" * 64, "url": "x"}}
    with pytest.raises(ToolError, match="doesn't match"):
        await server.download_signed_agreement(file_id="f1", folder=str(tmp_path))


async def test_errors_come_back_as_words_a_person_can_act_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("KICKOFF_API_KEY", raising=False)
    with pytest.raises(ToolError, match="KICKOFF_API_KEY isn't set"):
        await server.list_agreements()
    monkeypatch.setenv("KICKOFF_API_KEY", "kk_live_revoked")
    monkeypatch.setattr(server, "_transport", httpx.MockTransport(FakeKickoff(status=401)))
    with pytest.raises(ToolError, match="refused the API key"):
        await server.list_agreements()
    monkeypatch.setattr(server, "_transport", httpx.MockTransport(FakeKickoff(status=404)))
    with pytest.raises(ToolError, match="Agreement not found"):
        await server.get_file(file_id="nope")
