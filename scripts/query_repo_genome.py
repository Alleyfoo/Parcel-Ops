#!/usr/bin/env python3
"""Query the generated repo-genome agent map.

Outputs JSON by default. The result is agent startup context, not canonical truth.
Verify source/tests/schemas before editing.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
AGENT_MAP_PATH = ROOT / "repo_genome" / "generated" / "agent_map.json"
INTENT_STOPWORDS = {
    "add",
    "agent",
    "agents",
    "and",
    "before",
    "builder",
    "change",
    "code",
    "edit",
    "eval",
    "evaluation",
    "fix",
    "genome",
    "harden",
    "layer",
    "make",
    "modify",
    "query",
    "repo",
    "review",
    "script",
    "scripts",
    "test",
    "tests",
    "update",
    "use",
    "work",
}
UNIT_INTENT_TOKEN_ALLOWLIST = {
    "repo_genome.maintenance": {
        "builder",
        "genome",
        "query",
        "repo",
        "test",
        "tests",
    },
}


def load_agent_map() -> dict[str, Any]:
    if not AGENT_MAP_PATH.exists():
        rel = AGENT_MAP_PATH.relative_to(ROOT).as_posix()
        print(f"error: {rel} not found. Run scripts/build_repo_genome.py --write first.", file=sys.stderr)
        sys.exit(1)
    try:
        payload = json.loads(AGENT_MAP_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"error: {AGENT_MAP_PATH} is invalid JSON: {exc}", file=sys.stderr)
        sys.exit(1)
    if not isinstance(payload, dict) or not isinstance(payload.get("units"), dict):
        print(f"error: {AGENT_MAP_PATH} is not a repo-genome agent map", file=sys.stderr)
        sys.exit(1)
    return payload


def _strings(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (int, float, bool)):
        return [str(value)]
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            out.extend(_strings(item))
        return out
    if isinstance(value, dict):
        out: list[str] = []
        for key, item in value.items():
            out.extend(_strings(key))
            out.extend(_strings(item))
        return out
    return [str(value)]


def searchable(unit: dict[str, Any]) -> str:
    fields: list[str] = []
    for key in [
        "unit_id",
        "domain",
        "layer",
        "owner",
        "stability",
        "maturity",
        "summary",
        "entrypoints",
        "primary_files",
        "supporting_files",
        "tests",
        "validation_commands",
        "risk_tags",
        "artifacts",
        "doc_refs",
        "dependencies",
        "relationships",
    ]:
        fields.extend(_strings(unit.get(key)))
    return " ".join(fields).lower()


def intent_searchable(unit: dict[str, Any]) -> str:
    fields: list[str] = []
    for key in [
        "unit_id",
        "domain",
        "layer",
        "owner",
        "summary",
        "entrypoints",
        "primary_files",
        "tests",
        "validation_commands",
        "risk_tags",
    ]:
        fields.extend(_strings(unit.get(key)))
    return " ".join(fields).lower()


def intent_tokens(intent: str, *, unit_id: str | None = None) -> list[str]:
    allowlist = UNIT_INTENT_TOKEN_ALLOWLIST.get(unit_id or "", set())
    tokens = []
    for raw in intent.replace("_", " ").replace("-", " ").replace(".", " ").split():
        token = raw.strip().lower()
        if len(token) >= 3 and (token not in INTENT_STOPWORDS or token in allowlist):
            tokens.append(token)
    return tokens


def score_unit(unit: dict[str, Any], args: argparse.Namespace) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    text = searchable(unit)

    if args.unit:
        if unit.get("unit_id") == args.unit:
            score += 100
            reasons.append("unit_id exact match")
        else:
            return 0, []

    if args.file:
        query = args.file.lower().replace("\\", "/")
        paths = [
            *_strings(unit.get("primary_files")),
            *_strings(unit.get("supporting_files")),
            *_strings(unit.get("tests")),
            *_strings(unit.get("doc_refs")),
            *_strings(unit.get("artifacts")),
        ]
        if any(query in path.lower().replace("\\", "/") for path in paths):
            score += 60
            reasons.append("file path match")
        elif args.unit is None:
            return 0, []

    if args.symbol:
        query = args.symbol.lower()
        entrypoints = unit.get("entrypoints", [])
        if any(query in str(item.get("symbol", "")).lower() for item in entrypoints if isinstance(item, dict)):
            score += 70
            reasons.append("entrypoint symbol match")
        elif query in text:
            score += 20
            reasons.append("text symbol match")
        elif args.unit is None:
            return 0, []

    if args.intent:
        tokens = intent_tokens(args.intent, unit_id=str(unit.get("unit_id", "")))
        intent_text = intent_searchable(unit)
        matches = [token for token in tokens if token in intent_text]
        if matches:
            score += len(matches) * 12
            if len(matches) == len(tokens):
                score += 20
            reasons.append("intent tokens: " + ", ".join(matches))
        elif args.unit is None:
            return 0, []

    if not any([args.unit, args.file, args.symbol, args.intent]):
        score = 1
        reasons.append("listed")

    return score, reasons


def context_for(unit: dict[str, Any], score: int, reasons: list[str]) -> dict[str, Any]:
    return {
        "unit_id": unit["unit_id"],
        "score": score,
        "match_reasons": reasons,
        "summary": unit["summary"],
        "domain": unit["domain"],
        "layer": unit["layer"],
        "owner": unit["owner"],
        "stability": unit["stability"],
        "maturity": unit["maturity"],
        "entrypoints": unit["entrypoints"],
        "primary_files": unit["primary_files"],
        "supporting_files": unit.get("supporting_files", []),
        "tests": unit["tests"],
        "validation_commands": unit["validation_commands"],
        "risk_tags": unit["risk_tags"],
        "doc_refs": unit.get("doc_refs", []),
        "relationships": unit.get("relationships", []),
        "pre_edit_checklist": unit.get("pre_edit_checklist", []),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--unit", help="exact unit id")
    parser.add_argument("--file", help="path substring")
    parser.add_argument("--symbol", help="entrypoint symbol or text substring")
    parser.add_argument("--intent", help="natural-language task intent")
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()

    payload = load_agent_map()
    results: list[dict[str, Any]] = []
    for unit in payload["units"].values():
        score, reasons = score_unit(unit, args)
        if score > 0:
            results.append(context_for(unit, score, reasons))
    results.sort(key=lambda item: (-item["score"], item["unit_id"]))
    results = results[: max(0, args.limit)]

    output = {
        "kind": "repo_genome_query_result",
        "truth_hierarchy": payload.get("truth_hierarchy", []),
        "query": {
            "unit": args.unit,
            "file": args.file,
            "symbol": args.symbol,
            "intent": args.intent,
        },
        "candidate_units": results,
    }
    print(json.dumps(output, indent=2, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
