#!/usr/bin/env python3
"""Build and validate the generated repo-genome indexes.

The repo genome is a small compiled architectural memory layer:
manual unit YAML declares intent, generated JSON/Markdown provides deterministic
lookup surfaces for agents. No embeddings, no call graph, no LLM calls.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - dependency is declared in pyproject.
    yaml = None


ROOT = Path(__file__).resolve().parents[1]
UNIT_DIR = ROOT / "repo_genome" / "units"
GENERATED_DIR = ROOT / "repo_genome" / "generated"
DOCS_DIR = ROOT / "docs" / "generated" / "repo_genome"

REQUIRED_FIELDS = {
    "unit_id",
    "domain",
    "layer",
    "owner",
    "stability",
    "maturity",
    "summary",
    "entrypoints",
    "primary_files",
    "tests",
    "validation_commands",
    "risk_tags",
}
OPTIONAL_FIELDS = {
    "supporting_files",
    "artifacts",
    "dependencies",
    "doc_refs",
}
PATH_FIELDS = {
    "primary_files",
    "tests",
    "supporting_files",
    "artifacts",
    "doc_refs",
}
TRUTH_HIERARCHY = [
    "source/tests/schemas",
    "repo_genome map",
    "generated docs",
    "manual docs",
    "historical memory",
]


@dataclass(frozen=True)
class BuildResult:
    files: dict[Path, str]
    errors: list[str]


def rel(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def json_text(payload: Any) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def short_hash(path: Path) -> str:
    return sha256_file(path)[:16]


def load_unit_yaml(path: Path) -> dict[str, Any]:
    if yaml is None:
        raise RuntimeError("PyYAML is required. Install project dependencies first.")
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{rel(path)} must contain a YAML mapping")
    return payload


def _list_field(unit: dict[str, Any], key: str) -> list[Any]:
    value = unit.get(key, [])
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{unit.get('unit_id', '<unknown>')}.{key} must be a list")
    return value


def _string_list(unit: dict[str, Any], key: str) -> list[str]:
    values = _list_field(unit, key)
    bad = [value for value in values if not isinstance(value, str)]
    if bad:
        raise ValueError(f"{unit.get('unit_id', '<unknown>')}.{key} must contain strings")
    return values


def _entrypoints(unit: dict[str, Any]) -> list[dict[str, str]]:
    values = _list_field(unit, "entrypoints")
    out: list[dict[str, str]] = []
    for item in values:
        if not isinstance(item, dict):
            raise ValueError(f"{unit.get('unit_id', '<unknown>')}.entrypoints entries must be mappings")
        path = item.get("path")
        symbol = item.get("symbol")
        if not isinstance(path, str) or not isinstance(symbol, str):
            raise ValueError(
                f"{unit.get('unit_id', '<unknown>')}.entrypoints entries require path and symbol"
            )
        out.append({"path": path, "symbol": symbol})
    return out


def validate_unit(unit: dict[str, Any], source_path: Path) -> list[str]:
    errors: list[str] = []
    missing = sorted(REQUIRED_FIELDS - set(unit))
    if missing:
        errors.append(f"{rel(source_path)} missing required fields: {', '.join(missing)}")

    allowed = REQUIRED_FIELDS | OPTIONAL_FIELDS
    unknown = sorted(set(unit) - allowed)
    if unknown:
        errors.append(f"{rel(source_path)} has unknown fields: {', '.join(unknown)}")

    unit_id = unit.get("unit_id")
    if not isinstance(unit_id, str) or not unit_id:
        errors.append(f"{rel(source_path)} unit_id must be a non-empty string")

    for key in sorted(REQUIRED_FIELDS - {"entrypoints"}):
        if key not in unit:
            continue
        if key in {"primary_files", "tests", "validation_commands", "risk_tags"}:
            try:
                _string_list(unit, key)
            except ValueError as exc:
                errors.append(str(exc))
        elif not isinstance(unit.get(key), str):
            errors.append(f"{unit_id}.{key} must be a string")

    for key in sorted(OPTIONAL_FIELDS):
        if key in unit:
            try:
                _string_list(unit, key)
            except ValueError as exc:
                errors.append(str(exc))

    if "entrypoints" in unit:
        try:
            _entrypoints(unit)
        except ValueError as exc:
            errors.append(str(exc))

    return errors


def verify_referenced_paths(unit: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    unit_id = str(unit.get("unit_id", "<unknown>"))
    for entrypoint in _entrypoints(unit):
        path = ROOT / entrypoint["path"]
        if not path.exists():
            errors.append(f"{unit_id} entrypoint path missing: {entrypoint['path']}")
    for field in sorted(PATH_FIELDS):
        for raw_path in _string_list(unit, field):
            path = ROOT / raw_path
            if not path.exists():
                errors.append(f"{unit_id} {field} path missing: {raw_path}")
    return errors


def _relationship(target: str, relation: str, provenance: str) -> dict[str, str]:
    return {
        "target": target,
        "relation": relation,
        "provenance": provenance,
    }


def _path_hash(raw_path: str) -> str | None:
    path = ROOT / raw_path
    generated_roots = (GENERATED_DIR, DOCS_DIR)
    if any(path == root or root in path.parents for root in generated_roots):
        return None
    if path.is_file():
        return short_hash(path)
    return None


def _unit_tokens(unit: dict[str, Any]) -> set[str]:
    pieces = [str(unit.get("unit_id", "")), str(unit.get("summary", ""))]
    pieces.extend(_string_list(unit, "risk_tags"))
    tokens: set[str] = set()
    for piece in pieces:
        for token in piece.replace("_", ".").replace("-", ".").split("."):
            token = token.strip().lower()
            if len(token) >= 4:
                tokens.add(token)
    return tokens


def infer_test_relationships(units: list[dict[str, Any]]) -> dict[str, list[dict[str, str]]]:
    tests_root = ROOT / "tests"
    if not tests_root.exists():
        return {}
    inferred: dict[str, list[dict[str, str]]] = {unit["unit_id"]: [] for unit in units}
    test_paths = sorted(tests_root.glob("**/test_*.py"))
    for unit in units:
        declared = set(_string_list(unit, "tests"))
        tokens: set[str] = set()
        for primary in _string_list(unit, "primary_files"):
            stem = Path(primary).stem.lower()
            if len(stem) >= 8:
                tokens.add(stem)
        for path in test_paths:
            rpath = rel(path)
            if rpath in declared:
                continue
            haystack = path.name.lower()
            if any(token in haystack for token in tokens):
                inferred[unit["unit_id"]].append(
                    _relationship(rpath, "tests", "inferred_test_name")
                )
    return inferred


def build_payloads(units: list[dict[str, Any]]) -> dict[str, Any]:
    unit_by_id = {unit["unit_id"]: unit for unit in units}
    inferred_tests = infer_test_relationships(units)

    file_index: dict[str, Any] = {
        "kind": "repo_genome_file_index",
        "schema_version": 1,
        "files": {},
    }
    test_index: dict[str, Any] = {
        "kind": "repo_genome_test_index",
        "schema_version": 1,
        "tests": {},
    }
    doc_status_index: dict[str, Any] = {
        "kind": "repo_genome_doc_status_index",
        "schema_version": 1,
        "docs": {},
    }
    unit_index: dict[str, Any] = {
        "kind": "repo_genome_unit_index",
        "schema_version": 1,
        "units": [],
    }
    agent_map: dict[str, Any] = {
        "kind": "repo_genome_agent_map",
        "schema_version": 1,
        "truth_hierarchy": TRUTH_HIERARCHY,
        "provenance_types": [
            "manual_unit_yaml",
            "inferred_path_scan",
            "inferred_test_name",
            "inferred_doc_ref",
        ],
        "units": {},
    }

    for unit_id in sorted(unit_by_id):
        unit = unit_by_id[unit_id]
        entrypoints = _entrypoints(unit)
        primary_files = _string_list(unit, "primary_files")
        supporting_files = _string_list(unit, "supporting_files")
        tests = _string_list(unit, "tests")
        artifacts = _string_list(unit, "artifacts")
        doc_refs = _string_list(unit, "doc_refs")
        dependencies = _string_list(unit, "dependencies")

        relationships: list[dict[str, str]] = []
        for entrypoint in entrypoints:
            relationships.append(
                _relationship(
                    f"{entrypoint['path']}::{entrypoint['symbol']}",
                    "entrypoint",
                    "manual_unit_yaml",
                )
            )
        for path in primary_files:
            relationships.append(_relationship(path, "primary_file", "manual_unit_yaml"))
        for path in supporting_files:
            relationships.append(_relationship(path, "supporting_file", "manual_unit_yaml"))
        for path in tests:
            relationships.append(_relationship(path, "test", "manual_unit_yaml"))
        relationships.extend(inferred_tests.get(unit_id, []))
        for path in artifacts:
            relationships.append(_relationship(path, "artifact", "manual_unit_yaml"))
        for path in doc_refs:
            relationships.append(_relationship(path, "doc_ref", "inferred_doc_ref"))
        for dep in dependencies:
            relationships.append(_relationship(dep, "depends_on", "manual_unit_yaml"))

        for path, relation, provenance in [
            *[(p, "primary_file", "manual_unit_yaml") for p in primary_files],
            *[(p, "supporting_file", "manual_unit_yaml") for p in supporting_files],
            *[(p, "artifact", "manual_unit_yaml") for p in artifacts],
        ]:
            file_index["files"].setdefault(path, {
                "path": path,
                "sha256": _path_hash(path),
                "units": [],
            })
            file_index["files"][path]["units"].append({
                "unit_id": unit_id,
                "relation": relation,
                "provenance": provenance,
            })

        for path in tests:
            test_index["tests"].setdefault(path, {
                "path": path,
                "sha256": _path_hash(path),
                "units": [],
            })
            test_index["tests"][path]["units"].append({
                "unit_id": unit_id,
                "relation": "test",
                "provenance": "manual_unit_yaml",
            })
        for reln in inferred_tests.get(unit_id, []):
            path = reln["target"]
            test_index["tests"].setdefault(path, {
                "path": path,
                "sha256": _path_hash(path),
                "units": [],
            })
            test_index["tests"][path]["units"].append({
                "unit_id": unit_id,
                "relation": "test",
                "provenance": "inferred_test_name",
            })

        for path in doc_refs:
            doc_status_index["docs"].setdefault(path, {
                "path": path,
                "exists": (ROOT / path).exists(),
                "sha256": _path_hash(path),
                "status_hint": "current" if (ROOT / path).exists() else "missing",
                "units": [],
            })
            doc_status_index["docs"][path]["units"].append({
                    "unit_id": unit_id,
                    "relation": "doc_ref",
                    "provenance": "inferred_doc_ref",
                })

        compact = {
            "unit_id": unit_id,
            "domain": unit["domain"],
            "layer": unit["layer"],
            "owner": unit["owner"],
            "stability": unit["stability"],
            "maturity": unit["maturity"],
            "summary": unit["summary"].strip(),
            "entrypoints": entrypoints,
            "primary_files": primary_files,
            "tests": tests,
            "validation_commands": _string_list(unit, "validation_commands"),
            "risk_tags": _string_list(unit, "risk_tags"),
            "supporting_files": supporting_files,
            "artifacts": artifacts,
            "doc_refs": doc_refs,
            "dependencies": dependencies,
            "relationships": sorted(relationships, key=lambda item: (item["relation"], item["target"])),
        }
        unit_index["units"].append(compact)
        agent_map["units"][unit_id] = {
            **compact,
            "pre_edit_checklist": [
                "Read primary_files and entrypoints before editing.",
                "Verify source/tests/schemas first; generated repo_genome data is a lookup aid.",
                "Run listed validation_commands or record why a command was skipped.",
                "Update manual unit YAML only for architectural intent changes.",
                "Run python scripts/build_repo_genome.py --write and --check after related changes.",
            ],
        }

    for payload in (file_index, test_index):
        key = "files" if "files" in payload else "tests"
        for item in payload[key].values():
            item["units"].sort(key=lambda value: (value["unit_id"], value["relation"], value["provenance"]))

    unit_index["units"].sort(key=lambda item: item["unit_id"])
    return {
        "agent_map.json": agent_map,
        "unit_index.json": unit_index,
        "file_index.json": file_index,
        "test_index.json": test_index,
        "doc_status_index.json": doc_status_index,
    }


def markdown_unit_page(unit: dict[str, Any]) -> str:
    def bullets(values: list[str]) -> str:
        return "\n".join(f"- `{value}`" for value in values) if values else "- (none)"

    lines = [
        "<!-- Generated by scripts/build_repo_genome.py. Do not edit directly. -->",
        f"# {unit['unit_id']}",
        "",
        "> Generated repo-genome view. Canonical architectural intent lives in `repo_genome/units/*.yaml`.",
        "",
        f"- Domain: `{unit['domain']}`",
        f"- Layer: `{unit['layer']}`",
        f"- Owner: `{unit['owner']}`",
        f"- Stability: `{unit['stability']}`",
        f"- Maturity: `{unit['maturity']}`",
        "",
        "## Summary",
        "",
        unit["summary"].strip(),
        "",
        "## Primary Files",
        "",
        bullets(unit["primary_files"]),
        "",
        "## Tests",
        "",
        bullets(unit["tests"]),
        "",
        "## Validation Commands",
        "",
        bullets(unit["validation_commands"]),
        "",
        "## Risk Tags",
        "",
        bullets(unit["risk_tags"]),
        "",
        "## Relationships",
        "",
    ]
    for reln in unit["relationships"]:
        lines.append(
            f"- `{reln['relation']}` -> `{reln['target']}` "
            f"(provenance: `{reln['provenance']}`)"
        )
    lines.append("")
    return "\n".join(lines)


def markdown_index(units: list[dict[str, Any]]) -> str:
    lines = [
        "<!-- Generated by scripts/build_repo_genome.py. Do not edit directly. -->",
        "# Repo Genome",
        "",
        "Generated view over `repo_genome/units/*.yaml`.",
        "",
        "Truth hierarchy:",
        "",
    ]
    lines.extend(f"{idx}. {item}" for idx, item in enumerate(TRUTH_HIERARCHY, start=1))
    lines.extend(["", "## Units", ""])
    for unit in units:
        lines.append(
            f"- [{unit['unit_id']}]({unit['unit_id']}.md) "
            f"- `{unit['domain']}` / `{unit['layer']}`"
        )
    lines.append("")
    return "\n".join(lines)


def build() -> BuildResult:
    errors: list[str] = []
    units: list[dict[str, Any]] = []
    if not UNIT_DIR.exists():
        errors.append(f"unit directory missing: {rel(UNIT_DIR)}")
        return BuildResult(files={}, errors=errors)

    for path in sorted(UNIT_DIR.glob("*.yaml")):
        try:
            unit = load_unit_yaml(path)
            unit_errors = validate_unit(unit, path)
            errors.extend(unit_errors)
            if not unit_errors:
                errors.extend(verify_referenced_paths(unit))
            units.append(unit)
        except (OSError, RuntimeError, ValueError) as exc:
            errors.append(f"{rel(path)}: {exc}")

    seen: set[str] = set()
    for unit in units:
        unit_id = unit.get("unit_id")
        if isinstance(unit_id, str):
            if unit_id in seen:
                errors.append(f"duplicate unit_id: {unit_id}")
            seen.add(unit_id)
    valid_ids = seen
    for unit in units:
        for dep in _string_list(unit, "dependencies"):
            if dep not in valid_ids:
                errors.append(f"{unit.get('unit_id')} depends on unknown unit: {dep}")

    if errors:
        return BuildResult(files={}, errors=errors)

    payloads = build_payloads(units)
    files: dict[Path, str] = {}
    for name, payload in payloads.items():
        files[GENERATED_DIR / name] = json_text(payload)

    compiled_units = payloads["unit_index.json"]["units"]
    files[DOCS_DIR / "index.md"] = markdown_index(compiled_units)
    for unit in compiled_units:
        files[DOCS_DIR / f"{unit['unit_id']}.md"] = markdown_unit_page(unit)

    return BuildResult(files=files, errors=[])


def write_files(files: dict[Path, str]) -> None:
    for path in sorted(files):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(files[path], encoding="utf-8")


def unit_count(files: dict[Path, str]) -> int:
    agent_map = json.loads(files[GENERATED_DIR / "agent_map.json"])
    units = agent_map.get("units", {})
    return len(units) if isinstance(units, dict) else 0


def check_files(files: dict[Path, str]) -> list[str]:
    errors: list[str] = []
    expected_paths = set(files)
    for directory in (GENERATED_DIR, DOCS_DIR):
        if directory.exists():
            for path in directory.glob("*"):
                if path.is_file() and path not in expected_paths:
                    errors.append(f"stale generated file should be removed: {rel(path)}")
    for path, expected in sorted(files.items()):
        if not path.exists():
            errors.append(f"generated file missing: {rel(path)}")
            continue
        current = path.read_text(encoding="utf-8")
        if current != expected:
            errors.append(f"generated file stale: {rel(path)}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="write generated repo-genome files")
    parser.add_argument("--check", action="store_true", help="check generated files are current")
    args = parser.parse_args()

    if args.write == args.check:
        parser.error("choose exactly one of --write or --check")

    result = build()
    if result.errors:
        for error in result.errors:
            print(f"error: {error}", file=sys.stderr)
        return 1

    if args.write:
        write_files(result.files)
        print(f"wrote repo_genome generated files={len(result.files)} units={unit_count(result.files)}")
        return 0

    errors = check_files(result.files)
    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"repo_genome current files={len(result.files)} units={unit_count(result.files)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
