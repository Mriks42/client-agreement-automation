"""Generate the synthetic agreements used by the detection eval.

All content is invented test data (DECISIONS.md: test data only). Each fixture exercises a
layout an unseen agreement might have. `expected/<name>.json` records the ground truth:
which roles sign, and for each blank its page, the label text just before it, which
occurrence of that label it is on the page (reading order), its type and who fills it.

Run: uv run python -m evals.make_fixtures
"""

import io
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pymupdf

HERE = Path(__file__).parent
FIXTURES = HERE / "fixtures"
EXPECTED = HERE / "expected"

LETTER = pymupdf.paper_rect("letter")
CSS = """
body { font-family: sans-serif; font-size: 10.5pt; line-height: 1.35; }
h1 { font-size: 15pt; text-align: center; margin-bottom: 4pt; }
h2 { font-size: 11pt; margin-top: 10pt; margin-bottom: 2pt; }
p { margin-top: 3pt; margin-bottom: 3pt; text-align: justify; }
td { vertical-align: top; padding: 2pt 6pt 2pt 0; font-size: 10.5pt; }
.c { text-align: center; }
"""

BLANK = "_" * 26
SHORT = "_" * 14

BOILERPLATE = [
    (
        "Services",
        "Provider will perform the services described in the Scope of Work below "
        '(the "Services") with reasonable skill and care, using personnel of its choosing.',
    ),
    (
        "Fees and Payment",
        "Client will pay the fees stated in this Agreement. Invoices are due "
        "within thirty (30) days of receipt. Late amounts accrue interest at one percent (1%) per "
        "month or the maximum permitted by law, whichever is lower.",
    ),
    (
        "Term and Termination",
        "This Agreement begins on the Effective Date and continues until "
        "the Services are complete. Either party may terminate on fourteen (14) days' written "
        "notice. Client will pay for Services performed up to the termination date.",
    ),
    (
        "Confidentiality",
        "Each party will keep the other's non-public information confidential "
        "and use it only to perform this Agreement. This obligation survives for three (3) years "
        "after termination.",
    ),
    (
        "Intellectual Property",
        "On full payment, Client owns the deliverables created "
        "specifically for Client. Provider retains its pre-existing tools, know-how and general "
        "methods, and grants Client a perpetual licence to use them as part of the deliverables.",
    ),
    (
        "Limitation of Liability",
        "Neither party is liable for indirect or consequential "
        "losses. Each party's total liability is limited to the fees paid in the twelve (12) "
        "months before the claim.",
    ),
    (
        "Data Protection",
        "Provider will process any personal data only on Client's documented "
        "instructions and will apply appropriate technical and organisational security measures.",
    ),
    (
        "General",
        "This Agreement is the entire agreement between the parties on its subject. "
        "Changes must be in writing and signed by both parties. Signatures delivered "
        "electronically are binding.",
    ),
]


def clauses(start: int = 1, items: list[tuple[str, str]] | None = None) -> str:
    out = []
    for i, (title, body) in enumerate(items or BOILERPLATE, start=start):
        out.append(f"<h2>{i}. {title}</h2><p>{body}</p>")
    return "".join(out)


def write_story(path: Path, html: str) -> None:
    story = pymupdf.Story(html=html, user_css=CSS)
    writer = pymupdf.DocumentWriter(str(path))
    where = pymupdf.Rect(72, 66, LETTER.width - 72, LETTER.height - 66)
    more = True
    while more:
        device = writer.begin_page(LETTER)
        more, _ = story.place(where)
        story.draw(device)
        writer.end_page()
    writer.close()
    _normalize(path)


def _normalize(path: Path) -> None:
    """Fixed metadata so regenerated fixtures are byte-stable in git."""
    doc = pymupdf.open(path)
    doc.set_metadata(
        {
            "title": path.stem,
            "author": "eval fixtures",
            "creationDate": "",
            "modDate": "",
            "producer": "",
            "creator": "",
        }
    )
    data = doc.tobytes(garbage=4, deflate=True, no_new_id=True)
    doc.close()
    path.write_bytes(data)


