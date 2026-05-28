# Parcel Ops Control Tower

Operational dashboard for parcel and customs operations.

**Pattern:** Plan / Truth / Gap
- Plan = expected clearance times, SLA targets, carrier schedules
- Truth = live carrier status, customs state, document completeness
- Gap = the delta: delays, stops, exceptions that need action

## Lanes

| Lane | What it tracks |
|------|---------------|
| Arrival / Terminal | Physical receipt at terminal, scan confirmation |
| ICS2 / ENS | Entry Summary Declaration filing status |
| H7 Clearance | H7 customs procedure — pending, cleared, stopped |
| Documents | Commercial invoice, packing list, EUR1, completeness |
| Stopped Shipments | Customs holds, inspection requests, release status |
| Last-mile Handover | Carrier pickup, delivery confirmation |
| EU Trucks | EU truck arrival schedule, border crossing status |
| Support / SLA | Ticket queue, SLA breach risk, open queries |

## Running the dashboard

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Architecture

```
app.py                    — Streamlit dashboard (Plan/Truth/Gap view)
parcel_schema.py          — data schema, normalization, synthetic demo data
design/design.css         — visual design system (Geist font, CSS variables)
docs/manager/workspace/   — Manager Summoner workspace (plan + handouts)
tools/manager-summoner/   — Summoner pipeline tool
scripts/                  — genome build/query scripts
.agents/skills/           — agent role definitions (SKILL.md files)
```

## Agent system

Uses the Manager Summoner pipeline from the Vibechords project, adapted for parcel ops.

```bash
# Dispatch executor on handout 001
python tools/manager-summoner/summoner.py --spawn 001

# Check status
python tools/manager-summoner/summoner.py --check

# Run feedback pass on done file
python tools/manager-summoner/summoner.py --run 001
```

Set `SUMMONER_MODEL`, `EXECUTOR_MODEL` env vars to configure the LLM backend.
Supports `opencode/<provider>/<model>`, `ollama/<model>`, and `anthropic/<model>`.
