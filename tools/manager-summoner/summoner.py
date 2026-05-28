#!/usr/bin/env python3
"""Manager Summoner - lightweight event-driven feedback generator.

Watches a project directory for ``*_done.md`` files and writes
``feedback_NNN.md`` files using a configurable LLM. Reports facts only,
no decisions, no next-step suggestions. See README.md for details.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


POLL_INTERVAL_SEC = 2.0
# ~4 chars per token is a rough heuristic. 8000 chars ~= 2000 tokens.
FULL_PLAN_THRESHOLD_CHARS = 8000

# Allowed tracker status values and their intended transitions:
#   pending → spawned → done_submitted → reviewed → accepted
#                     → timed_out                  → needs_fix → pending (retry)
#                     → failed
#   any state → superseded  (plan change obsoletes the handout)
# The legacy value "done" is treated as an alias for "reviewed" on read.
VALID_STATES = frozenset({
    "pending", "spawned", "done_submitted", "reviewed", "accepted",
    "needs_fix", "failed", "timed_out", "superseded",
    "needs_human",           # manager surfaced a block; waiting for human decision
    "rejected_role_violation",  # worker violated forbidden paths/actions; do not auto-retry
})
# "done" is kept for backward compat but is not in VALID_STATES for new writes.
_LEGACY_DONE_ALIAS = "done"
# summoner.py lives at tools/manager-summoner/; project root is three levels up.
_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent.parent
# Workspace: where plan.md and handouts/ live (separate from the tool code).
DEFAULT_PROJECT_DIR = _REPO_ROOT / "docs" / "manager" / "workspace"
DEFAULT_MANAGER_DIR = _REPO_ROOT / "docs" / "manager"
DEFAULT_NOTES_DIR = DEFAULT_MANAGER_DIR / "notes"
DEFAULT_INDEX_PATH = _REPO_ROOT / "docs" / "repo-index" / "v1.json"

_INDEX_STOPWORDS = frozenset(
    "the and for are was were has have been this that with from will into when"
    " what which their them they also been about after before should could would"
    " more than then some each both true false none null self return class def"
    " import from pass else elif while break continue yield raise except finally"
    .split()
)


ROLE_TEMPLATES: dict[str, str] = {
    "executor": """# Executor

## Role
You handle one handout at a time. You do not read the full plan.
You do not make architectural decisions.

## Context injected by Summoner
The relevant plan section and task brief are already in your handout.
Do not go looking for other files unless the handout explicitly lists them.

## Done means
- handout_NNN_done.md written with a ## Proof section (see below)
- No files modified outside the handout scope
- Every git commit listed with its full hash
- Every verification command listed with its actual output

## Required ## Proof section in every done file
```
## Proof
- Command: <exact command run, e.g. `python -m pytest tests/ -v`>
- Exit code: <0 or non-zero integer>
- Test summary: <e.g. "16 passed in 0.29s" or "n/a — no tests required">
- Commit: <full 40-char hash, or "none">
- Files changed:
  - path/to/file.py
- Not completed: <list items or "none">
```
Do not write what you intended to run. Write what you actually ran.
All six fields are required. The mechanical verifier checks them before the model reads the done report.

## You are not
- A decision maker
- A planner
- A memory indexer
""",
    "strategist": """# Strategist

## Role
You watch plan coherence across the whole project.
You read digests, not individual handouts.
You do not touch code.

## Your input
- plan.md (read-only)
- summoner_run_NNN.md digest files

## You flag
- Drift from original plan direction
- Scope creep across multiple handouts
- Missing workstreams

## You never
- Write code
- Generate handouts
- Make decisions - you flag and propose only
""",
    "summoner": """# Summoner

## Role
You are the Manager Summoner's own identity.
You watch for ``*_done.md`` files. You generate ``feedback_NNN.md`` files.
You report facts. You do not make decisions and you do not modify code.

## Your job
- Read handout, done, and relevant plan slice
- Build a compact prompt
- Call the configured model
- Write feedback_NNN.md

## You never
- Modify plan.md
- Modify handout or done files
- Suggest next steps or recommend actions
""",
}


PROMPT_TEMPLATE = """## Original Plan (excerpt relevant to this handout)
{plan_slice}

## Task Brief
{handout}

## Mechanical Verification
{verification}

## What Was Done
{done}

## Your Job
List what was completed, what is missing, and what was not mentioned.
Use the mechanical verification above as ground truth — if it says a commit does not exist
or a file is missing, that overrides any claim in the done report.
Do not make decisions. Do not suggest next steps. Facts only.
Output markdown with exactly these five sections in this order:

## Completed
- one bullet per completed item, prefixed with the check mark glyph ✓

## Missing or Not Mentioned
- one bullet per missing item, prefixed with the cross glyph ✗
- use ? prefix if it is unclear whether the item was addressed

## Notes
one short paragraph of observations only, no recommendations

## Completion Status
Exactly one value on its own line:
clean | partial | blocked | failed | fabricated | unclear

## Failure Classification
failure_types: <comma-separated list from the vocabulary below, or "none">
severity: <low | medium | high | critical>

Failure type vocabulary:
  missing_commit          — code changed but not committed
  path_discrepancy        — paths in done report differ from handout or do not exist
  incomplete_reporting    — work done but done report omits tasks, commits, or verification
  fabricated_completion   — deliverables claimed but mechanically absent (no file, no commit)
  test_not_run            — tests required but not executed or output not captured
  out_of_scope            — executor touched files outside handout scope
  blocked_legitimately    — task could not proceed due to missing dependency
  proof_unparseable       — proof section missing required fields
  other                   — none of the above applies

Use fabricated_completion only when the mechanical verifier confirms deliverables are absent.
Use incomplete_reporting when deliverables exist but the report is inaccurate or incomplete.
Severity guide: critical=fabricated or data loss risk, high=missing commit or test, medium=reporting gap, low=minor discrepancy.
"""


@dataclass
class ModelResponse:
    body: str
    model_label: str
    actual_prompt_tokens: Optional[int] = None
    actual_response_tokens: Optional[int] = None
    actual_reasoning_tokens: Optional[int] = None
    actual_cache_read_tokens: Optional[int] = None
    fallback_reason: Optional[str] = None
    raw_info: Optional[dict] = None


@dataclass
class RunMetrics:
    handout_id: str
    model_label: str
    prompt_chars: int
    estimated_prompt_tokens: int
    response_chars: int
    estimated_response_tokens: int
    wall_clock_sec: float
    plan_full_chars: int
    plan_slice_chars: int
    used_slicing: bool
    agent_files_generated: list[str] = field(default_factory=list)
    opencode_integration: str = "untested"
    fallback_reason: Optional[str] = None
    actual_prompt_tokens: Optional[int] = None
    actual_response_tokens: Optional[int] = None
    actual_reasoning_tokens: Optional[int] = None
    actual_cache_read_tokens: Optional[int] = None
    raw_info: Optional[dict] = None


def _resolve_notes_dir(notes_dir: Optional[Path], manager_dir: Optional[Path]) -> Path:
    """Return the effective notes directory, applying the standard priority order.

    Priority: explicit notes_dir > manager_dir/notes > DEFAULT_NOTES_DIR.
    """
    if notes_dir is not None:
        return notes_dir
    if manager_dir is not None:
        return manager_dir / "notes"
    return DEFAULT_NOTES_DIR


def estimate_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4)


def slice_plan(plan: str, handout: str) -> tuple[str, bool]:
    """Return (sliced_plan, used_slicing).

    Skip slicing if the plan already fits under the token threshold.
    Otherwise keep H1/H2 sections whose heading shares a keyword with the
    handout headings.
    """
    if len(plan) <= FULL_PLAN_THRESHOLD_CHARS:
        return plan, False
    handout_headings = re.findall(r"^#{1,2}\s+(.+)$", handout, re.MULTILINE)
    keywords: set[str] = set()
    for h in handout_headings:
        for word in re.findall(r"[A-Za-z][A-Za-z0-9_-]{3,}", h):
            keywords.add(word.lower())
    if not keywords:
        return plan, False
    section_pattern = re.compile(r"^(#{1,2})\s+(.+)$", re.MULTILINE)
    matches = list(section_pattern.finditer(plan))
    if not matches:
        return plan, False
    kept: list[str] = []
    for i, m in enumerate(matches):
        heading = m.group(2).lower()
        if not any(kw in heading for kw in keywords):
            continue
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(plan)
        kept.append(plan[start:end])
    if not kept:
        return plan, False
    return "\n".join(kept).rstrip() + "\n", True


def build_prompt(plan_slice: str, handout: str, done: str, verification: str = "") -> str:
    return PROMPT_TEMPLATE.format(
        plan_slice=plan_slice,
        handout=handout,
        verification=verification or "(mechanical verification not run)",
        done=done,
    )


NEXT_HANDOUT_TEMPLATE = """{guide_context}## Original Plan
{plan}

## What Was Just Completed (Handout {handout_id})
{done}

## Feedback on What Was Done
{feedback}

## Current Codebase State (top relevant index entries)
{index_slice}

## Your Job
Write the next handout as a self-contained task brief for an executor agent.
Use this exact markdown format and nothing else before or after it:

# Handout {next_id} - <descriptive title>

## Scope
One paragraph: what this handout covers and why it comes next after handout {handout_id}.
Ground the scope in the current campaign from the Strategic Context above (if present).

## Tasks
Numbered list, 3-5 items. Each item is concrete and independently verifiable.

## Out of scope
What this handout explicitly does NOT cover.

## Done when
Bulleted acceptance criteria. Each criterion is checkable without human judgment.
"""


MOCK_BODY = """## Completed
- [mock] no LLM was called, so completed items cannot be inspected

## Missing or Not Mentioned
- [mock] real feedback requires a configured SUMMONER_MODEL

