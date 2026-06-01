"""Parcel Ops Control Tower — HS-classification showcase (regex vs LLM).

Curated test cases live in `llm_test_cases.json`. The regex classifier is a
small deterministic function over those cases (mirrors a production keyword
classifier). The LLM classifier calls a real model via `llm.client` when a
key is configured; otherwise it returns a "no key" placeholder.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------

@dataclass
class ClassificationResult:
    method: str
    hs_code: str
    confidence: float
    reasoning: str
    correct: Optional[bool] = None
    latency_ms: Optional[float] = None
    is_mock: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Test case loading
# ---------------------------------------------------------------------------

_CASES_PATH = Path(__file__).parent / "llm_test_cases.json"


def _load_cases() -> list[dict]:
    with _CASES_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def get_all_test_cases() -> list[dict]:
    return _load_cases()


# ---------------------------------------------------------------------------
# Regex classifier
# ---------------------------------------------------------------------------

# A small set of deliberately naive keyword rules. They match what a real
# production regex pipeline would do: look for headlining keywords, ignore
# context, and pick the first rule that fires. This is intentionally
# imperfect so the showcase has something to compare against.
_REGEX_RULES = [
    # Plastics
    (r"\bplastic\b", "3926.90", 0.72, "Keyword 'plastic' matches Chapter 39 (Plastics)."),
    # Steel / metal frames
    (r"\bmetal\b.*\bframe\b|\bsteel\b.*\bframe\b", "7308.90", 0.68, "Keyword 'metal'/'steel' and 'frame' match Chapter 73 (Iron/steel articles)."),
    # Ceramic
    (r"\bceramic\b.*\bmug\b|\bcoffee\b", "6911.10", 0.81, "Keyword 'ceramic'/'coffee' matches Chapter 69 (Ceramic tableware)."),
    # Generic USB / storage
    (r"\busb\b.*\bflash\b|\bflash drive\b", "8523.51", 0.74, "Keyword 'USB flash drive' matches 8523 (semiconductor media)."),
    # Voltage / integrated circuit
    (r"\bspannungsregler\b|integrated circuit|voltage regulator", "8504.40", 0.55, "Partial match on voltage regulator / integrated circuit. Low confidence without context."),
    # Photovoltaic
    (r"photovoltaic|modulo fotovoltaico|fotovoltai", "8541.40", 0.60, "Partial match on 'photovoltaic'. Lower confidence on multilingual variants."),
    # Software on paper (license key cards, manuals) — printed matter carrier
    (r"\bprinted\b.*\bcard\b|\bpaper\b.*\bcard\b|license key.*paper|license.*printed", "4901.99", 0.88, "Keyword 'printed/paper card' matches 4901 (printed matter). Some LLMs incorrectly associate 'software' with recording media even when no media is shipped."),
    # Software
    (r"\bsoftware\b", "8523.80", 0.65, "Keyword 'software' matches 8523 (recording media). Misses paper-only carrier nuance."),
    # T-shirt
    (r"\bt-shirt\b|t shirt", "6109.10", 0.95, "Direct match: T-shirt, knitted, cotton."),
    # Knives
    (r"\bknife|knives", "8211.91", 0.93, "Direct match: knives with cutting edges."),
    # Refurbished electronics (still HS 8471.30, but regex may not reason)
    (r"\blaptop\b|notebook computer", "8471.30", 0.90, "Direct match: portable automatic data processing machine."),
    # Generic electronics keywords
    (r"\belectronic\b", "8542.31", 0.45, "Generic 'electronic' keyword. Low confidence without specifics."),
    # Generic electronic housing in Chinese
    (r"塑料|电子元件", "3926.90", 0.40, "Detected '塑料' (plastic). Cannot parse full Chinese description."),
]


def classify_with_regex(description: str, context: str = "") -> ClassificationResult:
    """Naive keyword-based classifier over the description."""
    haystack = f"{description} {context}".lower()
    for pattern, hs_code, conf, reasoning in _REGEX_RULES:
        if re.search(pattern, haystack, flags=re.IGNORECASE):
            return ClassificationResult(
                method="regex",
                hs_code=hs_code,
                confidence=conf,
                reasoning=reasoning,
            )
    return ClassificationResult(
        method="regex",
        hs_code="9999.99",
        confidence=0.0,
        reasoning="No keyword rule fired.",
    )


# ---------------------------------------------------------------------------
# LLM classifier
# ---------------------------------------------------------------------------

def classify_with_llm(
    description: str,
    context: str = "",
    *,
    force_mock: bool = False,
) -> ClassificationResult:
    """Real LLM classification via the Gemini OpenAI-compat endpoint.

    Falls back to a "no key" placeholder if no API key is configured, or
    to a forced mock for unit testing.
    """
    if force_mock:
        return _mock_llm_result(description)

    try:
        from parcel_ops_llm import classify_hs_code, load_llm_config
        import streamlit as st
        overrides = st.session_state.get("_llm_overrides", {})
        try:
            secrets = dict(st.secrets) if hasattr(st, "secrets") else {}
        except Exception:
            secrets = {}
        cfg = load_llm_config(overrides, secrets)
    except Exception as e:
        return ClassificationResult(
            method="llm",
            hs_code="—",
            confidence=0.0,
            reasoning=f"LLM client not available: {e!s}",
            is_mock=True,
        )

    if not cfg.api_key and cfg.provider != "ollama":
        return ClassificationResult(
            method="llm",
            hs_code="—",
            confidence=0.0,
            reasoning=(
                f"No API key. Enter a {cfg.provider.title()} key in the sidebar, "
                f"or set GEMINI_API_KEY env var."
            ),
            is_mock=True,
        )

    try:
        parsed, latency = classify_hs_code(cfg, description, context)
    except Exception as e:
        return ClassificationResult(
            method="llm",
            hs_code="—",
            confidence=0.0,
            reasoning=f"Call failed: {e!s}",
            latency_ms=None,
            is_mock=False,
        )

    if "error" in parsed:
        return ClassificationResult(
            method="llm",
            hs_code="—",
            confidence=0.0,
            reasoning=str(parsed["error"]),
            latency_ms=latency * 1000,
            is_mock=False,
        )

    return ClassificationResult(
        method="llm",
        hs_code=str(parsed.get("hs_code", "—")),
        confidence=float(parsed.get("confidence", 0.5)),
        reasoning=str(parsed.get("reasoning", "")).strip(),
        latency_ms=latency * 1000,
        is_mock=False,
    )


def _mock_llm_result(description: str) -> ClassificationResult:
    """Pre-canned LLM result, used when no key is set or for tests."""
    cases = {c["description"]: c for c in _load_cases()}
    if description in cases:
        return ClassificationResult(
            method="llm",
            hs_code="0000.00",
            confidence=0.0,
            reasoning="Mock result — live LLM call not executed.",
            is_mock=True,
        )
    return ClassificationResult(
        method="llm",
        hs_code="0000.00",
        confidence=0.0,
        reasoning="Mock result — live LLM call not executed.",
        is_mock=True,
    )


# ---------------------------------------------------------------------------
# Gold-answer evaluation
# ---------------------------------------------------------------------------

def evaluate_results(case: dict, regex: ClassificationResult, llm: ClassificationResult) -> dict:
    """Mark each result against the case's gold HS code."""
    gold = case.get("gold_hs_code", "")
    return {
        "gold_hs_code": gold,
        "expected_winner": case.get("expected_winner"),
        "regex_correct": regex.hs_code == gold and regex.hs_code != "—",
        "llm_correct": llm.hs_code == gold and llm.hs_code != "—",
    }


