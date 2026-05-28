"""Parcel Ops Control Tower — Streamlit dashboard.

Pattern: Plan / Truth / Gap
  Plan  = expected clearance times, SLA targets, carrier schedules
  Truth = live carrier status, customs state, document completeness
  Gap   = delays, stops, exceptions that need action

Lanes: Arrival | ICS2/ENS | H7 | Documents | Stopped | Last-mile | EU Trucks | Support/SLA
"""

from __future__ import annotations

import os
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

from parcel_schema import (
    LANES,
    LANE_LABELS,
    LANE_CSS,
    LANE_LABEL_TEXT,
    LaneStatus,
    OpsKPIs,
    compute_kpis,
    demo_data_freshness,
    demo_diagnostics,
    demo_lane_statuses,
    demo_shipments,
    normalize_shipment_data,
    shipment_template,
)


# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Parcel Ops Control Tower",
    layout="wide",
    initial_sidebar_state="collapsed",
)

_CSS_PATH = Path(__file__).parent / "design" / "design.css"
if _CSS_PATH.exists():
    st.markdown(f"<style>{_CSS_PATH.read_text()}</style>", unsafe_allow_html=True)


def _md(html: str) -> None:
    cleaned = "\n".join(line.lstrip() for line in html.splitlines())
    st.markdown(cleaned.strip(), unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Session state defaults
# ---------------------------------------------------------------------------

if "view_mode" not in st.session_state:
    st.session_state.view_mode = "Ops"


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

TODAY = date.today()


@st.cache_data(ttl=120)
def load_demo_data():
    df = demo_shipments(today=TODAY)
    statuses = demo_lane_statuses(df["batch_id"].tolist())
    return df, statuses


def load_uploaded_data(uploaded_file):
    name = uploaded_file.name
    try:
        if name.lower().endswith(".csv"):
            raw = pd.read_csv(uploaded_file)
        else:
            raw = pd.read_excel(uploaded_file)
    except Exception as exc:
        return None, {}, [f"Could not read file: {exc}"]
    df, errors = normalize_shipment_data(raw)
    if df is None:
        return None, {}, errors
    statuses = demo_lane_statuses(df["batch_id"].tolist())
    return df, statuses, errors


# ---------------------------------------------------------------------------
# Top bar
# ---------------------------------------------------------------------------

def render_topbar() -> None:
    sync_time = TODAY.strftime("%d %b %Y")
    _md(f"""
    <div class="topbar">
      <div class="brand">
        <div class="brand-mark"></div>
        <span class="brand-name">Parcel Ops Control Tower</span>
        <span class="brand-tag">PROTO</span>
      </div>
      <div class="crumbs">
        <span>Ops</span>
        <span class="sep">/</span>
        <span class="cur">All Lanes</span>
      </div>
      <div class="top-spacer"></div>
      <div class="sync-pill">
        <div class="pulse"></div>
        {sync_time}
      </div>
    </div>
    """)


# ---------------------------------------------------------------------------
# KPI strip
# ---------------------------------------------------------------------------

def render_kpi_strip(kpis: OpsKPIs) -> None:
    crit_cls = "k-crit" if kpis.critical_count > 0 else "k-ok"
    stop_cls = "k-crit" if kpis.stopped_count > 0 else "k-ok"
    h7_cls = "k-warn" if kpis.h7_pending > 0 else "k-ok"
    sla_cls = "k-warn" if kpis.sla_breach_risk > 0 else "k-ok"
    _md(f"""
    <div class="kpis">
      <div class="kpi">
        <div class="top">
          <div class="lbl">Total Parcels</div>
        </div>
        <div class="val">{kpis.total_parcels:,}</div>
        <div class="foot">Active batches today</div>
      </div>
      <div class="kpi">
        <div class="top">
          <div class="lbl">Arriving Today</div>
        </div>
        <div class="val">{kpis.batches_arriving_today}</div>
        <div class="foot">Batches expected</div>
      </div>
      <div class="kpi {stop_cls}">
        <div class="top">
          <div class="lbl">Stopped</div>
        </div>
        <div class="val">{kpis.stopped_count}</div>
        <div class="foot">Customs hold or inspection</div>
      </div>
      <div class="kpi {h7_cls}">
        <div class="top">
          <div class="lbl">H7 Pending</div>
        </div>
        <div class="val">{kpis.h7_pending}</div>
        <div class="foot">Clearance not yet confirmed</div>
      </div>
      <div class="kpi {crit_cls}">
        <div class="top">
          <div class="lbl">Critical Issues</div>
        </div>
        <div class="val">{kpis.critical_count}</div>
        <div class="foot">Any lane in critical state</div>
      </div>
      <div class="kpi {sla_cls}">
        <div class="top">
          <div class="lbl">SLA Breach Risk</div>
        </div>
        <div class="val">{kpis.sla_breach_risk}</div>
        <div class="foot">Support lane warning or critical</div>
      </div>
    </div>
    """)


# ---------------------------------------------------------------------------
# Exceptions first (stopped / critical batches)
# ---------------------------------------------------------------------------

def render_exceptions_first(
    df: pd.DataFrame,
    lane_statuses: dict[str, dict[str, LaneStatus]],
) -> None:
    # Find batches with any critical lane
    critical_batches = [
        bid for bid, lanes in lane_statuses.items()
        if any(v == "critical" for v in lanes.values())
    ]
    if not critical_batches:
        _md("""
        <div class="section">
          <div class="section-head">
            <div class="left"><h2>Exceptions First</h2></div>
            <div class="right">0 critical</div>
          </div>
          <div style="color:var(--ink-4);font-family:var(--mono);font-size:12px;padding:12px 0;">
            No critical exceptions at this time.
          </div>
        </div>
        """)
        return

    _md(f"""
    <div class="section">
      <div class="section-head">
        <div class="left"><h2>Exceptions First <span class="muted">— act now</span></h2></div>
        <div class="right" style="color:var(--red);">{len(critical_batches)} critical</div>
      </div>
    </div>
    """)

    rows_html = ""
    for bid in critical_batches[:5]:
        row = df[df["batch_id"] == bid].iloc[0] if not df[df["batch_id"] == bid].empty else None
        if row is None:
            continue
        crit_lanes = [l for l, v in lane_statuses[bid].items() if v == "critical"]
        lanes_str = ", ".join(crit_lanes[:3])
        carrier = row.get("carrier", "—")
        pcount = int(row.get("parcel_count", 0))
        priority = str(row.get("priority", "normal")).upper()
        crit_cls = "crit" if priority == "CRITICAL" else ""
        rows_html += f"""
        <div class="fix-row {crit_cls}">
          <div class="fix-id">
            <span class="sku">{bid}</span>
            <span class="count">{pcount} parcels</span>
          </div>
          <div class="fix-main">
            <div class="fix-title">{carrier}</div>
            <div class="fix-meta">
              <span>Lanes: {lanes_str}</span>
              <span>Priority: {priority}</span>
            </div>
          </div>
          <div class="fix-pressure {'today' if crit_lanes else ''}">
            <span>CRITICAL LANES</span>
            <b>{len(crit_lanes)}</b>
          </div>
          <div class="fix-action">
            Check customs hold.<br>Contact carrier or customs broker.
          </div>
        </div>
        """
    _md(f'<div class="fix-list">{rows_html}</div>')


# ---------------------------------------------------------------------------
# Diagnostics panel
# ---------------------------------------------------------------------------

def render_diagnostics() -> None:
    entries = demo_diagnostics()
    if not entries:
        return

    crit_count = sum(1 for e in entries if e.severity == "critical")
    high_count = sum(1 for e in entries if e.severity == "high")

    _md(f"""
    <div class="section">
      <div class="section-head">
        <div class="left"><h2>Diagnostics <span class="muted">— automated detection</span></h2></div>
        <div class="right">{len(entries)} issues · {crit_count} critical · {high_count} high</div>
      </div>
    </div>
    """)

    for e in entries:
        sev_cls = {"critical": "crit", "high": "warn", "warning": "warn"}.get(e.severity, "")
        conf_pct = int(e.confidence * 100)
        conf_cls = "high" if e.confidence >= 0.90 else ("mid" if e.confidence >= 0.75 else "low")
        type_label = {
            "hs_mismatch": "HS CODE MISMATCH",
            "doc_missing": "DOCUMENT MISSING",
            "ens_missing": "ENS NOT FILED",
            "sla_breach": "SLA BREACH",
            "doc_incomplete": "DOCS INCOMPLETE",
        }.get(e.issue_type, e.issue_type.upper())

        _md(f"""
        <div class="diag-card {sev_cls}">
          <div class="diag-header">
            <div class="diag-id">
              <span class="sku">{e.batch_id}</span>
              <span class="diag-type">{type_label}</span>
            </div>
            <div class="diag-severity">{e.severity.upper()}</div>
          </div>
          <div class="diag-body">
            <div class="diag-row">
              <span class="diag-label">Declared</span>
              <span class="diag-value">{e.declared}</span>
            </div>
            <div class="diag-row">
              <span class="diag-label">Expected</span>
              <span class="diag-value diag-expected">{e.expected}</span>
            </div>
            <div class="diag-row">
              <span class="diag-label">Source</span>
              <span class="diag-value">{e.source}</span>
            </div>
            <div class="diag-row">
              <span class="diag-label">Confidence</span>
              <span class="diag-value diag-conf {conf_cls}">{conf_pct}%</span>
            </div>
            <div class="diag-detail">{e.detail}</div>
            <div class="diag-impact">
              <span class="diag-label">Duty Impact:</span> {e.duty_impact}
            </div>
            <div class="diag-action">
              <span class="diag-label">Action:</span> {e.suggested_action}
            </div>
          </div>
        </div>
        """)


# ---------------------------------------------------------------------------
# Lane matrix
# ---------------------------------------------------------------------------

def render_lane_matrix(
    df: pd.DataFrame,
    lane_statuses: dict[str, dict[str, LaneStatus]],
) -> None:
    # Build header cells
    header_cells = "".join(
        f'<div style="font-family:var(--mono);font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:var(--ink-4);text-align:center;">{LANE_LABELS.get(l, l)}</div>'
        for l in LANES
    )
    n_lanes = len(LANES)
    grid_cols = f"minmax(200px, .8fr) repeat({n_lanes}, minmax(68px, 1fr))"

    header_html = f"""
    <div style="display:grid;grid-template-columns:{grid_cols};gap:6px;align-items:center;
                padding:10px 14px;background:var(--surface-2);border-bottom:1px solid var(--line);
                font-family:var(--mono);font-size:10px;color:var(--ink-4);">
      <div>BATCH / CARRIER</div>
      {header_cells}
    </div>
    """

    rows_html = ""
    for _, row in df.iterrows():
        bid = row["batch_id"]
        carrier = row.get("carrier", "—")
        pcount = int(row.get("parcel_count", 0))
        origin = row.get("origin", "—")
        arrival = str(row.get("expected_arrival", "—"))
        lanes = lane_statuses.get(bid, {})

        cells_html = "".join(
            f'<div class="lane-cell {LANE_CSS.get(lanes.get(l, "pending"), "")}">'
            f'{LANE_LABEL_TEXT.get(lanes.get(l, "pending"), "—")}</div>'
            for l in LANES
        )

        rows_html += f"""
        <div style="display:grid;grid-template-columns:{grid_cols};gap:6px;align-items:center;
                    padding:10px 14px;border-bottom:1px solid var(--line);">
          <div>
            <div style="font-family:var(--mono);font-size:10.5px;color:var(--ink-4);">{bid} · {origin}</div>
            <div style="font-size:13px;font-weight:600;color:var(--ink);">{carrier}</div>
            <div style="font-family:var(--mono);font-size:10px;color:var(--ink-5);">{pcount:,} parcels · {arrival}</div>
          </div>
          {cells_html}
        </div>
        """

    _md(f"""
    <div class="section">
      <div class="section-head">
        <div class="left"><h2>Lane Matrix <span class="muted">— all batches</span></h2></div>
        <div class="right">{len(df)} batches</div>
      </div>
      <div class="lane-matrix">
        {header_html}
        {rows_html}
      </div>
    </div>
    """)


# ---------------------------------------------------------------------------
# Data freshness
# ---------------------------------------------------------------------------

def render_data_freshness() -> None:
    entries = demo_data_freshness()
    cards_html = ""
    for e in entries:
        cls = {"fresh": "", "stale": "stale", "missing": "missing", "partial": "partial"}.get(e.status, "")
        cards_html += f"""
        <div class="fresh-card {cls}">
          <div class="fresh-top">
            <div class="fresh-name">{e.name}</div>
            <div class="fresh-status">{e.status.upper()}</div>
          </div>
          <div class="fresh-time">{e.last_updated}</div>
          <div class="fresh-detail">{e.detail}</div>
        </div>
        """
    _md(f"""
    <div class="section">
      <div class="section-head">
        <div class="left"><h2>Data Sources <span class="muted">— freshness</span></h2></div>
      </div>
      <div class="fresh-grid">{cards_html}</div>
    </div>
    """)


# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------

def render_footer() -> None:
    _md(f"""
    <div class="footer">
      <span>PARCEL OPS CONTROL TOWER</span>
      <span>PROTO · {TODAY.strftime('%Y-%m-%d')} · DEMO DATA</span>
    </div>
    """)


# ---------------------------------------------------------------------------
# Data input panel
# ---------------------------------------------------------------------------

def render_data_input() -> tuple[pd.DataFrame, dict]:
    with st.expander("Data source — upload CSV / Excel", expanded=False):
        uploaded = st.file_uploader(
            "Upload shipment batch file",
            type=["xlsx", "csv"],
            help="Upload a CSV or Excel file with shipment batch data. Required columns: batch_id, carrier, origin, expected_arrival, parcel_count.",
        )
        if uploaded:
            df, statuses, errors = load_uploaded_data(uploaded)
            if errors:
                for e in errors:
                    st.warning(e)
            if df is not None:
                st.success(f"Loaded {len(df)} batches from {uploaded.name}")
                return df, statuses
    # Default: demo data
    return load_demo_data()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    render_topbar()

    st.markdown("<div style='height:24px'></div>", unsafe_allow_html=True)

    df, lane_statuses = render_data_input()

    kpis = compute_kpis(df, lane_statuses, today=TODAY)
    render_kpi_strip(kpis)

    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

    render_exceptions_first(df, lane_statuses)
    render_diagnostics()
    render_lane_matrix(df, lane_statuses)
    render_data_freshness()
    render_footer()


if __name__ == "__main__":
    main()
