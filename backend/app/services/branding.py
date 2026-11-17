"""A firm's logo and colour on its signing emails and signing page.

The logo is a PNG or JPEG of at most 512 KB, checked by its first bytes (never by its name or
the browser's claim), stored in GridFS and served publicly at a URL that includes its blob id, so
a new logo gets a new URL and the old one can be cached forever. The colour is a hex value; the
text on it is white or near-black, whichever reads better, so any colour stays readable.

With nothing set, everything looks exactly as before. "Secured by Kickoff" always stays.
"""

import re
from typing import Any

from bson import ObjectId
from gridfs import AsyncGridFSBucket
from pymongo import ReturnDocument
from pymongo.asynchronous.database import AsyncDatabase

from app.repo import parse_id
from app.services import storage
from app.services.errors import Invalid

MAX_LOGO = 512 * 1024
DEFAULT_COLOR = "#2f5bea"
WHITE = "#ffffff"
DARK = "#0f172a"
_HEX = re.compile(r"^#[0-9a-f]{6}$")
_TYPES = ((b"\x89PNG\r\n\x1a\n", "image/png"), (b"\xff\xd8\xff", "image/jpeg"))


def logo_type(data: bytes) -> str | None:
    return next((mime for magic, mime in _TYPES if data.startswith(magic)), None)


def _luminance(color: str) -> float:
    def channel(c: int) -> float:
        s = c / 255
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = (int(color[i : i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(a: str, b: str) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def ink(color: str) -> str:
    """The text colour on a button of this colour: white if it reads at 4.5:1 or better, else
    near-black, else black (pure black or white always reaches 4.5:1 on any colour)."""
    for candidate in (WHITE, DARK):
        if contrast(color, candidate) >= 4.5:
            return candidate
    return "#000000"


def text_on_white(color: str) -> str:
    """Links in the colour on a white page; a colour too light to read there falls back to
    near-black."""
    return color if contrast(color, WHITE) >= 4.5 else DARK


def normalize_color(value: str) -> str:
    v = value.strip().lower()
    if not v.startswith("#"):
        v = "#" + v
    if re.fullmatch(r"#[0-9a-f]{3}", v):
        v = "#" + "".join(c * 2 for c in v[1:])
    if not _HEX.match(v):
        raise Invalid(["Enter the colour as a hex value, like #1e40af."])
    return v


def logo_path(org_id: ObjectId, branding: dict[str, Any] | None) -> str | None:
    logo = (branding or {}).get("logo")
    return f"/api/branding/{org_id}/logo/{logo['blob_id']}" if logo else None


def public(org: dict[str, Any] | None, base_url: str = "") -> dict[str, Any]:
    """What a page or email needs. `color` is None when the firm hasn't chosen one."""
    branding = (org or {}).get("branding") or {}
    color = branding.get("color")
    path = logo_path(org["_id"], branding) if org else None
    return {
        "logo_url": f"{base_url.rstrip('/')}{path}" if path else None,
        "color": color,
        "ink": ink(color) if color else WHITE,
        "text": text_on_white(color) if color else DEFAULT_COLOR,
    }


async def load(db: AsyncDatabase, org_id: ObjectId) -> dict[str, Any] | None:
    org: dict[str, Any] | None = await db.orgs.find_one({"_id": org_id}, {"name": 1, "branding": 1})
    return org


# ---- changes, from Settings --------------------------------------------------------------------
async def set_logo(
    db: AsyncDatabase, bucket: AsyncGridFSBucket, org_id: ObjectId, data: bytes
) -> dict[str, Any]:
    if len(data) > MAX_LOGO:
        raise Invalid(["The logo is larger than 512 KB. Upload a smaller PNG or JPG."])
    mime = logo_type(data)
    if mime is None:
        raise Invalid(["The logo must be a PNG or JPG image."])
    blob = await storage.put(
        bucket, data, filename="logo", org_id=org_id, kind="logo", content_type=mime
    )
    before = await db.orgs.find_one_and_update(
        {"_id": org_id},
        {"$set": {"branding.logo": {"blob_id": blob, "type": mime, "size": len(data)}}},
        return_document=ReturnDocument.BEFORE,
    )
    old = ((before or {}).get("branding") or {}).get("logo")
    if old:
        await storage.delete(bucket, old["blob_id"])
    return await load(db, org_id) or {}


async def remove_logo(db: AsyncDatabase, bucket: AsyncGridFSBucket, org_id: ObjectId) -> None:
    before = await db.orgs.find_one_and_update(
        {"_id": org_id}, {"$unset": {"branding.logo": ""}}, return_document=ReturnDocument.BEFORE
    )
    old = ((before or {}).get("branding") or {}).get("logo")
    if old:
        await storage.delete(bucket, old["blob_id"])


async def set_color(db: AsyncDatabase, org_id: ObjectId, color: str | None) -> None:
    if color is None or not color.strip():
        await db.orgs.update_one({"_id": org_id}, {"$unset": {"branding.color": ""}})
        return
    await db.orgs.update_one({"_id": org_id}, {"$set": {"branding.color": normalize_color(color)}})


async def logo_file(
    db: AsyncDatabase, bucket: AsyncGridFSBucket, org_id: str, blob_id: str
) -> tuple[bytes, str] | None:
    """The public logo, only if it's that firm's current one."""
    oid, bid = parse_id(org_id), parse_id(blob_id)
    if oid is None or bid is None:
        return None
    org = await load(db, oid)
    logo = ((org or {}).get("branding") or {}).get("logo")
    if not logo or logo["blob_id"] != bid:
        return None
    return await storage.get(bucket, bid), logo["type"]
