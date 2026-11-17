"""Labels + measured slots -> template fields and roles (DECISIONS.md D2 steps 4-5).

Field rectangles come only from geometry we measured. Then sanity checks: fields stay inside the
page, don't overlap, and every signing role has somewhere to sign (otherwise it is listed in
`unsigned_roles` and the pipeline appends a signature page for it).
"""

from dataclasses import dataclass, field

from app.domain.fields import SENDER, Field, FieldSource, FieldType, Rect, Role
from app.services.detection.schema import NONE, Labels, SlotLabel, slug
from app.services.pdf_engine import AppendedField, Box, Extraction, PageText, Slot

_SIGN_HEIGHT = {"signature": 30.0, "initials": 20.0}
_MIN_HEIGHT = 10.0
_MIN_SIGNATURE_WIDTH = 100.0


@dataclass
class Proposal:
    roles: list[Role]
    fields: list[Field]
    unsigned_roles: list[Role]
    warnings: list[str] = field(default_factory=list)


def build(
    extraction: Extraction,
    labels: Labels,
    *,
    source: FieldSource,
    heuristic_slots: frozenset[str] = frozenset(),
) -> Proposal:
    slots = extraction.slot_map()
    pages = {p.number: p for p in extraction.pages}
    roles = _roles(labels)
    role_keys = {r.key for r in roles}
    warnings = list(labels.warnings)

    fields: list[Field] = []
    for lab in labels.slots:
        if lab.type == NONE or lab.type == "not_a_field" or lab.filled_by == NONE:
            continue
        slot = slots[lab.slot_id]
        ftype, key = _field_type(lab)
        filled_by = lab.filled_by if lab.filled_by in role_keys else SENDER
        if filled_by == SENDER and ftype in ("signature", "initials", "date_signed"):
            continue  # can't happen after schema.check; defensive for heuristic merges
        rect, redact = _rect(slot, ftype, pages[slot.page])
        fields.append(
            Field(
                id=f"fld_{slot.id}",
                key=key,
                label=lab.label.strip() or key.replace("_", " ").capitalize(),
                type=ftype,
                filled_by=filled_by,
                page=slot.page,
                rect=rect,
                required=lab.required,
                source="heuristic" if slot.id in heuristic_slots else source,
                slot_id=slot.id,
                redact=redact,
            )
        )
    _remove_instructions(fields, labels, slots)
    _dedupe_keys(fields)
    warnings += _resolve_overlaps(fields)
    signed = {f.filled_by for f in fields if f.type == "signature"}
    unsigned = [r for r in roles if r.key not in signed]
    for r in unsigned:
        warnings.append(
            f"No place for {r.label} to sign was found, so a signature page was added at the end."
        )
    return Proposal(roles=roles, fields=fields, unsigned_roles=unsigned, warnings=warnings)


def add_appended(proposal: Proposal, appended: list[AppendedField], source: FieldSource) -> None:
    labels = {r.key: r.label for r in proposal.roles}
    nice = {"signature": "signature", "name": "printed name", "date_signed": "date signed"}
    for i, a in enumerate(appended, start=1):
        proposal.fields.append(
            Field(
                id=f"fld_app{i}_{a.role_key}_{a.type}",
                key=f"{a.role_key}_{a.type}",
                label=f"{labels.get(a.role_key, a.role_key)} {nice[a.type]}",
                type=a.type,
                filled_by=a.role_key,
                page=a.page,
                rect=_to_rect(a.rect),
                required=True,
                source=source,
            )
        )
    _dedupe_keys(proposal.fields)


def _roles(labels: Labels) -> list[Role]:
    parties = {p.key: p for p in labels.parties}
    referenced = {s.filled_by for s in labels.slots if s.filled_by not in (SENDER, NONE)}
    signing = [p.key for p in labels.parties if p.signs_here or p.key in referenced]
    order = [k for k in labels.signing_order if k in signing]
    rank = {"client": 0, "firm": 1, "other": 2}
    order += sorted((k for k in signing if k not in order), key=lambda k: rank[parties[k].kind])
    roles = [
        Role(key=k, label=parties[k].name.strip() or k.title(), kind=parties[k].kind, order=i)
        for i, k in enumerate(order, start=1)
    ]
    if not roles:
        roles = [Role(key="client", label="Client", kind="client", order=1)]
    return roles


def _field_type(lab: SlotLabel) -> tuple[FieldType, str]:
    key = slug(lab.key) if lab.key else slug(lab.label)
    if lab.type == "title":
        return "text", key
    if lab.type == "name" and lab.filled_by == SENDER:
        return "text", key
    return lab.type, key  # type: ignore[return-value]