def get_statistics(results: Optional[list[dict]] = None) -> dict:
    """Compute summary stats from a list of {regex_correct, llm_correct, ...} dicts.

    If no results provided, returns zeros.
    """
    if not results:
        return {
            "total_cases": 0,
            "regex_accuracy": 0.0,
            "llm_accuracy": 0.0,
            "agreement_rate": 0.0,
            "regex_correct_count": 0,
            "llm_correct_count": 0,
            "both_correct": 0,
            "only_llm_correct": 0,
            "only_regex_correct": 0,
            "both_wrong": 0,
        }
    total = len(results)
    rc = sum(1 for r in results if r["regex_correct"])
    lc = sum(1 for r in results if r["llm_correct"])
    agree = sum(1 for r in results if r["regex_correct"] == r["llm_correct"])
    both = sum(1 for r in results if r["regex_correct"] and r["llm_correct"])
    only_llm = sum(1 for r in results if r["llm_correct"] and not r["regex_correct"])
    only_regex = sum(1 for r in results if r["regex_correct"] and not r["llm_correct"])
    both_wrong = total - both - only_llm - only_regex
    return {
        "total_cases": total,
        "regex_accuracy": rc / total,
        "llm_accuracy": lc / total,
        "agreement_rate": agree / total,
        "regex_correct_count": rc,
        "llm_correct_count": lc,
        "both_correct": both,
        "only_llm_correct": only_llm,
        "only_regex_correct": only_regex,
        "both_wrong": both_wrong,
    }


