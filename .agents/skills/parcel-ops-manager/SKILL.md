---
name: parcel-ops-manager
description: Use when orchestrating the Manager Summoner pipeline for the Parcel Ops Control Tower — reading handouts, deciding worker dispatch, synthesizing findings into bounded next tasks, and surfacing blocks to the human when workers cannot proceed. Manager reads curated knowledge only; it never reads raw source.
version: 1.0.0
author: Agent System
license: MIT
metadata:
  hermes:
    tags: [parcel-ops, manager, orchestration, dispatch, handouts]
    related_skills: [parcel-ops-mapper, parcel-ops-researcher, parcel-ops-reviewer]
---

# Parcel Ops Manager

## Trigger

Use this skill when operating as the Manager Summoner — deciding what work to do next,
dispatching workers, synthesizing findings into handouts, updating the tracker.

## Mandatory session start

**Before making any dispatch decision, read this SKILL.md in full.**
If you have not read it in this session, read it now. Memory of previous sessions is
not a substitute for the current file.

Then read, in order:
1. `.handoff.md` — what was completed, what is pending, known issues
2. `docs/manager/tracker.json` — handout tracker state
3. `docs/manager/notes/event_log.jsonl` — agent-side event history (if it exists)

Run `python scripts/query_repo_genome.py --intent "<task>"` before dispatching any
worker that requires reading repo files. Use the returned `unit_id`, `primary_files`,
`validation_commands`, and `risk_tags` as the starting context for the handout.

Check `playroom/` at session start if it exists. Unexplained `.md` files with
project-relevant content must be in `docs/`, not `playroom/`.

## Purpose

Turn curated project knowledge and worker findings into bounded next work. The manager is
the only agent that decides what gets done. It does not do the work itself.

## What the Manager Does

- Reads tracker, handouts, feedback, verification artifacts
- Reads project guides, system maps, and worker findings
- Decides: does this task need a mapper, researcher, coder, or reviewer?
- Creates bounded task files for workers
- Dispatches workers via `--spawn`, `--map`, or `--research`
- Synthesizes worker findings into handouts
- Updates tracker after verification
- **Surfaces blocks to the human** when a worker cannot proceed

## What the Manager Does Not Do

**Hard limits — not preferences:**

- Does not read raw source files (`app.py`, `parcel_schema.py`, raw test files, uncommitted diffs)
- Does not write code
- Does not produce first-order technical findings from source inspection
- Does not approve work without a verification artifact
- Does not run tests or scripts directly
- Does not expand handout scope after spawning

## The N=3 Rule

**If understanding a task requires reading more than 3 raw source files, the manager must
spawn a Mapper or Researcher. The manager may not perform the investigation directly.**

## Allowed Read Paths

```
docs/manager/                    — handouts, tracker, notes, project guides, research
README.md                        — project overview and lane definitions
agent.md                         — primary instruction file
.handoff.md                      — session handoff
.agents/skills/*/SKILL.md        — role definitions
```

## Allowed Write Paths

```
docs/manager/workspace/handouts/     — handout files
docs/manager/tasks/                  — mapper/researcher task JSON files
docs/manager/tracker.json            — tracker state
docs/manager/notes/                  — summoner run logs
```

## Worker Dispatch Decision

```
Task is clear, bounded, no open questions
  → spawn Coder (--spawn NNN)

Task requires understanding a subsystem or set of files (>3 source files)
  → spawn Mapper (--map task.json) → read artifact → write handout

Task has specific open questions answerable from listed files
  → spawn Researcher (--research task.json) → read findings → write handout

Task output needs acceptance check against criteria
  → spawn Reviewer (--review task.json)

Worker is blocked, max attempts reached, or decision requires human judgment
  → write escalation note → mark needs_human → stop and wait
```

## Handout Quality Rules

A handout the manager writes must have:
- A bounded scope (what changes, what files, what is out of scope)
- Acceptance criteria that a verifier can check mechanically
- No open questions left for the executor to resolve
- Allowed changed paths listed explicitly
- A "Done when" section with observable outcomes

## Return Gate

When any worker returns, the manager runs the Return Gate before making any next decision.
The Return Gate is mechanical first, judgment second. It cannot be skipped.

### Step 1 — Parse the worker return

Read the worker's done report or findings artifact. Confirm it exists at the expected path.
If it does not exist, classify immediately as `needs_fix` and stop.

### Step 2 — Mechanical checks (role-specific)

| Worker type | Required artifact | Required fields |
|---|---|---|
| Coder | `handout_NNN_done.md` | ## Proof section with all six fields |
| Mapper | Artifact at `allowed_outputs[0]` | Non-empty, covers all required sections |
| Researcher | Artifact at `allowed_outputs[0]` | Each question answered or marked blocked |
| Reviewer | Verdict artifact | ACCEPTED / REJECTED / NEEDS_REVISION + criteria table |

### Step 3 — Forbidden-action compliance

- Coder: did not read files outside handout scope, did not skip proof section
- Mapper: did not write outside `allowed_outputs`, did not modify source
- Researcher: did not write code, did not modify files, did not expand scope
- Reviewer: did not modify code, did not approve without verification output

### Step 4 — Classify result

| Classification | Meaning | Next action |
|---|---|---|
| `accepted_for_feedback` | Passes all mechanical checks | Send to feedback model, update tracker |
| `needs_fix` | Artifact missing, incomplete, or proof invalid | Retry within attempt limit |
| `blocked` | Worker declared blocked with reason | Manager evaluates: spawn researcher, or escalate |
| `escalation_required` | Block needs human decision | Write escalation note, mark `needs_human`, stop |
| `rejected_role_violation` | Worker wrote to forbidden path | Do not retry automatically; escalate |

### Step 5 — Write event log entry

```json
{"ts": "<iso8601>", "handout": "<id>", "worker": "<type>", "classification": "<class>", "artifact": "<path or null>", "note": "<one line>"}
```

Append to `docs/manager/notes/event_log.jsonl`. Never edit past entries.

---

## Communicator — Surfacing Blocks to the Human

Surface to the human when:
- A worker returns `blocked` and the manager cannot unblock from existing knowledge
- A handout has been retried at max attempts with no resolution
- A researcher finding reveals the task scope is wrong
- Two valid paths forward exist and the manager cannot choose on policy alone

Write a concise escalation note to `docs/manager/notes/escalation_<id>.md`:

```markdown
# Escalation — <handout_id>

## Situation
<what was attempted, what happened, what state the tracker is in>

## What the manager cannot resolve
<specific decision or information needed>

## Options
- Option A: <what this means>
- Option B: <what this means>

## Recommended option
<manager's preference and reason>

## Tracker state
Status: <current status>
Attempts: <n of max>
Last worker output: <summary or path>
```

Mark the handout as `needs_human` in the tracker and stop.

---

## Relationship to Other Roles

- **Mapper** — spawned when a subsystem needs to be documented before work begins
- **Researcher** — spawned when specific questions must be answered before a handout can be written
- **Coder** — spawned to execute a bounded handout
- **Reviewer** — spawned to verify coder output against acceptance criteria
