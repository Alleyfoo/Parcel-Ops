# Parcel Ops Control Tower — Implementation Plan

## Project
Build a Streamlit dashboard for parcel and customs operations.
Pattern: Plan / Truth / Gap (same as Campaign Readiness Monitor).
User: operations staff who manage customs clearance, carrier handoffs, and stopped shipments.

## Architecture

```
app.py              — Streamlit dashboard (lanes, KPIs, exceptions, freshness)
parcel_schema.py    — data schema, normalization, synthetic demo data, KPI aggregation
design/design.css   — visual design system (Geist font, tool-grade dashboard aesthetic)
```

## Lanes

| Lane | What it tracks |
|------|---------------|
| Arrival | Physical receipt at terminal, scan confirmation |
| ICS2/ENS | Entry Summary Declaration filing status |
| H7 | H7 customs procedure — pending, cleared, stopped |
| Documents | Invoice, packing list, EUR1, completeness |
| Stopped | Customs holds, inspection, release status |
| Last-mile | Carrier pickup, delivery confirmation |
| EU Trucks | EU truck arrival schedule, border crossing |
| Support | Ticket queue, SLA breach risk, open queries |

## Phase 1 — Prototype foundation (current)

**Goal:** Running Streamlit dashboard with all 8 lanes, synthetic demo data, correct
Plan/Truth/Gap structure. No live data connections yet.

### Handout 001 — Bootstrap and smoke test

Status: pending

- Verify the app runs: `streamlit run app.py`
- Confirm all 8 lanes render in the lane matrix
- Confirm KPI strip shows 6 KPIs
- Confirm exceptions section renders stopped/critical batches
- Confirm data freshness grid renders 6 source cards
- Commit

### Handout 002 — Upload mode and schema validation

Status: pending

- Wire the CSV/Excel uploader through `normalize_shipment_data()`
- Show validation errors when required columns are missing
- Show a preview table of the loaded batches
- Download a blank template (`.xlsx`) from the UI
- Commit

### Handout 003 — Styling and visual polish

Status: pending

- Verify CSS is loaded correctly (Geist font, CSS variables, no Streamlit chrome)
- Fix any rendering issues in lane matrix grid on common screen widths
- Add priority coloring to batch rows (critical = red border accent)
- Confirm responsive breakpoints work at 980px

## Phase 2 — Live data integration (future)

- Carrier API adapter (DHL, DSV, PostNord webhook or polling)
- Customs API adapter (H7 / ICS2 feed)
- Document store integration (completeness check)
- EU truck schedule feed
- Alerting / push notifications for critical state changes

## Phase 3 — Operational features (future)

- Shipment detail drawer (click a batch row to expand)
- SLA breach prediction based on H7 age + carrier delay pattern
- Export to CSV / handoff report for shift change
- Role-based views (Customs team vs Last-mile team vs Management)

## Conventions

- Python 3.11+
- All visual rendering via `_md(html)` — no native Streamlit components for layout
- `parcel_schema.py` owns all domain logic — `app.py` is display only
- No ORM, no database in Phase 1 — file-based data only
- Summoner workflow for implementation tasks: handout → done → feedback → next
