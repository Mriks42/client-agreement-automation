"""Detection eval: run the real pipeline on every fixture and score it against expected/*.json.

    uv run python -m evals.run --offline       # heuristics only, free
    uv run python -m evals.run                 # full pipeline with Claude (costs money; ask first)
    uv run python -m evals.run --baseline evals/out/<earlier report>.json   # compare runs

A blank is classified correctly when the field on that blank has the expected type and the
expected filler (the sender, or the role that matches the expected party). Expected blanks are
located by their label text and reading-order position, so scoring never uses our own labels.

Day 2 exit criterion: every signature block found on every fixture, and at least 90% of blanks
classified correctly. The exit code is 1 when the criterion isn't met.

DOCX fixtures need LibreOffice. Without it they are reported as skipped; run inside the
production image for the full set:
    docker run --rm agreement-app python -m evals.run --offline
"""

import argparse
import asyncio
import json
import logging
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import Settings
from app.services import ingest
from app.services.detection.llm import ClaudeLabeller
from app.services.detection.pipeline import Analysis, Labeller, analyze

HERE = Path(__file__).parent
FIXTURES = HERE / "fixtures"
EXPECTED = HERE / "expected"
OUT = HERE / "out"

# Approximate list prices for claude-opus-5-5, USD per million tokens (for the cost line only).
PRICE = {
    "input_tokens": 4.0,
    "output_tokens": 20.0,
    "cache_creation_input_tokens": 5.0,
    "cache_read_input_tokens": 0.2,
}


@dataclass
class FixtureResult:
    name: str
    status: str  # ok | skipped | error
    detector: str = ""
    fallback_reason: str | None = None
    blanks_total: int = 0
    blanks_correct: int = 0
    signatures_ok: bool = False
    roles: dict[str, str | None] = field(default_factory=dict)
    extra_roles: list[str] = field(default_factory=list)
    misses: list[str] = field(default_factory=list)
    false_positives: list[str] = field(default_factory=list)
    signature_problems: list[str] = field(default_factory=list)
    note: str = ""
    llm: dict[str, Any] | None = None


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().rstrip(":").strip().lower()


def score(name: str, expected: dict[str, Any], a: Analysis) -> FixtureResult:
    res = FixtureResult(name, "ok", detector=a.detector, fallback_reason=a.fallback_reason)
    res.llm = a.llm

    for exp_key, aliases in expected["roles"].items():
        match = None
        for r in a.roles:
            hay = f"{r.key.replace('_', ' ')} {r.label}".lower()
            if r.key in aliases or any(re.search(rf"\b{re.escape(al)}\b", hay) for al in aliases):
                match = r.key
                break
        res.roles[exp_key] = match
    mapped = {v for v in res.roles.values() if v}
    res.extra_roles = [r.key for r in a.roles if r.key not in mapped]

    by_slot = {f.slot_id: f for f in a.fields if f.slot_id}
    matched: set[str] = set()
    for exp in expected["fields"]:
        page_no = exp["page"] if exp["page"] > 0 else a.original_page_count + 1 + exp["page"]
        res.blanks_total += 1
        where = f"p{page_no} {exp['label']!r} #{exp['nth']}"
        want_by = (
            "sender"
            if exp["filled_by"] == "sender"
            else res.roles.get(exp["filled_by"]) or f"<no {exp['filled_by']} role>"
        )
        want = f"{exp['type']}/{want_by}"
        page = a.extraction.pages[page_no - 1] if page_no <= a.extraction.page_count else None
        candidates = [s for s in page.slots if norm(s.anchor) == norm(exp["label"])] if page else []
        if len(candidates) < exp["nth"]:
            res.misses.append(f"{where}: no blank found (expected {want})")
            if exp["type"] == "signature":
                res.signature_problems.append(f"{where}: signature blank not found")
            continue
        slot = candidates[exp["nth"] - 1]
        matched.add(slot.id)
        f = by_slot.get(slot.id)
        got = f"{f.type}/{f.filled_by}" if f else "not a field"
        if f is not None and f.type == exp["type"] and f.filled_by == want_by:
            res.blanks_correct += 1
            continue
        res.misses.append(f"{where}: expected {want}, got {got}")
        if exp["type"] == "signature":
            res.signature_problems.append(f"{where}: expected {want}, got {got}")

    for f in a.fields:
        if f.slot_id and f.slot_id not in matched:
            res.false_positives.append(f"p{f.page} {f.label!r} ({f.type}/{f.filled_by})")

    appended_sigs = {f.filled_by for f in a.fields if f.slot_id is None and f.type == "signature"}
    if expected.get("expect_appended_signature_page"):
        for exp_key, role in res.roles.items():
            if role is None:
                res.signature_problems.append(f"role {exp_key!r} not found")
            elif role not in appended_sigs:
                res.signature_problems.append(f"no appended signature field for {role!r}")
    elif appended_sigs:
        res.signature_problems.append(
            f"signature page appended for {sorted(appended_sigs)} although the document has "
            "signature blocks"
        )
    for exp_key, role in res.roles.items():
        if role is None and f"role {exp_key!r} not found" not in res.signature_problems:
            res.signature_problems.append(f"role {exp_key!r} not found")
    res.signatures_ok = not res.signature_problems
    return res


async def run_fixture(
    path: Path, expected: dict[str, Any], labeller: Labeller | None, settings: Settings
) -> FixtureResult:
    name = path.stem
    try:
        data = await asyncio.to_thread(path.read_bytes)
        ing = await ingest.ingest(data, settings)
    except ingest.IngestError as exc:
        if exc.code == "conversion_unavailable":
            return FixtureResult(name, "skipped", note="LibreOffice not installed")
        return FixtureResult(name, "error", note=f"ingest failed: {exc.code}")
    analysis = await analyze(ing.pdf, labeller=labeller)
    return score(name, expected, analysis)


