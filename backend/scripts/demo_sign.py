"""See the pipeline on your own agreement: detect fields, fill them with fake test data, sign as
fake signers, and write two PDFs next to each other:

    <name>-review.pdf   the fields the AI proposed (what a reviewer approves), colour per party
    <name>-signed.pdf   the agreement filled in and signed (values flattened into the page)

Usage (from backend/):
    uv run python -m scripts.demo_sign samples/agreement.pdf [more files...] [--offline]

Drop files in backend/samples/ (git-ignored, so nothing there is ever committed). DOCX needs
LibreOffice; without it locally, run inside the production image (see samples/README.md).
Online mode calls Claude (roughly 5-40 cents per document, depending on how many blanks it has).

This is a preview, not the signing flow: no emails, consent or audit certificate yet, and every
value is obviously fake test data.
"""

import argparse
import asyncio
import re
import sys
from datetime import date
from pathlib import Path

from app.config import Settings
from app.domain.fields import SENDER, Field, Rect, Role
from app.services import ingest, pdf_engine
from app.services.detection.llm import ClaudeLabeller
from app.services.detection.pipeline import Analysis, analyze
from app.services.pdf_engine import Box, Stamp

PALETTE = [(0.85, 0.2, 0.2), (0.15, 0.45, 0.85), (0.1, 0.6, 0.3), (0.6, 0.3, 0.7)]
SENDER_COLOR = (0.45, 0.45, 0.45)
FAKE_PEOPLE = {
    "client": ("Jane Client", "JC"),
    "firm": ("Sam Provider", "SP"),
    "other": ("Alex Other", "AO"),
}

# (pattern on key + label, fake value). First match wins; all values are obviously fake.
SENDER_VALUES = [
    (r"e-?mail", "jane.client@example.com"),
    (r"phone|tel", "555-0100"),
    (r"address", "100 Example Street, Testville, AZ 85001"),
    (
        r"(provider|firm|consultant|vendor|supplier).*name|name.*(provider|firm)",
        "Provider Test LLC",
    ),
    (r"(company|legal|official|client|customer|party|entity).*name|name of", "Acme Test Co"),
    (r"\bname\b|dear", "Jane Client"),
    (r"\bdays?\b|weeks?|months?|period of|length of time", "30"),
    (r"hourly|per hour|\brate\b", "$250"),
    (r"cents|percent|%", "10"),
    (r"fee|price|amount|cost|budget|retainer|deposit|\$|dollar|limit", "$5,000"),
    (r"law|state|country|jurisdiction|court|county", "Arizona"),
    (r"number|multiplier|#|\bno\b|count|percent", "3"),
    (r"payment (period|terms)|invoice", "30 days from receipt of invoice"),
    (r"unit|days, weeks|period|cadence", "days"),
    (r"title|position", "Director"),
    (
        r"matter|project|services|scope|describe|description|deliverable|obligation",
        "Test engagement: a strategy workshop followed by monthly advisory sessions.",
    ),
]


def fake_sender_value(f: Field) -> str:
    if f.type == "date":
        return date.today().strftime("%B %-d, %Y")
    hay = f"{f.key} {f.label}".lower().replace("_", " ")
    for pattern, value in SENDER_VALUES:
        if re.search(pattern, hay):
            return value
    return "Sample value"


def signer_values(f: Field, role: Role) -> str:
    name, initials = FAKE_PEOPLE[role.kind]
    if f.type == "initials":
        return initials
    if f.type == "date_signed":
        return date.today().strftime("%B %-d, %Y")
    if f.type in ("name", "signature"):
        return name
    hay = f"{f.key} {f.label}".lower().replace("_", " ")
    if "title" in hay:
        return "Director"
    if re.search(r"e-?mail", hay):
        return f"{name.split()[0].lower()}@example.com"
    return fake_sender_value(f)


def to_box(r: Rect) -> Box:
    return Box(r.x0, r.y0, r.x1, r.y1)


def build_stamps(a: Analysis) -> list[Stamp]:
    roles = {r.key: r for r in a.roles}
    shared: dict[tuple[str, str], str] = {}  # fields sharing a key share one value
    stamps = []
    for f in a.fields:
        if f.type == "checkbox":
            if f.required:  # optional option boxes are left for the sender to choose
                stamps.append(Stamp(f.page, to_box(f.rect), "check"))
            continue
        if f.filled_by == SENDER:
            value = shared.setdefault((SENDER, f.key), fake_sender_value(f))
        else:
            value = shared.setdefault((f.filled_by, f.key), signer_values(f, roles[f.filled_by]))
        kind = "signature" if f.type in ("signature", "initials") else "text"
        redact = [to_box(r) for r in f.redact]
        stamps.append(Stamp(f.page, to_box(f.rect), kind, value, redact=redact))
    return stamps


async def run(path: Path, settings: Settings, offline: bool) -> None:
    print(f"\n=== {path.name}")
    data = await asyncio.to_thread(path.read_bytes)
    try:
        ing = await ingest.ingest(data, settings)
    except ingest.IngestError as exc:
        print(f"  can't use this file: {exc.message}")
        return
    labeller = None if offline else ClaudeLabeller.from_settings(settings)
    a = await analyze(ing.pdf, labeller=labeller)
    how = (
        f"Claude ({a.llm['model']}, {a.llm['duration_ms'] / 1000:.0f}s)"
        if a.llm
        else "keyword rules"
    )
    fell_back = f"; fell back because {a.fallback_reason}" if a.fallback_reason and labeller else ""
    print(f"  {a.original_page_count} page(s), detected by {how}{fell_back}")
    print("  signers, in order: " + ", ".join(f"{r.order}. {r.label} ({r.kind})" for r in a.roles))
    counts: dict[str, int] = {}
    for f in a.fields:
        who = "sender" if f.filled_by == SENDER else f.filled_by
        counts[who] = counts.get(who, 0) + 1
    print("  fields: " + ", ".join(f"{n} for {who}" for who, n in counts.items()))
    if a.appended_signature_page:
        print("  a signature page was added at the end")
    for w in a.warnings:
        print(f"  note: {w}")

    colors = {r.key: PALETTE[i % len(PALETTE)] for i, r in enumerate(a.roles)}
    review = pdf_engine.draw_fields(
        a.pdf,
        [
            (f.page, to_box(f.rect), f"{f.label} [{f.type}]", colors.get(f.filled_by, SENDER_COLOR))
            for f in a.fields
        ],
    )
    signed = pdf_engine.stamp(a.pdf, build_stamps(a))
    out = path.parent / "out"
    out.mkdir(exist_ok=True)
    (out / f"{path.stem}-review.pdf").write_bytes(review)
    (out / f"{path.stem}-signed.pdf").write_bytes(signed)
    print(f"  wrote out/{path.stem}-review.pdf and out/{path.stem}-signed.pdf")


async def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--offline", action="store_true", help="keyword rules only; no API calls")
    args = parser.parse_args()
    settings = Settings()
    if not args.offline and settings.anthropic_api_key is None:
        print("ANTHROPIC_API_KEY is not set (backend/.env); running with keyword rules only.")
        args.offline = True
    for path in args.files:
        await run(path, settings, args.offline)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
