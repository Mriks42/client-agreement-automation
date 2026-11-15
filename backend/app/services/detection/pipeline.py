"""Detection end to end (DECISIONS.md D2): extract -> label (LLM, else heuristics) -> map ->
append a signature page for any signing role with nowhere to sign.

`analyze` never raises for detection problems: if the LLM fails for any reason, the result comes
from heuristics and `fallback_reason` says why, so the UI can tell the reviewer.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from app.domain.fields import SENDER, Field, Role
from app.services import pdf_engine
from app.services.detection import heuristics, mapper
from app.services.detection.llm import LlmResult, LlmUnavailable
from app.services.detection.schema import NONE, Labels, SlotLabel
from app.services.pdf_engine import Extraction

log = logging.getLogger(__name__)

Detector = Literal["ai", "heuristic"]


class Labeller(Protocol):
    async def label(
        self, extraction: Extraction, pdf: bytes, *, firm_name: str | None = None
    ) -> LlmResult: ...


@dataclass
class Analysis:
    pdf: bytes  # the normalized PDF, with a signature page appended if one was needed
    page_count: int
    original_page_count: int
    roles: list[Role]
    fields: list[Field]
    detector: Detector
    fallback_reason: str | None
    warnings: list[str]
    extraction: Extraction
    llm: dict[str, Any] | None = None
    heuristic_slots: list[str] = field(default_factory=list)

    @property
    def appended_signature_page(self) -> bool:
        return self.page_count > self.original_page_count


async def analyze(
    pdf: bytes, *, labeller: Labeller | None, firm_name: str | None = None
) -> Analysis:
    extraction = await asyncio.to_thread(pdf_engine.extract, pdf)
    fallback = heuristics.label(extraction)
    labels: Labels = fallback
    detector: Detector = "heuristic"
    reason: str | None = None
    llm_meta: dict[str, Any] | None = None
    filled: set[str] = set()

    if labeller is None:
        reason = "AI detection is not configured"
    else:
        try:
            result = await labeller.label(extraction, pdf, firm_name=firm_name)
            labels, filled = _fill_gaps(result.labels, fallback)
            detector = "ai"
            llm_meta = {
                "model": result.model,
                "attempts": result.attempts,
                "duration_ms": result.duration_ms,
                "usage": result.usage,
                "problems": result.problems,
            }
        except LlmUnavailable as exc:
            reason = str(exc)
        except Exception:
            log.exception("llm labelling failed unexpectedly")
            reason = "unexpected error in AI detection"
    if reason:
        log.warning("detection fell back to heuristics", extra={"reason": reason})

    proposal = mapper.build(extraction, labels, source=detector, heuristic_slots=frozenset(filled))
    out_pdf = pdf
    if proposal.unsigned_roles:
        signers = [(r.key, r.label) for r in proposal.unsigned_roles]
        out_pdf, appended = await asyncio.to_thread(
            pdf_engine.append_signature_page, pdf, signers, title=extraction.title
        )
        mapper.add_appended(proposal, appended, detector)
    if not extraction.has_text_layer:
        proposal.warnings.append(
            "This looks like a scanned document with no text, so blanks couldn't be found. "
            "Place any fields by hand; a signature page was added for signing."
        )
    return Analysis(
        pdf=out_pdf,
        page_count=pdf_engine.page_count(out_pdf) if out_pdf is not pdf else extraction.page_count,
        original_page_count=extraction.page_count,
        roles=proposal.roles,
        fields=proposal.fields,
        detector=detector,
        fallback_reason=reason,
        warnings=proposal.warnings,
        extraction=extraction,
        llm=llm_meta,
        heuristic_slots=sorted(filled),
    )


def _fill_gaps(ai: Labels, fallback: Labels) -> tuple[Labels, set[str]]:
    """Slots the model left unlabelled (or labelled invalidly) take the heuristic label, with
    the heuristic's party translated to the model's party of the same kind."""
    have = {s.slot_id for s in ai.slots}
    missing = [s for s in fallback.slots if s.slot_id not in have]
    if not missing:
        return ai, set()
    kind_of = {p.key: p.kind for p in fallback.parties}
    by_kind: dict[str, str] = {}
    for p in ai.parties:
        by_kind.setdefault(p.kind, p.key)
    patched: list[SlotLabel] = []
    for s in missing:
        if s.filled_by in (SENDER, NONE):
            patched.append(s)
            continue
        target = by_kind.get(kind_of.get(s.filled_by, ""))
        if target:
            patched.append(s.model_copy(update={"filled_by": target, "key": f"{target}_{s.type}"}))
        elif s.type in ("signature", "initials", "date_signed"):
            continue  # no matching party; the reviewer can add it
        else:
            patched.append(s.model_copy(update={"filled_by": SENDER}))
    warnings = list(ai.warnings)
    if patched:
        warnings.append(
            f"{len(patched)} blank(s) the AI didn't label were filled in by keyword rules."
        )
    merged = ai.model_copy(update={"slots": [*ai.slots, *patched], "warnings": warnings})
    return merged, {s.slot_id for s in patched}
