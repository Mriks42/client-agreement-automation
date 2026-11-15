"""Every PDF operation lives here: the only module that imports PyMuPDF (AGPL, DECISIONS.md D13).

Swapping the PDF library means rewriting this one file. Everything it returns is plain
dataclasses, so callers never touch PyMuPDF objects.

Coordinates are PDF points with a top-left origin. Pages are 1-based in this module's API.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

import pymupdf

SlotKind = Literal[
    "underscore", "dots", "drawn_line", "placeholder", "label_gap", "checkbox", "form_field"
]


class PdfError(Exception):
    """A document we can't use. `message` is safe to show to the user."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# ---- plain data -------------------------------------------------------------------------------
@dataclass(frozen=True)
class Box:
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    def x_overlap(self, other: "Box") -> float:
        return max(0.0, min(self.x1, other.x1) - max(self.x0, other.x0))

    def union(self, other: "Box") -> "Box":
        return Box(
            min(self.x0, other.x0),
            min(self.y0, other.y0),
            max(self.x1, other.x1),
            max(self.y1, other.y1),
        )


@dataclass
class Line:
    id: str
    page: int
    text: str
    bbox: Box
    baseline: float
    size: float
    bold: bool


@dataclass
class Slot:
    """A candidate blank, measured from the page. Detection decides what (if anything) it is."""

    id: str
    page: int
    kind: SlotKind
    bbox: Box
    baseline: float  # y on which a written value should sit
    line_id: str | None = None
    before: str = ""  # text on the same line between the previous slot (or line start) and this
    after: str = ""  # text on the same line between this slot and the next (or line end)
    caption: str = ""  # text right below a blank that has no label on its own line
    text: str = ""  # placeholder text, or a form field's name
    detail: str = ""  # e.g. a form field's type
    redact: list[Box] = field(default_factory=list)  # text to remove before writing a value

    @property
    def anchor(self) -> str:
        """The text a person would use to name this blank."""
        if self.kind == "placeholder":
            return self.text
        if self.kind == "checkbox":
            return self.before.strip() or self.after.strip() or self.text
        return self.before.strip() or self.caption.strip() or self.text


@dataclass
class PageText:
    number: int
    width: float
    height: float
    lines: list[Line]
    slots: list[Slot]
    has_text: bool


@dataclass
class Extraction:
    pages: list[PageText]
    title: str

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def has_text_layer(self) -> bool:
        return any(p.has_text for p in self.pages)

    @property
    def slots(self) -> list[Slot]:
        return [s for p in self.pages for s in p.slots]

    def slot_map(self) -> dict[str, Slot]:
        return {s.id: s for s in self.slots}

    def line_map(self) -> dict[str, Line]:
        return {ln.id: ln for p in self.pages for ln in p.lines}


@dataclass
class NormalizedPdf:
    data: bytes
    page_count: int


@dataclass
class AppendedField:
    role_key: str
    type: Literal["signature", "name", "date_signed"]
    page: int
    rect: Box


# ---- normalize --------------------------------------------------------------------------------
def normalize_pdf(data: bytes, *, max_pages: int) -> NormalizedPdf:
    """Validate an uploaded PDF and rewrite it into the form every later step relies on:
    unencrypted, rotation baked into the content (so coordinates match what people see),
    crop box equal to media box, and no JavaScript or embedded files."""
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise PdfError(
            "unreadable", "We couldn't read this PDF. It may be damaged; try exporting it again."
        ) from exc
    with doc:
        if doc.needs_pass:
            raise PdfError(
                "encrypted",
                "This PDF is password-protected. Remove the password, then upload it again.",
            )
        if doc.page_count == 0:
            raise PdfError("empty", "This PDF has no pages.")
        if doc.page_count > max_pages:
            raise PdfError(
                "too_many_pages",
                f"This document has {doc.page_count} pages; the limit is {max_pages}.",
            )
        try:
            for page in doc.pages():
                if page.cropbox != page.mediabox:
                    page.set_mediabox(page.cropbox)
                if page.rotation:
                    page.remove_rotation()
            doc.scrub(
                attached_files=True,
                embedded_files=True,
                javascript=True,
                reset_responses=True,
                thumbnails=True,
                # Leave everything that affects what the document says or shows.
                clean_pages=False,
                hidden_text=False,
                metadata=False,
                redactions=False,
                remove_links=False,
                reset_fields=False,
                xml_metadata=False,
            )
            out = doc.tobytes(garbage=3, deflate=True, encryption=pymupdf.PDF_ENCRYPT_NONE)  # type: ignore[attr-defined]
        except Exception as exc:
            raise PdfError(
                "unreadable",
                "We couldn't process this PDF. Try printing it to a new PDF and uploading that.",
            ) from exc
        return NormalizedPdf(data=out, page_count=doc.page_count)