## Notes
Mock placeholder. Set SUMMONER_MODEL (for example, ollama/mistral) to get real
feedback. The pipeline structure is intact regardless of model availability.
"""


def _int_or_none(v: object) -> Optional[int]:
    try:
        return int(v) if v is not None else None  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def call_mock() -> ModelResponse:
    return ModelResponse(body=MOCK_BODY, model_label="mock")


def call_ollama(model: str, prompt: str) -> ModelResponse:
    payload = {"model": model, "prompt": prompt, "stream": False}
    req = urllib.request.Request(
        "http://localhost:11434/api/generate",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return ModelResponse(
        body=data.get("response", "").strip(),
        model_label=f"ollama/{model}",
        actual_prompt_tokens=data.get("prompt_eval_count"),
        actual_response_tokens=data.get("eval_count"),
    )


def call_anthropic(model: str, prompt: str) -> ModelResponse:
    try:
        import anthropic  # type: ignore
    except ImportError as exc:
        raise RuntimeError("anthropic SDK not installed") from exc
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    aliases = {
        "claude-haiku": "claude-haiku-4-5-20251001",
        "claude-sonnet": "claude-sonnet-4-6",
        "claude-opus": "claude-opus-4-7",
    }
    model_id = aliases.get(model, model)
    client = anthropic.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model=model_id,
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )
    body = "".join(getattr(block, "text", "") for block in msg.content).strip()
    usage = getattr(msg, "usage", None)
    return ModelResponse(
        body=body,
        model_label=f"anthropic/{model_id}",
        actual_prompt_tokens=getattr(usage, "input_tokens", None) if usage else None,
        actual_response_tokens=getattr(usage, "output_tokens", None) if usage else None,
    )


def call_opencode_serve(model: str, prompt: str) -> ModelResponse:
    """Call the OpenCode headless server API.

    Requires ``opencode serve`` to be running (default port 4096).
    Override the port with the OPENCODE_PORT env var.

    model should be the OpenCode provider/model string, e.g.
    ``deepseek/deepseek-v4-flash``. That string comes from the part of
    SUMMONER_MODEL after the leading ``opencode/`` prefix, so the full
    env var looks like ``opencode/deepseek/deepseek-v4-flash``.
    """
    port = int(os.environ.get("OPENCODE_PORT", "4096"))
    base = f"http://localhost:{port}"
    # OPENCODE_TIMEOUT_SEC controls the HTTP wait for the model reply.
    # Default 180s is fine for short tasks; raise it for large-file reads.
    request_timeout = float(os.environ.get("OPENCODE_TIMEOUT_SEC", "180"))

    # Create a fresh session for this feedback run.
    session_req = urllib.request.Request(
        f"{base}/session",
        data=json.dumps({"title": "summoner"}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(session_req, timeout=10) as resp:
        session = json.loads(resp.read().decode("utf-8"))
    session_id = session["id"]

    # Split the model string into providerID / modelID.
    # model comes in as e.g. "opencode/deepseek-v4-flash-free"
    parts = model.split("/", 1)
    provider_id = parts[0] if len(parts) > 1 else "opencode"
    model_id = parts[-1]

    # Send the prompt synchronously and wait for the full reply.
    message_req = urllib.request.Request(
        f"{base}/session/{session_id}/message",
        data=json.dumps({
            "model": {"providerID": provider_id, "modelID": model_id},
            "parts": [{"type": "text", "text": prompt}],
        }).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(message_req, timeout=request_timeout) as resp:
        result = json.loads(resp.read().decode("utf-8"))

    text = "\n".join(
        p["text"]
        for p in result.get("parts", [])
        if p.get("type") == "text" and "text" in p
    )
    info = result.get("info", {}) or {}
    # OpenCode returns tokens as info.tokens.{input, output, reasoning, total,
    # cache: {read, write}}. Keep the raw info dict for the run log.
    tok = info.get("tokens") or {}
    cache = tok.get("cache") or {}
    return ModelResponse(
        body=text.strip(),
        model_label=f"opencode/{model}",
        actual_prompt_tokens=_int_or_none(tok.get("input")),
        actual_response_tokens=_int_or_none(tok.get("output")),
        actual_reasoning_tokens=_int_or_none(tok.get("reasoning")),
        actual_cache_read_tokens=_int_or_none(cache.get("read")),
        raw_info=info if info else None,
    )


def _call_model_spec(spec: str, prompt: str) -> ModelResponse:
    """Dispatch a single model spec string to the appropriate backend."""
    if spec.startswith("ollama/"):
        return call_ollama(spec.split("/", 1)[1], prompt)
    if spec.startswith("opencode/"):
        # e.g. opencode/deepseek-v4-flash-free or opencode/big-pickle
        # split off the "opencode/" prefix; the rest is the OpenCode model string
        return call_opencode_serve(spec.split("/", 1)[1], prompt)
    if spec.startswith("anthropic/"):
        return call_anthropic(spec.split("/", 1)[1], prompt)
    if spec.startswith("claude"):
        return call_anthropic(spec, prompt)
    raise RuntimeError(f"unknown model spec: {spec!r}")


def call_model(prompt: str, model_spec: Optional[str] = None,
               fallback_spec: Optional[str] = None) -> ModelResponse:
    """Call the configured model.

    model_spec overrides SUMMONER_MODEL when provided (used by the executor path
    so it can read EXECUTOR_MODEL independently of SUMMONER_MODEL).

    fallback_spec overrides SUMMONER_MODEL_FALLBACK when provided (used by the
    executor path so it can read EXECUTOR_MODEL_FALLBACK independently).

    If the primary model fails and a fallback is set, tries the fallback before
    dropping to mock.
    """
    spec = (model_spec or os.environ.get("SUMMONER_MODEL", "")).strip()
    fb = fallback_spec if fallback_spec is not None else os.environ.get("SUMMONER_MODEL_FALLBACK", "").strip()
    if not spec:
        resp = call_mock()
        resp.fallback_reason = "SUMMONER_MODEL not set"
        return resp
    try:
        return _call_model_spec(spec, prompt)
    except Exception as exc:
        if fb:
            try:
                resp = _call_model_spec(fb, prompt)
                resp.fallback_reason = f"{spec} failed: {exc}; using fallback {fb}"
                return resp
            except Exception as exc2:
                resp = call_mock()
                resp.fallback_reason = (
                    f"{spec} failed: {exc}; "
                    f"fallback {fb} failed: {exc2}"
                )
                return resp
        resp = call_mock()
        resp.fallback_reason = f"{spec} failed: {exc}"
        return resp


# Track whether we've already printed the OpenCode interference warning so it
# only appears once per process, not once per manager call.
_opencode_manager_warned = False


def call_manager_model(prompt: str) -> ModelResponse:
    """Call the manager's text-generation model (reads SUMMONER_MODEL).

    The manager must never write files — it only generates text (feedback,
    next-handout briefs). If SUMMONER_MODEL points to an OpenCode backend this
    function raises RuntimeError by default, because OpenCode build-mode can
    write files as a side effect of text-only calls.

    To override (not recommended): set ALLOW_MANAGER_OPENCODE=1.
    Preferred: set SUMMONER_MODEL=anthropic/<model> or ollama/<model>.
    """
    global _opencode_manager_warned
    spec = os.environ.get("SUMMONER_MODEL", "").strip()
    fallback_spec = os.environ.get("SUMMONER_MODEL_FALLBACK", "").strip()
    # Check primary, fallback, and executor model vars for OpenCode guard
    uses_opencode = (
        spec.startswith("opencode/")
        or fallback_spec.startswith("opencode/")
        or os.environ.get("EXECUTOR_MODEL", "").startswith("opencode/")
        or os.environ.get("EXECUTOR_MODEL_FALLBACK", "").startswith("opencode/")
    )
    if uses_opencode:
        allow = os.environ.get("ALLOW_MANAGER_OPENCODE", "").strip().lower()
        if allow not in ("1", "true", "yes"):
            raise RuntimeError(
                "SUMMONER_MODEL (or SUMMONER_MODEL_FALLBACK) is set to an OpenCode backend. "
                "OpenCode build-mode may write files during manager text calls. "
                "Set SUMMONER_MODEL=anthropic/<model> or SUMMONER_MODEL=ollama/<model>. "
                "To bypass this check (not recommended): ALLOW_MANAGER_OPENCODE=1"
            )
        if not _opencode_manager_warned:
            active = spec or fallback_spec
            print(
                f"WARNING: ALLOW_MANAGER_OPENCODE=1 — proceeding with OpenCode "
                f"manager calls ({active}); build-mode file writes are possible.",
                file=sys.stderr,
            )
            _opencode_manager_warned = True
    return call_model(prompt, model_spec=spec or None)


def call_executor_model(prompt: str) -> ModelResponse:
    """Call the executor model (reads EXECUTOR_MODEL → SUMMONER_MODEL → mock).

    The executor has full repository write access via OpenCode build-mode.
    Set EXECUTOR_MODEL to an ``opencode/<provider>/<model>`` spec.

    Fallback chain: EXECUTOR_MODEL_FALLBACK → SUMMONER_MODEL_FALLBACK.
    """
    primary = os.environ.get("EXECUTOR_MODEL", "").strip()
    spec = primary or os.environ.get("SUMMONER_MODEL", "").strip()
    fallback = (
        os.environ.get("EXECUTOR_MODEL_FALLBACK", "").strip()
        or os.environ.get("SUMMONER_MODEL_FALLBACK", "").strip()
    )
    return call_model(prompt, model_spec=spec or None, fallback_spec=fallback or None)


def call_mapper_model(prompt: str) -> ModelResponse:
    """Call the mapper model (reads MAPPER_MODEL → SUMMONER_MODEL → mock).

    Mapper agents are read-only: they inspect source and produce project guide artifacts.
    They do not write code or modify the repository.
    Set MAPPER_MODEL to an ``opencode/<provider>/<model>`` spec.
    """
    primary = os.environ.get("MAPPER_MODEL", "").strip()
    spec = primary or os.environ.get("SUMMONER_MODEL", "").strip()
    fallback = (
        os.environ.get("MAPPER_MODEL_FALLBACK", "").strip()
        or os.environ.get("SUMMONER_MODEL_FALLBACK", "").strip()
    )
    return call_model(prompt, model_spec=spec or None, fallback_spec=fallback or None)


def call_researcher_model(prompt: str) -> ModelResponse:
    """Call the researcher model (reads RESEARCHER_MODEL → SUMMONER_MODEL → mock).

    Researcher agents are read-only: they answer bounded questions from listed files
    and produce findings artifacts. They do not write code or modify the repository.
    Set RESEARCHER_MODEL to an ``opencode/<provider>/<model>`` spec.
    """
    primary = os.environ.get("RESEARCHER_MODEL", "").strip()
    spec = primary or os.environ.get("SUMMONER_MODEL", "").strip()
    fallback = (
        os.environ.get("RESEARCHER_MODEL_FALLBACK", "").strip()
        or os.environ.get("SUMMONER_MODEL_FALLBACK", "").strip()
    )
    return call_model(prompt, model_spec=spec or None, fallback_spec=fallback or None)


def call_reviewer_model(prompt: str) -> ModelResponse:
    """Call the reviewer model (reads REVIEWER_MODEL → SUMMONER_MODEL → mock).

    Reviewer agents are read-only: they check a coder's proof artifact against the
    handout acceptance criteria and write a verdict. They do not modify code or tests.
    Set REVIEWER_MODEL to an ``opencode/<provider>/<model>`` spec.
    """
    primary = os.environ.get("REVIEWER_MODEL", "").strip()
    spec = primary or os.environ.get("SUMMONER_MODEL", "").strip()
    fallback = (
        os.environ.get("REVIEWER_MODEL_FALLBACK", "").strip()
        or os.environ.get("SUMMONER_MODEL_FALLBACK", "").strip()
    )
    return call_model(prompt, model_spec=spec or None, fallback_spec=fallback or None)


EXECUTOR_PROMPT_TEMPLATE = """You are a task executor agent. Your sole job is to complete exactly what the handout below describes and then write a done file.

## Hard rules
- Write `{done_path}` when ALL tasks are complete (or when you have gone as far as you can).
- The done file must list: every file changed (with path), every git commit made (with hash), and every task that was NOT completed with a reason.
- Do not touch plan.md, other handout files, feedback files, run logs, or tracker.json.
- Stay inside the "Out of scope" boundary in the handout.
- If a task is blocked (missing dependency, unclear requirement) — document it in the done file and stop. Do not invent scope.

## Required proof section in the done file
The done file MUST end with a ## Proof section. A mechanical verifier checks these fields
before the feedback model reads your report. Missing or unparseable fields → incomplete_reporting.

```
## Proof
- Command: <exact command run, e.g. `python -m pytest tools/manager-summoner/tests/ -v`>
- Exit code: <0 or non-zero integer>
- Test summary: <e.g. "16 passed in 0.29s" or "n/a — no tests required">
- Commit: <full 40-char hash, or "none">
- Files changed:
  - path/to/changed_file.py
  - path/to/another_file.py
- Not completed: <list items or "none">
```

All six fields are required. If no code was changed, write `Commit: none` and omit files.
Do not fabricate output. If you did not run the command, say so explicitly.

## Project root
{root}

## Your handout (ID {handout_id})
{handout}
"""

MAPPER_PROMPT_TEMPLATE = """You are a Mapper agent. Your job is to read raw source and produce a structured project guide artifact. You do not modify source. You do not decide next implementation tasks.

## Hard rules
- Read ONLY the files listed under "Input files" below. Do not open any other file.
- Write ONLY to the path listed under "Output path" below. Do not create any other file.
- Do not modify any source file, test file, or existing documentation.
- Do not propose implementation tasks or fixes. Findings only.
- If the listed files are insufficient, write a blocked artifact at the output path explaining exactly which additional files are needed, then stop.
- Write the output artifact and stop.

## Task
{task_goal}

## Input files
{input_files}

## Output path
{output_path}

## Additional task details
{task_details}
"""

RESEARCHER_PROMPT_TEMPLATE = """You are a Researcher agent. Your job is to answer specific questions from a bounded list of files and write a findings artifact. You do not modify anything.

## Hard rules
- Read ONLY the files listed under "Input files" below. Do not open any other file.
- Write ONLY to the path listed under "Output path" below. Do not create any other file.
- Do not modify any source file, test file, or existing documentation.
- Do not propose or write fixes. Findings only.
- For each finding cite the exact file and line number.
- State confidence: confirmed (directly visible in source) or inferred (reasoned from context).
- If a question cannot be answered from the listed files, say so explicitly and state which file would be needed.
- Write the findings artifact and stop.

## Task
{task_goal}

## Questions to answer
{questions}

## Input files
{input_files}

## Output path
{output_path}
"""

REVIEWER_PROMPT_TEMPLATE = """You are a Reviewer agent. Your job is to check whether a coder's output satisfies the handout acceptance criteria and write a verdict artifact. You do not modify code, tests, or the task scope.

## Hard rules
- Read ONLY the files listed under "Input files" below. Do not open any other file.
- Write ONLY to the path listed under "Output path" below. Do not create any other file.
- Do not modify any source file, test file, or existing documentation.
- Do not redefine or widen the task scope.
- Do not approve without reading the verification output (proof artifact or test evidence).
- Do not mark a task complete if the proof artifact is missing or has unparseable fields.
- Write the verdict artifact and stop.

## Verdict format
The verdict artifact must state one of: ACCEPTED / REJECTED / NEEDS_REVISION.
List each acceptance criterion from the handout with status (PASS / FAIL / PARTIAL) and
cite specific evidence from the proof artifact. If REJECTED or NEEDS_REVISION, list exactly
what is missing or wrong.

## Task
{task_goal}

## Input files
{input_files}

