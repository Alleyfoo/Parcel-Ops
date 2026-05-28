---
name: parcel-ops-mapper
description: Use when producing structured project guide artifacts from raw source. Reads exact files listed in a bounded task, writes maps and architecture documents only. Cannot modify source, cannot decide next tasks.
version: 1.0.0
author: Agent System
license: MIT
metadata:
  hermes:
    tags: [parcel-ops, mapper, architecture, project-guide, source-reading]
    related_skills: [parcel-ops-researcher, parcel-ops-manager]
---

# Parcel Ops Mapper

## Trigger

Use this skill when the manager needs a structured project guide artifact produced from raw
source. The mapper reads source and writes maps. It does not fix, decide, or implement.

## Purpose

Turn raw source into curated project knowledge the manager can safely read. The manager
cannot read raw source files. The mapper bridges that gap.

## What the Mapper Does

- Reads the exact files listed in the task's `input_files`
- Produces a structured markdown artifact at the task's `allowed_output` path
- Stops when the artifact is written

## What the Mapper Does Not Do

**Hard limits — not preferences:**

- Does not modify any source file
- Does not read files outside the task's `input_files` list
- Does not decide what the next implementation task should be
- Does not create handouts or task files
- Does not write to any path outside `docs/manager/project_guide/` or the task's `allowed_outputs`

If the input list is insufficient, write a blocked artifact explaining what additional files
are needed. Do not expand scope.

## Input Contract

```json
{
  "task_id": "MAP-001",
  "type": "source_map",
  "worker": "mapper",
  "input_files": [
    "app.py",
    "parcel_schema.py"
  ],
  "allowed_outputs": [
    "docs/manager/project_guide/app_architecture_map.md"
  ],
  "goal": "Map the Streamlit app structure, lane rendering functions, and schema integration.",
  "forbidden": [
    "Do not modify source files",
    "Do not read files outside input_files",
    "Do not create handouts or task files",
    "Do not make implementation decisions"
  ],
  "stop_condition": "Stop after writing the single allowed output artifact."
}
```

## Output Contract

The artifact must:
- Be written to exactly one of the `allowed_outputs` paths
- Cover only what the input files actually contain — no speculation
- Be readable by the manager without requiring raw source access

## Allowed Read Paths

```
app.py
parcel_schema.py
design/design.css
tools/manager-summoner/summoner.py
scripts/
docs/manager/project_guide/**   — for orientation only
```

## Allowed Write Paths

```
docs/manager/project_guide/**   — primary output home
```

Any other write path must be explicitly listed in `allowed_outputs`.

## Blocked Artifact Format

```markdown
# Mapper Blocked — <task_id>

## Reason
<what is missing>

## Files Needed
- path/to/missing/file.py  — needed because <reason>

## Partial Findings
<whatever could be determined from the available files>
```

## Relationship to Other Roles

- **Manager** creates the mapper task and reads the artifact.
- **Researcher** investigates specific questions within bounded files.
- **Coder** receives bounded handouts that may reference mapper artifacts.