def fixture_files(only: list[str] | None) -> list[tuple[Path, dict[str, Any]]]:
    out = []
    for exp_path in sorted(EXPECTED.glob("*.json")):
        expected = json.loads(exp_path.read_text())
        ext = ".docx" if expected.get("source_format") == "docx" else ".pdf"
        src = FIXTURES / f"{exp_path.stem}{ext}"
        if only and exp_path.stem not in only:
            continue
        out.append((src, expected))
    return out


def report(results: list[FixtureResult], baseline: dict[str, Any] | None, min_acc: float) -> bool:
    base = {r["name"]: r for r in (baseline or {}).get("fixtures", [])}
    print(f"\n{'fixture':26} {'detector':9} {'blanks':>8} {'sigs':>5} {'FP':>3}  roles")
    for r in results:
        if r.status != "ok":
            print(f"{r.name:26} {r.status.upper():9} {'':>8} {'':>5} {'':>3}  {r.note}")
            continue
        blanks = f"{r.blanks_correct}/{r.blanks_total}"
        roles = ", ".join(f"{k}->{v or 'MISSING'}" for k, v in r.roles.items())
        if r.extra_roles:
            roles += f" (+{', '.join(r.extra_roles)})"
        delta = ""
        if r.name in base and base[r.name].get("status") == "ok":
            d = r.blanks_correct - base[r.name]["blanks_correct"]
            delta = f" ({d:+d})" if d else ""
        sigs = "ok" if r.signatures_ok else "FAIL"
        fps = len(r.false_positives)
        print(f"{r.name:26} {r.detector:9} {blanks + delta:>8} {sigs:>5} {fps:>3}  {roles}")
        if r.fallback_reason and r.detector == "heuristic":
            print(f"    fallback: {r.fallback_reason}")
        for line in r.signature_problems:
            print(f"    SIGNATURE  {line}")
        for line in r.misses:
            print(f"    miss       {line}")
        for line in r.false_positives:
            print(f"    extra      {line}")

    ok = [r for r in results if r.status == "ok"]
    total = sum(r.blanks_total for r in ok)
    correct = sum(r.blanks_correct for r in ok)
    acc = correct / total if total else 0.0
    sig_ok = sum(r.signatures_ok for r in ok)
    fps = sum(len(r.false_positives) for r in ok)
    print(f"\nBlanks classified correctly: {correct}/{total} ({acc:.1%})", end="")
    if baseline and baseline.get("accuracy") is not None:
        print(f"  [baseline {baseline['accuracy']:.1%}]", end="")
    print(f"\nSignature blocks found: {sig_ok}/{len(ok)} fixtures   False positives: {fps}")
    usage: dict[str, int] = {}
    for r in ok:
        for k, v in ((r.llm or {}).get("usage") or {}).items():
            usage[k] = usage.get(k, 0) + v
    if usage:
        cost = sum(PRICE.get(k, 0) * v / 1e6 for k, v in usage.items())
        print(f"LLM usage: {usage}  (approx ${cost:.2f})")
    skipped = [r.name for r in results if r.status == "skipped"]
    errors = [r.name for r in results if r.status == "error"]
    if skipped:
        print(f"Skipped: {', '.join(skipped)} (run inside the Docker image for the full set)")
    met = not errors and sig_ok == len(ok) and acc >= min_acc
    verdict = "MET" if met else "NOT MET"
    print(f"Exit criterion (all signature blocks, >= {min_acc:.0%} blanks): {verdict}")
    return met


async def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--offline", action="store_true", help="heuristics only; no API calls")
    parser.add_argument("--only", nargs="*", help="fixture names to run")
    parser.add_argument("--baseline", type=Path, help="earlier report JSON to compare against")
    parser.add_argument("--min-accuracy", type=float, default=0.9)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("-v", "--verbose", action="store_true", help="show pipeline logs")
    args = parser.parse_args()
    # Fallback and conversion problems are reported per fixture; logs are only for debugging.
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.CRITICAL,
        format="  log %(levelname)s %(name)s: %(message)s",
    )

    settings = Settings()
    labeller: Labeller | None = None
    if not args.offline:
        labeller = ClaudeLabeller.from_settings(settings)
        if labeller is None:
            print("ANTHROPIC_API_KEY is not set. Add it to backend/.env, or use --offline.")
            return 2
        print(f"Online eval with {settings.llm_model} (effort {settings.llm_effort}).")

    sem = asyncio.Semaphore(args.concurrency)

    async def one(path: Path, expected: dict[str, Any]) -> FixtureResult:
        async with sem:
            return await run_fixture(path, expected, labeller, settings)

    results = await asyncio.gather(*(one(p, e) for p, e in fixture_files(args.only)))
    baseline = json.loads(args.baseline.read_text()) if args.baseline else None
    met = report(list(results), baseline, args.min_accuracy)

    ok = [r for r in results if r.status == "ok"]
    total = sum(r.blanks_total for r in ok)
    OUT.mkdir(exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    out = OUT / f"{'offline' if args.offline else 'online'}-{stamp}.json"
    out.write_text(
        json.dumps(
            {
                "mode": "offline" if args.offline else "online",
                "model": None if args.offline else settings.llm_model,
                "effort": None if args.offline else settings.llm_effort,
                "created_at": stamp,
                "accuracy": sum(r.blanks_correct for r in ok) / total if total else None,
                "criterion_met": met,
                "fixtures": [asdict(r) for r in results],
            },
            indent=2,
        )
    )
    print(f"Report: {out.relative_to(HERE.parent)}")
    return 0 if met else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
