"""What a detector returns: the parties, and a label for each extracted slot.

Both the LLM (via structured outputs) and the heuristics produce `Labels`, so everything after
detection has one code path. The model never returns coordinates: it can only label slot IDs we
measured (DECISIONS.md D2 step 3).
"""

import re
from typing import Literal

from pydantic import BaseModel, Field

from app.domain.fields import SENDER

SlotType = Literal[
    "signature",
    "initials",
    "date_signed",
    "name",
    "title",
    "date",
    "text",
    "checkbox",
    "not_a_field",
]
NONE = "none"
SIGNER_TYPES = frozenset({"signature", "initials", "date_signed", "name", "title"})


class Party(BaseModel):
    key: str = Field(description="Short snake_case identifier, e.g. 'client' or 'provider'.")
    name: str = Field(
        description="How the agreement names this party, e.g. 'Client' or 'Northwind LLC'."
    )
    kind: Literal["firm", "client", "other"]
    signs_here: bool = Field(
        description="False if this party does not sign this document or has already signed it."
    )


class SlotLabel(BaseModel):
    slot_id: str
    type: SlotType
    filled_by: str = Field(description="'sender', a party key, or 'none' for not_a_field.")
    key: str = Field(description="snake_case value key; repeat it for slots holding one value.")
    label: str = Field(description="Short label for a form, e.g. 'Client legal name'.")
    required: bool


class Labels(BaseModel):
    parties: list[Party]
    signing_order: list[str] = Field(description="Keys of the parties that sign, in order.")
    slots: list[SlotLabel]
    warnings: list[str] = Field(description="Short notes for the human reviewer. May be empty.")


def slug(text: str, fallback: str = "field") -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    if s and s[0].isdigit():
        s = f"f_{s}"
    return s[:48] or fallback


def check(labels: Labels, slot_ids: list[str]) -> tuple[Labels, list[str]]:
    """Validate labels against the slots we extracted. Returns a cleaned copy (invalid entries
    dropped, keys normalized) and a list of problems phrased so the model can fix them."""
    problems: list[str] = []
    known = set(slot_ids)

    parties: list[Party] = []
    for p in labels.parties:
        key = slug(p.key, "party")
        if key in (SENDER, NONE) or key in {q.key for q in parties}:
            problems.append(f"Party key '{p.key}' is reserved or duplicated; use a unique key.")
            continue
        parties.append(p.model_copy(update={"key": key}))
    party_keys = {p.key for p in parties}
    rename = {p.key: slug(p.key, "party") for p in labels.parties}

    seen: set[str] = set()
    slots: list[SlotLabel] = []
    for s in labels.slots:
        if s.slot_id not in known:
            problems.append(f"Slot '{s.slot_id}' does not exist; only label the listed slot IDs.")
            continue
        if s.slot_id in seen:
            problems.append(f"Slot '{s.slot_id}' was labelled more than once.")
            continue
        filled_by = rename.get(s.filled_by, s.filled_by)
        if s.type == "not_a_field":
            filled_by = NONE
        elif filled_by == SENDER and s.type in ("signature", "initials", "date_signed"):
            problems.append(
                f"Slot '{s.slot_id}' is a {s.type} but filled_by is 'sender'; "
                "a signer must fill it."
            )
            continue
        elif filled_by != SENDER and filled_by not in party_keys:
            problems.append(
                f"Slot '{s.slot_id}' has filled_by '{s.filled_by}', which is neither 'sender' nor "
                "a listed party key."
            )
            continue
        seen.add(s.slot_id)
        slots.append(s.model_copy(update={"filled_by": filled_by, "key": slug(s.key)}))

    missing = [sid for sid in slot_ids if sid not in seen]
    if missing:
        shown = ", ".join(missing[:30]) + (" ..." if len(missing) > 30 else "")
        problems.append(f"These slots have no label; label every slot exactly once: {shown}")

    order = [rename.get(k, k) for k in labels.signing_order]
    order = [k for i, k in enumerate(order) if k in party_keys and k not in order[:i]]
    cleaned = Labels(parties=parties, signing_order=order, slots=slots, warnings=labels.warnings)
    return cleaned, problems
