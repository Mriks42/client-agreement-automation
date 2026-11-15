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


def page_of(path: Path, text: str) -> int:
    """1-based page where `text` first appears. Uses PyMuPDF's plain text search, so the ground
    truth doesn't depend on the extractor under test."""
    with pymupdf.open(path) as doc:
        for page in doc.pages():
            if page.search_for(text):
                return page.number + 1
    raise ValueError(f"{text!r} not found in {path.name}")


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
            field(page_of(path, "Project fee (USD):"), "Project fee (USD):", "text", "sender"),
            field(page_of(path, "Project description:"), "Project description:", "text", "sender"),
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


# ---- hand-placed layouts (what Word and PDF tools export, rather than HTML) ----------------
class Canvas:
    """Places text and rules directly, for layouts the HTML story can't express."""

    def __init__(self) -> None:
        self.doc = pymupdf.open()
        self.page = self.doc.new_page(width=LETTER.width, height=LETTER.height)
        self.y = 72.0

    def text(self, x: float, y: float, s: str, size: float = 10.5, bold: bool = False) -> float:
        self.page.insert_text((x, y), s, fontsize=size, fontname="hebo" if bold else "helv")
        return pymupdf.get_text_length(s, fontname="hebo" if bold else "helv", fontsize=size)

    def para(self, s: str, size: float = 10.5, gap: float = 6) -> None:
        rect = pymupdf.Rect(72, self.y, LETTER.width - 72, self.y + 200)
        spare = self.page.insert_textbox(rect, s, fontsize=size, fontname="helv")
        self.y += 200 - spare + gap

    def rule(self, x0: float, y: float, x1: float, width: float = 0.6) -> None:
        self.page.draw_line((x0, y), (x1, y), width=width)

    def labelled_rule(self, label: str, y: float, x: float = 72, length: float = 220) -> None:
        w = self.text(x, y, label)
        self.rule(x + w + 6, y + 1.5, x + w + 6 + length)

    def new_page(self) -> None:
        self.page = self.doc.new_page(width=LETTER.width, height=LETTER.height)
        self.y = 72.0

    def save(self, path: Path) -> None:
        self.doc.save(path, garbage=4, deflate=True, no_new_id=True)
        self.doc.close()
        _normalize(path)


LOREM = (
    "Consultant will provide the advisory services described in each statement of work with "
    "reasonable care and skill. Client will give Consultant timely access to the information "
    "and people needed. Fees are invoiced monthly and are due within thirty days. Either party "
    "may end this Agreement on thirty days' written notice."
)


def drawn_signature_lines() -> dict[str, Any]:
    """Word-style export: blanks are drawn rules, signature lines carry captions *below* them,
    a heading is underlined (not a blank), a bordered fee table must not produce fields, and a
    placeholder wraps across two lines."""
    c = Canvas()
    c.text(72, c.y, "Advisory Services Agreement", size=15, bold=True)
    c.y += 26
    c.para(
        'This Advisory Services Agreement is between Quill Test Consulting Inc. ("Consultant") '
        'and the client identified below ("Client").'
    )
    c.labelled_rule("Client name:", c.y + 10)
    c.labelled_rule("Monthly fee:", c.y + 30, length=120)
    c.y += 50
    c.page.insert_text((72, c.y + 10), "Client notice address: [CLIENT\nADDRESS]", fontsize=10.5)
    c.y += 44
    w = c.text(72, c.y, "Scope and Fees", bold=True)
    c.rule(72, c.y + 2, 72 + w, width=0.5)  # underlined heading, not a blank
    c.y += 14
    # A bordered fee table: every rule here is a table border.
    rows = [
        ("Service", "Rate"),
        ("Strategy workshop", "USD 2,000"),
        ("Monthly advisory", "USD 3,500"),
    ]
    top = c.y
    for i, (a, b) in enumerate(rows):
        c.text(78, top + 14 + i * 20, a, bold=i == 0)
        c.text(298, top + 14 + i * 20, b, bold=i == 0)
    for i in range(len(rows) + 1):
        c.rule(72, top + i * 20, 452, width=0.5)
    for x in (72, 292, 452):
        c.page.draw_line((x, top), (x, top + len(rows) * 20), width=0.5)
    c.y = top + len(rows) * 20 + 20
    c.para(LOREM)
    c.y += 10
    c.text(72, c.y, "QUILL TEST CONSULTING INC.", bold=True)
    for i, caption in enumerate(("Authorized signature", "Printed name", "Date")):
        y = c.y + 42 + i * 36
        c.rule(72, y, 300)
        c.text(72, y + 11, caption, size=8)
    c.y += 42 + 3 * 36 + 10
    c.text(72, c.y, "CLIENT", bold=True)
    for i, caption in enumerate(("Client signature", "Printed name", "Date")):
        y = c.y + 42 + i * 36
        c.rule(72, y, 300)
        c.text(72, y + 11, caption, size=8)
    path = FIXTURES / "drawn_signature_lines.pdf"
    c.save(path)
    return {
        "description": "Drawn rules, captions under signature lines, bordered table, wrapped "
        "placeholder, underlined heading",
        "roles": {
            "consultant": ["consultant", "quill", "firm", "provider", "company"],
            "client": ["client", "customer"],
        },
        "fields": [
            field(1, "Client name:", "text", "sender"),
            field(1, "Monthly fee:", "text", "sender"),
            field(1, "[CLIENT ADDRESS]", "text", "sender"),
            field(1, "Authorized signature", "signature", "consultant"),
            field(1, "Printed name", "name", "consultant", nth=1),
            field(1, "Date", "date_signed", "consultant", nth=1),
            field(1, "Client signature", "signature", "client"),
            field(1, "Printed name", "name", "client", nth=2),
            field(1, "Date", "date_signed", "client", nth=2),
        ],
    }


