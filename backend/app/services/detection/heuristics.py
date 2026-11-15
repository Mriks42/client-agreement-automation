"""Keyword and layout heuristics: the fallback when the LLM is unavailable, and the offline eval
baseline. Deliberately conservative and general; a person reviews every result either way.

1. Parties come from defined terms such as `Acme LLC ("Provider")`, or from capitalized party
   words used repeatedly ("Client will pay...").
2. Each slot gets a type from the words just before it (or its caption, or placeholder text).
3. Each signature slot is assigned to the party named on its line or in the heading above its
   column; name/title/date slots below a signature in the same column belong to that signer.
"""

import re
from dataclasses import dataclass, field

from app.services.detection.schema import Labels, Party, SlotLabel, SlotType, slug
from app.services.pdf_engine import Extraction, Line, PageText, Slot

FALLBACK_WARNING = "Fields were proposed by keyword rules, not AI. Check every field carefully."

_SIGNATURE = re.compile(r"\b(signature|signed|sign here|signatory)\b|^by\b", re.I)
_INITIALS = re.compile(r"\binitials?\b", re.I)
_NAME = re.compile(r"\bname\b", re.I)
_TITLE = re.compile(r"\b(title|position|capacity)\b", re.I)
_DATE = re.compile(r"\bdated?\b", re.I)
_CONSENT = re.compile(r"\b(i|we)\s+(accept|agree|acknowledge|consent|confirm)", re.I)
_LABEL_LINE = re.compile(
    r"^\s*(?:[A-Za-z'\u2019]+\s){0,3}"
    r"(signature|signed|by|name|printed name|print name|title|position|date|dated|initials?"
    r"|company|organi[sz]ation|entity|e-?mail|address|mail|phone|telephone|mobile|fax)\s*:",
    re.I,
)
_DEFINED = re.compile(
    r"([A-Z][\w.,&'\u2019 -]{0,80}?)?\s*\(\s*(?:the\s+|each\s+a\s+)?"
    r"[\"\u201c]([A-Z][A-Za-z ]{1,30})[\"\u201d]"
)
CLIENT_WORDS = ("client", "customer", "buyer", "purchaser", "licensee", "recipient")
FIRM_WORDS = (
    "provider",
    "service provider",
    "company",
    "consultant",
    "contractor",
    "firm",
    "studio",
    "agency",
    "supplier",
    "vendor",
    "licensor",
    "seller",
    "advisor",
    "adviser",
)
_NAME_JOINERS = {"of", "and", "&", "the", "de", "la"}


@dataclass
class _Party:
    key: str
    name: str
    kind: str
    aliases: set[str] = field(default_factory=set)


def label(extraction: Extraction) -> Labels:
    parties = _find_parties(extraction)
    types: dict[str, SlotType] = {}
    owner: dict[str, str] = {}
    labels_text: dict[str, str] = {}

    for page in extraction.pages:
        for slot in page.slots:
            types[slot.id], labels_text[slot.id] = _classify(slot, page)

    for page in extraction.pages:
        lines = {ln.id: ln for ln in page.lines}
        sigs = [s for s in page.slots if types[s.id] == "signature"]
        for sig in sigs:
            party = _party_in(sig.before, parties) or _heading_party(sig, page, lines, parties)
            if party:
                owner[sig.id] = party.key
        _resolve_side_by_side(sigs, owner, parties)
        for slot in page.slots:
            t = types[slot.id]
            if t == "checkbox" and _CONSENT.search(slot.before + " " + slot.after):
                owner[slot.id] = _client(parties).key  # "I accept/agree..." is the signer's act
            elif t == "initials":
                p = _party_in(slot.before + " " + slot.caption, parties)
                owner[slot.id] = p.key if p else _client(parties).key
            elif t in ("name", "title", "date"):
                owning = _owning_signature(slot, sigs, lines)
                if owning is not None:
                    owner[slot.id] = owner.get(owning.id, "")  # filled in below if unresolved
                    if t == "date":
                        types[slot.id] = "date_signed"

    # Unresolved signatures: the client first, then the firm.
    for page in extraction.pages:
        for slot in page.slots:
            if types[slot.id] == "signature" and not owner.get(slot.id):
                used = {owner[s] for s in owner if types[s] == "signature"}
                client = _client(parties)
                owner[slot.id] = client.key if client.key not in used else _firm(parties).key
    for page in extraction.pages:
        lines = {ln.id: ln for ln in page.lines}
        sigs = [s for s in page.slots if types[s.id] == "signature"]
        for slot in page.slots:
            if slot.id in owner and not owner[slot.id]:
                owning = _owning_signature(slot, sigs, lines)
                owner[slot.id] = owner[owning.id] if owning else _client(parties).key

    out_slots: list[SlotLabel] = []
    for slot in extraction.slots:
        t = types[slot.id]
        text = labels_text[slot.id]
        if t == "not_a_field":
            out_slots.append(
                SlotLabel(
                    slot_id=slot.id,
                    type=t,
                    filled_by="none",
                    key="none",
                    label=text or "Not a field",
                    required=False,
                )
            )
            continue
        who = owner.get(slot.id)
        if who is None:
            if t in ("name", "title"):
                t = "text"  # a name/title outside any signature block is per-file data
            out_slots.append(
                SlotLabel(
                    slot_id=slot.id,
                    type=t,
                    filled_by="sender",
                    key=slug(text or "field"),
                    label=text or "Field",
                    required=t != "checkbox",
                )
            )
        else:
            out_slots.append(
                SlotLabel(
                    slot_id=slot.id,
                    type=t,
                    filled_by=who,
                    key=f"{who}_{t}",
                    label=_signer_label(t, parties, who),
                    required=t != "checkbox",
                )
            )

    signing = {s.filled_by for s in out_slots if s.type == "signature"}
    if not signing:
        signing = {_client(parties).key}  # nowhere to sign: the client signs an appended page
    used = signing | {s.filled_by for s in out_slots}
    party_list = [
        Party(key=p.key, name=p.name, kind=p.kind, signs_here=p.key in signing)
        for p in parties
        if p.key in used or p.kind != "other"
    ]
    rank = {"client": 0, "firm": 1, "other": 2}
    order = [p.key for p in sorted(party_list, key=lambda p: rank[p.kind]) if p.signs_here]
    warnings = [FALLBACK_WARNING]
    return Labels(parties=party_list, signing_order=order, slots=out_slots, warnings=warnings)


