# AI Assistant Instructions

## Start Here

When starting a new session, read these files in order:

1. [.handoff.md](.handoff.md) — session state, what was completed, what is pending, known issues
2. [README.md](README.md) — project overview, lane definitions, architecture
3. [docs/manager/workspace/plan.md](docs/manager/workspace/plan.md) — current implementation plan
4. [docs/manager/tracker.json](docs/manager/tracker.json) — handout tracker state

## Operating Rules

- Treat `docs/manager/tracker.json` as the source of truth for handout status.
- Before dispatching any worker, query the repo genome: `python scripts/query_repo_genome.py --intent "<task>"`.
- Manager must read `SKILL.md` before making any dispatch decision.
- Do not modify source files during manager/mapper/researcher roles.
- Always update `.handoff.md` at session end.

## Repo-Genome Startup Rule

Before starting implementation, debugging, refactor, or research work, run:
```
python scripts/query_repo_genome.py --intent "<task>"
```
Use the returned `unit_id`, `primary_files`, `validation_commands`, and `risk_tags` as startup context.
Run `python scripts/build_repo_genome.py --write` after adding new unit YAML files.

## Manager Summoner Pipeline

```bash
# Dispatch executor on a handout
python tools/manager-summoner/summoner.py --spawn NNN

# Run feedback pass after done file is written
python tools/manager-summoner/summoner.py --run NNN

# Check tracker state
python tools/manager-summoner/summoner.py --check

# Auto-next-handout generation
python tools/manager-summoner/summoner.py --next NNN
```

Required env vars (at minimum one):
- `SUMMONER_MODEL` — manager/feedback model (e.g. `anthropic/claude-sonnet`)
- `EXECUTOR_MODEL` — executor model (e.g. `opencode/deepseek/deepseek-v4-flash`)

## Skills

Local skills in `.agents/skills/` define roles. Key skills:
- **parcel-ops-manager** — orchestration, dispatch decisions, handout writing
- **parcel-ops-mapper** — structural source mapping
- **parcel-ops-researcher** — bounded investigation of specific questions
- **parcel-ops-reviewer** — acceptance verdict on coder output

See individual `SKILL.md` files for trigger conditions and hard limits.

## Output Style

- Repository content must be plain and professional.
- No emoji in code, docs, or comments unless explicitly requested.
- Dashboard strings follow operational / logistics domain vocabulary.

## Playroom Policy

`playroom/` is a scratch area for exploratory work. Do not commit files there without moving them to `docs/`. Close each session with `Playroom status: clean.` or a named explanation.