def label_gaps_and_leaders() -> dict[str, Any]:
    """'By:'/'Name:' labels with nothing after them, side by side; dotted blanks; a table of
    contents whose dot leaders are not blanks; a {{curly}} placeholder."""
    c = Canvas()
    c.text(72, c.y, "Terms of Engagement", size=15, bold=True)
    c.y += 24
    c.text(72, c.y, "Contents", bold=True)
    for i, (title, pg) in enumerate((("1. Scope", "1"), ("2. Fees", "1"), ("3. Signatures", "1"))):
        c.text(72, c.y + 16 + i * 14, f"{title} " + "." * 70 + f" {pg}")
    c.y += 16 + 3 * 14 + 12
    c.para(
        'These terms are agreed between Signal Test Partners LLP ("Firm") and the client named '
        'below ("Client").'
    )
    c.text(72, c.y + 4, "Matter: " + "." * 80)
    c.text(72, c.y + 22, "Reference no.: " + "." * 40)
    c.y += 40
    c.para("1. Scope. " + LOREM.replace("Consultant", "Firm"))
    c.para("2. Fees. The fixed fee for this matter is {{fixed_fee}}, payable on signature.")
    c.para("3. Signatures. Each party signs below. " + LOREM.replace("Consultant", "Firm")[:160])
    c.y += 8
    c.text(72, c.y, "SIGNAL TEST PARTNERS LLP", bold=True)
    c.text(330, c.y, "CLIENT", bold=True)
    for i, lab in enumerate(("By:", "Name:", "Title:", "Date:")):
        y = c.y + 28 + i * 24
        c.text(72, y, lab)
        c.text(330, y, lab)
    path = FIXTURES / "label_gaps_and_leaders.pdf"
    c.save(path)
    rows = [("By:", "signature"), ("Name:", "name"), ("Title:", "text"), ("Date:", "date_signed")]
    fields = [
        field(1, "Matter:", "text", "sender"),
        field(1, "Reference no.:", "text", "sender"),
        field(1, "{{fixed_fee}}", "text", "sender"),
    ]
    for party, nth in (("firm", 1), ("client", 2)):
        fields += [field(1, lab, t, party, nth=nth) for lab, t in rows]
    return {
        "description": "Label-only signature blocks side by side, dotted blanks, TOC leaders",
        "roles": {
            "firm": ["firm", "signal", "provider", "company"],
            "client": ["client", "customer"],
        },
        "fields": fields,
    }


def fillable_form() -> dict[str, Any]:
    """A fillable PDF: AcroForm text fields, a checkbox and a signature field."""
    c = Canvas()
    c.text(72, c.y, "Client Intake and Engagement Form", size=15, bold=True)
    c.y += 24
    c.para(
        'Orbit Test Advisory Ltd ("Adviser") will act for the client named in this form '
        '("Client") on the terms below.'
    )
    specs = [
        ("Client name:", "client_name", pymupdf.PDF_WIDGET_TYPE_TEXT, 220),
        ("Client email:", "client_email", pymupdf.PDF_WIDGET_TYPE_TEXT, 220),
        ("Engagement start:", "start_date", pymupdf.PDF_WIDGET_TYPE_TEXT, 120),
    ]
    y = c.y + 8
    for label, name, kind, width in specs:
        w = c.text(72, y + 12, label)
        widget = pymupdf.Widget()
        widget.field_name = name
        widget.field_type = kind
        widget.rect = pymupdf.Rect(72 + w + 8, y, 72 + w + 8 + width, y + 16)
        c.page.add_widget(widget)
        y += 26
    c.y = y + 6
    for _ in range(2):
        c.para(LOREM.replace("Consultant", "Adviser"))
    y = c.y + 6
    w = c.text(72, y + 11, "I accept these terms:")
    box = pymupdf.Widget()
    box.field_name = "accept_terms"
    box.field_type = pymupdf.PDF_WIDGET_TYPE_CHECKBOX
    box.rect = pymupdf.Rect(72 + w + 8, y, 72 + w + 20, y + 12)
    c.page.add_widget(box)
    y += 34
    w = c.text(72, y + 14, "Client signature:")
    sig = pymupdf.Widget()
    sig.field_name = "client_signature"
    sig.field_type = pymupdf.PDF_WIDGET_TYPE_SIGNATURE
    sig.rect = pymupdf.Rect(72 + w + 8, y, 72 + w + 208, y + 22)
    c.page.add_widget(sig)
    y += 34
    w = c.text(72, y + 12, "Date:")
    dt = pymupdf.Widget()
    dt.field_name = "client_date"
    dt.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    dt.rect = pymupdf.Rect(72 + w + 8, y, 72 + w + 128, y + 16)
    c.page.add_widget(dt)
    path = FIXTURES / "fillable_form.pdf"
    c.save(path)
    return {
        "description": "Fillable PDF: text fields, checkbox, signature field",
        "roles": {"client": ["client", "customer"]},
        "fields": [
            field(1, "Client name:", "text", "sender"),
            field(1, "Client email:", "text", "sender"),
            field(1, "Engagement start:", "date", "sender"),
            field(1, "I accept these terms:", "checkbox", "client"),
            field(1, "Client signature:", "signature", "client"),
            field(1, "Date:", "date_signed", "client"),
        ],
    }


