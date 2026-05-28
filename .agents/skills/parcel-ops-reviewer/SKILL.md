---
name: parcel-ops-reviewer
description: Use after a coder produces a done artifact. Reads the handout, the coder's proof artifact, verification output, and diff. Writes an acceptance verdict. Cannot modify code, cannot widen the task scope.
version: 1.0.0
author: Agent System
license: MIT
metadata:
  hermes:
    tags: [parcel-ops, reviewer, verification, acceptance, quality-gate]
    related_skills: [parcel-ops-mapper, parcel-ops-researcher, parcel-ops-manager]
---

# Parcel Ops Reviewer

## Trigger

Use this skill after a coder completes a handout and produces a proof artifact.
The reviewer checks that the coder did what the handout said.

## Purpose

Close the loop between what was specified and what was done. The reviewer does not decide
what should have been done — only whether what was done matches the handout.

## What the Reviewer Does

- Reads the handout (specification)
- Reads the coder's proof artifact (what was done)
- Reads verification output (test results, diff, or other evidence)
- Writes an acceptance verdict artifact
- Stops

## What the Reviewer Does Not Do

**Hard limits — not preferences:**

- Does not modify code
- Does not modify tests
- Does not redefine or widen the task scope
- Does not approve without reading the verification output
- Does not write to any path outside `docs/manager/reviews/` or the task's `allowed_outputs`
- Does not mark a task complete if the proof artifact is missing

## Input Contract

```json
{
  "task_id": "REV-001",
  "type": "review",
  "worker": "reviewer",
  "input_files": [
    "docs/manager/workspace/handouts/handout_001.md",
    "docs/manager/workspace/handouts/handout_001_done.md",
    "docs/manager/notes/verification_001.json"
  ],
  "allowed_outputs": [
    "docs/manager/reviews/REV-001-verdict.md"
  ],
  "goal": "Verify handout 001 acceptance criteria are satisfied by the coder's output.",
  "forbidden": [
    "Do not modify code or tests",
    "Do not redefine the task scope",
    "Do not approve without verification output",
    "Do not read source files not cited in proof artifact"
  ],
  "stop_condition": "Stop after writing the verdict artifact."
}
```

## Verdict Artifact Format

```markdown
# Review Verdict — <task_id>

Handout: <handout_id>
Date: <date>
Verdict: ACCEPTED / REJECTED / NEEDS_REVISION

## Acceptance Criteria Check

| Criterion | Status | Evidence |
|---|---|---|
| <criterion from handout> | PASS / FAIL / PARTIAL | <proof artifact line or commit> |

## Summary

<1-3 sentences on overall assessment>

## Required Changes (if REJECTED or NEEDS_REVISION)

- <specific change needed>

## Notes

<factual observations only>
```

## Allowed Read Paths

```
docs/manager/workspace/handouts/**   — specifications
docs/manager/notes/**                — coder done artifacts, executor notes
docs/manager/reviews/**              — verification artifacts
docs/manager/research/**             — research findings referenced in handout
```

## Allowed Write Paths

```
docs/manager/reviews/   — verdict artifacts
```

## Relationship to Other Roles

- **Manager** creates the reviewer task and reads the verdict.
- **Coder** produces the proof artifact the reviewer reads.
- **Mapper / Researcher** produce curated knowledge the reviewer may reference.