def compare_methods(description: str, context: str = "", *, force_mock: bool = False) -> dict:
    """Run both methods and return a combined result for a single case."""
    regex = classify_with_regex(description, context)
    llm = classify_with_llm(description, context, force_mock=force_mock)
    return {
        "description": description,
        "context": context,
        "regex": regex.to_dict(),
        "llm": llm.to_dict(),
    }


# ---------------------------------------------------------------------------
# Saved results (pre-captured baseline, persistent across sessions)
# ---------------------------------------------------------------------------
#
# The first time a visitor opens the LLM tab they see a real per-case table
# populated from `llm_saved_results.json` — no API key required. They can
# click "Run LLM on all cases" to re-run the live batch and overwrite it
# (the fresh results are written back to the JSON via `save_results`).
#
# The seed file is regenerated by `scripts/capture_llm_baseline.py`, which
# paces calls to stay under the free-tier 5 RPM limit.

import os
from datetime import datetime, timezone
from typing import Any

_SAVED_PATH = Path(__file__).parent / "llm_saved_results.json"


def build_evald(case: dict, llm_res: ClassificationResult) -> dict:
    """Build a single per-case evald in the shape the UI expects.

    Used by both the runtime (in `app.py`) and the capture script so the
    JSON written to disk is byte-for-byte compatible with the in-memory
    session state.
    """
    regex_res = classify_with_regex(case["description"], case.get("context", ""))
    evald = evaluate_results(case, regex_res, llm_res)
    evald["case_id"] = case["id"]
    evald["description"] = case["description"]
    evald["expected_winner"] = case.get("expected_winner")
    evald["regex_result"] = regex_res.to_dict()
    evald["llm_result"] = llm_res.to_dict()
    evald["llm_pitfall"] = case.get("llm_pitfall")
    evald["gold_reasoning"] = case.get("gold_reasoning", "")
    return evald


def make_baseline_results() -> list[dict]:
    """Build a per-case evald list with regex scores but the no-key LLM placeholder.

    Used as a last-resort fallback when no `llm_saved_results.json` exists
    and no API key is set. The LLM column will show "Mock result — no API
    key" for every case; regex scores are always real.
    """
    results = []
    for case in _load_cases():
        # LLM placeholder mirrors classify_with_llm()'s no-key path
        llm_stub = ClassificationResult(
            method="llm",
            hs_code="—",
            confidence=0.0,
            reasoning="No API key. Enter a Gemini key in the sidebar, or set GEMINI_API_KEY env var.",
            is_mock=True,
        )
        results.append(build_evald(case, llm_stub))
    return results


def load_saved_results() -> Optional[dict[str, Any]]:
    """Load the pre-captured baseline from disk.

    Returns None if the file is missing or malformed. The file format is
    `{"version": 1, "captured_at": ISO8601, "model": "...", "results": [...]}`.
    """
    if not _SAVED_PATH.exists():
        return None
    try:
        with _SAVED_PATH.open("r", encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    # Light schema check — if the shape changed, treat as missing so the
    # caller falls back to a fresh baseline rather than rendering broken
    # expanders.
    if not isinstance(payload, dict) or "results" not in payload:
        return None
    if not isinstance(payload["results"], list):
        return None
    return payload


def save_results(
    results: list[dict],
    model: str,
    captured_at: Optional[str] = None,
    path: Optional[Path] = None,
) -> Path:
    """Persist a per-case evald list to `llm_saved_results.json`.

    Writes atomically (tmp file + rename) so a partial write can never
    leave the file unparseable. Returns the path that was written.
    """
    target = Path(path) if path is not None else _SAVED_PATH
    payload = {
        "version": 1,
        "captured_at": captured_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": model,
        "results": results,
    }
    tmp = target.with_suffix(target.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    os.replace(tmp, target)
    return target
