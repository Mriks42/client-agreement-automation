"""Detection: schema validation, the LLM path (against a fake client), fallbacks, and mapping."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
import pytest

from app.services import pdf_engine
from app.services.detection import heuristics
from app.services.detection.llm import ClaudeLabeller
from app.services.detection.pipeline import analyze
from app.services.detection.schema import Labels, Party, SlotLabel, check

FIXTURES = Path(__file__).parent.parent / "evals" / "fixtures"


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


# ---- fake Anthropic client --------------------------------------------------------------------
class _Stream:
    def __init__(self, message: Any) -> None:
        self.message = message

    async def __aenter__(self) -> "_Stream":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def get_final_message(self) -> Any:
        return self.message


class FakeClient:
    def __init__(self, *responses: Any) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))

    def _stream(self, **kwargs: Any) -> _Stream:
        self.calls.append(kwargs)
        r = self.responses.pop(0)
        if isinstance(r, BaseException):
            raise r
        return _Stream(r)


def message(
    text: str | None = None,
    *,
    stop_reason: str = "end_turn",
    content: list[Any] | None = None,
    model: str = "claude-opus-5-5",
) -> Any:
    return SimpleNamespace(
        stop_reason=stop_reason,
        stop_details=SimpleNamespace(category="cyber") if stop_reason == "refusal" else None,
        model=model,
        content=content if content is not None else [SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=1000, output_tokens=200, cache_read_input_tokens=0),
    )


def labeller(client: FakeClient) -> ClaudeLabeller:
    return ClaudeLabeller(client, model="claude-opus-5-5", effort="high")


def good_labels(pdf: bytes) -> Labels:
    """A complete, valid answer for a fixture (built from the heuristics, which score 100% on
    this fixture), relabelled with the parties an LLM would name."""
    return heuristics.label(pdf_engine.extract(pdf))


# ---- schema.check -----------------------------------------------------------------------------
def test_check_reports_every_kind_of_problem_and_keeps_the_valid_rest() -> None:
    labels = Labels(
        parties=[
            Party(key="Client", name="Client", kind="client", signs_here=True),
            Party(key="sender", name="Bad", kind="other", signs_here=True),
        ],
        signing_order=["Client", "ghost"],
        slots=[
            SlotLabel(
                slot_id="p1-s1",
                type="text",
                filled_by="sender",
                key="Client Name",
                label="Client name",
                required=True,
            ),
            SlotLabel(
                slot_id="p1-s1",
                type="text",
                filled_by="sender",
                key="dup",
                label="dup",
                required=True,
            ),
            SlotLabel(
                slot_id="p9-s9", type="text", filled_by="sender", key="x", label="x", required=True
            ),
            SlotLabel(
                slot_id="p1-s2",
                type="signature",
                filled_by="sender",
                key="sig",
                label="Signature",
                required=True,
            ),
            SlotLabel(
                slot_id="p1-s3",
                type="date_signed",
                filled_by="Client",
                key="d",
                label="Date",
                required=True,
            ),
        ],
        warnings=[],
    )
    cleaned, problems = check(labels, ["p1-s1", "p1-s2", "p1-s3", "p1-s4"])
    text = " | ".join(problems)
    assert "reserved" in text and "more than once" in text and "does not exist" in text
    assert "filled_by is 'sender'" in text and "p1-s2, p1-s4" in text
    assert [p.key for p in cleaned.parties] == ["client"]
    assert cleaned.signing_order == ["client"]
    assert [(s.slot_id, s.filled_by, s.key) for s in cleaned.slots] == [
        ("p1-s1", "sender", "client_name"),
        ("p1-s3", "client", "d"),
    ]


# ---- LLM path ---------------------------------------------------------------------------------
async def test_llm_request_shape_and_success() -> None:
    pdf = fixture("two_party_services.pdf")
    client = FakeClient(message(good_labels(pdf).model_dump_json()))
    result = await analyze(pdf, labeller=labeller(client), firm_name="Northwind Test Advisory")

    assert result.detector == "ai" and result.fallback_reason is None
    assert {f.source for f in result.fields} == {"ai"}
    [call] = client.calls
    assert call["model"] == "claude-opus-5-5"
    assert call["thinking"] == {"type": "adaptive"}
    assert call["output_config"]["effort"] == "high"
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert call["fallbacks"] == "default" and "server-side-fallback-2026-07-01" in call["betas"]
    for banned in ("temperature", "top_p", "top_k", "tool_choice"):
        assert banned not in call
    doc, task = call["messages"][0]["content"][:2]
    assert doc["type"] == "document" and doc["cache_control"] == {"type": "ephemeral"}
    assert "⟦p2-s2⟧" in task["text"]  # slots are marked inline in the listing
    assert "Northwind Test Advisory" in call["system"]
    assert result.llm and result.llm["usage"]["input_tokens"] == 1000


async def test_invalid_answer_is_retried_with_the_problems_listed() -> None:
    pdf = fixture("two_party_services.pdf")
    good = good_labels(pdf)
    partial = good.model_copy(update={"slots": good.slots[:3]})
    client = FakeClient(message(partial.model_dump_json()), message(good.model_dump_json()))
    result = await analyze(pdf, labeller=labeller(client))

    assert result.detector == "ai" and len(client.calls) == 2
    retry_text = client.calls[1]["messages"][0]["content"][-1]["text"]
    assert "previous answer" in retry_text and "p2-s9" in retry_text
    assert result.llm and result.llm["attempts"] == 2


async def test_slots_still_unlabelled_after_retry_are_filled_by_heuristics() -> None:
    pdf = fixture("two_party_services.pdf")
    good = good_labels(pdf)
    partial = good.model_copy(update={"slots": good.slots[:-1]})
    client = FakeClient(message(partial.model_dump_json()), message(partial.model_dump_json()))
    result = await analyze(pdf, labeller=labeller(client))

    assert result.detector == "ai"
    assert result.heuristic_slots == ["p2-s9"]
    filled = next(f for f in result.fields if f.slot_id == "p2-s9")
    assert filled.source == "heuristic" and filled.type == "date_signed"


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (message(stop_reason="refusal", content=[]), "declined"),
        (message('{"parties": [', stop_reason="max_tokens"), "max_tokens"),
        (message("not json at all"), "schema"),
        (anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.test")), "reach"),
        (RuntimeError("boom"), "unexpected"),
    ],
)
async def test_any_llm_failure_falls_back_to_heuristics(response: Any, reason: str) -> None:
    pdf = fixture("two_party_services.pdf")
    result = await analyze(pdf, labeller=labeller(FakeClient(response)))
    assert result.detector == "heuristic"
    assert result.fallback_reason and reason in result.fallback_reason
    assert {f.filled_by for f in result.fields if f.type == "signature"} == {"provider", "client"}


async def test_only_text_after_a_mid_stream_fallback_counts() -> None:
    pdf = fixture("two_party_services.pdf")
    content = [
        SimpleNamespace(type="text", text='{"parties": [{"key": "cli'),
        SimpleNamespace(type="fallback"),
        SimpleNamespace(type="text", text=good_labels(pdf).model_dump_json()),
    ]
    result = await analyze(
        pdf, labeller=labeller(FakeClient(message(content=content, model="claude-opus-5")))
    )
    assert result.detector == "ai"
    assert result.llm and result.llm["model"] == "claude-opus-5"


async def test_no_labeller_means_heuristics_with_a_reason() -> None:
    result = await analyze(fixture("two_party_services.pdf"), labeller=None)
    assert result.detector == "heuristic"
    assert result.fallback_reason == "AI detection is not configured"


# ---- mapping and signature pages --------------------------------------------------------------
async def test_fields_sit_inside_the_page_without_overlapping() -> None:
    result = await analyze(fixture("two_party_services.pdf"), labeller=None)
    sizes = pdf_engine.page_sizes(result.pdf)
    for f in result.fields:
        w, h = sizes[f.page - 1]
        assert 0 <= f.rect.x0 < f.rect.x1 <= w and 0 <= f.rect.y0 < f.rect.y1 <= h
    for i, a in enumerate(result.fields):
        for b in result.fields[i + 1 :]:
            if a.page == b.page:
                assert a.rect.intersection_area(b.rect) == 0, (a.label, b.label)
    sig = next(f for f in result.fields if f.type == "signature")
    text = next(f for f in result.fields if f.type == "text")
    assert sig.rect.height > text.rect.height


async def test_repeated_placeholder_shares_one_value_and_is_redacted() -> None:
    result = await analyze(fixture("bracket_placeholders.pdf"), labeller=None)
    names = [f for f in result.fields if f.key == "client_name"]
    assert len(names) == 2 and all(f.redact for f in names)


async def test_document_without_signature_block_gets_a_signature_page() -> None:
    result = await analyze(fixture("no_signature_block.pdf"), labeller=None)
    assert result.appended_signature_page
    appended = [f for f in result.fields if f.slot_id is None]
    assert {f.type for f in appended} == {"signature", "name", "date_signed"}
    assert all(f.page == result.page_count for f in appended)
    assert any("signature page" in w for w in result.warnings)


async def test_scan_gets_a_signature_page_and_a_manual_placement_warning() -> None:
    result = await analyze(fixture("scanned.pdf"), labeller=None)
    assert result.appended_signature_page and result.page_count == 3
    assert any("scanned" in w for w in result.warnings)


async def test_party_that_already_signed_gets_no_signature_page() -> None:
    result = await analyze(fixture("single_party_engagement.pdf"), labeller=None)
    assert not result.appended_signature_page
    assert [r.key for r in result.roles] == ["client"]


def test_instruction_next_to_a_blank_is_removed_with_its_value() -> None:
    import pymupdf

    from app.services.detection import mapper

    doc = pymupdf.open()
    doc.new_page().insert_text(
        (72, 100), "Initial deposit of $____________ [PROVIDE AMOUNT] by Friday."
    )
    pdf = doc.tobytes()
    ex = pdf_engine.extract(pdf)
    blank = next(s for s in ex.slots if s.kind == "underscore")
    note = next(s for s in ex.slots if s.kind == "placeholder")
    labels = Labels(
        parties=[Party(key="client", name="Client", kind="client", signs_here=True)],
        signing_order=["client"],
        slots=[
            SlotLabel(
                slot_id=blank.id,
                type="text",
                filled_by="sender",
                key="deposit",
                label="Deposit",
                required=True,
            ),
            SlotLabel(
                slot_id=note.id,
                type="not_a_field",
                filled_by="none",
                key="none",
                label="Instruction",
                required=False,
            ),
        ],
        warnings=[],
    )
    proposal = mapper.build(ex, labels, source="ai")
    [field] = proposal.fields
    assert field.slot_id == blank.id and len(field.redact) == 1
    assert field.redact[0].x0 == pytest.approx(note.bbox.x0)