def template_with_options() -> dict[str, Any]:
    """An order-form template like the open-standard ones: [XX] numbers, a multi-line instruction
    placeholder, drafting notes (not blanks), option checkboxes, [ # ], and full-width table rules
    (not blanks). Mirrors patterns from a blind test on a public template."""
    c = Canvas()
    c.text(72, c.y, "Services Order Form", size=15, bold=True)
    c.y += 26
    c.page.insert_text(
        (72, c.y),
        'This Order Form [XX] is between Lumen Test Advisory Inc. ("Provider") and\n'
        '[CLIENT LEGAL NAME] ("Client").',
        fontsize=10.5,
    )
    c.y += 30
    c.rule(54, c.y, 558, width=0.5)
    c.text(72, c.y + 18, "Services", bold=True)
    c.page.insert_text(
        (200, c.y + 18),
        "[Describe the services to be performed, including key people,\n"
        "the overall timeline and milestones.]",
        fontsize=10.5,
    )
    c.page.insert_text(
        (200, c.y + 50), "[Drafting note: Delete the next row if there is no deposit.]", fontsize=9
    )
    c.y += 64
    c.rule(54, c.y, 558, width=0.5)
    c.text(72, c.y + 18, "Pricing", bold=True)
    for i, option in enumerate(("Fixed fee", "Time and materials")):
        c.text(200, c.y + 18 + i * 18, "[    ]")
        c.text(228, c.y + 18 + i * 18, option)
    c.text(200, c.y + 58, "Payment is due within [ # ] days of each invoice.")
    c.y += 72
    c.rule(54, c.y, 558, width=0.5)
    c.y += 24
    c.para("By signing below, each party agrees to this Order Form.")
    c.text(72, c.y + 6, "LUMEN TEST ADVISORY INC.", bold=True)
    c.text(330, c.y + 6, "[CLIENT LEGAL NAME]", bold=True)
    for i, lab in enumerate(("Signature:", "Name:", "Date:")):
        y = c.y + 34 + i * 24
        c.text(72, y, f"{lab} {SHORT}")
        c.text(330, y, f"{lab} {SHORT}")
    path = FIXTURES / "template_with_options.pdf"
    c.save(path)
    services = (
        "[Describe the services to be performed, including key people, the overall timeline "
        "and milestones.]"
    )
    fields = [
        field(1, "[XX]", "text", "sender"),
        field(1, "[CLIENT LEGAL NAME]", "text", "sender", nth=1),
        field(1, services, "text", "sender"),
        field(1, "Fixed fee", "checkbox", "sender"),
        field(1, "Time and materials", "checkbox", "sender"),
        field(1, "[ # ]", "text", "sender"),
        field(1, "[CLIENT LEGAL NAME]", "text", "sender", nth=2),
    ]
    rows = [("Signature:", "signature"), ("Name:", "name"), ("Date:", "date_signed")]
    for party, nth in (("provider", 1), ("client", 2)):
        fields += [field(1, lab, t, party, nth=nth) for lab, t in rows]
    return {
        "description": "Template: [XX], instruction block, drafting note, option boxes, [ # ], "
        "full-width rules",
        "roles": {
            "provider": ["provider", "lumen", "firm", "company"],
            "client": ["client", "customer"],
        },
        "fields": fields,
    }


GENERATORS = [
    two_party_services,
    single_party_engagement,
    bracket_placeholders,
    initials_every_page,
    no_signature_block,
    scanned,
    engagement_docx,
    drawn_signature_lines,
    label_gaps_and_leaders,
    fillable_form,
    template_with_options,
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
