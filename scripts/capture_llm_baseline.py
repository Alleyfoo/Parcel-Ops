"""Capture a live LLM baseline and persist it to `llm_saved_results.json`.

This is the one-time (or as-needed) data-collection step that produces the
pre-saved file shipped with the repo. On first open of the LLM tab, a
visitor sees real per-case results without configuring an API key. They
can then click "Run LLM on all cases" inside the dashboard to refresh
those results with their own key (the dashboard writes the new file back
in place).

Usage
-----
    # Cloud (gemini, paced to stay under the 5 RPM free-tier limit)
    python scripts/capture_llm_baseline.py --provider gemini --api-key YOUR_GEMINI_KEY

    # Local ollama (no rate limit, no API key, ~3s/case)
    python scripts/capture_llm_baseline.py --provider ollama --model llama3.2:latest

    # Or set the env vars (the dashboard reads these too)
    set GEMINI_API_KEY=YOUR_GEMINI_KEY        # PowerShell
    set LLM_PROVIDER=ollama
    set LLM_MODEL=llama3.2:latest
    python scripts/capture_llm_baseline.py

Pacing
------
Free-tier Gemini is throttled to ~5 RPM (one call per 12s). The script
paces at 13s/call to stay safely below the limit and stops cleanly on a
429, saving whatever it captured so far. Ollama is local with no rate
limit, so by default we send the next call as soon as the previous one
returns (~30s for 12 cases).
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
    get_all_test_cases,
    get_statistics,
    save_results,
)
from parcel_ops_llm import (
    LLMError,
    RateLimitError,
    classify_hs_code,
    load_llm_config,
)


PACE_SECONDS = 13.0


def _print(msg: str) -> None:
    # Plain stdout, no rich output — the user is watching a 3-min script.
    # Use ASCII fallbacks for ✓/✗ etc so the script works on Windows
    # consoles that default to cp1252.
    safe = msg.replace("✓", "+").replace("✗", "x").replace("…", "...")
    try:
        print(safe, flush=True)
    except UnicodeEncodeError:
        # Last-ditch: encode with errors=replace
        print(safe.encode("ascii", errors="replace").decode("ascii"), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture a live LLM baseline.")
    parser.add_argument(
        "--provider",
        default=None,
        choices=["gemini", "ollama"],
        help="Provider to call. Default: gemini (matches the dashboard's default).",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="API key. Falls back to GEMINI_API_KEY / OLLAMA_API_KEY / LLM_API_KEY env. Ignored for ollama.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Model to call (default: parcel_ops_llm default for the provider).",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="Override the OpenAI-compat base URL (e.g. for remote ollama).",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Override output path. Default: <repo>/llm_saved_results.json",
    )
    parser.add_argument(
        "--no-pace",
        action="store_true",
        help="Don't sleep between calls. Use for ollama (no rate limit).",
    )
    args = parser.parse_args()

    overrides = {}
    if args.provider:
        overrides["provider"] = args.provider
    if args.api_key:
        overrides["api_key"] = args.api_key
    if args.model:
        overrides["model"] = args.model
    if args.base_url:
        overrides["base_url"] = args.base_url

    cfg = load_llm_config(overrides)
    if cfg.provider != "ollama" and not cfg.api_key:
        _print(f"No API key for {cfg.provider}. Pass --api-key or set GEMINI_API_KEY.")
        return 2

    cases = get_all_test_cases()
    n = len(cases)
    # No pacing for ollama — no rate limit, no API cost per call.
    pace = 0.0 if (args.no_pace or cfg.provider == "ollama") else PACE_SECONDS
    _print(f"Capturing baseline: provider={cfg.provider}  model={cfg.model}  base_url={cfg.base_url}  cases={n}  pace={pace:.0f}s")
    if pace > 0:
        _print(f"Estimated time: ~{int(n * pace / 60)} min.\n")
    else:
        _print("")

    results: list[dict] = []
    rate_limited_at: int | None = None

    for i, case in enumerate(cases, 1):
        _print(f"[{i}/{n}] {case['description'][:60]}...")
        t0 = time.perf_counter()
        try:
            # Call the LLM directly with our own config. Don't go through
            # llm_classifier.classify_with_llm — it always rebuilds cfg
            # from streamlit session state, ignoring ours.
            parsed, latency = classify_hs_code(cfg, case["description"], case.get("context", ""))
            llm_dict = parsed
            # Normalize the error shape to a ClassificationResult for build_evald
            if "error" in parsed:
                llm_res = ClassificationResult(
                    method="llm",
                    hs_code="—",
                    confidence=0.0,
                    reasoning=str(parsed["error"]),
                    latency_ms=latency * 1000,
                    is_mock=False,
                )
            else:
                llm_res = ClassificationResult(
                    method="llm",
                    hs_code=str(parsed.get("hs_code", "—")),
                    confidence=float(parsed.get("confidence", 0.5)),
                    reasoning=str(parsed.get("reasoning", "")).strip(),
                    latency_ms=latency * 1000,
                    is_mock=False,
                )
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
        llm_latency = llm_dict.get("latency_ms")
        latency_s = f"  ({llm_latency:.0f} ms)" if isinstance(llm_latency, (int, float)) else ""
        _print(f"  {ok}  gold={gold}  llm={actual}{latency_s}")

        if i < n and pace > 0:
            elapsed = time.perf_counter() - t0
            sleep_for = max(0.0, pace - elapsed)
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