# ---- parties ----------------------------------------------------------------------------------
def _find_parties(extraction: Extraction) -> list[_Party]:
    found: dict[str, _Party] = {}
    for page in extraction.pages:
        for block in _blocks_text(page):
            for m in _DEFINED.finditer(block):
                term = m.group(2).strip()
                kind = _kind_of(term)
                if kind is None:
                    continue
                key = slug(term)
                party = found.setdefault(key, _Party(key, term, kind, {term.lower()}))
                entity = _trailing_name(m.group(1) or "")
                if entity:
                    party.aliases.add(entity.lower())
    if not found:
        # No defined terms: party words used as names ("Client will pay ...").
        text = " ".join(ln.text for p in extraction.pages for ln in p.lines)
        for word in (*CLIENT_WORDS, *FIRM_WORDS):
            if len(re.findall(rf"\b{word.title()}\b", text)) >= 2:
                kind = _kind_of(word)
                if kind:
                    found.setdefault(slug(word), _Party(slug(word), word.title(), kind, {word}))
    parties = list(found.values())
    if not any(p.kind == "client" for p in parties):
        parties.insert(0, _Party("client", "Client", "client", {"client"}))
    return parties


def _kind_of(term: str) -> str | None:
    t = term.lower()
    if t in CLIENT_WORDS:
        return "client"
    if t in FIRM_WORDS:
        return "firm"
    return None


def _trailing_name(text: str) -> str:
    """'This Agreement is between Acme Test LLC' -> 'Acme Test LLC'."""
    words = text.strip().split()
    taken: list[str] = []
    for w in reversed(words):
        if w[:1].isupper() or w.lower() in _NAME_JOINERS:
            taken.append(w)
        else:
            break
    while taken and taken[-1].lower() in _NAME_JOINERS:
        taken.pop()
    name = " ".join(reversed(taken)).strip(" ,")
    return name if len(name) >= 3 else ""


def _blocks_text(page: PageText) -> list[str]:
    # Lines are in reading order; joining consecutive lines lets a defined term wrap.
    texts = [ln.text for ln in page.lines]
    return [" ".join(texts[i : i + 3]) for i in range(len(texts))]


def _party_in(text: str, parties: list[_Party]) -> _Party | None:
    t = text.lower()
    best: tuple[int, _Party] | None = None
    for p in parties:
        for alias in p.aliases:
            if re.search(rf"\b{re.escape(alias)}\b", t) and (best is None or len(alias) > best[0]):
                best = (len(alias), p)
    return best[1] if best else None


def _client(parties: list[_Party]) -> _Party:
    return next(p for p in parties if p.kind == "client")


def _firm(parties: list[_Party]) -> _Party:
    firm = next((p for p in parties if p.kind == "firm"), None)
    if firm is None:
        firm = _Party("firm", "Firm", "firm", {"firm"})
        parties.append(firm)
    return firm