def page_count(data: bytes) -> int:
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        return doc.page_count


def page_sizes(data: bytes) -> list[tuple[float, float]]:
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        return [(p.rect.width, p.rect.height) for p in doc]


def render_page_png(data: bytes, page: int, *, dpi: int = 110) -> bytes:
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        if not 1 <= page <= doc.page_count:
            raise PdfError("no_such_page", f"Page {page} does not exist.")
        return doc[page - 1].get_pixmap(dpi=dpi).tobytes("png")


# ---- extract ----------------------------------------------------------------------------------
# Long limits: templates use multi-line instructions such as "[Describe the services ...]".
_PLACEHOLDER_RE = re.compile(
    r"\[[^\[\]]{1,400}\]|\{\{[^{}]{1,120}\}\}|<<[^<>]{1,120}>>|«[^«»]{1,120}»"
    r"|\(\s{0,6}[X✓✔]?\s{0,6}\)"  # option boxes "( )"; lowercase "(x)" is a clause number
)
# Brackets that are drafting, not blanks: [1], [a], [iv], [sic], [Drafting note: ...]. Roman
# numerals are lowercase only: "[XX]" is a placeholder.
_NOT_PLACEHOLDER_RE = re.compile(
    r"^\[\s*(?:\d+|[a-z]|[ivx]{1,4}|(?i:sic|emphasis added|reserved|intentionally left blank))"
    r"\s*\]$"
    r"|^\[\s*(?i:drafting note|note to drafter|note to draft|note)\b"
)
_OPTION_BOX_RE = re.compile(r"[\[(]\s?[xX✓✔]?\s?[\])]")
_CHECKBOX_CHARS = frozenset("☐□❑❒▢☑☒")
_LABEL_RE = re.compile(
    r"(?:[A-Za-z'\u2019]+\s){0,3}"
    r"(signature|signed|sign here|by|name|printed name|print name|title|position|date|dated"
    r"|initials?)\s*:",
    re.I,
)
_TOC_TAIL_RE = re.compile(r"^\s*\d+\s*$")
_MIN_UNDERSCORES = 3
_MIN_DOTS = 6
_MIN_SEGMENT = 36.0  # pt; shorter horizontal strokes are decoration or glyph parts
_ROW_TOLERANCE = 3.0  # pt; baselines closer than this are the same row


@dataclass
class _Char:
    c: str
    box: Box
    baseline: float
    size: float
    bold: bool


@dataclass
class _RawLine:
    chars: list[_Char]
    bbox: Box
    baseline: float
    size: float
    bold: bool
    block: int
    id: str = ""

    @property
    def text(self) -> str:
        return "".join(ch.c for ch in self.chars)


@dataclass
class _Draft:
    """A slot before IDs and context are assigned."""

    kind: SlotKind
    bbox: Box
    baseline: float
    line: _RawLine | None
    text: str = ""
    detail: str = ""
    redact: list[Box] = field(default_factory=list)


def extract(data: bytes) -> Extraction:
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        pages = [_extract_page(page) for page in doc]
    return Extraction(pages=pages, title=_guess_title(pages[0]) if pages else "")


