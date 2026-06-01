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

# Free-tier friendly defaults. Models are presented in display order.
DEFAULT_GEMINI_MODELS = [
    "gemini-2.0-flash",
    "gemini-2.5-flash",
    "gemini-1.5-flash",
    "gemini-1.5-flash-8b",
    "gemini-1.5-pro",
]

SYSTEM_PROMPT = (
    "You are a customs classification assistant for the EU Combined Nomenclature. "
    "Given a product description and optional context, classify the product into a "
    "6-digit Harmonized System (HS) code. Apply the General Rules of Interpretation (GRI). "
    "Respond ONLY with a single JSON object in this exact shape, no prose, no markdown:\n"
    '{"hs_code": "NNNN.NN", "confidence": 0.0-1.0, "reasoning": "<one short paragraph>"}\n'
    "Be precise: only return an HS code you can defend. If you are guessing, lower the confidence."
)


@dataclass
class LLMConfig:
    provider: str
    model: str
    api_key: str
    base_url: str
    timeout: float = 30.0


def load_llm_config(
    session_overrides: Optional[dict] = None,
    secrets: Optional[dict] = None,
) -> LLMConfig:
    """Resolve LLM config with precedence: env > session > secrets > defaults.

    Precedence matches the sql-editor project so the UX is familiar.
    """
    overrides = session_overrides or {}
    sec = secrets or {}

    # API key precedence
    api_key = (
        os.environ.get("GEMINI_API_KEY")
        or os.environ.get("LLM_API_KEY")
        or overrides.get("api_key", "")
        or (sec.get("GEMINI_API_KEY") if isinstance(sec, dict) else "")
        or ""
    )

    provider = (
        os.environ.get("LLM_PROVIDER")
        or overrides.get("provider", "gemini")
    )

    model = (
        os.environ.get("LLM_MODEL")
        or overrides.get("model", "gemini-2.0-flash")
    )

    if provider == "gemini":
        base_url = GEMINI_BASE_URL
    else:
        base_url = os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1"

    return LLMConfig(
        provider=provider,
        model=model,
        api_key=api_key,
        base_url=base_url,
    )


# ---------------------------------------------------------------------------
# HTTP client (OpenAI-compatible chat completions)
# ---------------------------------------------------------------------------

class LLMError(Exception):
    pass


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
    if not cfg.api_key:
        raise LLMError("No API key configured. Set one in the LLM panel, or set GEMINI_API_KEY env var.")

    url = f"{cfg.base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": cfg.model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 400,
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
        raise LLMError(f"HTTP {e.code}: {err_body[:300]}") from e
    except urllib.error.URLError as e:
        raise LLMError(f"Network error: {e.reason}") from e
    except TimeoutError as e:
        raise LLMError(f"Request timed out after {cfg.timeout:.0f}s") from e

    if status >= 400:
        raise LLMError(f"HTTP {status}: {body[:300]}")

    try:
        return json.loads(body)
    except json.JSONDecodeError as e:
        raise LLMError(f"Non-JSON response: {body[:300]}") from e


# ---------------------------------------------------------------------------
# HS classification call
# ---------------------------------------------------------------------------

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
    candidates = [text]
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
                    candidates.append(text[start:i + 1])
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
    raw = chat_completion(cfg, user_prompt)
    latency = time.perf_counter() - t0

    try:
        content = raw["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        return ({"error": f"Unexpected response shape: {raw!r}", "raw": str(raw)[:500]}, latency)

    text = _strip_markdown_fences(content)
    parsed = _extract_json_object(text)

    if parsed is None:
        # Show enough of the raw text in the error for debugging,
        # but cap it so the UI doesn't blow up on huge responses.
        return (
            {"error": f"Model did not return JSON: {text[:200]}", "raw": text[:1000]},
            latency,
        )

    if "hs_code" not in parsed:
        return (
            {"error": f"Missing hs_code field: {parsed!r}", "raw": text[:1000]},
            latency,
        )

    parsed.setdefault("confidence", 0.5)
    parsed.setdefault("reasoning", "")
    return (parsed, latency)


# ---------------------------------------------------------------------------
# Connection probe
# ---------------------------------------------------------------------------

def probe_connection(cfg: LLMConfig) -> tuple[bool, str]:
    """Cheap connectivity check. Returns (ok, message)."""
    if not cfg.api_key:
        return (False, "No API key configured")
    if cfg.provider == "gemini":
        # Use the OpenAI-compat /models endpoint as a cheap auth probe
        url = f"{cfg.base_url.rstrip('/')}/models"
    else:
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