# ---- slots ------------------------------------------------------------------------------------
def _classify(slot: Slot, page: PageText) -> tuple[SlotType, str]:
    if slot.kind == "checkbox" or (
        slot.kind == "form_field" and slot.detail.lower().startswith(("checkbox", "radiobutton"))
    ):
        return "checkbox", _short(slot.before or slot.after) or "Checkbox"
    if slot.kind == "placeholder":
        inner = slot.text.strip("[]{}<>«» ").strip()
        if _SIGNATURE.search(inner):
            return "signature", inner.title()
        if _INITIALS.search(inner):
            return "initials", inner.title()
        if _DATE.search(inner):
            return "date", inner.title()
        return "text", inner.title()
    label_src = slot.before or slot.caption or slot.text
    tail = _tail(label_src)
    if not re.search(r"[A-Za-z]", tail):
        if slot.bbox.width > 0.4 * page.width or slot.kind == "dots":
            return "not_a_field", "Decorative line"
        tail = ""
    if slot.kind == "form_field" and "signature" in slot.detail.lower():
        return "signature", _short(tail) or "Signature"
    if _INITIALS.search(tail):
        return "initials", _short(tail)
    if _SIGNATURE.search(tail):
        return "signature", _short(tail)
    if _NAME.search(tail):
        return "name", _short(tail)
    if _TITLE.search(tail):
        return "title", _short(tail)
    if _DATE.search(tail):
        return "date", _short(tail)
    return "text", _short(tail) or "Field"


def _tail(text: str) -> str:
    """The last few words before a blank: what a person would read as its label."""
    words = re.split(r"\s+", text.strip())
    tail = " ".join(words[-5:])
    # Cut at sentence punctuation so "...as agreed. Name:" reads as "Name:".
    parts = re.split(r"[.;]\s+", tail)
    return parts[-1]


def _short(text: str) -> str:
    return text.strip().rstrip(":").strip()[:60]


def _signer_label(t: str, parties: list[_Party], who: str) -> str:
    name = next((p.name for p in parties if p.key == who), who.title())
    nice = {"date_signed": "date signed"}.get(t, t)
    return f"{name} {nice}"


def _heading_party(
    sig: Slot, page: PageText, lines: dict[str, Line], parties: list[_Party]
) -> _Party | None:
    """The party named by the nearest non-label line above this signature, in its column."""
    own = lines.get(sig.line_id or "")
    col_x0 = own.bbox.x0 if own else sig.bbox.x0
    col_x1 = sig.bbox.x1
    above = [
        ln
        for ln in page.lines
        if 0 < sig.baseline - ln.baseline <= 160
        and col_x0 - 30 <= ln.bbox.x0 <= col_x1
        and ln.id != sig.line_id
    ]
    for ln in sorted(above, key=lambda ln: -ln.baseline):
        has_slot = any(s.line_id == ln.id and s.kind != "placeholder" for s in page.slots)
        if has_slot or _LABEL_LINE.match(ln.text):
            continue
        party = _party_in(ln.text, parties)
        if party:
            return party
        if len(ln.text) > 80:
            return None  # reached body text without finding a heading
    return None


def _resolve_side_by_side(sigs: list[Slot], owner: dict[str, str], parties: list[_Party]) -> None:
    rows: dict[int, list[Slot]] = {}
    for s in sigs:
        rows.setdefault(round(s.baseline / 4), []).append(s)
    for row in rows.values():
        if len(row) != 2:
            continue
        known = [owner.get(s.id) for s in row]
        if known.count(None) == 1:
            taken = next(k for k in known if k)
            other = next((p for p in parties if p.key != taken and p.kind != "other"), None)
            if other:
                for s in row:
                    owner.setdefault(s.id, other.key)


def _owning_signature(slot: Slot, sigs: list[Slot], lines: dict[str, Line]) -> Slot | None:
    same_line = [
        s for s in sigs if s.line_id and s.line_id == slot.line_id and s.bbox.x0 < slot.bbox.x0
    ]
    if same_line:
        return max(same_line, key=lambda s: s.bbox.x0)
    own = lines.get(slot.line_id or "")
    x0 = own.bbox.x0 if own else slot.bbox.x0
    candidates = []
    for s in sigs:
        sig_line = lines.get(s.line_id or "")
        sx0 = sig_line.bbox.x0 if sig_line else s.bbox.x0
        if abs(sx0 - x0) <= 40 and -4 <= slot.baseline - s.baseline <= 130:
            candidates.append(s)
    return max(candidates, key=lambda s: s.baseline, default=None)