def _extract_page(page: pymupdf.Page) -> PageText:
    number = page.number + 1
    width, height = page.rect.width, page.rect.height
    widgets = list(page.widgets() or [])
    # Text inside a form field is the field's own appearance (its value or a "SIGN" badge), not
    # document text; the field itself becomes a slot.
    widget_boxes = [Box(w.rect.x0, w.rect.y0, w.rect.x1, w.rect.y1) for w in widgets]
    raw_lines = [
        ln for ln in _read_lines(page) if not any(_inside(ln.bbox, b) for b in widget_boxes)
    ]

    drafts: list[_Draft] = []
    drafts += _placeholders(raw_lines)
    covered = [d.bbox for d in drafts]
    for line in raw_lines:
        drafts += _char_runs(line, covered)
    drafts += _drawn_lines(page, raw_lines, drafts)
    drafts = _form_fields(widgets, raw_lines, drafts)
    drafts += _label_gaps(raw_lines, drafts, width)

    ordered_lines = _reading_order(raw_lines, key=lambda ln: (ln.baseline, ln.bbox.x0))
    lines = []
    for i, raw in enumerate(ordered_lines, start=1):
        raw.id = f"p{number}-l{i}"
        lines.append(
            Line(
                id=raw.id,
                page=number,
                text=_clean(raw.text),
                bbox=raw.bbox,
                baseline=raw.baseline,
                size=raw.size,
                bold=raw.bold,
            )
        )
    ordered = _reading_order(drafts, key=lambda d: (d.baseline, d.bbox.x0))
    slots = [
        Slot(
            id=f"p{number}-s{i}",
            page=number,
            kind=d.kind,
            bbox=d.bbox,
            baseline=d.baseline,
            line_id=d.line.id if d.line else None,
            text=d.text,
            detail=d.detail,
            redact=d.redact,
        )
        for i, d in enumerate(ordered, start=1)
    ]
    _attach_context(slots, ordered, raw_lines)
    non_space = sum(len(ln.text.strip()) for ln in raw_lines)
    return PageText(number, width, height, lines, slots, has_text=non_space >= 10)


