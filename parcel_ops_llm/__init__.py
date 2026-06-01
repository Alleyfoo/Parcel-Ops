"""LLM client for Parcel Ops HS-classification showcase.

Mirrors the sql-editor pattern: stdlib HTTP (no SDK dep) against the
OpenAI-compatible Gemini endpoint. API key is session-only and never
written to disk.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
OLLAMA_BASE_URL = "http://127.0.0.1:11434/v1"

# Free-tier friendly defaults. gemini-2.5-flash is the default: earlier
# in testing it returned cleaner JSON than 2.0-flash when response_format
# is honoured. Models are presented in display order.
DEFAULT_GEMINI_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-1.5-flash",
    "gemini-1.5-flash-8b",
    "gemini-1.5-pro",
]

# Ollama defaults. The model list is dynamic — `load_llm_config()` queries
# the live /v1/models endpoint and falls back to these when the server
# isn't reachable (so the UI still has *something* to populate the
# dropdown with). gemma4:latest is the default: at 10GB it scored
# 4/12 (33%) on the showcase, tied with regex — best of the locally
# available options. llama3.2:latest (2GB) gets 0/12; qwen3.5:9b is
# a thinking-tier model that hits the max_tokens budget and returns
# empty content, so it's listed but not recommended.
DEFAULT_OLLAMA_MODELS = [
    "gemma4:latest",
    "llama3.1:8b",
    "qwen3.5:9b",
    "llama3.2:latest",
    "codestral:latest",
    "devstral:latest",
]

SYSTEM_PROMPT = (
    "You are a customs classification assistant for the EU Combined Nomenclature. "
    "Given a product description and optional context, classify the product into a "
    "6-digit Harmonized System (HS) code. Apply the General Rules of Interpretation (GRI). "
    "You MUST respond with a single JSON object — no prose before, no prose after, no "
    "markdown code fences, no commentary. The JSON object must have exactly these fields:\n"
    '{"hs_code": "NNNN.NN", "confidence": 0.0-1.0, "reasoning": "<one short paragraph>"}\n'
    "Be precise: only return an HS code you can defend. If you are guessing, lower the confidence."
)


@dataclass
class LLMConfig:
    provider: str
    model: str
    api_key: str
    base_url: str
    # Generous default for local ollama: thinking-tier models can take
    # 20-30s per case on first call. 60s leaves headroom without making
    # the dashboard feel hung on a true network failure.
    timeout: float = 60.0


def load_llm_config(
    session_overrides: Optional[dict] = None,
    secrets: Optional[dict] = None,
) -> LLMConfig:
    """Resolve LLM config with precedence: env > session > secrets > defaults.

    Precedence matches the sql-editor project so the UX is familiar.

    Provider is "gemini" (default, cloud, needs an API key) or "ollama"
    (local, talks to http://127.0.0.1:11434/v1, no key required).
    """
    overrides = session_overrides or {}
    sec = secrets or {}

    # API key precedence. Ollama doesn't need a key, but the client still
    # sends a Bearer header; we just don't *require* one. Gemini and any
    # other OpenAI-compat provider do require it.
    api_key = (
        os.environ.get("GEMINI_API_KEY")
        or os.environ.get("OLLAMA_API_KEY")
        or os.environ.get("LLM_API_KEY")
        or overrides.get("api_key", "")
        or (sec.get("GEMINI_API_KEY") if isinstance(sec, dict) else "")
        or (sec.get("OLLAMA_API_KEY") if isinstance(sec, dict) else "")
        or ""
    )

    provider = (
        os.environ.get("LLM_PROVIDER")
        or overrides.get("provider", "gemini")
    )

    # Default model is provider-specific. Ollama has many tags (`:latest`,
    # `:8b`, etc) so the dashboard usually overrides this.
    default_model = "gemma4:latest" if provider == "ollama" else "gemini-2.5-flash"
    model = (
        os.environ.get("LLM_MODEL")
        or overrides.get("model", default_model)
    )

    # Base URL per provider. Env override wins so power users can point
    # at a remote ollama or a different Gemini proxy.
    if provider == "ollama":
        base_url = os.environ.get("OLLAMA_BASE_URL") or OLLAMA_BASE_URL
        if overrides.get("base_url"):
            base_url = overrides["base_url"]
    elif provider == "gemini":
        base_url = GEMINI_BASE_URL
        if overrides.get("base_url"):
            base_url = overrides["base_url"]
    else:
        base_url = os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1"
        if overrides.get("base_url"):
            base_url = overrides["base_url"]

    return LLMConfig(
        provider=provider,
        model=model,
        api_key=api_key,
        base_url=base_url,
    )


def list_models(
    cfg: LLMConfig,
    *,
    fallback: Optional[list[str]] = None,
) -> list[str]:
    """Best-effort list of model IDs the provider exposes.

    Returns `fallback` (or the package default for the provider) when
    the endpoint isn't reachable, so the dashboard's model dropdown
    is never empty.
    """
    url = f"{cfg.base_url.rstrip('/')}/models"
    req = urllib.request.Request(url, method="GET", headers={
        "Authorization": f"Bearer {cfg.api_key or 'ollama'}",
    })
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            body = resp.read().decode("utf-8")
        payload = json.loads(body)
        ids = [m.get("id") for m in payload.get("data", []) if m.get("id")]
        if ids:
            return ids
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError, OSError):
        pass
    if fallback is not None:
        return fallback
    if cfg.provider == "ollama":
        return list(DEFAULT_OLLAMA_MODELS)
    return list(DEFAULT_GEMINI_MODELS)


# ---------------------------------------------------------------------------
# HTTP client (OpenAI-compatible chat completions)
# ---------------------------------------------------------------------------

class LLMError(Exception):
    """Generic LLM client error."""


class RateLimitError(LLMError):
    """Raised on HTTP 429 from the provider. Carries retry_after hint when available."""

    def __init__(self, message: str, retry_after: Optional[float] = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


def chat_completion(
    cfg: LLMConfig,
    user_prompt: str,
    *,
    system_prompt: str = SYSTEM_PROMPT,
) -> dict:
    """Call an OpenAI-compatible /chat/completions endpoint and return the parsed JSON.

    Raises LLMError on transport, auth, or schema errors. Returns the raw
    response dict; caller is responsible for extracting content.
    """
    # Cloud providers (gemini) require an API key. Ollama doesn't, but
    # the Bearer header is still sent (ollama ignores it).
    if not cfg.api_key and cfg.provider != "ollama":
        raise LLMError(
            f"No API key configured for {cfg.provider}. "
            f"Set one in the LLM panel, or set GEMINI_API_KEY env var."
        )

    url = f"{cfg.base_url.rstrip('/')}/chat/completions"
    # Ollama thinking-tier models (gemma4, qwen3.5, etc.) burn the entire
    # max_tokens budget on hidden reasoning and return empty content when
    # it's too small. 400 is fine for gemini; bump to 2000 for ollama so
    # the model has room to think *and* produce a JSON answer.
    max_tokens = 2000 if cfg.provider == "ollama" else 400
    payload = {
        "model": cfg.model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.1,
        "max_tokens": max_tokens,
        # Gemini's OpenAI-compat endpoint supports response_format for
        # constrained JSON output. Some models (notably the "thinking"
        # tier like 2.5-flash) otherwise emit prose + JSON-with-bugs
        # which is hard to parse robustly. Falls back gracefully if
        # the provider does not honour it.
        "response_format": {"type": "json_object"},
    }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {cfg.api_key}",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=cfg.timeout) as resp:
            body = resp.read().decode("utf-8")
            status = resp.status
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        # Detect 429 (rate limit) explicitly so callers can back off
        # cleanly instead of treating it as a generic HTTP error.
        if e.code == 429:
            retry_after = _parse_retry_after(err_body)
            raise RateLimitError(
                f"Rate limited (HTTP 429). Free tier is ~5 requests/minute; "
                f"wait {retry_after:.0f}s and retry.",
                retry_after=retry_after,
            ) from e
        raise LLMError(f"HTTP {e.code}: {err_body[:300]}") from e
    except urllib.error.URLError as e:
        raise LLMError(f"Network error: {e.reason}") from e
    except TimeoutError as e:
        raise LLMError(f"Request timed out after {cfg.timeout:.0f}s") from e

    if status >= 400:
        if status == 429:
            retry_after = _parse_retry_after(body)
            raise RateLimitError(
                f"Rate limited (HTTP 429). Free tier is ~5 requests/minute; "
                f"wait {retry_after:.0f}s and retry.",
                retry_after=retry_after,
            )
        raise LLMError(f"HTTP {status}: {body[:300]}")

    try:
        return json.loads(body)
    except json.JSONDecodeError as e:
        raise LLMError(f"Non-JSON response: {body[:300]}") from e


def _parse_retry_after(body: str) -> float:
    """Best-effort extraction of a retry-after seconds value from a 429 body.

    Falls back to 13 seconds (one slot past the 12-second free-tier
    window) when the body doesn't carry a hint.
    """
    import re
    m = re.search(r"retry[_\s]*after[^0-9]*([0-9]+(?:\.[0-9]+)?)", body, flags=re.IGNORECASE)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return 13.0


# ---------------------------------------------------------------------------
# HS classification call
# ---------------------------------------------------------------------------

def _sanitize_llm_json(text: str) -> str:
    """Lightly clean common LLM JSON mistakes.

    Handles: BOM, smart quotes, trailing commas before } or ].
    Does NOT try to fix unquoted keys or single-quoted strings — those
    are too ambiguous. If the model emits those, the parser will fail
    and the user will see the raw response in the UI.
    """
    if not text:
        return text
    # Strip BOM
    if text.startswith("﻿"):
        text = text[1:]
    # Replace smart double quotes with regular
    text = text.replace("“", '"').replace("”", '"')
    # Replace smart single quotes with regular (use as string quotes — fragile)
    text = text.replace("‘", "'").replace("’", "'")
    # Drop trailing commas before } or ]
    import re
    text = re.sub(r",\s*([}\]])", r"\1", text)
    return text


def _strip_markdown_fences(text: str) -> str:
    """Strip ```json ... ``` fences if present. Robust to leading language tag."""
    text = text.strip()
    if not text.startswith("```"):
        return text
    # Drop the opening fence (with optional language tag like ```json)
    body = text[3:].lstrip()
    if body.lower().startswith("json"):
        body = body[4:].lstrip()
    # Drop the closing fence
    if body.endswith("```"):
        body = body[:-3].rstrip()
    return body


def _extract_json_object(text: str) -> dict | None:
    """Try to find and parse a JSON object embedded in the text.

    Returns the parsed dict, or None if no parseable object was found.
    Tries, in order:
      1. The whole text as JSON
      2. The first {...} block where braces balance
    """
    candidates = [text, _sanitize_llm_json(text)]
    # Greedy outer-brace extraction: find the first { and the matching }
    start = text.find("{")
    if start != -1:
        depth = 0
        in_str = False
        escape = False
        for i, ch in enumerate(text[start:], start=start):
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    sub = text[start:i + 1]
                    candidates.append(sub)
                    candidates.append(_sanitize_llm_json(sub))
                    break
    for c in candidates:
        c = c.strip()
        if not c:
            continue
        try:
            obj = json.loads(c)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def classify_hs_code(
    cfg: LLMConfig,
    description: str,
    context: str = "",
) -> tuple[dict, float]:
    """Classify a product description into an HS code via the LLM.

    Returns (parsed_json, latency_seconds). The parsed JSON has the shape
    {"hs_code": str, "confidence": float, "reasoning": str} or
    {"error": str, "raw": str} if the model did not return parseable JSON.
    """
    user_prompt = f"Description: {description}"
    if context:
        user_prompt += f"\nContext: {context}"
    user_prompt += "\n\nReturn the JSON object only."

    t0 = time.perf_counter()
    try:
        raw = chat_completion(cfg, user_prompt)
        latency = time.perf_counter() - t0
    except RateLimitError as e:
        latency = time.perf_counter() - t0
        return (
            {
                "error": str(e),
                "rate_limited": True,
                "retry_after": e.retry_after,
            },
            latency,
        )
    except LLMError as e:
        return ({"error": str(e)}, time.perf_counter() - t0)

    try:
        content = raw["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        return ({"error": f"Unexpected response shape: {raw!r}", "raw": str(raw)[:500]}, latency)

    text = _strip_markdown_fences(content)
    parsed = _extract_json_object(text)

    if parsed is None:
        # Show enough of the raw text in the error for debugging,
        # but cap it so the UI doesn't blow up on huge responses.
        # Include the length so the next debug session can tell at a
        # glance whether the response was truncated, wrapped, etc.
        snippet = text[:600]
        # Also write the full text to stderr so it shows up in the
        # Streamlit Cloud logs even if the user can't see the UI raw
        # expander. Helps diagnose format bugs without re-running.
        import sys
        print(
            f"[parcel_ops_llm] parse_failed: len={len(text)} repr={text!r}",
            file=sys.stderr,
        )
        return (
            {
                "error": f"Model did not return JSON (len={len(text)}): {snippet}",
                "raw": text[:2000],
            },
            latency,
        )

    if "hs_code" not in parsed:
        return (
            {"error": f"Missing hs_code field: {parsed!r}", "raw": text[:2000]},
            latency,
        )

    parsed["hs_code"] = _normalize_hs_code(parsed["hs_code"])
    parsed.setdefault("confidence", 0.5)
    parsed.setdefault("reasoning", "")
    return (parsed, latency)


def _normalize_hs_code(value: object) -> str:
    """Coerce an HS code string to the canonical 6-digit `NNNN.NN` form.

    Smaller local models (and gemini 2.5-flash on a bad day) sometimes
    emit 8- or 10-digit codes. Customs classification only has 6 digits;
    the extra digits are subheadings the LLM is hallucinating. We trim
    to the first 6 digits and reformat. If the value is unparseable we
    return it unchanged so the caller can see the raw model output.
    """
    if not isinstance(value, str):
        return str(value)
    s = value.strip()
    # Strip everything that isn't a digit, then take the first 6.
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) < 6:
        return s  # give up; let the eval show "wrong"
    return f"{digits[:4]}.{digits[4:6]}"


# ---------------------------------------------------------------------------
# Connection probe
# ---------------------------------------------------------------------------

def probe_connection(cfg: LLMConfig) -> tuple[bool, str]:
    """Cheap connectivity check. Returns (ok, message)."""
    if not cfg.api_key and cfg.provider != "ollama":
        return (False, f"No API key configured for {cfg.provider}")
    url = f"{cfg.base_url.rstrip('/')}/models"
    req = urllib.request.Request(url, method="GET", headers={
        "Authorization": f"Bearer {cfg.api_key}",
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return (resp.status == 200, f"HTTP {resp.status}")
    except urllib.error.HTTPError as e:
        return (False, f"HTTP {e.code}: {e.reason}")
    except urllib.error.URLError as e:
        return (False, f"Network: {e.reason}")
    except TimeoutError:
        return (False, "Timed out after 10s")
    except Exception as e:
        return (False, f"Error: {e!s}"[:200])