def _rect(slot: Slot, ftype: FieldType, page: PageText) -> tuple[Rect, list[Rect]]:
    b = slot.bbox
    if slot.kind == "form_field" or (slot.kind == "checkbox" and ftype == "checkbox"):
        return _clip(_to_rect(b), page), []
    # "[X]" also reads as a ticked box; labelled as a value ("$[X]" for a fee), it is a placeholder.
    if slot.kind in ("placeholder", "checkbox"):
        rect = _to_rect(b)
        if ftype in _SIGN_HEIGHT:
            rect = Rect(x0=b.x0, y0=b.y1 - _SIGN_HEIGHT[ftype], x1=max(b.x1, b.x0 + 60), y1=b.y1)
        return _clip(rect, page), [_to_rect(r) for r in slot.redact]

    # A line-like blank: the value sits on the line, so the field grows upward from it, but not
    # into the text above.
    bottom = slot.baseline + 2
    font_height = b.height if slot.kind != "drawn_line" else 12.0
    want = _SIGN_HEIGHT.get(ftype, max(12.0, min(font_height * 1.15, 18.0)))
    top = bottom - want
    ceiling = max(
        (
            ln.bbox.y1
            for ln in page.lines
            if ln.id != slot.line_id and ln.bbox.y1 <= bottom - 4 and ln.bbox.x_overlap(b) > 0
        ),
        default=None,
    )
    if ceiling is not None:
        top = max(top, ceiling + 1)
    if bottom - top < _MIN_HEIGHT:
        top = bottom - _MIN_HEIGHT
    x0, x1 = b.x0, b.x1
    if ftype == "signature" and x1 - x0 < _MIN_SIGNATURE_WIDTH:
        x1 = x0 + _MIN_SIGNATURE_WIDTH
    return _clip(Rect(x0=x0, y0=top, x1=x1, y1=bottom), page), []


def _remove_instructions(fields: list[Field], labels: Labels, slots: dict[str, Slot]) -> None:
    """A bracketed instruction right next to a blank ("$____ [PROVIDE AMOUNT]") is not a field
    itself, but it must not survive into the signed copy: remove it with the blank's value.
    "Next to" means on the same line, or else the blank ends one line and the instruction starts
    the next one."""
    by_slot = {f.slot_id: f for f in fields if f.slot_id}
    blanks = [(slots[sid], f) for sid, f in by_slot.items() if slots[sid].kind != "placeholder"]

    def alone_on_row(s: Slot, side: str) -> bool:
        """Nothing (text or slot) after `s` on its line (side='right') or before it ('left')."""
        text = s.after if side == "right" else s.before
        if text.strip():
            return False
        return not any(
            o.page == s.page
            and o is not s
            and abs(o.baseline - s.baseline) <= 3
            and (o.bbox.x0 >= s.bbox.x1 if side == "right" else o.bbox.x1 <= s.bbox.x0)
            for o in slots.values()
        )

    for lab in labels.slots:
        note = slots[lab.slot_id]
        if lab.type != "not_a_field" or note.kind != "placeholder":
            continue
        same_page = [(b, f) for b, f in blanks if b.page == note.page]
        target = next(
            (
                f
                for b, f in same_page
                if abs(b.baseline - note.baseline) <= 3
                and max(b.bbox.x0 - note.bbox.x1, note.bbox.x0 - b.bbox.x1) <= 40
            ),
            None,
        )
        if target is None and alone_on_row(note, "left"):
            target = next(
                (
                    f
                    for b, f in same_page
                    if 3 < note.baseline - b.baseline <= 2 * max(b.bbox.height, 10)
                    and alone_on_row(b, "right")
                ),
                None,
            )
        if target is not None:
            target.redact = [*target.redact, *(_to_rect(r) for r in note.redact)]


def _clip(r: Rect, page: PageText) -> Rect:
    return Rect(
        x0=max(0.0, min(r.x0, page.width - 1)),
        y0=max(0.0, min(r.y0, page.height - 1)),
        x1=max(1.0, min(r.x1, page.width)),
        y1=max(1.0, min(r.y1, page.height)),
    )


def _to_rect(b: Box) -> Rect:
    return Rect(x0=b.x0, y0=b.y0, x1=b.x1, y1=b.y1)


def _resolve_overlaps(fields: list[Field]) -> list[str]:
    """Shrink the lower of two overlapping fields; warn if that would make it unusable."""
    warnings: list[str] = []
    by_page: dict[int, list[Field]] = {}
    for f in fields:
        by_page.setdefault(f.page, []).append(f)
    for page_fields in by_page.values():
        page_fields.sort(key=lambda f: f.rect.y0)
        for i, upper in enumerate(page_fields):
            for lower in page_fields[i + 1 :]:
                if upper.rect.intersection_area(lower.rect) <= 0:
                    continue
                new_top = upper.rect.y1 + 0.5
                if lower.rect.y1 - new_top >= _MIN_HEIGHT * 0.8:
                    lower.rect = lower.rect.model_copy(update={"y0": new_top})
                else:
                    warnings.append(
                        f"'{upper.label}' and '{lower.label}' overlap on page {upper.page}."
                    )
    return warnings


def _dedupe_keys(fields: list[Field]) -> None:
    """Fields share a value only when they share key, filler and type."""
    seen: dict[str, tuple[str, str]] = {}
    for f in fields:
        sig = (f.filled_by, f.type)
        key = f.key
        n = 2
        while key in seen and seen[key] != sig:
            key = f"{f.key}_{n}"
            n += 1
        seen.setdefault(key, sig)
        f.key = key