## Output path
{output_path}
"""


@dataclass
class SpawnResult:
    handout_id: str
    executor_model: str
    spawned: bool
    fallback_reason: Optional[str] = None
    wall_clock_sec: float = 0.0
    response_preview: str = ""


def spawn_executor(
    root: Path,
    handout_id: str,
    notes_dir: Optional[Path] = None,
    manager_dir: Optional[Path] = None,
) -> SpawnResult:
    """Invoke an executor agent to work on handout_NNN.md.

    Uses EXECUTOR_MODEL env var; falls back to SUMMONER_MODEL; then to mock.
    Writes a spawn log to notes_dir/executor_spawn_NNN.md.
    """
    effective_notes_dir = _resolve_notes_dir(notes_dir, manager_dir)
    handout_path = root / "handouts" / f"handout_{handout_id}.md"
    done_path = root / "handouts" / f"handout_{handout_id}_done.md"

    if not handout_path.exists():
        return SpawnResult(
            handout_id=handout_id,
            executor_model="none",
            spawned=False,
            fallback_reason=f"handout_{handout_id}.md not found",
        )

    # Run preflight checks before handing off to executor.
    preflight_warnings = preflight_handout(handout_path, _REPO_ROOT)
    if preflight_warnings:
        for w in preflight_warnings:
            print(f"  ! preflight: {w}")

    handout = handout_path.read_text(encoding="utf-8")
    prompt = EXECUTOR_PROMPT_TEMPLATE.format(
        done_path=done_path,
        root=root,
        handout_id=handout_id,
        handout=handout,
    )

    # Mark spawned and record timestamp + attempt count before the executor starts,
    # so a crash or timeout is visible in the tracker.
    if manager_dir is not None:
        with _tracker_lock(manager_dir):
            data = load_tracker(manager_dir)
            current = next(
                (e for e in data.get("handouts", []) if e.get("id") == handout_id), {}
            )
            attempt = int(current.get("attempt_count", 0)) + 1
            # Store timeout_sec so --check can compare age against the value that
            # was in effect at spawn time, not the current env var.
            spawn_timeout = float(os.environ.get("EXECUTOR_TIMEOUT_SEC", "300"))
            _update_tracker_fields(
                manager_dir, handout_id,
                status="spawned",
                spawned_at=datetime.now(timezone.utc).isoformat(),
                attempt_count=attempt,
                timeout_sec=spawn_timeout,
            )

    t0 = time.perf_counter()
    response = call_executor_model(prompt)
    wall = time.perf_counter() - t0

    # If a done file appeared, advance to done_submitted and record finish time.
    if manager_dir is not None and done_path.exists():
        _update_tracker_fields(
            manager_dir, handout_id,
            status="done_submitted",
            finished_at=datetime.now(timezone.utc).isoformat(),
        )

    result = SpawnResult(
        handout_id=handout_id,
        executor_model=response.model_label,
        spawned=not bool(response.fallback_reason),
        fallback_reason=response.fallback_reason,
        wall_clock_sec=wall,
        response_preview=response.body[:200].replace("\n", " "),
    )

    # Write a spawn log so the manager has a record of what was attempted.
    _write_spawn_log(effective_notes_dir, handout_id, result, response)
    return result


def _write_spawn_log(notes_dir: Path, handout_id: str, result: SpawnResult, response: ModelResponse) -> None:
    notes_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).date().isoformat()
    lines = [
        f"# Executor Spawn {handout_id}",
        f"Generated: {today}",
        "",
        "## Spawn Details",
        f"- executor_model: {result.executor_model}",
        f"- spawned: {result.spawned}",
        f"- wall_clock_sec: {result.wall_clock_sec:.3f}",
    ]
    if result.fallback_reason:
        lines.append(f"- fallback_reason: {result.fallback_reason}")
    if response.actual_prompt_tokens is not None:
        lines.append(f"- actual_prompt_tokens: {response.actual_prompt_tokens}")
    if response.actual_response_tokens is not None:
        lines.append(f"- actual_response_tokens: {response.actual_response_tokens}")
    if response.actual_reasoning_tokens is not None:
        lines.append(f"- actual_reasoning_tokens: {response.actual_reasoning_tokens}")
    if response.raw_info:
        lines.append(f"- raw_info: {json.dumps(response.raw_info)}")
    lines += ["", "## Response Preview", result.response_preview or "(empty)"]
    (notes_dir / f"executor_spawn_{handout_id}.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def write_feedback(notes_dir: Path, handout_id: str, body: str, model_label: str) -> Path:
    notes_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).date().isoformat()
    header = (
        f"# Feedback {handout_id}\n"
        f"Generated: {today}\n"
        f"Model: {model_label}\n\n"
    )
    out = notes_dir / f"feedback_{handout_id}.md"
    out.write_text(header + body.strip() + "\n", encoding="utf-8")
    return out


def _write_event_log(
    notes_dir: Path,
    handout_id: str,
    worker: str,
    classification: Optional[str],
    artifact: Optional[str],
    note: str,
) -> None:
    """Append one JSON line to docs/manager/notes/event_log.jsonl.

    Creates the file if it does not exist (append-only). Each line is a
    self-contained JSON object:
      {"ts": "<iso8601>", "handout": "<id>", "worker": "<type>",
       "classification": "<class>", "artifact": "<path or null>",
       "note": "<one line>"}
    """
    notes_dir.mkdir(parents=True, exist_ok=True)
    log_path = notes_dir / "event_log.jsonl"
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "handout": handout_id,
        "worker": worker,
        "classification": classification or "unknown",
        "artifact": artifact,
        "note": note,
    }
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


def _build_blocked_feedback_body(handout_id: str, status: str) -> str:
    """Return a feedback body explaining why the model was not called.

    Used by process() when mechanical verification returns
    ``proof_absent`` or ``proof_unparseable``.
    """
    reasons = {
        "proof_absent": (
            "The done file contains no `## Proof` section."
        ),
        "proof_unparseable": (
            "The done file has a `## Proof` section but one or more "
            "required fields (Command, Exit code, Test summary, Commit, "
            "Files changed, Not completed) are missing or empty."
        ),
    }
    reason = reasons.get(status, "Unknown mechanical verification status.")
    return (
        f"## Blocked\n\n"
        f"Mechanical verifier returned `{status}`. The feedback model was not called.\n\n"
        f"## Reason\n\n"
        f"{reason}\n\n"
        f"## Action required\n\n"
        f"The executor must resubmit with a well-formed `## Proof` section before "
        f"this handout can advance.\n\n"
        f"## Completion Status\n"
        f"blocked\n\n"
        f"## Failure Classification\n"
        f"failure_types: {status}\n"
        f"severity: high\n"
    )


def write_run_log(notes_dir: Path, metrics: RunMetrics) -> Path:
    notes_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).date().isoformat()
    lines = [
        f"# Summoner Run {metrics.handout_id}",
        f"Generated: {today}",
        "",
        "## Measurements",
        f"- model: {metrics.model_label}",
        f"- estimated prompt tokens: {metrics.estimated_prompt_tokens}",
        f"- estimated response tokens: {metrics.estimated_response_tokens}",
        f"- actual prompt tokens: {metrics.actual_prompt_tokens if metrics.actual_prompt_tokens is not None else 'unavailable'}",
        f"- actual response tokens: {metrics.actual_response_tokens if metrics.actual_response_tokens is not None else 'unavailable'}",
        f"- actual reasoning tokens: {metrics.actual_reasoning_tokens if metrics.actual_reasoning_tokens is not None else 'unavailable'}",
        f"- actual cache read tokens: {metrics.actual_cache_read_tokens if metrics.actual_cache_read_tokens is not None else 'unavailable'}",
        f"- total context tokens: {((metrics.actual_prompt_tokens or 0) + (metrics.actual_cache_read_tokens or 0)) or 'unavailable'}",
        f"- model cost: {metrics.raw_info.get('cost', 'unavailable') if metrics.raw_info else 'unavailable'}",
        f"- prompt chars: {metrics.prompt_chars}",
        f"- response chars: {metrics.response_chars}",
        f"- wall-clock seconds: {metrics.wall_clock_sec:.3f}",
        f"- plan full chars: {metrics.plan_full_chars}",
        f"- plan slice chars: {metrics.plan_slice_chars}",
        f"- used plan slicing: {metrics.used_slicing}",
        f"- agent files generated: {', '.join(metrics.agent_files_generated) if metrics.agent_files_generated else 'none (already initialized)'}",
        f"- OpenCode integration: {metrics.opencode_integration}",
    ]
    if metrics.fallback_reason:
        lines.append(f"- fallback reason: {metrics.fallback_reason}")
    if metrics.raw_info:
        lines.append(f"- raw model info: {json.dumps(metrics.raw_info)}")
    out = notes_dir / f"summoner_run_{metrics.handout_id}.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def process(
    root: Path,
    handout_id: str,
    manager_dir: Optional[Path] = None,
    index_path: Optional[Path] = None,
    notes_dir: Optional[Path] = None,
    auto_next: bool = True,
) -> RunMetrics:
    effective_notes_dir = _resolve_notes_dir(notes_dir, manager_dir)
    plan_path = root / "plan.md"
    handouts_dir = root / "handouts"
    handout_path = handouts_dir / f"handout_{handout_id}.md"
    done_path = handouts_dir / f"handout_{handout_id}_done.md"
    if not plan_path.exists():
        raise FileNotFoundError(f"missing {plan_path}")
    if not handout_path.exists():
        raise FileNotFoundError(f"missing {handout_path}")
    if not done_path.exists():
        raise FileNotFoundError(f"missing {done_path}")
    plan = plan_path.read_text(encoding="utf-8")
    handout = handout_path.read_text(encoding="utf-8")
    done = done_path.read_text(encoding="utf-8")
    plan_slice, used_slicing = slice_plan(plan, handout)
    # Run mechanical verification before the model reads the done report.
    verification = run_mechanical_verification(
        done_path, handout_path, _REPO_ROOT, effective_notes_dir
    )
    mech_status = verification["mechanical_status"]
    if mech_status in ("proof_absent", "proof_unparseable"):
        feedback_body = _build_blocked_feedback_body(handout_id, mech_status)
        model_label = f"blocked \u2014 {mech_status}"
        write_feedback(effective_notes_dir, handout_id, feedback_body, model_label)
        if manager_dir is not None:
            _update_tracker_fields(
                manager_dir, handout_id,
                status="needs_fix",
                failure_types=[mech_status],
            )
        metrics = RunMetrics(
            handout_id=handout_id,
            model_label=model_label,
            prompt_chars=0,
            estimated_prompt_tokens=0,
            response_chars=len(feedback_body),
            estimated_response_tokens=estimate_tokens(feedback_body),
            wall_clock_sec=0.0,
            plan_full_chars=len(plan),
            plan_slice_chars=len(plan_slice),
            used_slicing=used_slicing,
            fallback_reason=f"blocked: mechanical status {mech_status}",
        )
        write_run_log(effective_notes_dir, metrics)
        return metrics
    verification_text = _format_verification_for_prompt(verification)
    prompt = build_prompt(plan_slice, handout, done, verification=verification_text)
    t0 = time.perf_counter()
    response = call_manager_model(prompt)
    wall = time.perf_counter() - t0
    write_feedback(effective_notes_dir, handout_id, response.body, response.model_label)
    metrics = RunMetrics(
        handout_id=handout_id,
        model_label=response.model_label,
        prompt_chars=len(prompt),
        estimated_prompt_tokens=estimate_tokens(prompt),
        response_chars=len(response.body),
        estimated_response_tokens=estimate_tokens(response.body),
        wall_clock_sec=wall,
        plan_full_chars=len(plan),
        plan_slice_chars=len(plan_slice),
        used_slicing=used_slicing,
        actual_prompt_tokens=response.actual_prompt_tokens,
        actual_response_tokens=response.actual_response_tokens,
        actual_reasoning_tokens=response.actual_reasoning_tokens,
        actual_cache_read_tokens=response.actual_cache_read_tokens,
        fallback_reason=response.fallback_reason,
        raw_info=response.raw_info,
    )
    write_run_log(effective_notes_dir, metrics)
    if manager_dir is not None:
        upsert_tracker_entry(manager_dir, root, handout_id, metrics, notes_dir=effective_notes_dir)
    if auto_next:
        next_path = generate_next_handout(
            root, handout_id, manager_dir=manager_dir, index_path=index_path, notes_dir=effective_notes_dir
        )
        if next_path:
            next_id = str(int(handout_id) + 1).zfill(3)
            print(f"  generated handout_{next_id}.md")
    return metrics


def _keywords_from_text(text: str, min_len: int = 4) -> set[str]:
    words = re.findall(r"[A-Za-z][A-Za-z0-9_-]{%d,}" % (min_len - 1), text)
    return {w.lower() for w in words} - _INDEX_STOPWORDS


def _extract_failure_type(feedback_path: Path) -> Optional[str]:
    """Return the primary failure type from a feedback file (backward compat wrapper).

    Reads ## Completion Status (new format) or ## Failure Type (old format).
    Returns None if the file does not exist or neither section is present.
    """
    classification = _extract_failure_classification(feedback_path)
    if classification is None:
        return None
    return classification.get("completion_status") or classification.get("failure_type")


def _extract_failure_classification(feedback_path: Path) -> Optional[dict]:
    """Parse structured failure classification from a feedback file.

    Returns a dict with keys:
      completion_status: str (clean/partial/blocked/failed/fabricated/unclear)
      failure_types: list[str]
      severity: str (low/medium/high/critical)

    Falls back to parsing old single-label ## Failure Type for backward compat.
    Returns None if file does not exist or no recognizable section found.
    """
    try:
        text = feedback_path.read_text(encoding="utf-8")
    except Exception:
        return None

    # New structured format: ## Completion Status + ## Failure Classification
    status_m = re.search(r"^## Completion Status\s*\n+([\w]+)", text, re.MULTILINE)
    ftypes_m = re.search(r"^failure_types:\s*(.+)$", text, re.MULTILINE)
    severity_m = re.search(r"^severity:\s*(\w+)$", text, re.MULTILINE)
    if status_m:
        ftypes_raw = ftypes_m.group(1).strip() if ftypes_m else "none"
        failure_types = (
            []
            if ftypes_raw.lower() in ("none", "")
            else [t.strip() for t in ftypes_raw.split(",") if t.strip()]
        )
        return {
            "completion_status": status_m.group(1).strip(),
            "failure_types": failure_types,
            "severity": severity_m.group(1).strip() if severity_m else "unknown",
        }

    # Old single-label format: ## Failure Type
    old_m = re.search(r"^## Failure Type\s*\n+(\w+)", text, re.MULTILINE)
    if old_m:
        label = old_m.group(1).strip()
        return {
            "completion_status": label,
            "failure_type": label,  # legacy key
            "failure_types": [],
            "severity": "unknown",
        }

    return None


# ---------------------------------------------------------------------------
# Proof section parsing
# ---------------------------------------------------------------------------

_PROOF_FIELDS = ("Command", "Exit code", "Test summary", "Commit", "Files changed", "Not completed")


def _parse_proof_section(done_text: str) -> dict:
    """Extract fields from the ## Proof section of a done file.

    Returns a dict with keys matching _PROOF_FIELDS (lowercased, spaces→underscores),
    plus 'files_changed_list' (parsed list) and 'has_proof_section' (bool).
    Values are stripped strings; missing fields have value None.
    """
    result: dict = {
        "has_proof_section": False,
        "command": None,
        "exit_code": None,
        "test_summary": None,
        "commit": None,
        "files_changed": None,
        "files_changed_list": [],
        "not_completed": None,
    }

    # Find the ## Proof heading
    heading_m = re.search(r"^## Proof\s*$", done_text, re.MULTILINE)
    if not heading_m:
        return result
    result["has_proof_section"] = True

    # Extract block until next ## heading or end of string
    block_start = heading_m.end()
    next_heading = re.search(r"^## ", done_text[block_start:], re.MULTILINE)
    block = done_text[block_start: block_start + next_heading.start()] if next_heading else done_text[block_start:]

    # Parse line-by-line using splitlines — avoids MULTILINE anchor edge cases
    single_fields = {
        "Command": "command",
        "Exit code": "exit_code",
        "Test summary": "test_summary",
        "Commit": "commit",
        "Not completed": "not_completed",
    }
    files_list: list[str] = []
    collecting_files = False

    for line in block.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        # Check if this is a known single-line field (captures everything after "FieldName: ")
        matched_field = False
        for field, key in single_fields.items():
            if re.match(rf"^[-*]\s+{re.escape(field)}:", stripped, re.IGNORECASE):
                value_m = re.match(rf"^[-*]\s+{re.escape(field)}:\s*(.*)", stripped, re.IGNORECASE)
                result[key] = value_m.group(1).strip() if value_m and value_m.group(1).strip() else None
                collecting_files = False
                matched_field = True
                break
        if matched_field:
            continue

        # Files changed field
        if re.match(r"^[-*]\s+Files changed:", stripped, re.IGNORECASE):
            collecting_files = True
            value_m = re.match(r"^[-*]\s+Files changed:\s*(.*)", stripped, re.IGNORECASE)
            inline = value_m.group(1).strip() if value_m else ""
            if inline and inline.lower() not in ("none", ""):
                files_list = [f.strip() for f in inline.split(",") if f.strip()]
                collecting_files = False
            continue

        # Sub-bullets under Files changed (indented or plain bullet on next line)
        if collecting_files:
            if re.match(r"^[-*]\s+\S", stripped) and not re.search(r"^[-*]\s+\w[\w\s]+:", stripped):
                # It's a plain file path bullet, not a new "Field:" line
                content = re.sub(r"^[-*]\s+", "", stripped).strip()
                files_list.append(content)
            elif line and line[0] in (" ", "\t"):
                # Indented sub-bullet
                content = re.sub(r"^[-*]\s+", stripped.lstrip(), "").strip()
                # Strip leading bullet chars
                content = re.sub(r"^[-*]\s*", "", stripped).strip()
                files_list.append(content)
            else:
                collecting_files = False

    if files_list:
        result["files_changed"] = ", ".join(files_list)
        result["files_changed_list"] = files_list
    elif result.get("commit") is not None:
        result["files_changed"] = "none"

    return result


# ---------------------------------------------------------------------------
# Mechanical verifier
# ---------------------------------------------------------------------------

_MECHANICAL_STATUS_PRIORITY = [
    "proof_absent",
    "proof_unparseable",
    "commit_claim_false",       # commit hash doesn't exist in repo
    "commit_unreachable",      # v2: commit exists but not reachable from HEAD
    "files_not_in_diff",       # v2: commit real but claimed files absent from its diff
    "scope_violation",         # v2: commit touches files outside allowed_paths
    "unstaged_scope_violation", # v2: dirty files outside allowed_paths
    "listed_files_missing",           # claimed files don't exist on disk
    "exit_code_nonzero",
    "no_commit_claimed",
    "whitespace_only_md_diff",   # commit touches .md files but adds/removes no real content
    "mechanically_clean",
]


def _check_md_diff_non_trivial(commit_hash: str, repo_root: Path) -> dict[str, bool]:
    """
    For each .md file in the commit's diff, return True if the diff contains
    at least one non-whitespace added or removed line, False if the diff is
    whitespace-only or empty.

    Returns: dict mapping relative file path -> bool (True = non-trivial)
    """
    result: dict[str, bool] = {}
    try:
        cp = subprocess.run(
            ["git", "diff-tree", "--no-commit-id", "--root", "-r", "-p", commit_hash],
            capture_output=True,
            text=True,
            cwd=repo_root,
            timeout=10,
        )
        if cp.returncode != 0:
            return result
        patch = cp.stdout
    except Exception:
        return result

    current_file: str | None = None
    for line in patch.splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            if len(parts) >= 4:
                b_path = parts[3]
                fpath = b_path[2:]
                if fpath.endswith(".md"):
                    current_file = fpath
                    result[fpath] = False
                else:
                    current_file = None
            else:
                current_file = None
        elif current_file is not None:
            if line.startswith("--- ") or line.startswith("+++ "):
                continue
            if line.startswith("+") or line.startswith("-"):
                content = line[1:]
                if content.strip():
                    result[current_file] = True
    return result


def _parse_handout_proof_policy(handout_text: str) -> dict:
    """Extract proof policy from a handout's '## Proof requirements' section.

    Returns a dict with keys:
      requires_commit      bool | None
      requires_tests       bool | None
      allow_no_file_changes bool | None
      allowed_paths        list[str]  — path prefixes; empty = no restriction
    """
    policy: dict = {
        "requires_commit": None,
        "requires_tests": None,
        "allow_no_file_changes": None,
        "allowed_paths": [],
    }
    in_section = False
    for line in handout_text.splitlines():
        if re.match(r"^##\s+Proof requirements", line, re.IGNORECASE):
            in_section = True
            continue
        if in_section:
            if line.startswith("##"):
                break
            m = re.match(r"^\s*[-*]\s+(.+)", line)
            if not m:
                continue
            entry = m.group(1).strip()
            low = entry.lower()
            if low.startswith("commit required:"):
                val = low.split(":", 1)[1].strip()
                policy["requires_commit"] = val in ("yes", "true", "1")
            elif low.startswith("tests required:"):
                val = low.split(":", 1)[1].strip()
                policy["requires_tests"] = val in ("yes", "true", "1")
            elif low.startswith("allow no file changes:"):
                val = low.split(":", 1)[1].strip()
                policy["allow_no_file_changes"] = val in ("yes", "true", "1")
            elif low.startswith("allowed changed paths:"):
                raw = entry.split(":", 1)[1].strip()
                policy["allowed_paths"] = [p.strip() for p in raw.split(",") if p.strip()]
    return policy


def _check_unstaged_violations(
    repo_root: Path,
    allowed_paths: list[str],
    exempt_paths: list[str] | None = None,
) -> list[str]:
    """Return list of tracked-and-dirty files outside allowed_paths.

    Uses git status --porcelain. Only flags files that were previously tracked
    and have been modified, staged, or deleted (XY codes where at least one
    column is not '?' and not ' '). Untracked-new files (??) are excluded
    because they are indistinguishable from normal pipeline artifact files
    (notes, done files, tracker.json) that accumulate between sessions.
    Newly committed files are already caught by the files_in_diff check.

    exempt_paths: paths (relative to repo root) that are modified by the
    summoner itself during normal pipeline operation (tracker.json, .handoff.md)
    and should never be flagged as executor scope violations.

    Returns [] if no violations or no allowed_paths set.
    """
    if not allowed_paths:
        return []
    exempt_set: set[str] = set(exempt_paths or [])
    try:
        cp = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, cwd=repo_root, timeout=10,
        )
        if cp.returncode != 0:
            return []
        violations: list[str] = []
        for line in cp.stdout.splitlines():
            if len(line) < 4:
                continue
            xy = line[:2]
            # Skip fully untracked files — captured by files_in_diff when committed
            if xy == "??":
                continue
            raw = line[3:]
            if " -> " in raw:
                path = raw.split(" -> ", 1)[1]
            else:
                path = raw
            # Normalise path separators to forward-slash for consistent matching
            path = path.replace("\\", "/")
            if path in exempt_set:
                continue
            if not any(path.startswith(p.rstrip("/")) for p in allowed_paths):
                violations.append(path)
        return sorted(violations)
    except Exception:
        return []


def run_mechanical_verification(
    done_path: Path,
    handout_path: Path,
    repo_root: Path,
    notes_dir: Path,
) -> dict:
    """Run mechanical checks on a done file and write verification_NNN.json.

    Checks:
    - proof section present and all six fields parseable
    - exit code is "0"
    - commit hash exists in git (if provided)
    - files listed in Files changed exist on disk

    Returns the verification dict. Writes verification_NNN.json to notes_dir.
    The handout_id is inferred from done_path stem (handout_NNN_done.md).
    """
    stem = done_path.stem  # e.g. "handout_009_done"
    handout_id_m = re.search(r"handout_(\d+)_done", stem)
    handout_id = handout_id_m.group(1).zfill(3) if handout_id_m else "000"

    today = datetime.now(timezone.utc).date().isoformat()
    verification: dict = {
        "handout_id": handout_id,
        "generated": today,
        "done_file_exists": done_path.exists(),
        "has_proof_section": False,
        "proof_fields": {},
        "proof_policy": {},
        "checks": {
            "exit_code_zero": None,
            "commit_verified": None,
            "commit_reachable": None,    # v2: ancestor of HEAD?
            "diff_files": None,          # v2: files actually changed in commit
            "files_in_diff": {},         # v2: claimed files vs commit diff
            "scope_violations": None,    # v2: files outside allowed_paths
            "files_exist": {},
            "missing_required_fields": [],
            "proof_parseable": False,
        },
        "executor_reported": {},
        "mechanical_status": "proof_absent",
    }

    if not done_path.exists():
        _write_verification_json(notes_dir, handout_id, verification)
        return verification

    done_text = done_path.read_text(encoding="utf-8")
    proof = _parse_proof_section(done_text)

    # Parse handout proof policy (needed for scope check and commit_missing logic)
    if handout_path and handout_path.exists():
        handout_text = handout_path.read_text(encoding="utf-8", errors="replace")
        policy = _parse_handout_proof_policy(handout_text)
        verification["proof_policy"] = policy
    else:
        policy = _parse_handout_proof_policy("")

    verification["has_proof_section"] = proof["has_proof_section"]
    if not proof["has_proof_section"]:
        _write_verification_json(notes_dir, handout_id, verification)
        return verification

    # Record parsed fields
    verification["proof_fields"] = {
        k: proof[k]
        for k in ("command", "exit_code", "test_summary", "commit", "files_changed", "not_completed")
    }

    # Check all required fields present
    missing = [f for f in ("command", "exit_code", "test_summary", "commit") if not proof[f]]
    verification["checks"]["missing_required_fields"] = missing
    verification["checks"]["proof_parseable"] = len(missing) == 0

    if missing:
        verification["mechanical_status"] = "proof_unparseable"
        _write_verification_json(notes_dir, handout_id, verification)
        return verification

    # Exit code check — treat "n/a" as passing when tests are not required
    ec = proof["exit_code"].strip()
    requires_tests = policy.get("requires_tests", True)
    if ec.lower() in ("n/a", "na", "none", "") and not requires_tests:
        verification["checks"]["exit_code_zero"] = True  # no tests required; n/a is valid
    else:
        verification["checks"]["exit_code_zero"] = ec == "0"

    # Tests reported check (executor-self-reported; not re-executed)
    ts = (proof["test_summary"] or "").strip().lower()
    verification["executor_reported"] = {
        "tests_reported": bool(ts) and ts not in ("n/a", "none", ""),
        "test_summary_raw": proof["test_summary"] or "",
    }

    # Commit check
    commit_val = (proof["commit"] or "").strip().lower()
    if commit_val in ("none", "none — no code changes", "none — no code changes required", ""):
        verification["checks"]["commit_verified"] = None  # intentionally absent
    else:
        # Verify commit exists in git.
        # Strip backticks (executors sometimes write `hash`) and take first token.
        commit_hash = proof["commit"].strip().split()[0].strip("`'")
        try:
            cp = subprocess.run(
                ["git", "cat-file", "-t", commit_hash],
                capture_output=True,
                cwd=repo_root,
                timeout=10,
            )
            if cp.returncode == 0:
                verification["checks"]["commit_verified"] = True
            else:
                # Executor sometimes writes a correct 8-char prefix but pads with
                # invented digits to look like a full 40-char hash.  Fall back to
                # the first 8 characters before declaring fabrication.
                short = commit_hash[:8]
                cp2 = subprocess.run(
                    ["git", "cat-file", "-t", short],
                    capture_output=True,
                    cwd=repo_root,
                    timeout=10,
                )
                if cp2.returncode == 0:
                    # Short hash is real — resolve to the canonical full hash so
                    # downstream checks (reachable, diff) use the correct object.
                    rev = subprocess.run(
                        ["git", "rev-parse", short],
                        capture_output=True,
                        text=True,
                        cwd=repo_root,
                        timeout=10,
                    )
                    commit_hash = rev.stdout.strip() or short
                    verification["checks"]["commit_verified"] = True
                    verification["checks"]["commit_hash_normalized"] = commit_hash
                else:
                    verification["checks"]["commit_verified"] = False
        except Exception:
            verification["checks"]["commit_verified"] = False

    # Files exist check
    for fpath in proof["files_changed_list"]:
        full = repo_root / fpath
        verification["checks"]["files_exist"][fpath] = full.exists()

    # ------------------------------------------------------------------
    # v2 relationship checks — only run when commit is confirmed to exist
    # ------------------------------------------------------------------
    if verification["checks"]["commit_verified"] is True:
        # Use the normalized hash if the verifier resolved a padded short hash
        commit_hash = (
            verification["checks"].get("commit_hash_normalized")
            or proof["commit"].strip().split()[0].strip("`'")
        )

        # Check: commit is reachable from HEAD (not an orphan / unrelated branch)
        try:
            cp = subprocess.run(
                ["git", "merge-base", "--is-ancestor", commit_hash, "HEAD"],
                capture_output=True,
                cwd=repo_root,
                timeout=10,
            )
            verification["checks"]["commit_reachable"] = cp.returncode == 0
        except Exception:
            verification["checks"]["commit_reachable"] = False

        # Get the set of files actually changed by this commit.
        # --root handles root commits (no parent) — without it diff-tree emits nothing.
        diff_files: Optional[set] = None
        try:
            cp = subprocess.run(
                ["git", "diff-tree", "--no-commit-id", "--root", "-r", "--name-only", commit_hash],
                capture_output=True,
                text=True,
                cwd=repo_root,
                timeout=10,
            )
            if cp.returncode == 0:
                diff_files = {ln.strip() for ln in cp.stdout.splitlines() if ln.strip()}
                verification["checks"]["diff_files"] = sorted(diff_files)
        except Exception:
            pass  # diff_files stays None; checks left as None

        # Check: each claimed file appears in the commit's diff
        if diff_files is not None:
            files_in_diff = {}
            for fpath in proof["files_changed_list"]:
                files_in_diff[fpath] = fpath in diff_files
            verification["checks"]["files_in_diff"] = files_in_diff

        # Check: scope enforcement — diff files must be within allowed_paths
        allowed_paths = policy.get("allowed_paths", [])
        if allowed_paths and diff_files is not None:
            violations = [
                f for f in sorted(diff_files)
                if not any(f.startswith(p.rstrip("/")) for p in allowed_paths)
            ]
            verification["checks"]["scope_violations"] = violations

        # Unstaged scope violation check
        if allowed_paths:
            # Exempt summoner-internal files that are legitimately modified during
            # the feedback pass (tracker.json, .handoff.md) — these are never
            # executor scope violations regardless of the handout's allowed_paths.
            try:
                tracker_rel = str(
                    (notes_dir.parent / "tracker.json").relative_to(repo_root)
                ).replace("\\", "/")
            except ValueError:
                # notes_dir not under repo_root (e.g. in tests) — use canonical path
                tracker_rel = "docs/manager/tracker.json"
            summoner_exempt = [tracker_rel, ".handoff.md"]
            unstaged = _check_unstaged_violations(repo_root, allowed_paths, summoner_exempt)
            verification["checks"]["unstaged_scope_violations"] = unstaged

        # Trivial .md diff check
        if diff_files is not None:
            md_files = [f for f in diff_files if f.endswith(".md")]
            if md_files:
                md_nontrivial = _check_md_diff_non_trivial(commit_hash, repo_root)
                verification["checks"]["md_diff_nontrivial"] = md_nontrivial

    # Derive mechanical_status (worst condition wins, checked low→high priority)
    status = "mechanically_clean"
    if not verification["checks"]["exit_code_zero"]:
        status = "exit_code_nonzero"
    if any(not v for v in verification["checks"]["files_exist"].values()):
        status = "listed_files_missing"
    # v2: scope violation
    if verification["checks"].get("scope_violations"):
        status = "scope_violation"
    # v2: unstaged scope violation
    if verification["checks"].get("unstaged_scope_violations"):
        status = "unstaged_scope_violation"
    # v2: claimed files absent from commit diff
    if any(not v for v in verification["checks"].get("files_in_diff", {}).values()):
        status = "files_not_in_diff"
    # v2: commit exists but not in HEAD's history
    if verification["checks"].get("commit_reachable") is False:
        status = "commit_unreachable"
    # commit doesn't exist at all
    if verification["checks"]["commit_verified"] is False:
        status = "commit_claim_false"
    requires_commit = policy.get("requires_commit", True)
    if verification["checks"]["commit_verified"] is None and status == "mechanically_clean":
        if requires_commit is not False:
            status = "no_commit_claimed"  # informational; suppress when handout declares no commit needed
    if status == "mechanically_clean":
        md_nontrivial = verification["checks"].get("md_diff_nontrivial", {})
        if md_nontrivial and not all(md_nontrivial.values()):
            status = "whitespace_only_md_diff"
    verification["mechanical_status"] = status

    _write_verification_json(notes_dir, handout_id, verification)
    return verification


def _write_verification_json(notes_dir: Path, handout_id: str, data: dict) -> None:
    notes_dir.mkdir(parents=True, exist_ok=True)
    out = notes_dir / f"verification_{handout_id}.json"
    out.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _format_verification_for_prompt(v: dict) -> str:
    """Format verification dict as a readable block for injection into PROMPT_TEMPLATE."""
    lines = [
        f"done_file_exists: {v['done_file_exists']}",
        f"has_proof_section: {v['has_proof_section']}",
        f"mechanical_status: {v['mechanical_status']}",
    ]
    checks = v.get("checks", {})
    if checks.get("missing_required_fields"):
        lines.append(f"missing_required_fields: {', '.join(checks['missing_required_fields'])}")
    lines.append(f"exit_code_zero: {checks.get('exit_code_zero')}")
    lines.append(f"commit_verified: {checks.get('commit_verified')}")
    if checks.get("commit_reachable") is not None:
        lines.append(f"commit_reachable: {checks['commit_reachable']}")
    if checks.get("files_exist"):
        for path, exists in checks["files_exist"].items():
            lines.append(f"  file_exists [{path}]: {exists}")
    if checks.get("files_in_diff"):
        for path, in_diff in checks["files_in_diff"].items():
            lines.append(f"  file_in_diff [{path}]: {in_diff}")
    if checks.get("scope_violations"):
        lines.append(f"scope_violations: {', '.join(checks['scope_violations'])}")
    if checks.get("unstaged_scope_violations"):
        lines.append(f"unstaged_scope_violations: {', '.join(checks['unstaged_scope_violations'])}")
    er = v.get("executor_reported", {})
    if er:
        lines.append(f"executor_reported.tests_reported: {er.get('tests_reported')}  [self-reported; not re-executed]")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Handout preflight
# ---------------------------------------------------------------------------

def preflight_handout(handout_path: Path, repo_root: Path) -> list[str]:
    """Check a handout for common structural problems before spawning an executor.

    Returns a list of warning strings. Empty list means no issues found.
    Checks:
    - ## Done when section is present
    - ## Out of scope section is present
    - src/ or tests/ paths in backticks exist on disk (skips paths that look like new-file targets)
    """
    warnings: list[str] = []
    try:
        text = handout_path.read_text(encoding="utf-8")
    except Exception as exc:
        return [f"cannot read handout: {exc}"]

    if "## Done when" not in text and "## Done When" not in text:
        warnings.append("missing '## Done when' section — executor has no acceptance criteria")

    if "## Out of scope" not in text and "## Out of Scope" not in text:
        warnings.append("missing '## Out of scope' section — executor scope is unbounded")

    # Check backtick-quoted src/ and tests/ paths
    quoted_paths = re.findall(r"`((?:src|tests)/[^`\s]+)`", text)
    for qp in quoted_paths:
        full = repo_root / qp
        if not full.exists():
            # Could be a new file the executor is supposed to create — only warn, don't block
            warnings.append(f"path referenced in handout does not exist: {qp} (new file target?)")

    return warnings


# Human-owned files in the project guide that refresh_manager_guide must never overwrite.
# These describe the managed PROJECT (Vibechords), not the Summoner tool itself.
_GUIDE_HUMAN_OWNED = frozenset({
    "project_charter.md",
    "current_goal.md",
    "active_roadmap.md",
    "known_gotchas.md",
})

# Project guide directory name (relative to manager_dir).
# Separate from docs/manager/guide/ which holds Summoner self-tracking state.
_PROJECT_GUIDE_DIR = "project_guide"


def _build_guide_context(manager_dir: Path) -> str:
    """Assemble a compact project context packet from the project guide.

    Reads files from docs/manager/project_guide/ — descriptions of the managed
    project (Vibechords), not of the Summoner tool itself. Concatenates present
    files under a # Project Context header. Returns empty string if the project
    guide directory is absent, so callers can safely check `if guide_context`.
    """
    guide_dir = manager_dir / _PROJECT_GUIDE_DIR
    if not guide_dir.is_dir():
        return ""
    parts: list[str] = []
    for filename, label in [
        ("project_charter.md", "Project Charter"),
        ("current_goal.md", "Current Goal"),
        ("active_roadmap.md", "Active Roadmap"),
        ("known_gotchas.md", "Known Gotchas"),
        ("repo_architecture.generated.md", "Repo Architecture"),
        ("test_conventions.generated.md", "Test Conventions"),
    ]:
        path = guide_dir / filename
        if path.exists():
            content = path.read_text(encoding="utf-8").strip()
            if content:
                parts.append(f"## {label}\n{content}")
    if not parts:
        return ""
    return "# Project Context\n\n" + "\n\n".join(parts) + "\n\n"


def refresh_manager_guide(
    manager_dir: Path,
    notes_dir: Path,
    root: Path,
) -> Path:
    """Generate guide/status.generated.md from tracker state and feedback files.

    Reads tracker.json for task counts, scans feedback files for ## Failure Type
    classifications (requires feedback written after the Failure Type section was
    added to PROMPT_TEMPLATE). Does NOT touch human-owned project guide files.

    The generated status file lives in docs/manager/guide/ (Summoner self-tracking),
    not in docs/manager/project_guide/ (which describes the managed project).

    Returns the path to the written status.generated.md.
    """
    guide_dir = manager_dir / "guide"
    guide_dir.mkdir(parents=True, exist_ok=True)

    data = load_tracker(manager_dir)
    handouts = data.get("handouts", [])

    done_statuses = frozenset({"done", "reviewed", "accepted", "done_submitted"})
    active_statuses = frozenset({"spawned"})
    failed_statuses = frozenset({"failed", "timed_out"})

    status_counts: dict[str, int] = {}
    for entry in handouts:
        s = entry.get("status", "unknown")
        status_counts[s] = status_counts.get(s, 0) + 1

    completed = sum(v for k, v in status_counts.items() if k in done_statuses)
    active = sum(v for k, v in status_counts.items() if k in active_statuses)
    failed = sum(v for k, v in status_counts.items() if k in failed_statuses)
    needs_fix = status_counts.get("needs_fix", 0)
    pending = status_counts.get("pending", 0)
    total = len(handouts)

    # Scan feedback files for failure-type evidence.
    evidence: dict[str, list[str]] = {}  # failure_type -> [handout IDs]
    for entry in handouts:
        hid = entry.get("id", "")
        ft = _extract_failure_type(notes_dir / f"feedback_{hid}.md")
        if ft and ft != "clean_completion":
            evidence.setdefault(ft, []).append(hid)

    today = datetime.now(timezone.utc).date().isoformat()
    lines = [
        "# Manager Guide Status",
        "",
        f"Generated: {today}",
        "",
        "## Current task state",
        f"- {total} handouts total",
        f"- {completed} completed (reviewed / accepted / done_submitted)",
        f"- {pending} pending",
        f"- {active} active spawns",
        f"- {failed} failed or timed out",
        f"- {needs_fix} needs_fix",
        "",
        "## Demonstrated value (from feedback ## Failure Type sections)",
    ]
    if evidence:
        for ft, hids in sorted(evidence.items()):
            lines.append(f"- {ft}: task(s) {', '.join(hids)}")
    else:
        lines.append(
            "- No non-clean failures found in feedback files"
            " (feedback may predate the ## Failure Type section)"
        )

    lines += ["", "## Project guide files"]
    project_guide_dir = manager_dir / _PROJECT_GUIDE_DIR
    for fname in sorted(_GUIDE_HUMAN_OWNED):
        path = project_guide_dir / fname
        status = "present" if path.exists() else "MISSING — create in docs/manager/project_guide/"
        lines.append(f"- {fname}: {status}")

    status_path = guide_dir / "status.generated.md"
    status_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return status_path


def load_index_slice(index_path: Path, keywords: set[str], max_entries: int = 15) -> str:
    if not index_path.exists():
        return "(repo index not found — set --index-path or ensure docs/repo-index/v1.json exists)"
    try:
        entries: list[dict] = json.loads(index_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return f"(index load failed: {exc})"

    def score(entry: dict) -> int:
        haystack = " ".join(filter(None, [
            entry.get("title", ""),
            entry.get("summary", ""),
            entry.get("why_might_matter", ""),
            entry.get("source_path", ""),
            " ".join(entry.get("topic_tags", [])),
            " ".join(entry.get("component_tags", [])),
            " ".join(entry.get("subsystem_tags", [])),
        ])).lower()
        return sum(1 for kw in keywords if kw in haystack)

    ranked = sorted(((score(e), e) for e in entries), key=lambda x: -x[0])
    top = [e for s, e in ranked if s > 0][:max_entries]
    if not top:
        return "(no index entries matched the current handout keywords)"

    lines = []
    for e in top:
        tag = e.get("domain", "?")[:4].upper()
        path = e.get("source_path", "?")
        title = e.get("title", "")
        blurb = (e.get("summary") or e.get("why_might_matter") or "")[:90].replace("\n", " ")
        lines.append(f"[{tag}] {path} — {title}" + (f": {blurb}" if blurb else ""))
    return "\n".join(lines)


def _build_next_handout_prompt(
    plan: str,
    handout_id: str,
    done: str,
    feedback: str,
    index_slice: str,
    guide_context: str,
    next_id: str,
) -> str:
    """Build the prompt string for generate_next_handout().

    Extracted as a pure function so tests can verify guide content actually
    lands in the prompt without triggering a model call.
    """
    return NEXT_HANDOUT_TEMPLATE.format(
        guide_context=guide_context,
        plan=plan,
        handout_id=handout_id,
        done=done,
        feedback=feedback,
        index_slice=index_slice,
        next_id=next_id,
    )


def generate_next_handout(
    root: Path,
    handout_id: str,
    manager_dir: Optional[Path] = None,
    index_path: Optional[Path] = None,
    notes_dir: Optional[Path] = None,
) -> Optional[Path]:
    effective_notes_dir = _resolve_notes_dir(notes_dir, manager_dir)
    next_id = str(int(handout_id) + 1).zfill(3)
    next_path = root / "handouts" / f"handout_{next_id}.md"
    if next_path.exists():
        return None  # already written, do not overwrite

    plan_path = root / "plan.md"
    done_path = root / "handouts" / f"handout_{handout_id}_done.md"
    feedback_path = effective_notes_dir / f"feedback_{handout_id}.md"
    if not all(p.exists() for p in [plan_path, done_path, feedback_path]):
        return None

    plan_raw = plan_path.read_text(encoding="utf-8")
    done = done_path.read_text(encoding="utf-8")
    feedback = feedback_path.read_text(encoding="utf-8")

    keyword_source = done + " " + feedback
    plan_sliced, slicing_used = slice_plan(plan_raw, keyword_source)

    keywords = _keywords_from_text(keyword_source)
    idx_path = index_path if index_path is not None else DEFAULT_INDEX_PATH
    index_slice = load_index_slice(idx_path, keywords)
    guide_context = _build_guide_context(manager_dir) if manager_dir is not None else ""

    prompt = _build_next_handout_prompt(
        plan=plan_sliced,
        handout_id=handout_id,
        done=done,
        feedback=feedback,
        index_slice=index_slice,
        guide_context=guide_context,
        next_id=next_id,
    )
    response = call_manager_model(prompt)
    body = response.body.strip()

    # Strip any model preamble before the expected H1
    if not body.startswith("#"):
        for i, line in enumerate(body.splitlines()):
            if line.startswith(f"# Handout {next_id}"):
                body = "\n".join(body.splitlines()[i:])
                break

    next_path.write_text(body + "\n", encoding="utf-8")

    if manager_dir is not None:
        title = _extract_title(next_path)
        today = datetime.now(timezone.utc).date().isoformat()
        # One locked block: mark source accepted + add next as pending atomically.
        with _tracker_lock(manager_dir):
            data = load_tracker(manager_dir)
            for entry in data.get("handouts", []):
                if entry.get("id") == handout_id:
                    entry["status"] = "accepted"
                    break
            else:
                data.setdefault("handouts", []).append(
                    {"id": handout_id, "status": "accepted", "created": today}
                )
            data.setdefault("handouts", []).append({
                "id": next_id,
                "title": title,
                "status": "pending",
                "created": today,
            })
            save_tracker(manager_dir, data)

    return next_path


def _extract_title(handout_path: Path) -> str:
    try:
        for line in handout_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("# "):
                return line[2:].strip()
    except Exception:
        pass
    return handout_path.stem


def load_tracker(manager_dir: Path) -> dict:
    path = manager_dir / "tracker.json"
    if not path.exists():
        return {"version": 1, "updated": "", "handouts": []}
    return json.loads(path.read_text(encoding="utf-8"))


def save_tracker(manager_dir: Path, data: dict) -> None:
    """Write tracker.json. Must be called while holding _tracker_lock."""
    manager_dir.mkdir(parents=True, exist_ok=True)
    data["updated"] = datetime.now(timezone.utc).date().isoformat()
    (manager_dir / "tracker.json").write_text(
        json.dumps(data, indent=2) + "\n", encoding="utf-8"
    )


# Thread-local depth counter for re-entrant lock support.
# Re-entrancy is needed because generate_next_handout() calls update_tracker_status()
# inside its own locked block.
_tracker_lock_depth = threading.local()


@contextlib.contextmanager
def _tracker_lock(manager_dir: Path, timeout_sec: float = 5.0):
    """Cross-process file lock for tracker.json read-modify-write cycles.

    Uses an exclusive lock file so concurrent subprocesses (parent watch loop +
    executor child) cannot corrupt the JSON.  Re-entrant within the same thread
    so nested callers just yield through without deadlocking.
    """
    depth = getattr(_tracker_lock_depth, "n", 0)
    if depth > 0:
        # Already holding the lock in this thread — yield through.
        _tracker_lock_depth.n = depth + 1
        try:
            yield
        finally:
            _tracker_lock_depth.n -= 1
        return

    lock_path = manager_dir / "tracker.lock"
    manager_dir.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_sec
    while True:
        try:
            lock_path.open("x").close()   # atomic exclusive create
            break
        except FileExistsError:
            if time.monotonic() > deadline:
                raise TimeoutError(
                    f"Could not acquire tracker lock after {timeout_sec:.1f}s. "
                    f"Delete {lock_path} if it is stale."
                )
            time.sleep(0.05)

    _tracker_lock_depth.n = 1
    try:
        yield
    finally:
        _tracker_lock_depth.n = 0
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def _update_tracker_fields(manager_dir: Path, handout_id: str, **fields: object) -> None:
    """Atomically merge ``fields`` into a tracker entry, creating a stub if needed.

    Acquires _tracker_lock for the full read-modify-write cycle so concurrent
    subprocesses cannot interleave their writes.
    """
    with _tracker_lock(manager_dir):
        data = load_tracker(manager_dir)
        today = datetime.now(timezone.utc).date().isoformat()
        for entry in data.get("handouts", []):
            if entry.get("id") == handout_id:
                entry.update(fields)
                save_tracker(manager_dir, data)
                return
        data.setdefault("handouts", []).append(
            {"id": handout_id, "created": today, **fields}
        )
        save_tracker(manager_dir, data)


def update_tracker_status(manager_dir: Path, handout_id: str, status: str) -> None:
    """Set the status field of a tracker entry, creating a stub if needed.

    Only accepts values from VALID_STATES.  The legacy "done" value is accepted
    on read (see _LEGACY_DONE_ALIAS) but should not be written by new code.
    """
    if status not in VALID_STATES:
        raise ValueError(
            f"invalid tracker status {status!r}; valid: {sorted(VALID_STATES)}"
        )
    _update_tracker_fields(manager_dir, handout_id, status=status)


def upsert_tracker_entry(
    manager_dir: Path,
    root: Path,
    handout_id: str,
    metrics: RunMetrics,
    notes_dir: Optional[Path] = None,
) -> None:
    title = _extract_title(root / "handouts" / f"handout_{handout_id}.md")
    today = datetime.now(timezone.utc).date().isoformat()
    effective_notes_dir = _resolve_notes_dir(notes_dir, manager_dir)
    payload = {
        "id": handout_id,
        "title": title,
        "status": "reviewed",
        "completed": today,
        "model": metrics.model_label,
        "prompt_tokens": metrics.actual_prompt_tokens if metrics.actual_prompt_tokens is not None else metrics.estimated_prompt_tokens,
        "response_tokens": metrics.actual_response_tokens if metrics.actual_response_tokens is not None else metrics.estimated_response_tokens,
        "reasoning_tokens": metrics.actual_reasoning_tokens,
        "cache_read_tokens": metrics.actual_cache_read_tokens,
        "wall_clock_sec": round(metrics.wall_clock_sec, 3),
        "notes_dir": str(effective_notes_dir),
    }
    with _tracker_lock(manager_dir):
        data = load_tracker(manager_dir)
        for entry in data.get("handouts", []):
            if entry.get("id") == handout_id:
                entry.update(payload)
                save_tracker(manager_dir, data)
                return
        data.setdefault("handouts", []).append({"created": today, **payload})
        save_tracker(manager_dir, data)


def _load_task_file(task_path: Path) -> dict:
    """Load and validate a JSON task file for mapper/researcher dispatch."""
    import json as _json
    if not task_path.exists():
        raise FileNotFoundError(f"Task file not found: {task_path}")
    data = _json.loads(task_path.read_text(encoding="utf-8"))
    required = {"task_id", "type", "goal", "input_files", "allowed_outputs"}
    missing = required - data.keys()
    if missing:
        raise ValueError(f"Task file missing required fields: {missing}")
    return data


def spawn_mapper(
    task_path: Path,
    repo_root: Path,
    notes_dir: Optional[Path] = None,
) -> SpawnResult:
    """Invoke a mapper agent to produce a project guide artifact from a task JSON file.

    The mapper is read-only: it inspects source files and writes one structured
    artifact to docs/manager/project_guide/. It cannot modify source.
    """
    task = _load_task_file(task_path)
    task_id = task["task_id"]
    output_path = task["allowed_outputs"][0] if task["allowed_outputs"] else "unknown"
    input_files_str = "\n".join(f"- {f}" for f in task["input_files"])
    task_details = task.get("forbidden", [])
    task_details_str = "\n".join(f"- {f}" for f in task_details) if task_details else "None"

    prompt = MAPPER_PROMPT_TEMPLATE.format(
        task_goal=task["goal"],
        input_files=input_files_str,
        output_path=output_path,
        task_details=task_details_str,
    )

    if notes_dir:
        log_path = notes_dir / f"mapper_spawn_{task_id}.md"
        log_path.write_text(
            f"# Mapper spawn — {task_id}\n\nTask: {task_path}\nOutput: {output_path}\n",
            encoding="utf-8",
        )

    t0 = time.perf_counter()
    response = call_mapper_model(prompt)
    wall = time.perf_counter() - t0

    return SpawnResult(
        handout_id=task_id,
        executor_model=response.model_label or "mock",
        spawned=not bool(response.fallback_reason),
        fallback_reason=response.fallback_reason,
        wall_clock_sec=wall,
        response_preview=response.body[:200] if response.body else "",
    )


def spawn_researcher(
    task_path: Path,
    repo_root: Path,
    notes_dir: Optional[Path] = None,
) -> SpawnResult:
    """Invoke a researcher agent to answer bounded questions from a task JSON file.

    The researcher is read-only: it reads listed files, answers listed questions,
    and writes one findings artifact to docs/manager/research/. It cannot modify source.
    """
    task = _load_task_file(task_path)
    task_id = task["task_id"]
    output_path = task["allowed_outputs"][0] if task["allowed_outputs"] else "unknown"
    input_files_str = "\n".join(f"- {f}" for f in task["input_files"])
    questions = task.get("questions", [])
    questions_str = "\n".join(f"- {q}" for q in questions) if questions else task["goal"]

    prompt = RESEARCHER_PROMPT_TEMPLATE.format(
        task_goal=task["goal"],
        questions=questions_str,
        input_files=input_files_str,
        output_path=output_path,
    )

    if notes_dir:
        log_path = notes_dir / f"researcher_spawn_{task_id}.md"
        log_path.write_text(
            f"# Researcher spawn — {task_id}\n\nTask: {task_path}\nOutput: {output_path}\n",
            encoding="utf-8",
        )

    t0 = time.perf_counter()
    response = call_researcher_model(prompt)
    wall = time.perf_counter() - t0

    return SpawnResult(
        handout_id=task_id,
        executor_model=response.model_label or "mock",
        spawned=not bool(response.fallback_reason),
        fallback_reason=response.fallback_reason,
        wall_clock_sec=wall,
        response_preview=response.body[:200] if response.body else "",
    )


def cmd_map(task_file: str, repo_root: Path, notes_dir: Optional[Path] = None) -> int:
    """Dispatch a mapper agent from a task JSON file."""
    task_path = Path(task_file).resolve()
    print(f"[map] dispatching mapper for task: {task_path.name}")
    try:
        result = spawn_mapper(task_path, repo_root, notes_dir=notes_dir)
    except (FileNotFoundError, ValueError) as e:
        print(f"[map] ERROR: {e}")
        return 1
    if result.spawned:
        print(f"[map] mapper completed in {result.wall_clock_sec:.1f}s")
    else:
        print(f"[map] mapper did not complete: {result.fallback_reason}")
        return 1
    return 0


def cmd_research(task_file: str, repo_root: Path, notes_dir: Optional[Path] = None) -> int:
    """Dispatch a researcher agent from a task JSON file."""
    task_path = Path(task_file).resolve()
    print(f"[research] dispatching researcher for task: {task_path.name}")
    try:
        result = spawn_researcher(task_path, repo_root, notes_dir=notes_dir)
    except (FileNotFoundError, ValueError) as e:
        print(f"[research] ERROR: {e}")
        return 1
    if result.spawned:
        print(f"[research] researcher completed in {result.wall_clock_sec:.1f}s")
    else:
        print(f"[research] researcher did not complete: {result.fallback_reason}")
        return 1
    return 0


def spawn_reviewer(
    task_path: Path,
    repo_root: Path,
    notes_dir: Optional[Path] = None,
) -> SpawnResult:
    """Invoke a reviewer agent to check a coder's output against handout criteria.

    The reviewer is read-only: it reads the handout, proof artifact, and verification
    output, then writes one verdict artifact to docs/manager/reviews/. It cannot
    modify source, tests, or widen task scope.
    """
    task = _load_task_file(task_path)
    task_id = task["task_id"]
    output_path = task["allowed_outputs"][0] if task["allowed_outputs"] else "unknown"
    input_files_str = "\n".join(f"- {f}" for f in task["input_files"])

    prompt = REVIEWER_PROMPT_TEMPLATE.format(
        task_goal=task["goal"],
        input_files=input_files_str,
        output_path=output_path,
    )

    if notes_dir:
        log_path = notes_dir / f"reviewer_spawn_{task_id}.md"
        log_path.write_text(
            f"# Reviewer spawn — {task_id}\n\nTask: {task_path}\nOutput: {output_path}\n",
            encoding="utf-8",
        )

    t0 = time.perf_counter()
    response = call_reviewer_model(prompt)
    wall = time.perf_counter() - t0

    return SpawnResult(
        handout_id=task_id,
        executor_model=response.model_label or "mock",
        spawned=not bool(response.fallback_reason),
        fallback_reason=response.fallback_reason,
        wall_clock_sec=wall,
        response_preview=response.body[:200] if response.body else "",
    )


def cmd_review(task_file: str, repo_root: Path, notes_dir: Optional[Path] = None) -> int:
    """Dispatch a reviewer agent from a task JSON file."""
    task_path = Path(task_file).resolve()
    print(f"[review] dispatching reviewer for task: {task_path.name}")
    try:
        result = spawn_reviewer(task_path, repo_root, notes_dir=notes_dir)
    except (FileNotFoundError, ValueError) as e:
        print(f"[review] ERROR: {e}")
        return 1
    if result.spawned:
        print(f"[review] reviewer completed in {result.wall_clock_sec:.1f}s")
    else:
        print(f"[review] reviewer did not complete: {result.fallback_reason}")
        return 1
    return 0


def cmd_init(root: Path) -> int:
    agents_dir = root / ".agents"
    handouts_dir = root / "handouts"
    agents_dir.mkdir(exist_ok=True)
    handouts_dir.mkdir(exist_ok=True)
    generated: list[str] = []
    for name, content in ROLE_TEMPLATES.items():
        path = agents_dir / f"{name}.agent.md"
        path.write_text(content, encoding="utf-8")
        generated.append(path.name)
        print(f"wrote .agents/{path.name}")
    today = datetime.now(timezone.utc).date().isoformat()
    lines = [
        "# Summoner Run init",
        f"Generated: {today}",
        "",
        "## Measurements",
        f"- agent files generated: {', '.join(generated)}",
    ]
    for n in generated:
        size = (agents_dir / n).stat().st_size
        lines.append(f"- {n}: {size} chars, ~{estimate_tokens((agents_dir / n).read_text(encoding='utf-8'))} tokens")
    lines.append("- OpenCode integration: untested")
    log = root / "summoner_run_init.md"
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {log.name}")
    return 0


def _normalize_id(raw: str) -> str:
    digits = "".join(c for c in raw if c.isdigit())
    if not digits:
        raise ValueError(f"handout id must contain digits, got {raw!r}")
    return digits.zfill(3)


def cmd_run(
    root: Path,
    raw_id: str,
    manager_dir: Optional[Path] = None,
    index_path: Optional[Path] = None,
    notes_dir: Optional[Path] = None,
    auto_next: bool = True,
) -> int:
    try:
        handout_id = _normalize_id(raw_id)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    effective_notes_dir = _resolve_notes_dir(notes_dir, manager_dir)
    try:
        metrics = process(
            root, handout_id,
            manager_dir=manager_dir, index_path=index_path,
            notes_dir=effective_notes_dir, auto_next=auto_next,
        )
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    feedback = effective_notes_dir / f"feedback_{handout_id}.md"
    print(
        f"wrote {feedback} "
        f"({metrics.response_chars} chars, model={metrics.model_label}, "
        f"{metrics.wall_clock_sec:.2f}s)"
    )
    if metrics.fallback_reason:
        print(f"  fallback: {metrics.fallback_reason}")
    if not auto_next:
        print(f"  (--no-auto-next: run --next {handout_id} to generate next handout after review)")
    # Append one line to the event log after every gate evaluation.
    classification = _extract_failure_type(feedback)
    done_path = root / "handouts" / f"handout_{handout_id}_done.md"
    artifact = str(done_path.relative_to(_REPO_ROOT)) if done_path.exists() else None
    if metrics.fallback_reason:
        note = f"fallback: {metrics.fallback_reason}"
    else:
        note = f"model={metrics.model_label} chars={metrics.response_chars} wall={metrics.wall_clock_sec:.1f}s"
    _write_event_log(
        effective_notes_dir,
        handout_id,
        worker="executor",
        classification=classification,
        artifact=artifact,
        note=note,
    )
    return 0


def cmd_next(
    root: Path,
    raw_id: str,
    manager_dir: Optional[Path] = None,
    index_path: Optional[Path] = None,
    notes_dir: Optional[Path] = None,
) -> int:
    """Generate the next handout from an already-reviewed handout."""
    try:
        handout_id = _normalize_id(raw_id)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    next_id = str(int(handout_id) + 1).zfill(3)
    next_path = root / "handouts" / f"handout_{next_id}.md"
    if next_path.exists():
        print(f"handout_{next_id}.md already exists — nothing to generate")
        return 0
    feedback_path = _resolve_notes_dir(notes_dir, manager_dir) / f"feedback_{handout_id}.md"
    if not feedback_path.exists():
        print(f"error: feedback_{handout_id}.md not found — run --run {handout_id} first", file=sys.stderr)
        return 1
    generated = generate_next_handout(
        root, handout_id, manager_dir=manager_dir, index_path=index_path, notes_dir=notes_dir,
    )
    if generated:
        print(f"generated handout_{next_id}.md")
    else:
        print(f"nothing generated (plan exhausted or handout_{next_id}.md already exists)")
    return 0


def cmd_spawn(
    root: Path,
    raw_id: str,
    manager_dir: Optional[Path] = None,
    notes_dir: Optional[Path] = None,
) -> int:
    """Spawn an executor agent on a single handout and return.

    When SUMMONER_WORKTREE=1, runs the executor inside a dedicated git worktree
    with scope validation and automatic merge back to the current branch.
    """
    try:
        handout_id = _normalize_id(raw_id)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    done_path = root / "handouts" / f"handout_{handout_id}_done.md"
    if done_path.exists():
        print(f"handout_{handout_id}_done.md already exists — nothing to spawn")
        return 0
    executor_spec = (os.environ.get("EXECUTOR_MODEL") or os.environ.get("SUMMONER_MODEL") or "").strip()
    use_worktree = os.environ.get("SUMMONER_WORKTREE", "").strip().lower() in ("1", "true", "yes")
    mode = "worktree" if use_worktree else "direct"
    print(f"spawning executor on handout {handout_id} (model={executor_spec or 'mock'}, mode={mode})")
    if use_worktree:
        repo_root = _REPO_ROOT
        result = spawn_executor_in_worktree(
            repo_root, root, handout_id, notes_dir=notes_dir, manager_dir=manager_dir
        )
    else:
        result = spawn_executor(root, handout_id, notes_dir=notes_dir, manager_dir=manager_dir)
    if result.spawned:
        print(f"  executor finished ({result.wall_clock_sec:.2f}s)")
        if done_path.exists():
            print(f"  done file written: handout_{handout_id}_done.md")
        else:
            print(f"  WARNING: executor returned but done file not found — check spawn log")
    else:
        print(f"  executor not spawned: {result.fallback_reason}")
    return 0


def cmd_preflight(root: Path, raw_id: str) -> int:
    """Run preflight checks on a handout without spawning an executor."""
    try:
        handout_id = _normalize_id(raw_id)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    handout_path = root / "handouts" / f"handout_{handout_id}.md"
    if not handout_path.exists():
        print(f"error: handout_{handout_id}.md not found", file=sys.stderr)
        return 2
    warnings = preflight_handout(handout_path, _REPO_ROOT)
    if not warnings:
        print(f"handout {handout_id}: preflight OK")
        return 0
    print(f"handout {handout_id}: {len(warnings)} preflight warning(s)")
    for w in warnings:
        print(f"  ! {w}")
    return 1


def _async_spawn(
    root: Path,
    next_id: str,
    effective_notes_dir: Path,
    manager_dir: Optional[Path],
    timeout_sec: float,
    active_spawns: dict,
    script_path: Path,
) -> None:
    """Run --spawn NNN as a child process with a hard timeout.

    Uses subprocess.Popen + communicate(timeout=N) so the executor can be
    forcibly killed on TimeoutExpired.  threading.Thread alone cannot hard-
    terminate a blocked model API call, but killing the child process can.
    """
    def ts() -> str:
        return datetime.now().strftime("%H:%M:%S")

    done_path = root / "handouts" / f"handout_{next_id}_done.md"
    cmd = [sys.executable, str(script_path), "--spawn", next_id,
           "--project-dir", str(root)]
    if manager_dir is not None:
        cmd += ["--manager-dir", str(manager_dir)]
    cmd += ["--notes-dir", str(effective_notes_dir)]

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=os.environ.copy(),
        )
        try:
            stdout, stderr = proc.communicate(timeout=timeout_sec)
            exit_code = proc.returncode
        except subprocess.TimeoutExpired:
            proc.kill()
            _, stderr = proc.communicate()
            stderr_text = stderr.decode("utf-8", errors="replace") if stderr else ""
            if manager_dir is not None:
                _update_tracker_fields(
                    manager_dir, next_id,
                    status="timed_out",
                    finished_at=datetime.now(timezone.utc).isoformat(),
                    last_error=f"killed after {timeout_sec:.0f}s timeout",
                )
            # Append timeout record to spawn log (subprocess may have written the file already)
            _append_timeout_log(effective_notes_dir, next_id, timeout_sec, stderr_text)
            print(f"[{ts()}] [auto-spawn {next_id}] TIMEOUT after {timeout_sec:.0f}s — process killed, marked timed_out")
            return

        # Subprocess exited normally — check outcome
        if done_path.exists():
            # spawn_executor() inside the subprocess already marked done_submitted
            print(f"[{ts()}] [auto-spawn {next_id}] done file ready (exit {exit_code})")
        else:
            if manager_dir is not None:
                _update_tracker_fields(
                    manager_dir, next_id,
                    status="failed",
                    finished_at=datetime.now(timezone.utc).isoformat(),
                    last_error=f"subprocess exited {exit_code} with no done file",
                    exit_code=exit_code,
                )
            print(
                f"[{ts()}] [auto-spawn {next_id}] WARNING: subprocess exited {exit_code} "
                f"but done file missing — marked failed"
            )
    except Exception as exc:
        if manager_dir is not None:
            _update_tracker_fields(
                manager_dir, next_id,
                status="failed",
                finished_at=datetime.now(timezone.utc).isoformat(),
                last_error=str(exc),
            )
        print(f"[{ts()}] [auto-spawn {next_id}] failed: {exc}")
    finally:
        active_spawns.pop(next_id, None)


def _append_timeout_log(notes_dir: Path, handout_id: str, timeout_sec: float, stderr_text: str) -> None:
    """Append a timeout record to the executor spawn log (creates if absent)."""
    notes_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).date().isoformat()
    section = "\n".join([
        "",
        f"## Timeout — {today}",
        f"- timeout_sec: {timeout_sec:.0f}",
        "- outcome: process killed after timeout",
        "- stderr (last 500 chars):",
        stderr_text[-500:] if stderr_text else "(empty)",
    ])
    log_path = notes_dir / f"executor_spawn_{handout_id}.md"
    if log_path.exists():
        with log_path.open("a", encoding="utf-8") as f:
            f.write(section + "\n")
    else:
        log_path.write_text(
            f"# Executor Spawn {handout_id}\nGenerated: {today}\n" + section + "\n",
            encoding="utf-8",
        )


def cmd_watch(
    root: Path,
    manager_dir: Optional[Path] = None,
    index_path: Optional[Path] = None,
    notes_dir: Optional[Path] = None,
    auto_spawn: bool = False,
) -> int:
    handouts_dir = root / "handouts"
    if not handouts_dir.exists():
        print("no handouts/ directory - run --init first", file=sys.stderr)
        return 1
    effective_notes_dir = _resolve_notes_dir(notes_dir, manager_dir)
    timeout_sec = float(os.environ.get("EXECUTOR_TIMEOUT_SEC", "300"))
    spawn_label = f" + auto-spawn (async, timeout={timeout_sec:.0f}s)" if auto_spawn else ""
    print(
        f"watching {handouts_dir.relative_to(root)}/ for *_done.md files"
        f"{spawn_label} (Ctrl+C to stop, poll={POLL_INTERVAL_SEC}s)"
    )
    skipped: set[str] = set()
    # Track in-flight background executor threads: {handout_id: Thread}
    active_spawns: dict[str, threading.Thread] = {}
    try:
        while True:
            for done in sorted(handouts_dir.glob("handout_*_done.md")):
                m = re.match(r"handout_(\d+)_done\.md$", done.name)
                if not m:
                    continue
                hid = m.group(1)
                feedback = effective_notes_dir / f"feedback_{hid}.md"
                if feedback.exists() or hid in skipped:
                    continue
                ts = datetime.now().strftime("%H:%M:%S")
                print(f"[{ts}] processing handout {hid}")
                try:
                    metrics = process(root, hid, manager_dir=manager_dir, index_path=index_path, notes_dir=effective_notes_dir)
                    print(
                        f"  wrote feedback_{hid}.md "
                        f"(model={metrics.model_label}, {metrics.wall_clock_sec:.2f}s)"
                    )
                    if metrics.fallback_reason:
                        print(f"  fallback: {metrics.fallback_reason}")
                    # After feedback, process() may have generated the next handout.
                    # If auto_spawn is on, fire the executor in a background thread so
                    # the watch loop keeps polling during the executor's run.
                    if auto_spawn:
                        next_id = str(int(hid) + 1).zfill(3)
                        next_handout = handouts_dir / f"handout_{next_id}.md"
                        next_done = handouts_dir / f"handout_{next_id}_done.md"
                        if (
                            next_handout.exists()
                            and not next_done.exists()
                            and next_id not in active_spawns
                        ):
                            print(f"  [auto-spawn] launching executor for handout {next_id} (subprocess, timeout={timeout_sec:.0f}s)")
                            t = threading.Thread(
                                target=_async_spawn,
                                args=(root, next_id, effective_notes_dir, manager_dir, timeout_sec, active_spawns, Path(__file__).resolve()),
                                daemon=True,
                                name=f"executor-{next_id}",
                            )
                            active_spawns[next_id] = t
                            t.start()
                except FileNotFoundError as exc:
                    print(f"  skipped: {exc}")
                    skipped.add(hid)
                except Exception as exc:
                    print(f"  failed: {exc}")
            time.sleep(POLL_INTERVAL_SEC)
    except KeyboardInterrupt:
        if active_spawns:
            print(f"\nstopped (waiting for {len(active_spawns)} running executor(s)…)")
            for t in list(active_spawns.values()):
                t.join(timeout=5.0)
        else:
            print("\nstopped")
        return 0


def cmd_check(root: Path, manager_dir: Optional[Path] = None, notes_dir: Optional[Path] = None) -> int:
    handouts_dir = root / "handouts"
    effective_notes_dir = _resolve_notes_dir(notes_dir, manager_dir)
    if not handouts_dir.exists():
        print("no handouts/ directory - run --init first")
        return 0

    # Collect IDs from handouts/, notes/, and tracker
    ids: set[str] = set()
    for f in handouts_dir.iterdir():
        m = re.match(r"handout_(\d+)(?:_done)?\.md$", f.name)
        if m:
            ids.add(m.group(1))
    if effective_notes_dir.exists():
        for f in effective_notes_dir.iterdir():
            m = re.match(r"(?:feedback|summoner_run)_(\d+)\.md$", f.name)
            if m:
                ids.add(m.group(1))

    if not ids:
        print("no handouts found")
        return 0

    # Load full tracker entries for status, staleness, and detail columns.
    tracker_entries: dict[str, dict] = {}
    if manager_dir is not None:
        data = load_tracker(manager_dir)
        for entry in data.get("handouts", []):
            eid = entry.get("id", "")
            tracker_entries[eid] = entry

    now_utc = datetime.now(timezone.utc)
    grace_sec = 60.0  # allow 60s beyond stored timeout before flagging stale

    stale_warnings: list[str] = []

    def _display_status(entry: dict) -> str:
        raw = entry.get("status", "-")
        if raw == _LEGACY_DONE_ALIAS:
            return "reviewed"
        if raw == "needs_human":
            return "[!] needs_human"
        if raw == "rejected_role_violation":
            return "[ERR] role_viol"
        return raw

    def _staleness(entry: dict) -> str:
        """Return age string + stale flag for spawned entries; empty otherwise."""
        if entry.get("status") != "spawned":
            return ""
        raw_ts = entry.get("spawned_at")
        if not raw_ts:
            return "!no-ts"
        try:
            spawned_dt = datetime.fromisoformat(raw_ts)
            age = now_utc - spawned_dt
            age_s = int(age.total_seconds())
            timeout = float(entry.get("timeout_sec", os.environ.get("EXECUTOR_TIMEOUT_SEC", "300")))
            if age_s > timeout + grace_sec:
                return f"!{age_s}s"
            return f"{age_s}s"
        except Exception:
            return "!bad-ts"

    mark = lambda b: "yes" if b else " - "
    print(f"{'ID':<6} {'status':<16} {'age':<8} {'att':<5} {'handout':<10} {'done':<8} {'feedback':<10} {'run-log':<10} {'spawned':<10}")
    print(f"{'-'*4:<6} {'-'*14:<16} {'-'*6:<8} {'-'*3:<5} {'-'*8:<10} {'-'*6:<8} {'-'*8:<10} {'-'*7:<10} {'-'*7:<10}")
    for hid in sorted(ids):
        handout = (handouts_dir / f"handout_{hid}.md").exists()
        done = (handouts_dir / f"handout_{hid}_done.md").exists()
        feedback = (effective_notes_dir / f"feedback_{hid}.md").exists()
        run_log = (effective_notes_dir / f"summoner_run_{hid}.md").exists()
        spawned_log = (effective_notes_dir / f"executor_spawn_{hid}.md").exists()
        entry = tracker_entries.get(hid, {})
        status = _display_status(entry)
        age_str = _staleness(entry)
        attempt = entry.get("attempt_count", "-")
        if age_str.startswith("!"):
            last_err = entry.get("last_error", "")
            stale_warnings.append(
                f"  {hid}  {status}  age={age_str[1:]}  attempts={attempt}"
                + (f"  last_error={last_err[:60]}" if last_err else "")
            )
        print(f"{hid:<6} {status:<16} {age_str:<8} {str(attempt):<5} {mark(handout):<10} {mark(done):<8} {mark(feedback):<10} {mark(run_log):<10} {mark(spawned_log):<10}")

    print(f"\nhandouts/  -> {handouts_dir}")
    print(f"notes/     -> {effective_notes_dir}")

    if stale_warnings:
        print("\n! STALE (spawned longer than timeout + 60s grace):")
        for w in stale_warnings:
            print(w)

    if manager_dir is not None:
        tracker_path = manager_dir / "tracker.json"
        if tracker_path.exists():
            data = load_tracker(manager_dir)
            entries = data.get("handouts", [])
            by_state: dict[str, int] = {}
            for e in entries:
                s = e.get("status", "unknown")
                if s == _LEGACY_DONE_ALIAS:
                    s = "reviewed"
                by_state[s] = by_state.get(s, 0) + 1
            summary = ", ".join(f"{c} {s}" for s, c in sorted(by_state.items()))
            print(f"\ntracker:   -> {tracker_path}")
            print(f"  {summary}, {len(entries)} total")
        else:
            print(f"\ntracker: not found at {tracker_path}")
    return 0


def cmd_mark(
    manager_dir: Path,
    raw_id: str,
    status: str,
) -> int:
    """Manually set a handout's tracker status (e.g. needs_fix, superseded)."""
    try:
        handout_id = _normalize_id(raw_id)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if status not in VALID_STATES:
        print(
            f"error: invalid status {status!r}; valid: {sorted(VALID_STATES)}",
            file=sys.stderr,
        )
        return 2
    update_tracker_status(manager_dir, handout_id, status)
    print(f"handout {handout_id} marked as {status!r}")
    return 0


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    """Run a git command, raising CalledProcessError on non-zero exit."""
    return subprocess.run(
        ["git"] + args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
    )