def _read_lines(page: pymupdf.Page) -> list[_RawLine]:
    flags = pymupdf.TEXT_PRESERVE_WHITESPACE | pymupdf.TEXT_MEDIABOX_CLIP
    raw = page.get_text("rawdict", flags=flags)
    out: list[_RawLine] = []
    for block in raw["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block["lines"]:
            if abs(line["dir"][1]) > 0.1:  # vertical or rotated text: never a fill-in line
                continue
            chars: list[_Char] = []
            for span in line["spans"]:
                bold = bool(span["flags"] & 16) or "bold" in span["font"].lower()
                for c in span["chars"]:
                    chars.append(_Char(c["c"], Box(*c["bbox"]), c["origin"][1], span["size"], bold))
            if not "".join(ch.c for ch in chars).strip():
                continue
            visible = [ch for ch in chars if not ch.c.isspace()]
            out.append(
                _RawLine(
                    chars=chars,
                    bbox=Box(*line["bbox"]),
                    baseline=max(ch.baseline for ch in visible),
                    size=max(ch.size for ch in visible),
                    bold=all(ch.bold for ch in visible),
                    block=block["number"],
                )
            )
    return out


def _placeholders(lines: list[_RawLine]) -> list[_Draft]:
    """[CLIENT NAME], {{fee}}, <<Date>>, «Address». Matched per block so a placeholder that
    wraps onto the next line is still one slot."""
    drafts: list[_Draft] = []
    by_block: dict[int, list[_RawLine]] = {}
    for line in lines:
        by_block.setdefault(line.block, []).append(line)
    for block_lines in by_block.values():
        stream: list[tuple[_RawLine | None, _Char | None]] = []
        for line in block_lines:
            if stream:
                stream.append((None, None))  # line break reads as a space
            stream += [(line, ch) for ch in line.chars]
        text = "".join(ch.c if ch else " " for _, ch in stream)
        for m in _PLACEHOLDER_RE.finditer(text):
            inner = re.sub(r"\s+", " ", m.group(0)).strip()
            kind: SlotKind = "checkbox" if _OPTION_BOX_RE.fullmatch(inner) else "placeholder"
            if kind == "placeholder" and (
                _NOT_PLACEHOLDER_RE.match(inner) or not re.search(r"[A-Za-z●•_#]", inner)
            ):
                continue
            parts: dict[int, tuple[_RawLine, list[Box]]] = {}
            for ln, ch in stream[m.start() : m.end()]:
                if ln is None or ch is None or ch.c.isspace():
                    continue
                parts.setdefault(id(ln), (ln, []))[1].append(ch.box)
            rects = [_union_all(boxes) for _, boxes in parts.values()]
            first_line = next(iter(parts.values()))[0]
            drafts.append(
                _Draft(
                    kind=kind,
                    bbox=_placeholder_box(rects),
                    baseline=first_line.baseline,
                    line=first_line,
                    text=inner,
                    redact=rects if kind == "placeholder" else [],
                )
            )
    return drafts


def _placeholder_box(rects: list[Box]) -> Box:
    """Where a value replacing this placeholder goes. A multi-line placeholder that starts its
    own line (a paragraph or table cell of instructions) becomes one block. One that starts
    mid-sentence shares its lines with other text, so the value goes in its widest fragment."""
    if len(rects) == 1:
        return rects[0]
    left = min(r.x0 for r in rects[1:])
    if rects[0].x0 <= left + 12:
        return Box(left, rects[0].y0, max(r.x1 for r in rects), rects[-1].y1)
    return max(rects, key=lambda r: r.width)


def _char_runs(line: _RawLine, covered: list[Box]) -> list[_Draft]:
    """Underscore runs, dot leaders and checkbox glyphs inside one text line."""
    drafts: list[_Draft] = []
    chars = line.chars
    i = 0
    while i < len(chars):
        ch = chars[i]
        if ch.c in _CHECKBOX_CHARS:
            drafts.append(_Draft("checkbox", ch.box, line.baseline, line))
            i += 1
            continue
        if ch.c in "_.…":
            j = i
            while j < len(chars) and chars[j].c == ch.c:
                j += 1
            run = chars[i:j]
            box = _union_all([c.box for c in run])
            tail = "".join(c.c for c in chars[j:])
            is_blank = (ch.c == "_" and len(run) >= _MIN_UNDERSCORES) or (
                ch.c in ".…" and len(run) >= (_MIN_DOTS if ch.c == "." else 2)
            )
            if (
                is_blank
                and box.width >= 15
                and not any(box.x_overlap(c) > 0 and _same_row(box, c) for c in covered)
                and not (ch.c != "_" and _TOC_TAIL_RE.match(tail))  # table-of-contents leader
            ):
                kind: SlotKind = "underscore" if ch.c == "_" else "dots"
                drafts.append(_Draft(kind, box, line.baseline, line))
            i = j
            continue
        i += 1
    return drafts


def _drawn_lines(page: pymupdf.Page, lines: list[_RawLine], existing: list[_Draft]) -> list[_Draft]:
    """Signature lines drawn as vector strokes, which is how Word and LibreOffice export
    underlined tabs and drawn rules."""
    horizontals: list[Box] = []
    verticals: list[Box] = []
    for d in page.get_drawings():
        visible = d.get("color") is not None or d.get("fill") is not None
        if not visible:
            continue
        for item in d["items"]:
            if item[0] == "l":
                p1, p2 = item[1], item[2]
                if abs(p1.y - p2.y) <= 1.0 and abs(p2.x - p1.x) >= _MIN_SEGMENT:
                    y = (p1.y + p2.y) / 2
                    horizontals.append(Box(min(p1.x, p2.x), y, max(p1.x, p2.x), y))
                elif abs(p1.x - p2.x) <= 1.0 and abs(p2.y - p1.y) >= 6:
                    verticals.append(Box(p1.x, min(p1.y, p2.y), p1.x, max(p1.y, p2.y)))
            elif item[0] == "re":
                r = item[1]
                if r.height <= 2.5 and r.width >= _MIN_SEGMENT:
                    y = (r.y0 + r.y1) / 2
                    horizontals.append(Box(r.x0, y, r.x1, y))
                elif r.width <= 2.5 and r.height >= 6:
                    verticals.append(Box((r.x0 + r.x1) / 2, r.y0, (r.x0 + r.x1) / 2, r.y1))

    def touches_vertical(x: float, y: float) -> bool:
        return any(abs(v.x0 - x) <= 2.5 and v.y0 - 2.5 <= y <= v.y1 + 2.5 for v in verticals)

    page_chars = [ch for ln in lines for ch in ln.chars]
    drafts: list[_Draft] = []
    for seg in _merge_segments(horizontals):
        y = seg.y0
        if seg.width >= 0.75 * page.rect.width:
            continue  # a table row rule or decorative separator, never a fill-in line
        if touches_vertical(seg.x0, y) and touches_vertical(seg.x1, y):
            continue  # table border
        above = [
            ch
            for ch in page_chars
            if seg.x0 <= (ch.box.x0 + ch.box.x1) / 2 <= seg.x1 and y - 6 <= ch.baseline <= y + 1.5
        ]
        inked = sum(ch.box.width for ch in above if not ch.c.isspace())
        if inked > 0.4 * seg.width:
            continue  # underlined text, or an underscore blank we already have
        box = Box(seg.x0, y - 12, seg.x1, y)
        if any(
            _same_row(box, d.bbox) and box.x_overlap(d.bbox) > 0.5 * box.width for d in existing
        ):
            continue
        row = [ln for ln in lines if y - 7 <= ln.baseline <= y + 1.5]
        line = min(row, key=lambda ln: _x_distance(ln.bbox, seg), default=None)
        if line is not None and _x_distance(line.bbox, seg) > 40:
            line = None  # unrelated text further along the row
        drafts.append(_Draft("drawn_line", box, y - 1.5, line))
    return drafts


def _form_fields(
    widgets: list[pymupdf.Widget], lines: list[_RawLine], drafts: list[_Draft]
) -> list[_Draft]:
    """Existing PDF form fields are explicit blanks; they replace any blank drawn under them."""
    if not widgets:
        return drafts
    kept = list(drafts)
    for w in widgets:
        r = w.rect
        box = Box(r.x0, r.y0, r.x1, r.y1)
        kept = [
            d
            for d in kept
            if not (box.x_overlap(d.bbox) > 0.5 * d.bbox.width and abs(d.baseline - box.y1) < 12)
        ]
        row = [ln for ln in lines if box.y0 - 2 <= ln.baseline <= box.y1 + 2]
        line = min(row, key=lambda ln: _x_distance(ln.bbox, box), default=None)
        kept.append(
            _Draft(
                "form_field",
                box,
                box.y1 - 2,
                line,
                text=(w.field_label or w.field_name or "").strip(),
                detail=f"{w.field_type_string} form field",
            )
        )
    return kept


def _label_gaps(lines: list[_RawLine], drafts: list[_Draft], page_width: float) -> list[_Draft]:
    """'Signature:' or 'Date:' followed by empty space (no line drawn): the blank is the gap."""
    out: list[_Draft] = []
    for line in lines:
        chunks = _chunks(line)
        row = [ln for ln in lines if ln is not line and _same_row(ln.bbox, line.bbox)]
        for i, (text, _x0, x1) in enumerate(chunks):
            if not _LABEL_RE.fullmatch(text.strip()):
                continue
            nxt = chunks[i + 1][1] if i + 1 < len(chunks) else None
            right = [ln.bbox.x0 for ln in row if ln.bbox.x0 > x1]
            end = min([v for v in [nxt, *right] if v is not None], default=page_width - 54)
            end = min(end - 6, x1 + 240, page_width - 36)
            gap = Box(
                x1 + 6, line.baseline - line.size * 0.9, end, line.baseline + line.size * 0.25
            )
            if gap.width < 50:
                continue
            if any(_same_row(gap, d.bbox) and gap.x_overlap(d.bbox) > 0 for d in drafts + out):
                continue
            out.append(_Draft("label_gap", gap, line.baseline, line))
    return out


def _attach_context(slots: list[Slot], drafts: list[_Draft], lines: list[_RawLine]) -> None:
    by_line: dict[int, list[int]] = {}
    for idx, d in enumerate(drafts):
        if d.line is not None:
            by_line.setdefault(id(d.line), []).append(idx)
    raw_by_id = {id(ln): ln for ln in lines}
    for key, idxs in by_line.items():
        line = raw_by_id[key]
        idxs.sort(key=lambda i: drafts[i].bbox.x0)
        for pos, i in enumerate(idxs):
            left = drafts[idxs[pos - 1]].bbox.x1 if pos > 0 else -1e9
            right = drafts[idxs[pos + 1]].bbox.x0 if pos + 1 < len(idxs) else 1e9
            box = drafts[i].bbox
            slots[i].before = _clean(_text_between(line, left, box.x0))
            slots[i].after = _clean(_text_between(line, box.x1, right))
    for i, slot in enumerate(slots):
        if slot.kind == "checkbox" and not slot.after:
            box = drafts[i].bbox
            right_of = [
                ln for ln in lines if _same_row(ln.bbox, box) and 0 <= ln.bbox.x0 - box.x1 <= 300
            ]
            if right_of:
                slot.after = _clean(min(right_of, key=lambda ln: ln.bbox.x0).text)
            continue
        if re.search(r"[A-Za-z]", slot.before + slot.after) or slot.kind == "placeholder":
            continue
        box = drafts[i].bbox
        wide = Box(box.x0 - 24, box.y0, box.x1 + 24, box.y1)
        below = [
            ln
            for ln in lines
            if 0 < ln.baseline - slot.baseline <= 22
            and ln is not drafts[i].line
            and ln.bbox.x_overlap(wide) > 0
        ]
        if below:
            nearest = min(below, key=lambda ln: ln.baseline)
            slot.caption = _clean(_text_between(nearest, box.x0 - 24, box.x1 + 24))


# ---- helpers ----------------------------------------------------------------------------------
def _chunks(line: _RawLine) -> list[tuple[str, float, float]]:
    """Split a line at wide gaps (tabs, runs of spaces) into (text, x0, x1) chunks."""
    out: list[tuple[str, float, float]] = []
    cur: list[_Char] = []
    for ch in line.chars:
        if ch.c.isspace():
            continue
        if cur and ch.box.x0 - cur[-1].box.x1 > max(12.0, 1.5 * ch.size):
            out.append(("".join(c.c for c in cur), cur[0].box.x0, cur[-1].box.x1))
            cur = []
        cur.append(ch)
    # Re-insert single spaces inside chunks for readability.
    if cur:
        out.append(("".join(c.c for c in cur), cur[0].box.x0, cur[-1].box.x1))
    return [(_respace(line, x0, x1), x0, x1) for _, x0, x1 in out]


def _respace(line: _RawLine, x0: float, x1: float) -> str:
    return _clean(_text_between(line, x0 - 0.1, x1 + 0.1))


def _text_between(line: _RawLine, x0: float, x1: float) -> str:
    return "".join(ch.c for ch in line.chars if x0 <= (ch.box.x0 + ch.box.x1) / 2 <= x1)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _inside(inner: Box, outer: Box, tol: float = 1.0) -> bool:
    return (
        inner.x0 >= outer.x0 - tol
        and inner.y0 >= outer.y0 - tol
        and inner.x1 <= outer.x1 + tol
        and inner.y1 <= outer.y1 + tol
    )


def _union_all(boxes: list[Box]) -> Box:
    out = boxes[0]
    for b in boxes[1:]:
        out = out.union(b)
    return out


def _same_row(a: Box, b: Box) -> bool:
    overlap = min(a.y1, b.y1) - max(a.y0, b.y0)
    return overlap > 0.5 * min(a.height or 1, b.height or 1)


def _x_distance(a: Box, b: Box) -> float:
    if a.x1 < b.x0:
        return b.x0 - a.x1
    if b.x1 < a.x0:
        return a.x0 - b.x1
    return 0.0


def _merge_segments(segs: list[Box]) -> list[Box]:
    out: list[Box] = []
    for s in sorted(segs, key=lambda b: (round(b.y0, 1), b.x0)):
        if out and abs(out[-1].y0 - s.y0) <= 1.0 and s.x0 <= out[-1].x1 + 2:
            prev = out[-1]
            out[-1] = Box(prev.x0, prev.y0, max(prev.x1, s.x1), prev.y1)
        else:
            out.append(s)
    return out


def _reading_order[T](items: list[T], key: Callable[[T], tuple[float, float]]) -> list[T]:
    """Top-to-bottom, then left-to-right within a row (side-by-side signature columns)."""
    pending = sorted(items, key=lambda it: key(it)[0])
    rows: list[list[T]] = []
    row_y: float | None = None
    for it in pending:
        y = key(it)[0]
        if row_y is None or y - row_y > _ROW_TOLERANCE:
            rows.append([])
            row_y = y
        rows[-1].append(it)
    return [it for row in rows for it in sorted(row, key=lambda it: key(it)[1])]


def _guess_title(page: PageText) -> str:
    top = [ln for ln in page.lines if ln.bbox.y1 < page.height * 0.4 and len(ln.text) > 3]
    if not top:
        return ""
    best = max(top, key=lambda ln: (round(ln.size, 1), ln.bold, -ln.bbox.y0))
    return best.text[:120]


# ---- write ------------------------------------------------------------------------------------
def append_signature_page(
    data: bytes, signers: list[tuple[str, str]], *, title: str
) -> tuple[bytes, list[AppendedField]]:
    """Append a signature page with a block (signature, name, date) for each (role_key, label).

    Used when a signing party has nowhere to sign in the document itself, including scans."""
    fields: list[AppendedField] = []
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        original_pages = doc.page_count
        width, height = doc[-1].rect.width, doc[-1].rect.height
        left, right = 72.0, width - 72.0
        mid = left + (right - left) * 0.55
        block_height = 128.0
        page: pymupdf.Page | None = None
        y = 0.0
        for role_key, label in signers:
            if page is None or y + block_height > height - 72:
                page = doc.new_page(width=width, height=height)
                y = _signature_page_header(page, title, original_pages, left, right)
            page_no = page.number + 1
            page.insert_text((left, y + 14), label[:80], fontname="hebo", fontsize=11.5)
            sig_y = y + 64
            page.draw_line((left, sig_y), (mid - 18, sig_y), width=0.6)
            page.insert_text((left, sig_y + 11), "Signature", fontname="helv", fontsize=8)
            fields.append(
                AppendedField(
                    role_key, "signature", page_no, Box(left, sig_y - 36, mid - 18, sig_y)
                )
            )
            row_y = sig_y + 44
            page.draw_line((left, row_y), (mid - 18, row_y), width=0.6)
            page.insert_text((left, row_y + 11), "Printed name", fontname="helv", fontsize=8)
            fields.append(
                AppendedField(role_key, "name", page_no, Box(left, row_y - 16, mid - 18, row_y))
            )
            page.draw_line((mid, row_y), (right, row_y), width=0.6)
            page.insert_text((mid, row_y + 11), "Date", fontname="helv", fontsize=8)
            fields.append(
                AppendedField(role_key, "date_signed", page_no, Box(mid, row_y - 16, right, row_y))
            )
            y += block_height
        out = doc.tobytes(garbage=3, deflate=True)
    return out, fields


def _signature_page_header(
    page: pymupdf.Page, title: str, original_pages: int, left: float, right: float
) -> float:
    page.insert_text((left, 84), "Signature page", fontname="hebo", fontsize=15)
    name = f"“{title}”" if title else "the preceding document"
    pages = f"{original_pages} page" + ("s" if original_pages != 1 else "")
    note = (
        f"This page is part of {name} ({pages}). It was added so that each party below can "
        "sign the document electronically."
    )
    page.insert_textbox(
        pymupdf.Rect(left, 96, right, 140), note, fontname="helv", fontsize=9.5, lineheight=1.3
    )
    return 150.0


@dataclass
class Stamp:
    """One value to write into the PDF. Placeholder text under `redact` is removed first."""

    page: int
    rect: Box
    kind: Literal["text", "check", "signature"]
    value: str = ""  # the text, or the signer's name for a typed signature
    image: bytes | None = None  # PNG of a drawn signature
    redact: list[Box] = field(default_factory=list)


_INK = (0.06, 0.14, 0.42)  # dark blue, so signer marks stand out from printed text


def stamp(data: bytes, stamps: list[Stamp]) -> bytes:
    """Write values into the PDF (flattened: plain page content, no form fields left on top).

    Long values shrink to fit their field instead of reflowing the document (DECISIONS.md D3)."""
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        by_page: dict[int, list[Stamp]] = {}
        for s in stamps:
            by_page.setdefault(s.page, []).append(s)
        for number, items in by_page.items():
            page = doc[number - 1]
            redact = [r for s in items for r in s.redact]
            if redact:
                for r in redact:
                    page.add_redact_annot(_prect(r), fill=False)
                # Remove only the placeholder text; keep lines, highlights and images.
                page.apply_redactions(
                    images=pymupdf.PDF_REDACT_IMAGE_NONE,  # type: ignore[attr-defined]
                    graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,  # type: ignore[attr-defined]
                )
            for w in list(page.widgets() or []):
                if any(_prect(s.rect).intersects(w.rect) for s in items):
                    page.delete_widget(w)
            for s in items:
                _write(page, s)
        return doc.tobytes(garbage=3, deflate=True)


def _write(page: pymupdf.Page, s: Stamp) -> None:
    r = _prect(s.rect)
    if s.kind == "check":
        inset = min(r.width, r.height) * 0.2
        a = pymupdf.Rect(r.x0 + inset, r.y0 + inset, r.x1 - inset, r.y1 - inset)
        page.draw_line(a.tl, a.br, color=_INK, width=1.2)
        page.draw_line(a.tr, a.bl, color=_INK, width=1.2)
        return
    if s.kind == "signature" and s.image:
        page.insert_image(r, stream=s.image, keep_proportion=True)
        return
    text = s.value.strip()
    if not text:
        return
    font = "tiit" if s.kind == "signature" else "helv"
    color = _INK if s.kind == "signature" else (0, 0, 0)
    if s.kind != "signature" and r.height >= 22:  # a block: wrap, shrinking until it fits
        size = 10.0
        while size >= 5:
            if page.insert_textbox(r, text, fontsize=size, fontname=font, color=color) >= 0:
                return
            size -= 0.5
    size = min(
        r.height * (0.8 if s.kind == "signature" else 0.72), 20.0 if font == "tiit" else 11.0
    )
    width = pymupdf.get_text_length(text, fontname=font, fontsize=size)
    if width > r.width - 3:
        size = max(4.0, size * (r.width - 3) / width)
    baseline = r.y1 - max(1.5, r.height * 0.18)
    page.insert_text((r.x0 + 1.5, baseline), text, fontsize=size, fontname=font, color=color)


def draw_fields(
    data: bytes, boxes: list[tuple[int, Box, str, tuple[float, float, float]]]
) -> bytes:
    """A review copy: each (page, rect, label, rgb) drawn as a tinted, labelled box."""
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        for number, box, label, rgb in boxes:
            page = doc[number - 1]
            r = _prect(box)
            page.draw_rect(r, color=rgb, fill=rgb, fill_opacity=0.15, width=0.7)
            page.insert_text((r.x0 + 1, r.y0 + 5.5), label[:60], fontsize=5, color=rgb)
        return doc.tobytes(garbage=3, deflate=True)


def _prect(b: Box) -> pymupdf.Rect:
    return pymupdf.Rect(b.x0, b.y0, b.x1, b.y1)
