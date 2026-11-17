"""A file as the firm's other systems see it: the public API's response, and `data.file` in every
webhook. Kept apart from files.py so the webhook job can build it without an import cycle."""

from datetime import UTC, datetime
from typing import Any


def iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def file_view(f: dict[str, Any], base_url: str) -> dict[str, Any]:
    fid = str(f["_id"])
    base = base_url.rstrip("/")
    api = f.get("api") or {}
    tpl = f["template"]
    return {
        "id": fid,
        "title": f["title"],
        # draft: waiting for a person at the firm to check the preview and send it.
        "status": f["status"],
        "agreement": {"id": str(tpl["id"]), "name": tpl["name"], "version": tpl["version"]},
        "signers": [
            {
                "role": r["role"],
                "role_label": r["role_label"],
                "order": r["order"],
                "name": r["name"],
                "email": r["email"],
                "status": r["status"],
                "signed_at": iso(r.get("signed_at")),
                "declined_at": iso(r.get("declined_at")),
            }
            for r in f["recipients"]
        ],
        "metadata": api.get("metadata") or {},
        "created_via": "api" if api else "app",
        "created_at": iso(f.get("created_at")),
        "sent_at": iso(f.get("sent_at")),
        "completed_at": iso(f.get("completed_at")),
        "declined_at": iso(f.get("declined_at")),
        "voided_at": iso(f.get("voided_at")),
        "expired_at": iso(f.get("expired_at")),
        # Present once the signed PDF (with its audit certificate) exists.
        "signed_pdf": {
            "sha256": f["final_sha256"],
            "url": f"{base}/api/v1/files/{fid}/signed.pdf",
        }
        if f.get("finalized_at") and f.get("final_sha256")
        else None,
        "url": f"{base}/files/{fid}",
    }
