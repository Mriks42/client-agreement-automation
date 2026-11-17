"""Kickoff MCP server: lets an AI assistant (Claude Desktop, Claude Code, or any other MCP client)
prepare a firm's client agreements in Kickoff and follow them until they're signed.

It is a thin layer over Kickoff's public API (/api/v1) and acts with a Kickoff API key, so it can
do only what that API allows: list the approved agreements, open a file as a draft, check files,
and download a signed agreement. It cannot send anything. A person at the firm checks each
draft's preview and presses Confirm and send in Kickoff (DECISIONS.md D5).

Configuration, as environment variables:
    KICKOFF_API_KEY        required. In Kickoff: Settings, then API keys, then Create key.
    KICKOFF_URL            optional. Defaults to the hosted app.
    KICKOFF_DOWNLOAD_DIR   optional. Where signed agreements are saved (default ~/Downloads).
"""

import hashlib
import logging
import os
import re
from pathlib import Path
from typing import Annotated, Any, Literal

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

DEFAULT_URL = "https://kickoff-r713.onrender.com"
TIMEOUT_S = 60.0  # the hosted free tier can take a while to wake up

INSTRUCTIONS = """\
Kickoff gets a firm's standard client agreement signed on every file (one job for one client).
The usual flow:
1. list_agreements: find the agreement, its signers (roles, in signing order) and the details a
   file fills in.
2. open_file: prepare a draft with this client's details. Nothing is sent. Give the user the
   draft's url: a person must check the preview there and press Confirm and send. You can't send.
3. list_files / get_file: see who has signed and who it's waiting for.
4. download_signed_agreement: save the signed PDF once everyone has signed.
Never invent signers' names or emails, or the agreement's details: ask the user for what you
don't know."""

mcp = FastMCP("kickoff", instructions=INSTRUCTIONS, log_level="WARNING")
logging.getLogger("httpx").setLevel(
    logging.WARNING
)  # one line per request is noise in a client's log

# Tests swap in an httpx.MockTransport here.
_transport: httpx.AsyncBaseTransport | None = None

READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=True)


class Signer(BaseModel):
    role: str = Field(description="The signer's role key from list_agreements, e.g. 'client'")
    name: str = Field(description="The signer's full name")
    email: str = Field(description="Where their signing link is sent")


# ---- talking to Kickoff ------------------------------------------------------------------------
def _client() -> httpx.AsyncClient:
    key = os.environ.get("KICKOFF_API_KEY", "").strip()
    if not key:
        raise ToolError(
            "KICKOFF_API_KEY isn't set. Create a key in Kickoff (Settings, then API keys) and add "
            "it to this server's configuration."
        )
    return httpx.AsyncClient(
        base_url=os.environ.get("KICKOFF_URL", DEFAULT_URL).rstrip("/"),
        headers={"authorization": f"Bearer {key}", "user-agent": "kickoff-mcp/0.1"},
        timeout=TIMEOUT_S,
        transport=_transport,
    )


def _problem(r: httpx.Response) -> str:
    """Kickoff's own explanation of an error, in words a person can act on."""
    try:
        detail = r.json().get("detail")
    except ValueError:
        detail = None
    if isinstance(detail, str):
        return detail
    if isinstance(detail, dict):
        return " ".join([str(detail.get("message", "")), *map(str, detail.get("problems", []))])
    if isinstance(detail, list):  # request validation: [{"loc": [...], "msg": ...}]
        return "; ".join(
            f"{'.'.join(str(p) for p in e.get('loc', [])[1:])}: {e.get('msg', '')}" for e in detail
        )
    return f"Kickoff answered HTTP {r.status_code}."


async def _request(method: str, path: str, **kwargs: Any) -> httpx.Response:
    async with _client() as client:
        try:
            r = await client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise ToolError(f"Couldn't reach Kickoff ({type(exc).__name__}). Try again.") from exc
    if r.status_code == 401:
        raise ToolError(
            "Kickoff refused the API key. It may have been revoked: create a new one in "
            "Settings, then API keys."
        )
    if r.status_code >= 400:
        raise ToolError(_problem(r))
    return r


def _summary(f: dict[str, Any]) -> dict[str, Any]:
    """A file in the shape an assistant needs to answer "where does it stand?"."""
    active = next((s for s in f["signers"] if s["status"] == "active"), None)
    return {
        "id": f["id"],
        "title": f["title"],
        "status": f["status"],
        "agreement": f["agreement"]["name"],
        "signers": [
            {
                "name": s["name"],
                "role": s["role_label"],
                "status": s["status"],
                "signed_at": s["signed_at"],
            }
            for s in f["signers"]
        ],
        "waiting_for": {"name": active["name"], "email": active["email"]} if active else None,
        "signed_pdf_ready": bool(f.get("signed_pdf")),
        "reference": (f.get("metadata") or {}).get("reference"),
        "url": f["url"],
    }


# ---- tools ---------------------------------------------------------------------------------------
@mcp.tool(title="List agreements", annotations=READ_ONLY)
async def list_agreements() -> list[dict[str, Any]]:
    """The firm's approved agreements. Each has its id, its signers in signing order (role key and
    label), and the fields a new file fills in (key, label, type, required). Use the id and the
    keys with open_file."""
    r = await _request("GET", "/api/v1/templates")
    return list(r.json())