def _zip_normalize(path: Path) -> None:
    """DOCX is a zip; zip entries carry the current time. Rewrite with a fixed timestamp."""
    buf = io.BytesIO()
    with zipfile.ZipFile(path) as src, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in sorted(src.infolist(), key=lambda i: i.filename):
            info = zipfile.ZipInfo(item.filename, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            dst.writestr(info, src.read(item.filename))
    path.write_bytes(buf.getvalue())


def sig_table_two_party(left_title: str, right_title: str) -> str:
    rows = "".join(
        f"<tr><td>{label} {BLANK}</td><td>{label} {BLANK}</td></tr>"
        for label in ("By:", "Name:", "Title:", "Date:")
    )
    return (
        f"<table style='width:100%; margin-top:14pt'><tr><td><b>{left_title}</b></td>"
        f"<td><b>{right_title}</b></td></tr>{rows}</table>"
    )


def field(
    page: int, label: str, ftype: str, filled_by: str, nth: int = 1, required: bool = True
) -> dict[str, Any]:
    return {
        "page": page,
        "label": label,
        "nth": nth,
        "type": ftype,
        "filled_by": filled_by,
        "required": required,
    }


# --------------------------------------------------------------------------------------------
def two_party_services() -> dict[str, Any]:
    """Classic two-party services agreement; side-by-side signature blocks; a decorative
    underscore rule that must NOT become a field."""
    html = f"""
    <h1>Consulting Services Agreement</h1>
    <p class='c'>between Northwind Test Advisory LLC ("Provider") and the client named below</p>
    <p>Client legal name: {BLANK}{BLANK}</p>
    <p>Client address: {BLANK}{BLANK}</p>
    <p>Effective Date: {SHORT}</p>
    <p class='c'>{"_" * 60}</p>
    {clauses()}
    <h2>9. Scope of Work and Fees</h2>
    <p>Project fee (USD): {SHORT}</p>
    <p>Project description: {BLANK}{BLANK}</p>
    <p>IN WITNESS WHEREOF, the parties have signed this Agreement as of the Effective Date.</p>
    {sig_table_two_party("NORTHWIND TEST ADVISORY LLC", "CLIENT")}
    """
    path = FIXTURES / "two_party_services.pdf"
    write_story(path, html)
    last = pymupdf.open(path).page_count
    return {
        "description": "Two-party services agreement, side-by-side signature blocks",
        "roles": {
            "provider": ["provider", "firm", "company", "consultant", "northwind"],
            "client": ["client", "customer"],
        },
        "fields": [
            field(1, "Client legal name:", "text", "sender"),
            field(1, "Client address:", "text", "sender"),
            field(1, "Effective Date:", "date", "sender"),
            field(last, "Project fee (USD):", "text", "sender"),
            field(last, "Project description:", "text", "sender"),
            field(last, "By:", "signature", "provider", nth=1),
            field(last, "Name:", "name", "provider", nth=1),
            field(last, "Title:", "text", "provider", nth=1),
            field(last, "Date:", "date_signed", "provider", nth=1),
            field(last, "By:", "signature", "client", nth=2),
            field(last, "Name:", "name", "client", nth=2),
            field(last, "Title:", "text", "client", nth=2),
            field(last, "Date:", "date_signed", "client", nth=2),
        ],
        "not_fields": [{"page": 1, "label": "", "why": "decorative separator rule"}],
    }


def single_party_engagement() -> dict[str, Any]:
    """Engagement letter already signed by the firm; only the client countersigns, stacked."""
    html = f"""
    <p>Bluefin Test Partners</p>
    <p>100 Example Avenue, Suite 5, Testville</p>
    <p>Date: {SHORT}</p>
    <p>Dear {BLANK}:</p>
    <p>Thank you for choosing Bluefin Test Partners. This letter confirms the terms on which we
    will act for you on the matter described below.</p>
    <p>Matter: {BLANK}{BLANK}</p>
    {clauses(items=BOILERPLATE[:5])}
    <p>Yours sincerely,</p>
    <p><i>/s/ Pat Example</i><br/>Pat Example, Managing Partner<br/>Bluefin Test Partners</p>
    <h2>Acceptance</h2>
    <p>I have read and agree to the terms of this letter.</p>
    <p>Signature: {BLANK}</p>
    <p>Printed name: {BLANK}</p>
    <p>Date: {SHORT}</p>
    """
    path = FIXTURES / "single_party_engagement.pdf"
    write_story(path, html)
    last = pymupdf.open(path).page_count
    return {
        "description": "Engagement letter; firm pre-signed (/s/), client signs a stacked block",
        "roles": {"client": ["client", "customer", "recipient"]},
        "fields": [
            field(1, "Date:", "date", "sender", nth=1),
            field(1, "Dear", "text", "sender"),
            field(1, "Matter:", "text", "sender"),
            field(last, "Signature:", "signature", "client"),
            field(last, "Printed name:", "name", "client"),
            field(last, "Date:", "date_signed", "client", nth=1 if last > 1 else 2),
        ],
    }


def bracket_placeholders() -> dict[str, Any]:
    """Inline [BRACKETED] placeholders inside running text, plus a signature line with an
    inline date on the same line."""
    html = f"""
    <h1>Master Services Agreement</h1>
    <p>This Master Services Agreement is made on [EFFECTIVE DATE] between Harbor Test Labs Inc.
    ("Company") and [CLIENT NAME], having its principal office at [CLIENT ADDRESS]
    ("Client").</p>
    <p>Client will pay Company a monthly retainer of [MONTHLY FEE] in advance.</p>
    {clauses(start=1, items=BOILERPLATE[2:])}
    <h2>Signatures</h2>
    <p>Harbor Test Labs Inc.</p>
    <p>Authorized signature: {BLANK} Date: {SHORT}</p>
    <p>[CLIENT NAME]</p>
    <p>Authorized signature: {BLANK} Date: {SHORT}</p>
    """
    path = FIXTURES / "bracket_placeholders.pdf"
    write_story(path, html)
    last = pymupdf.open(path).page_count
    return {
        "description": "Bracket placeholders in body text; signature + date on one line",
        "roles": {
            "company": ["company", "provider", "firm", "harbor"],
            "client": ["client", "customer"],
        },
        "fields": [
            field(1, "[EFFECTIVE DATE]", "date", "sender"),
            field(1, "[CLIENT NAME]", "text", "sender"),
            field(1, "[CLIENT ADDRESS]", "text", "sender"),
            field(1, "[MONTHLY FEE]", "text", "sender"),
            field(last, "[CLIENT NAME]", "text", "sender", nth=1 if last > 1 else 2),
            field(last, "Authorized signature:", "signature", "company", nth=1),
            field(last, "Date:", "date_signed", "company", nth=1),
            field(last, "Authorized signature:", "signature", "client", nth=2),
            field(last, "Date:", "date_signed", "client", nth=2),
        ],
        "placeholder_style": "bracket",
    }


def initials_every_page() -> dict[str, Any]:
    """Three pages with a client initials box in every footer."""
    html = f"""
    <h1>Client Onboarding Agreement</h1>
    <p>Client: {BLANK}</p>
    {clauses()}{clauses(start=9, items=BOILERPLATE[:6])}
    <h2>Signature</h2>
    <p>Client signature: {BLANK}</p>
    <p>Date: {SHORT}</p>
    """
    path = FIXTURES / "initials_every_page.pdf"
    write_story(path, html)
    doc = pymupdf.open(path)
    for page in doc:
        page.insert_text((400, 760), "Client initials: ________", fontsize=9)
    data = doc.tobytes(garbage=4, deflate=True, no_new_id=True)
    doc.close()
    path.write_bytes(data)
    _normalize(path)
    n = pymupdf.open(path).page_count
    fields = [field(1, "Client:", "text", "sender")]
    fields += [field(p, "Client initials:", "initials", "client") for p in range(1, n + 1)]
    fields += [
        field(n, "Client signature:", "signature", "client"),
        field(n, "Date:", "date_signed", "client"),
    ]
    return {
        "description": "Initials in every page footer",
        "roles": {"client": ["client"]},
        "fields": fields,
    }


def no_signature_block() -> dict[str, Any]:
    """Terms with nowhere to sign: the pipeline must append a signature page."""
    html = f"<h1>Standard Terms of Business</h1>{clauses()}"
    write_story(FIXTURES / "no_signature_block.pdf", html)
    return {
        "description": "No signature block anywhere; expect appended signature page",
        "roles": {"client": ["client", "customer"]},
        "fields": [],
        "expect_appended_signature_page": True,
    }


def scanned() -> dict[str, Any]:
    """Image-only copy of the two-party agreement: no text layer at all."""
    src = pymupdf.open(FIXTURES / "two_party_services.pdf")
    out = pymupdf.open()
    for page in src:
        pix = page.get_pixmap(dpi=110, colorspace=pymupdf.csGRAY)
        new = out.new_page(width=page.rect.width, height=page.rect.height)
        new.insert_image(new.rect, stream=pix.tobytes("png"))
    path = FIXTURES / "scanned.pdf"
    out.save(path, garbage=4, deflate=True, no_new_id=True)
    _normalize(path)
    return {
        "description": "Scanned (image-only); expect appended signature page + manual",
        "roles": {"client": ["client"]},
        "fields": [],
        "no_text_layer": True,
        "expect_appended_signature_page": True,
    }


def engagement_docx() -> dict[str, Any]:
    """A Word document: tests DOCX→PDF conversion and table-based signature blocks."""
    from docx import Document
    from docx.shared import Pt

    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)
    doc.add_heading("Statement of Engagement", level=1)
    doc.add_paragraph('Silverline Test Studio Ltd ("Studio") and the client below agree:')
    doc.add_paragraph(f"Client company: {BLANK}")
    doc.add_paragraph(f"Start date: {SHORT}")
    doc.add_paragraph(f"Estimated budget: {SHORT}")
    for i, (title, body) in enumerate(BOILERPLATE[:6], start=1):
        doc.add_heading(f"{i}. {title}", level=2)
        doc.add_paragraph(body.replace("Provider", "Studio"))
    table = doc.add_table(rows=4, cols=2)
    labels = [("Silverline Test Studio Ltd", "Client")]
    labels += [
        (f"{label} {SHORT}", f"{label} {SHORT}") for label in ("Signature:", "Name:", "Date:")
    ]
    for row, (left, right) in zip(table.rows, labels, strict=True):
        row.cells[0].text = left
        row.cells[1].text = right
    path = FIXTURES / "engagement.docx"
    doc.core_properties.author = "eval fixtures"
    fixed = datetime(2026, 1, 1, tzinfo=UTC)
    doc.core_properties.created = fixed
    doc.core_properties.modified = fixed
    doc.core_properties.last_modified_by = "eval fixtures"
    doc.save(str(path))
    _zip_normalize(path)
    return {
        "description": "DOCX with a two-column signature table (converted to PDF on upload)",
        "source_format": "docx",
        "roles": {
            "studio": ["studio", "provider", "firm", "silverline", "company"],
            "client": ["client", "customer"],
        },
        # Page numbers refer to the converted PDF; the last page holds the signature table.
        "fields": [
            field(1, "Client company:", "text", "sender"),
            field(1, "Start date:", "date", "sender"),
            field(1, "Estimated budget:", "text", "sender"),
            field(-1, "Signature:", "signature", "studio", nth=1),
            field(-1, "Name:", "name", "studio", nth=1),
            field(-1, "Date:", "date_signed", "studio", nth=1),
            field(-1, "Signature:", "signature", "client", nth=2),
            field(-1, "Name:", "name", "client", nth=2),
            field(-1, "Date:", "date_signed", "client", nth=2),
        ],
    }


GENERATORS = [
    two_party_services,
    single_party_engagement,
    bracket_placeholders,
    initials_every_page,
    no_signature_block,
    scanned,
    engagement_docx,
]


def main() -> None:
    FIXTURES.mkdir(exist_ok=True)
    EXPECTED.mkdir(exist_ok=True)
    for gen in GENERATORS:
        expected = gen()
        name = gen.__name__ if gen is not engagement_docx else "engagement"
        (EXPECTED / f"{name}.json").write_text(json.dumps(expected, indent=2) + "\n")
        print(f"  {name}: {expected['description']}")


if __name__ == "__main__":
    main()