def _worktree_path(repo_root: Path, handout_id: str) -> Path:
    return repo_root / ".summoner" / "worktrees" / f"handout-{handout_id}"


def _worktree_branch(handout_id: str) -> str:
    return f"summoner/handout-{handout_id}"


# Files the executor must never modify — checked during scope validation.
_FORBIDDEN_PATHS = frozenset({
    "plan.md",
    "docs/manager/tracker.json",
})
_FORBIDDEN_GLOBS = (
    "docs/manager/notes/",
    "docs/manager/workspace/handouts/handout_",
)


def spawn_executor_in_worktree(
    repo_root: Path,
    project_root: Path,
    handout_id: str,
    notes_dir: Optional[Path] = None,
    manager_dir: Optional[Path] = None,
) -> SpawnResult:
    """Spawn an executor in a dedicated git worktree with scope validation on merge.

    Workflow:
    1. Create worktree at .summoner/worktrees/handout-NNN on branch summoner/handout-NNN
    2. Run spawn_executor() with --project-dir pointing at the worktree
    3. Validate changed files against handout scope (no forbidden files)
    4. Merge back to current branch with --no-ff
    5. Remove worktree
    6. On scope violation or merge conflict: mark needs_fix, clean up worktree

    Set SUMMONER_WORKTREE=1 to enable; disabled by default to preserve existing behaviour.
    """
    effective_notes_dir = _resolve_notes_dir(notes_dir, manager_dir)
    wt_path = _worktree_path(repo_root, handout_id)
    branch = _worktree_branch(handout_id)
    main_branch = "main"

    # Resolve handout source path in the main working tree
    handout_src = project_root / "handouts" / f"handout_{handout_id}.md"
    if not handout_src.exists():
        return SpawnResult(
            handout_id=handout_id,
            executor_model="none",
            spawned=False,
            fallback_reason=f"handout_{handout_id}.md not found",
        )

    try:
        # 1. Ensure branch doesn't already exist (cleanup from aborted run)
        try:
            _git(["branch", "-D", branch], repo_root)
        except subprocess.CalledProcessError:
            pass  # branch didn't exist, fine

        # 2. Create worktree
        wt_path.parent.mkdir(parents=True, exist_ok=True)
        _git(["worktree", "add", str(wt_path), "-b", branch, main_branch], repo_root)

        # 3. Copy handout file into the worktree's handouts directory so spawn_executor
        #    can find it (the workspace handouts/ may live outside the worktree root).
        wt_handouts = wt_path / "docs" / "manager" / "workspace" / "handouts"
        wt_handouts.mkdir(parents=True, exist_ok=True)
        (wt_handouts / f"handout_{handout_id}.md").write_text(
            handout_src.read_text(encoding="utf-8"), encoding="utf-8"
        )

        # 4. Run the executor inside the worktree
        wt_project_root = wt_path / project_root.relative_to(repo_root)
        result = spawn_executor(
            wt_project_root,
            handout_id,
            notes_dir=effective_notes_dir,
            manager_dir=manager_dir,
        )

        if not result.spawned:
            return result

        # 5. Scope validation: check what was changed
        status_out = _git(["status", "--porcelain"], wt_path).stdout
        changed_files: list[str] = []
        for line in status_out.splitlines():
            if len(line) > 3:
                changed_files.append(line[3:].strip())

        violations: list[str] = []
        for f in changed_files:
            # Reject known forbidden paths and directory prefixes
            if f in _FORBIDDEN_PATHS:
                violations.append(f"{f} (forbidden file)")
                continue
            for prefix in _FORBIDDEN_GLOBS:
                if f.startswith(prefix) and f"handout_{handout_id}" not in f:
                    violations.append(f"{f} (outside handout scope)")

        if violations:
            msg = "scope violation: " + "; ".join(violations[:3])
            if manager_dir is not None:
                _update_tracker_fields(manager_dir, handout_id, status="needs_fix", last_error=msg)
            result.fallback_reason = msg
            print(f"  [worktree] scope violation in handout {handout_id}: {msg}")
            _git(["worktree", "remove", "--force", str(wt_path)], repo_root)
            try:
                _git(["branch", "-D", branch], repo_root)
            except subprocess.CalledProcessError:
                pass
            return result

        # 6. Commit any uncommitted changes in the worktree
        try:
            _git(["add", "-A"], wt_path)
            _git(["commit", "--allow-empty", "-m",
                  f"chore: executor work for handout {handout_id}"], wt_path)
        except subprocess.CalledProcessError:
            pass  # nothing to commit, that's fine

        # 7. Merge back to main branch
        try:
            _git(["checkout", main_branch], repo_root)
            _git(["merge", "--no-ff", branch,
                  "-m", f"merge: executor handout {handout_id} from {branch}"], repo_root)
        except subprocess.CalledProcessError as exc:
            msg = f"merge conflict on {branch}: {exc.stderr[:200]}"
            if manager_dir is not None:
                _update_tracker_fields(manager_dir, handout_id, status="needs_fix", last_error=msg)
            result.fallback_reason = msg
            print(f"  [worktree] merge conflict — cleaning up worktree at {wt_path}")
            # Abort the merge so the main branch stays clean
            try:
                _git(["merge", "--abort"], repo_root)
            except subprocess.CalledProcessError:
                pass
            _git(["worktree", "remove", "--force", str(wt_path)], repo_root)
            try:
                _git(["branch", "-D", branch], repo_root)
            except subprocess.CalledProcessError:
                pass
            return result

        # 8. Clean up worktree on success
        _git(["worktree", "remove", "--force", str(wt_path)], repo_root)
        try:
            _git(["branch", "-D", branch], repo_root)
        except subprocess.CalledProcessError:
            pass  # branch already gone, fine
        print(f"  [worktree] merged and cleaned up (branch {branch})")
        return result

    except subprocess.CalledProcessError as exc:
        msg = f"git error: {exc.stderr[:200] if exc.stderr else str(exc)}"
        if manager_dir is not None:
            _update_tracker_fields(manager_dir, handout_id, status="failed", last_error=msg)
        return SpawnResult(
            handout_id=handout_id,
            executor_model="none",
            spawned=False,
            fallback_reason=msg,
        )