@mcp.tool(
    title="Prepare a file (draft)",
    annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True
    ),
)
async def open_file(
    agreement_id: Annotated[str, Field(description="An agreement id from list_agreements")],
    signers: Annotated[
        list[Signer], Field(description="One per signer role of the agreement, any order")
    ],
    values: Annotated[
        dict[str, str | bool] | None,
        Field(
            description="The agreement's fields by key (from list_agreements): text, dates as "
            "YYYY-MM-DD, true or false for checkboxes. Leave out what isn't known."
        ),
    ] = None,
    title: Annotated[
        str | None, Field(description="How the file is listed, e.g. the client and the job")
    ] = None,
    reference: Annotated[
        str | None,
        Field(
            description="Your own reference for this job, e.g. a CRM deal id. Preparing a file "
            "again with the same reference returns the same file instead of a duplicate."
        ),
    ] = None,
) -> dict[str, Any]:
    """Prepares a file: the agreement with this client's details, as a DRAFT. Nothing is sent.
    Give the user the returned url. A person at the firm checks the exact PDF there and presses
    Confirm and send."""
    body: dict[str, Any] = {
        "template_id": agreement_id,
        "signers": [s.model_dump() for s in signers],
        "values": values or {},
    }
    if title:
        body["title"] = title
    headers = {}
    if reference:
        body["metadata"] = {"reference": reference}
        headers["idempotency-key"] = f"mcp:{reference}"
    f = (await _request("POST", "/api/v1/files", json=body, headers=headers)).json()
    return {
        **_summary(f),
        "next_step": f"Nothing has been sent yet. Open {f['url']} to check the preview, then "
        "press Confirm and send.",
    }


FileStatus = Literal["waiting", "draft", "completed", "declined", "expired", "voided", "all"]
_STATUSES: dict[str, str | None] = {
    "waiting": "sent,partially_signed",
    "draft": "draft",
    "completed": "completed",
    "declined": "declined",
    "expired": "expired",
    "voided": "voided",
    "all": None,
}


@mcp.tool(title="List files", annotations=READ_ONLY)
async def list_files(
    status: Annotated[
        FileStatus,
        Field(
            description="'waiting': sent and not yet signed by everyone. 'draft': prepared but "
            "not sent. 'all': every file."
        ),
    ] = "waiting",
    limit: Annotated[int, Field(ge=1, le=100, description="At most this many")] = 20,
) -> list[dict[str, Any]]:
    """The firm's files (one per client job), most recent activity first, with who has signed and
    who each one is waiting for."""
    params: dict[str, Any] = {"limit": limit}
    if (wanted := _STATUSES[status]) is not None:
        params["status"] = wanted
    r = await _request("GET", "/api/v1/files", params=params)
    return [_summary(f) for f in r.json()]


@mcp.tool(title="Get a file", annotations=READ_ONLY)
async def get_file(
    file_id: Annotated[str, Field(description="A file id from open_file or list_files")],
) -> dict[str, Any]:
    """One file in detail: status, each signer's status and signing time, who it's waiting for,
    and whether the signed PDF is ready."""
    f = (await _request("GET", f"/api/v1/files/{file_id}")).json()
    return {**_summary(f), "sent_at": f["sent_at"], "completed_at": f["completed_at"]}


@mcp.tool(
    title="Download the signed agreement",
    annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True
    ),
)
async def download_signed_agreement(
    file_id: Annotated[str, Field(description="A completed file's id")],
    folder: Annotated[
        str | None,
        Field(description="Where to save it on this computer. Default: the Downloads folder."),
    ] = None,
) -> dict[str, Any]:
    """Saves the signed agreement, with its audit certificate, as a PDF on this computer. Returns
    where it was saved and its SHA-256 fingerprint (the one Kickoff has on record)."""
    f = (await _request("GET", f"/api/v1/files/{file_id}")).json()
    if not f.get("signed_pdf"):
        if f["status"] == "completed":
            raise ToolError("Everyone has signed. The signed PDF is being made: try in a minute.")
        raise ToolError(f"This file isn't signed by everyone yet (status: {f['status']}).")
    pdf = (await _request("GET", f"/api/v1/files/{file_id}/signed.pdf")).content
    sha256 = hashlib.sha256(pdf).hexdigest()
    if sha256 != f["signed_pdf"]["sha256"]:
        raise ToolError("The download doesn't match the fingerprint on record. Try again.")
    where = Path(
        folder or os.environ.get("KICKOFF_DOWNLOAD_DIR") or Path.home() / "Downloads"
    ).expanduser()
    where.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^A-Za-z0-9 ._-]", "", f["title"]).strip() or "agreement"
    path = where / f"{stem} (signed).pdf"
    path.write_bytes(pdf)
    return {"saved_to": str(path), "sha256": sha256, "bytes": len(pdf), "file": _summary(f)}


def main() -> None:
    mcp.run()  # stdio: the client (Claude Desktop, Claude Code, ...) starts this process


if __name__ == "__main__":
    main()
