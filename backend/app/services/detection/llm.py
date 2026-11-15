"""Slot labelling with Claude (DECISIONS.md D12).

One structured-output call: the PDF plus our slot listing in, `Labels` JSON out. If the answer
fails validation we retry once with the problems listed. Every failure raises `LlmUnavailable`,
and the pipeline falls back to heuristics: detection must never block the user.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import anthropic
from anthropic import AsyncAnthropic, transform_schema
from pydantic import ValidationError

from app.config import Settings
from app.services.detection import prompt
from app.services.detection.schema import Labels, check
from app.services.pdf_engine import Extraction

log = logging.getLogger(__name__)

# Server-side refusal fallback: a declined request is re-run on Anthropic's recommended model
# for that refusal category, inside the same call.
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_TOKENS = 64_000  # thinking + JSON; streamed, so a high ceiling costs nothing unless used
CALL_TIMEOUT_S = 150.0
TOTAL_BUDGET_S = 240.0  # stays inside the job lease (worker/queue.py LEASE)
MAX_REQUEST_BYTES = 30 * 1024 * 1024  # API request cap is 32 MB; leave room for the listing
LABELS_SCHEMA = transform_schema(Labels.model_json_schema())


class LlmUnavailable(Exception):
    """The model gave no usable answer. The message is a short reason for logs and the UI."""


@dataclass
class LlmResult:
    labels: Labels
    problems: list[str]  # validation problems left after the retry (entries were dropped)
    model: str  # the model that actually answered (differs if a fallback ran)
    attempts: int
    duration_ms: int
    usage: dict[str, int] = field(default_factory=dict)


class ClaudeLabeller:
    def __init__(self, client: Any, *, model: str, effort: str) -> None:
        self.client = client
        self.model = model
        self.effort = effort

    @classmethod
    def from_settings(cls, settings: Settings) -> "ClaudeLabeller | None":
        if settings.anthropic_api_key is None:
            return None
        client = AsyncAnthropic(
            api_key=settings.anthropic_api_key.get_secret_value(),
            timeout=CALL_TIMEOUT_S,
            max_retries=2,
        )
        return cls(client, model=settings.llm_model, effort=settings.llm_effort)

    async def label(
        self, extraction: Extraction, pdf: bytes, *, firm_name: str | None = None
    ) -> LlmResult:
        if len(pdf) * 4 // 3 > MAX_REQUEST_BYTES:
            raise LlmUnavailable("the document is too large to send to the model")
        start = time.monotonic()
        slot_ids = [s.id for s in extraction.slots]
        system = prompt.system_prompt(firm_name)
        usage: dict[str, int] = {}

        labels, problems, model = await self._call(
            system, prompt.user_content(extraction, pdf), slot_ids, usage, CALL_TIMEOUT_S
        )
        attempts = 1
        remaining = TOTAL_BUDGET_S - (time.monotonic() - start)
        if problems and remaining > 30:
            log.warning("llm labels failed validation; retrying", extra={"problems": problems[:5]})
            attempts = 2
            try:
                retry = await self._call(
                    system,
                    prompt.user_content(extraction, pdf, problems=problems),
                    slot_ids,
                    usage,
                    min(CALL_TIMEOUT_S, remaining),
                )
                if len(retry[1]) <= len(problems):
                    labels, problems, model = retry
            except LlmUnavailable as exc:
                log.warning(
                    "llm retry failed; keeping the first answer", extra={"reason": str(exc)}
                )
        duration_ms = int((time.monotonic() - start) * 1000)
        log.info(
            "llm labelling done",
            extra={
                "model": model,
                "attempts": attempts,
                "ms": duration_ms,
                "problems": len(problems),
                "usage": usage,
            },
        )
        return LlmResult(labels, problems, model, attempts, duration_ms, usage)

    async def _call(
        self,
        system: str,
        content: list[dict[str, Any]],
        slot_ids: list[str],
        usage: dict[str, int],
        timeout_s: float,
    ) -> tuple[Labels, list[str], str]:
        try:
            async with asyncio.timeout(timeout_s):
                async with self.client.beta.messages.stream(
                    model=self.model,
                    max_tokens=MAX_TOKENS,
                    system=system,
                    messages=[{"role": "user", "content": content}],
                    thinking={"type": "adaptive"},
                    output_config={
                        "effort": self.effort,
                        "format": {"type": "json_schema", "schema": LABELS_SCHEMA},
                    },
                    betas=[FALLBACK_BETA],
                    fallbacks="default",
                ) as stream:
                    message = await stream.get_final_message()
        except TimeoutError as exc:
            raise LlmUnavailable(f"the model took longer than {timeout_s:.0f}s") from exc
        except anthropic.APIStatusError as exc:
            raise LlmUnavailable(f"API error {exc.status_code}") from exc
        except anthropic.APIConnectionError as exc:
            raise LlmUnavailable("could not reach the API") from exc

        _add_usage(usage, getattr(message, "usage", None))
        if message.stop_reason == "refusal":
            details = getattr(message, "stop_details", None)
            category = getattr(details, "category", None) if details else None
            raise LlmUnavailable(f"the model declined (category: {category or 'unspecified'})")
        if message.stop_reason == "max_tokens":
            raise LlmUnavailable("the model's answer was cut off (max_tokens)")
        # After a mid-stream fallback, content holds the declined model's partial text, then a
        # `fallback` block, then the answer. Only text after the last fallback block counts.
        parts: list[str] = []
        for block in message.content:
            if block.type == "fallback":
                parts = []
            elif block.type == "text":
                parts.append(block.text)
        try:
            labels = Labels.model_validate_json("".join(parts))
        except ValidationError as exc:
            raise LlmUnavailable("the model's answer did not match the schema") from exc
        cleaned, problems = check(labels, slot_ids)
        return cleaned, problems, str(getattr(message, "model", self.model))


def _add_usage(total: dict[str, int], usage: Any) -> None:
    if usage is None:
        return
    for name in (
        "input_tokens",
        "output_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
    ):
        value = getattr(usage, name, None)
        if isinstance(value, int):
            total[name] = total.get(name, 0) + value