CORRECTIVE_HANDOUT_TEMPLATE = """## Original Plan
{plan}

## Failed Handout (ID {handout_id})
{handout}

## Previous Done Report
{done}

## Manager Feedback on What Failed
{feedback}

## Failure Details
- status: {status}
- attempt_count: {attempt_count}
- last_error: {last_error}

## Your Job
Write a corrective handout that addresses the failure above.
The corrective handout must explain: why the previous attempt failed, what must
be done differently, and what concrete verification steps confirm success.
Use the same format as a normal handout:

# Handout {next_id} - <descriptive title>

## Scope
One paragraph explaining what went wrong and what this handout fixes.

## Tasks
Numbered list, 3-5 items. Each item directly addresses a gap from the failure.

## Out of scope
What this handout explicitly does NOT cover.

## Done when
Bulleted acceptance criteria. Each criterion is checkable without human judgment.
"""

_RETRYABLE_STATES = frozenset({"failed", "timed_out", "needs_fix", "rejected_role_violation"})
DEFAULT_MAX_ATTEMPTS = 3


def cmd_retry(
    root: Path,
    raw_id: str,
    manager_dir: Path,
    notes_dir: Optional[Path] = None,
    index_path: Optional[Path] = None,
    corrective: bool = False,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> int:
    """Reset a failed/timed_out/needs_fix handout and re-spawn (or generate corrective brief).

    Default (no --corrective): resets status to pending and re-spawns the same handout.
    With --corrective: generates a new handout brief that explains the failure and
    asks the executor to address it. Use this for needs_fix where the brief itself
    needs to change, not just the executor's execution.
    """
    try:
        handout_id = _normalize_id(raw_id)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    data = load_tracker(manager_dir)
    entry = next((e for e in data.get("handouts", []) if e.get("id") == handout_id), None)
    if entry is None:
        print(f"error: no tracker entry for handout {handout_id}", file=sys.stderr)
        return 1

    status = entry.get("status", "")

    # needs_human requires a human decision before any retry — do not auto-retry.
    if status == "needs_human":
        print(
            f"error: handout {handout_id} has status 'needs_human'. "
            "This state requires a human decision before retrying. "
            "Review the feedback, resolve the blocker, then --mark it needs_fix or pending.",
            file=sys.stderr,
        )
        return 1

    if status not in _RETRYABLE_STATES:
        print(
            f"error: handout {handout_id} has status {status!r}; "
            f"only {sorted(_RETRYABLE_STATES)} can be retried.",
            file=sys.stderr,
        )
        return 1

    # rejected_role_violation can be retried but warrants explicit review first.
    if status == "rejected_role_violation":
        print(
            f"WARNING: handout {handout_id} has status 'rejected_role_violation'. "
            "The worker previously violated forbidden paths or actions. "
            "Ensure the handout scope is clear before re-spawning.",
            file=sys.stderr,
        )

    attempt_count = int(entry.get("attempt_count", 0))
    if attempt_count >= max_attempts:
        print(
            f"error: handout {handout_id} has reached {max_attempts} attempt(s). "
            f"Use --max-attempts to increase the limit, or --mark it superseded.",
            file=sys.stderr,
        )
        return 1

    effective_notes_dir = _resolve_notes_dir(notes_dir, manager_dir)

    if corrective:
        return _cmd_retry_corrective(
            root, handout_id, manager_dir, effective_notes_dir, index_path, entry
        )

    # Plain retry: reset to pending, clear failure fields, re-spawn same handout.
    _update_tracker_fields(
        manager_dir, handout_id,
        status="pending",
        last_error=None,
        exit_code=None,
        finished_at=None,
    )
    print(f"handout {handout_id} reset to pending (was {status!r}, attempt {attempt_count}/{max_attempts})")
    return cmd_spawn(root, handout_id, manager_dir=manager_dir, notes_dir=effective_notes_dir)


def _cmd_retry_corrective(
    root: Path,
    handout_id: str,
    manager_dir: Path,
    notes_dir: Path,
    index_path: Optional[Path],
    entry: dict,
) -> int:
    """Generate a new corrective handout brief that addresses the failure."""
    plan_path = root / "plan.md"
    handout_path = root / "handouts" / f"handout_{handout_id}.md"
    done_path = root / "handouts" / f"handout_{handout_id}_done.md"
    feedback_path = notes_dir / f"feedback_{handout_id}.md"

    if not plan_path.exists():
        print(f"error: missing {plan_path}", file=sys.stderr)
        return 1
    if not handout_path.exists():
        print(f"error: missing {handout_path}", file=sys.stderr)
        return 1

    plan = plan_path.read_text(encoding="utf-8")
    handout = handout_path.read_text(encoding="utf-8")
    done = done_path.read_text(encoding="utf-8") if done_path.exists() else "(no done file written)"
    feedback = feedback_path.read_text(encoding="utf-8") if feedback_path.exists() else "(no feedback written)"

    # Generate next sequential ID for the corrective brief.
    data = load_tracker(manager_dir)
    existing_ids = {int(e.get("id", "0")) for e in data.get("handouts", [])}
    next_num = max(existing_ids, default=0) + 1
    next_id = str(next_num).zfill(3)
    next_path = root / "handouts" / f"handout_{next_id}.md"

    prompt = CORRECTIVE_HANDOUT_TEMPLATE.format(
        plan=plan,
        handout_id=handout_id,
        handout=handout,
        done=done,
        feedback=feedback,
        status=entry.get("status", "unknown"),
        attempt_count=entry.get("attempt_count", "?"),
        last_error=entry.get("last_error") or "(none recorded)",
        next_id=next_id,
    )
    response = call_manager_model(prompt)
    body = response.body.strip()
    if not body.startswith("#"):
        for i, line in enumerate(body.splitlines()):
            if line.startswith(f"# Handout {next_id}"):
                body = "\n".join(body.splitlines()[i:])
                break

    # Write to temp first, run preflight
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md",
                                      delete=False, encoding="utf-8") as tf:
        tf.write(body + "\n")
        tmp_path = Path(tf.name)

    warnings = preflight_handout(tmp_path, _REPO_ROOT)
    tmp_path.unlink(missing_ok=True)

    if warnings:
        print(f"[summoner] corrective handout has {len(warnings)} preflight warning(s):")
        for w in warnings:
            print(f"  - {w}")
        print("[summoner] falling back to plain retry (corrective handout rejected)")
        update_tracker_status(manager_dir, handout_id, "needs_fix")
        return 0

    next_path.write_text(body + "\n", encoding="utf-8")

    title = _extract_title(next_path)
    today = datetime.now(timezone.utc).date().isoformat()
    with _tracker_lock(manager_dir):
        data = load_tracker(manager_dir)
        # Mark the failed handout as superseded by the corrective one.
        for e in data.get("handouts", []):
            if e.get("id") == handout_id:
                e["status"] = "superseded"
                e["superseded_by"] = next_id
                break
        data.setdefault("handouts", []).append({
            "id": next_id,
            "title": title,
            "status": "pending",
            "created": today,
            "corrective_for": handout_id,
        })
        save_tracker(manager_dir, data)

    print(f"generated corrective handout_{next_id}.md (supersedes {handout_id})")
    return 0


