# Handout 001 — Bootstrap and smoke test

## Scope

Verify the Parcel Ops Control Tower Streamlit app runs correctly from a clean install.
All 8 lanes must render in the lane matrix. KPI strip, exceptions section, and data freshness
grid must all render without errors. Commit when clean.

This is the first handout in Phase 1. The app and schema are already written.
This handout is about verifying the foundation works, not adding features.

## Tasks

1. Install dependencies: `pip install -r requirements.txt`
2. Run the app: `streamlit run app.py` — confirm it starts without import errors
3. Verify all 8 lanes appear in the lane matrix header row: Arrival, ICS2/ENS, H7, Documents, Stopped, Last-mile, EU Trucks, Support
4. Verify the KPI strip renders 6 KPI cards (Total Parcels, Arriving Today, Stopped, H7 Pending, Critical Issues, SLA Breach Risk)
5. Verify the Exceptions First section renders for any batches with critical lane status
6. Verify the Data Sources freshness grid shows 6 source cards
7. If any errors are found, fix them in `app.py` or `parcel_schema.py` and note what was fixed
8. Commit

## Out of scope

- Live data connections (Phase 2)
- Upload mode testing (Handout 002)
- CSS visual polish (Handout 003)
- Any new features not listed above

## Done when

- `streamlit run app.py` starts without Python errors or import failures
- All 8 lane column headers are visible in the lane matrix
- 6 KPI cards are visible in the KPI strip
- At least one exception row renders in the Exceptions First section (demo data has critical batches)
- 6 data source freshness cards render
- Git commit exists with all changed files

## Proof requirements

- Commit required: yes
- Tests required: no
- Allow no file changes: yes (if app runs clean with no fixes needed)
- Allowed changed paths: app.py, parcel_schema.py, design/design.css

## Allowed changed paths

- `app.py`
- `parcel_schema.py`
- `design/design.css`
