"""The public verify page's lookup: is this PDF a signed original? The browser computes the
PDF's SHA-256 itself (the PDF never leaves the device) and asks here. No login: holding the
exact PDF is what lets someone ask, and the answer shows only what that PDF already shows."""

import re
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response

from app import repo

router = APIRouter(prefix="/api/verify", tags=["verify"])

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@router.get("/{sha256}")
async def verify(sha256: str, request: Request, response: Response) -> dict[str, Any]:
    digest = sha256.strip().lower()
    if not _SHA256.match(digest):
        raise HTTPException(
            status_code=422, detail="That isn't a SHA-256 fingerprint (64 letters and digits)."
        )
    response.headers["cache-control"] = "no-store"
    db = request.app.state.db.db
    f = await repo.find_file_by_final_sha256(db, digest)
    if f is None or not f.get("finalized_at"):
        return {"match": False}
    org = await db.orgs.find_one({"_id": f["org_id"]}, {"name": 1})
    return {
        "match": True,
        "agreement": f["template"]["name"],
        "title": f["title"],
        "firm": (org or {}).get("name") or "",
        "completed_at": f.get("completed_at") or f["finalized_at"],
        "signers": [
            {"name": r["name"], "role": r["role_label"]}
            for r in sorted(f["recipients"], key=lambda r: r["order"])
        ],
    }