def cmd_retry_failed(
    root: Path,
    manager_dir: Path,
    notes_dir: Optional[Path] = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> int:
    """Reset and re-spawn all failed/timed_out entries under the attempt cap."""
    data = load_tracker(manager_dir)
    candidates = [
        e for e in data.get("handouts", [])
        if e.get("status") in ("failed", "timed_out")
        and int(e.get("attempt_count", 0)) < max_attempts
    ]
    if not candidates:
        print("no retryable entries (failed/timed_out under attempt cap)")
        return 0
    rc = 0
    for entry in candidates:
        hid = entry.get("id", "?")
        print(f"retrying handout {hid}…")
        result = cmd_retry(root, hid, manager_dir, notes_dir=notes_dir, max_attempts=max_attempts)
        if result != 0:
            rc = result
    return rc


def cmd_refresh_guide(
    manager_dir: Path,
    notes_dir: Path,
    root: Path,
) -> int:
    """Generate guide/status.generated.md from tracker state and feedback files.

    Human-owned project guide files (docs/manager/project_guide/) are never touched.
    Run after completing a batch of handouts to refresh Summoner self-tracking state.
    The project guide (project_charter.md, current_goal.md, etc.) must be maintained
    separately — it describes the managed project, not the Summoner tool.
    """
    status_path = refresh_manager_guide(manager_dir, notes_dir, root)
    print(f"wrote {status_path}")
    project_guide_dir = manager_dir / _PROJECT_GUIDE_DIR
    for fname in sorted(_GUIDE_HUMAN_OWNED):
        path = project_guide_dir / fname
        if not path.exists():
            print(f"  NOTE: project_guide/{fname} is absent — create in {project_guide_dir}")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="summoner",
        description="Manager Summoner - generate feedback files from handout/done pairs.",
    )
    parser.add_argument(
        "--project-dir",
        default=None,
        help=f"project root containing plan.md and handouts/ (default: {DEFAULT_PROJECT_DIR})",
    )
    parser.add_argument(
        "--manager-dir",
        default=None,
        help=f"path to manager tracker directory (default: {DEFAULT_MANAGER_DIR})",
    )
    parser.add_argument(
        "--notes-dir",
        default=None,
        help=f"path for summoner output (feedback, run logs) — defaults to <manager-dir>/notes",
    )
    parser.add_argument(
        "--index-path",
        default=None,
        help=f"path to repo-index JSON for next-handout generation (default: {DEFAULT_INDEX_PATH})",
    )
    parser.add_argument(
        "--auto-spawn",
        action="store_true",
        help="(--watch only) spawn executor on each newly generated handout",
    )
    parser.add_argument(
        "--no-auto-next",
        action="store_true",
        help="(--run only) write feedback but do not generate the next handout; use --next NNN after reviewing",
    )
    parser.add_argument(
        "--corrective",
        action="store_true",
        help="(--retry only) generate a new corrective handout instead of re-running the same one",
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=DEFAULT_MAX_ATTEMPTS,
        help=f"(--retry/--retry-failed) max spawn attempts before blocking retry (default: {DEFAULT_MAX_ATTEMPTS})",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--init", action="store_true", help="generate .agents/ folder")
    group.add_argument("--watch", action="store_true", help="poll for new *_done.md files")
    group.add_argument("--run", metavar="NNN", help="process a single handout (feedback only by default; use --no-auto-next to defer next-handout generation)")
    group.add_argument("--next", metavar="NNN", help="generate next handout from an already-reviewed handout (use after --run --no-auto-next)")
    group.add_argument("--spawn", metavar="NNN", help="spawn executor agent on handout NNN")
    group.add_argument("--preflight", metavar="NNN", help="check handout NNN for structural problems before spawning")
    group.add_argument("--check", action="store_true", help="print status table")
    group.add_argument(
        "--mark",
        nargs=2,
        metavar=("NNN", "STATUS"),
        help=f"manually set tracker status for handout NNN; valid: {sorted(VALID_STATES)}",
    )
    group.add_argument(
        "--retry",
        metavar="NNN",
        help="reset a failed/timed_out/needs_fix handout to pending and re-spawn it",
    )
    group.add_argument(
        "--retry-failed",
        action="store_true",
        help="reset and re-spawn all failed/timed_out entries under --max-attempts",
    )
    group.add_argument(
        "--refresh-guide",
        action="store_true",
        help="regenerate guide/status.generated.md from tracker + feedback; does not touch human-owned guide files",
    )
    group.add_argument(
        "--map",
        metavar="TASK_JSON",
        help="dispatch a mapper agent from a task JSON file; mapper reads source and writes a project guide artifact (read-only, no code changes)",
    )
    group.add_argument(
        "--research",
        metavar="TASK_JSON",
        help="dispatch a researcher agent from a task JSON file; researcher answers bounded questions and writes a findings artifact (read-only, no code changes)",
    )
    group.add_argument(
        "--review",
        metavar="TASK_JSON",
        help="dispatch a reviewer agent from a task JSON file; reviewer checks coder output against handout acceptance criteria and writes a verdict artifact (read-only, no code changes)",
    )
    args = parser.parse_args(argv)
    root = Path(args.project_dir).resolve() if args.project_dir else DEFAULT_PROJECT_DIR
    manager_dir = Path(args.manager_dir).resolve() if args.manager_dir else DEFAULT_MANAGER_DIR
    notes_dir = Path(args.notes_dir).resolve() if args.notes_dir else manager_dir / "notes"
    index_path = Path(args.index_path).resolve() if args.index_path else DEFAULT_INDEX_PATH
    if args.init:
        return cmd_init(root)
    if args.watch:
        return cmd_watch(root, manager_dir=manager_dir, index_path=index_path, notes_dir=notes_dir, auto_spawn=args.auto_spawn)
    if args.run:
        return cmd_run(
            root, args.run,
            manager_dir=manager_dir, index_path=index_path, notes_dir=notes_dir,
            auto_next=not args.no_auto_next,
        )
    if args.next:
        return cmd_next(root, args.next, manager_dir=manager_dir, index_path=index_path, notes_dir=notes_dir)
    if args.spawn:
        return cmd_spawn(root, args.spawn, manager_dir=manager_dir, notes_dir=notes_dir)
    if args.preflight:
        return cmd_preflight(root, args.preflight)
    if args.check:
        return cmd_check(root, manager_dir=manager_dir, notes_dir=notes_dir)
    if args.mark:
        return cmd_mark(manager_dir, args.mark[0], args.mark[1])
    if args.retry:
        return cmd_retry(
            root, args.retry, manager_dir,
            notes_dir=notes_dir, index_path=index_path,
            corrective=args.corrective, max_attempts=args.max_attempts,
        )
    if args.retry_failed:
        return cmd_retry_failed(root, manager_dir, notes_dir=notes_dir, max_attempts=args.max_attempts)
    if args.refresh_guide:
        return cmd_refresh_guide(manager_dir, notes_dir, root)
    if args.map:
        return cmd_map(args.map, repo_root=_REPO_ROOT, notes_dir=notes_dir)
    if args.research:
        return cmd_research(args.research, repo_root=_REPO_ROOT, notes_dir=notes_dir)
    if args.review:
        return cmd_review(args.review, repo_root=_REPO_ROOT, notes_dir=notes_dir)
    return 1


if __name__ == "__main__":
    sys.exit(main())
