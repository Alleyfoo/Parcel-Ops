"""Capture a live Gemini baseline and persist it to `llm_saved_results.json`.

This is the one-time (or as-needed) data-collection step that produces the
pre-saved file shipped with the repo. On first open of the LLM tab, a
visitor sees real per-case results without configuring an API key. They
can then click "Run LLM on all cases" inside the dashboard to refresh
those results with their own key (the dashboard writes the new file back
in place).

Usage
-----
    # Pass the key on the command line
    python scripts/capture_llm_baseline.py --api-key YOUR_GEMINI_KEY

    # Or set the env var (this is what the dashboard reads too)
    set GEMINI_API_KEY=YOUR_GEMINI_KEY        # PowerShell
    export GEMINI_API_KEY=YOUR_GEMINI_KEY     # bash
    python scripts/capture_llm_baseline.py

    # Pick a different model (default: gemini-2.5-flash)
    python scripts/capture_llm_baseline.py --model gemini-2.0-flash

Pacing
------
Free-tier Gemini is throttled to ~5 RPM (one call per 12s). The script
paces at 13s/call to stay safely below the limit and stops cleanly on a
429, saving whatever it captured so far. The dashboard's "Re-run" button
behaves the same way.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# Make the repo root importable when run as a script
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from llm_classifier import (
    ClassificationResult,
    build_evald,
    classify_with_llm,
    get_all_test_cases,
    get_statistics,
    save_results,
)
from parcel_ops_llm import LLMError, RateLimitError, load_llm_config


PACE_SECONDS = 13.0


def _print(msg: str) -> None:
    # Plain stdout, no rich output — the user is watching a 3-min script.
    print(msg, flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture a live LLM baseline.")
    parser.add_argument(
        "--api-key",
        default=None,
        help="Gemini API key. Falls back to GEMINI_API_KEY / LLM_API_KEY env.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Model to call (default: parcel_ops_llm default, gemini-2.5-flash).",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Override output path. Default: <repo>/llm_saved_results.json",
    )
    args = parser.parse_args()

    overrides = {}
    if args.api_key:
        overrides["api_key"] = args.api_key
    if args.model:
        overrides["model"] = args.model

    cfg = load_llm_config(overrides)
    if not cfg.api_key:
        _print("No API key. Pass --api-key or set GEMINI_API_KEY.")
        return 2

    cases = get_all_test_cases()
    n = len(cases)
    _print(f"Capturing baseline: model={cfg.model}  cases={n}  pace={PACE_SECONDS:.0f}s")
    _print(f"Estimated time: ~{int(n * PACE_SECONDS / 60)} min.\n")

    results: list[dict] = []
    rate_limited_at: int | None = None

    for i, case in enumerate(cases, 1):
        _print(f"[{i}/{n}] {case['description'][:60]}…")
        t0 = time.perf_counter()
        try:
            llm_res = classify_with_llm(case["description"], case.get("context", ""))
        except (RateLimitError, LLMError) as e:
            _print(f"  ERROR: {e}")
            # Re-raise via the classifier's no-key stub so the JSON
            # still has a well-formed LLM entry.
            llm_res = ClassificationResult(
                method="llm",
                hs_code="—",
                confidence=0.0,
                reasoning=f"Capture failed: {e}",
                is_mock=False,
            )
            if isinstance(e, RateLimitError):
                rate_limited_at = i
                _print(f"  Rate limited at case {i}. Stopping batch.")
                break

        results.append(build_evald(case, llm_res))
        llm_dict = llm_res.to_dict()
        ok = "✓" if llm_dict.get("hs_code") == case["gold_hs_code"] else "✗"
        gold = case["gold_hs_code"]
        actual = llm_dict.get("hs_code", "—")
        latency = llm_dict.get("latency_ms")
        latency_s = f"  ({latency:.0f} ms)" if isinstance(latency, (int, float)) else ""
        _print(f"  {ok}  gold={gold}  llm={actual}{latency_s}")

        if i < n:
            elapsed = time.perf_counter() - t0
            sleep_for = max(0.0, PACE_SECONDS - elapsed)
            if sleep_for > 0:
                time.sleep(sleep_for)

    # Persist. If we hit a 429 partway through, also fill the remaining
    # cases with a "skipped" stub so the file is always complete (matches
    # the dashboard's in-memory behaviour).
    if rate_limited_at is not None:
        for case in cases[rate_limited_at:]:
            stub = ClassificationResult(
                method="llm",
                hs_code="—",
                confidence=0.0,
                reasoning=(
                    "Skipped — rate limit hit during baseline capture. "
                    "Re-run from the dashboard to fill these in."
                ),
                is_mock=False,
            )
            results.append(build_evald(case, stub))

    out = Path(args.out) if args.out else ROOT / "llm_saved_results.json"
    save_results(results, model=cfg.model, path=out)

    stats = get_statistics(results)
    _print("")
    _print(f"Saved {len(results)} cases to {out}")
    _print(f"  Regex accuracy: {stats['regex_accuracy']:.0%}")
    _print(f"  LLM accuracy:   {stats['llm_accuracy']:.0%}")
    return 0 if rate_limited_at is None else 1


if __name__ == "__main__":
    raise SystemExit(main())
