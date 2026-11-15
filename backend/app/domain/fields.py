"""Fields and roles: what a template asks each party to fill in, and where.

Coordinates are PDF points with a top-left origin on the *normalized* PDF (rotation baked in at
ingest, see pdf_engine.normalize_pdf), so they match what the reviewer sees. Pages are 1-based.
"""

from typing import Any, Literal

from pydantic import BaseModel
from pydantic import Field as PydField

FieldType = Literal["signature", "initials", "date_signed", "name", "text", "date", "checkbox"]
FieldSource = Literal["ai", "heuristic", "manual"]
RoleKind = Literal["firm", "client", "other"]

SENDER = "sender"
# Types only a signer can fill (the sender can't sign or date on someone's behalf).
SIGNER_ONLY_TYPES: frozenset[str] = frozenset({"signature", "initials", "date_signed"})


class Rect(BaseModel):
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

    def intersection_area(self, other: "Rect") -> float:
        w = min(self.x1, other.x1) - max(self.x0, other.x0)
        h = min(self.y1, other.y1) - max(self.y0, other.y0)
        return w * h if w > 0 and h > 0 else 0.0


class Role(BaseModel):
    key: str = PydField(description="snake_case, unique within the template, e.g. 'client'")
    label: str
    kind: RoleKind
    order: int = PydField(description="1-based signing order")


class Field(BaseModel):
    id: str
    key: str = PydField(
        description="Value key. Fields that share a key (and filled_by) share one value, "
        "e.g. every [CLIENT NAME] placeholder, or a signer's initials on every page."
    )
    label: str
    type: FieldType
    filled_by: str = PydField(description="'sender' or a role key")
    page: int
    rect: Rect
    required: bool = True
    source: FieldSource
    slot_id: str | None = None
    # Placeholder text (e.g. "[CLIENT NAME]") that must be removed before the value is written.
    redact: list[Rect] = PydField(default_factory=list)


def sender_inputs(fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The new-file form: one input per value the sender types, in reading order. Fields that
    share a key share the input, so each [CLIENT NAME] is typed once."""
    inputs: dict[str, dict[str, Any]] = {}
    for f in sorted(fields, key=lambda f: (f["page"], f["rect"]["y0"], f["rect"]["x0"])):
        if f["filled_by"] != SENDER:
            continue
        if (seen := inputs.get(f["key"])) is not None:
            seen["count"] += 1
            seen["required"] = seen["required"] or f["required"]
            continue
        height = f["rect"]["y1"] - f["rect"]["y0"]
        inputs[f["key"]] = {
            "key": f["key"],
            "label": f["label"],
            "type": f["type"],
            "required": f["required"],
            # A tall text box is a paragraph (stamping wraps text in boxes 22 pt and taller).
            "multiline": f["type"] == "text" and height >= 22,
            "page": f["page"],
            "count": 1,
        }
    return list(inputs.values())
