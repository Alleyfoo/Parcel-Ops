"""Parcel Ops Control Tower — Streamlit dashboard.

Pattern: Plan / Truth / Gap
  Plan  = expected clearance times, SLA targets, carrier schedules
  Truth = live carrier status, customs state, document completeness
  Gap   = delays, stops, exceptions that need action

Lanes: Arrival | ICS2/ENS | H7 | Documents | Stopped | Last-mile | EU Trucks | Support/SLA
"""

from __future__ import annotations

from datetime import date
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
    HS_TREE,
    hs_tree_path,
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


def render_filters(
    df: pd.DataFrame,
    lane_statuses: dict[str, dict[str, LaneStatus]],
) -> tuple[pd.DataFrame, dict[str, dict[str, LaneStatus]]]:
    """Render filter bar and return filtered dataframe and lane statuses."""
    
    with st.expander("Filters & Search", expanded=True):
        col1, col2, col3 = st.columns(3)
        
        with col1:
            carriers = sorted(df["carrier"].unique().tolist())
            selected_carriers = st.multiselect(
                "Carrier",
                options=carriers,
                default=carriers,
                help="Filter by carrier name"
            )
            
            origins = sorted(df["origin"].unique().tolist())
            selected_origins = st.multiselect(
                "Origin",
                options=origins,
                default=origins,
                help="Filter by country of origin"
            )
        
        with col2:
            # Lane status filter
            status_options = ["All", "Critical only", "Warning or worse", "Has pending lanes"]
            selected_status = st.selectbox(
                "Lane Status",
                options=status_options,
                help="Filter by lane status severity"
            )
            
            # Search
            search_term = st.text_input(
                "Search",
                placeholder="Batch ID or HS code...",
                help="Search by batch ID or HS code"
            )
        
        with col3:
            # Date range
            min_date = df["expected_arrival"].min()
            max_date = df["expected_arrival"].max()
            
            date_range = st.date_input(
                "Arrival Date Range",
                value=(min_date, max_date),
                min_value=min_date,
                max_value=max_date,
                help="Filter by expected arrival date"
            )
            
            # Export button
            if st.button("Export Filtered Data", type="secondary"):
                # Prepare export data
                export_df = df.copy()
                export_df["critical_lanes"] = export_df["batch_id"].apply(
                    lambda bid: ", ".join([
                        lane for lane, status in lane_statuses.get(bid, {}).items()
                        if status == "critical"
                    ]) or "None"
                )
                csv = export_df.to_csv(index=False)
                st.download_button(
                    label="Download CSV",
                    data=csv,
                    file_name=f"filtered_batches_{TODAY.isoformat()}.csv",
                    mime="text/csv"
                )
    
    # Apply filters
    filtered_df = df.copy()
    
    # Carrier filter
    if selected_carriers:
        filtered_df = filtered_df[filtered_df["carrier"].isin(selected_carriers)]
    
    # Origin filter
    if selected_origins:
        filtered_df = filtered_df[filtered_df["origin"].isin(selected_origins)]
    
    # Date range filter
    if len(date_range) == 2:
        start_date, end_date = date_range
        filtered_df = filtered_df[
            (filtered_df["expected_arrival"] >= start_date) &
            (filtered_df["expected_arrival"] <= end_date)
        ]
    
    # Search filter
    if search_term:
        search_lower = search_term.lower()
        filtered_df = filtered_df[
            filtered_df["batch_id"].str.lower().str.contains(search_lower, na=False) |
            filtered_df["hs_code"].astype(str).str.lower().str.contains(search_lower, na=False)
        ]
    
    # Lane status filter
    if selected_status != "All":
        batch_ids = filtered_df["batch_id"].tolist()
        filtered_batch_ids = []
        
        for bid in batch_ids:
            lanes = lane_statuses.get(bid, {})
            if selected_status == "Critical only":
                if any(status == "critical" for status in lanes.values()):
                    filtered_batch_ids.append(bid)
            elif selected_status == "Warning or worse":
                if any(status in ["critical", "warning"] for status in lanes.values()):
                    filtered_batch_ids.append(bid)
            elif selected_status == "Has pending lanes":
                if any(status == "pending" for status in lanes.values()):
                    filtered_batch_ids.append(bid)
        
        filtered_df = filtered_df[filtered_df["batch_id"].isin(filtered_batch_ids)]
    
    # Filter lane_statuses to match filtered_df
    filtered_batch_ids = set(filtered_df["batch_id"].tolist())
    filtered_statuses = {
        bid: lanes for bid, lanes in lane_statuses.items()
        if bid in filtered_batch_ids
    }
    
    # Show filter summary
    if len(filtered_df) < len(df):
        st.info(f"Showing {len(filtered_df)} of {len(df)} batches")
    
    return filtered_df, filtered_statuses


# ---------------------------------------------------------------------------
# Data Pipeline Showcase
# ---------------------------------------------------------------------------

def render_pipeline_showcase() -> None:
    """Technical showcase of diagnostic data pipeline architecture."""

    _md("""
    <div class="section">
      <div class="section-head">
        <div class="left"><h2>Data Pipeline <span class="muted">— diagnostic detection architecture</span></h2></div>
        <div class="right">FI-2026-007 case study</div>
      </div>
    </div>
    """)

    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

    col1, col2 = st.columns([1, 1])

    with col1:
        _md("""
        <div class="pipeline-card">
          <div class="pipeline-header">
            <div class="pipeline-num">01</div>
            <div class="pipeline-title">Source: Commercial Invoice (Excel)</div>
          </div>
          <div class="pipeline-body">
            <div class="pipeline-meta">
              <span class="pipeline-label">System:</span> Shipper ERP Export
            </div>
            <div class="pipeline-meta">
              <span class="pipeline-label">Format:</span> .xlsx via SFTP
            </div>
            <div class="pipeline-meta">
              <span class="pipeline-label">Frequency:</span> On shipment creation
            </div>
            <div class="pipeline-code">
Batch ID: FI-2026-007
Shipper: Shenzhen Electronics Ltd
Invoice #: INV-2026-04-1847
Date: 2026-05-26

Line Items:
  Qty: 2400  |  SKU: IC-7805-REG  |  Desc: "Electronic voltage regulators, integrated circuits"
  Qty: 1200  |  SKU: PCB-CTRL-V3  |  Desc: "Control board assembly with ICs"
  Qty: 800   |  SKU: CAP-100UF    |  Desc: "Capacitors, electronic components"

Declared HS Code: 3926.90
Declared Description: "Plastic articles"
Declared Value: USD 57,400
            </div>
            <div class="pipeline-issue">
              <span class="pipeline-label">Issue:</span> Invoice describes electronic components but HS code 3926.90 is for plastic articles
            </div>
          </div>
        </div>
        """)

    with col2:
        _md("""
        <div class="pipeline-card">
          <div class="pipeline-header">
            <div class="pipeline-num">02</div>
            <div class="pipeline-title">Source: Document Scanner (OCR)</div>
          </div>
          <div class="pipeline-body">
            <div class="pipeline-meta">
              <span class="pipeline-label">System:</span> ABBYY FlexiCapture
            </div>
            <div class="pipeline-meta">
              <span class="pipeline-label">Format:</span> PDF scan → JSON
            </div>
            <div class="pipeline-meta">
              <span class="pipeline-label">Confidence:</span> 94% OCR accuracy
            </div>
            <div class="pipeline-code">
{
  "invoice_number": "INV-2026-04-1847",
  "shipper": "Shenzhen Electronics Ltd",
  "line_items": [
    {
      "quantity": 2400,
      "description": "Electronic voltage regulators, integrated circuits",
      "keywords_extracted": ["electronic", "voltage", "regulators", "integrated", "circuits"]
    },
    {
      "quantity": 1200,
      "description": "Control board assembly with ICs",
      "keywords_extracted": ["control", "board", "assembly", "ICs"]
    }
  ],
  "declared_hs": "3926.90",
  "declared_value": 57400.00,
  "ocr_confidence": 0.94
}
            </div>
            <div class="pipeline-note">
              <span class="pipeline-label">Note:</span> OCR extracted keywords suggest electronics, not plastics
            </div>
          </div>
        </div>
        """)

    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

    col3, col4 = st.columns([1, 1])

    with col3:
        _md("""
        <div class="pipeline-card">
          <div class="pipeline-header">
            <div class="pipeline-num">03</div>
            <div class="pipeline-title">Source: HS Code Database (TARIC)</div>
          </div>
          <div class="pipeline-body">
            <div class="pipeline-meta">
              <span class="pipeline-label">System:</span> EU TARIC API
            </div>
            <div class="pipeline-meta">
              <span class="pipeline-label">Format:</span> REST JSON
            </div>
            <div class="pipeline-meta">
              <span class="pipeline-label">Update:</span> Daily sync
            </div>
            <div class="pipeline-code">
HS Code: 3926.90
Description: "Articles of plastics and articles of other materials of headings 3901 to 3914"
Duty Rate: 6.5%
Restrictions: None

HS Code: 8542.31
Description: "Electronic integrated circuits: Processors and controllers"
Duty Rate: 0%
Restrictions: Dual-use export control (low risk)

HS Code: 8542.39
Description: "Electronic integrated circuits: Other"
Duty Rate: 0%
Restrictions: None
            </div>
            <div class="pipeline-match">
              <span class="pipeline-label">Best Match:</span> 8542.31 or 8542.39 based on "integrated circuits" keywords
            </div>
          </div>
        </div>
        """)

    with col4:
        _md("""
        <div class="pipeline-card">
          <div class="pipeline-header">
            <div class="pipeline-num">04</div>
            <div class="pipeline-title">Comparison Engine (Python)</div>
          </div>
          <div class="pipeline-body">
            <div class="pipeline-meta">
              <span class="pipeline-label">System:</span> Custom Python service
            </div>
            <div class="pipeline-meta">
              <span class="pipeline-label">Framework:</span> pandas + scikit-learn
            </div>
            <div class="pipeline-meta">
              <span class="pipeline-label">Schedule:</span> Every 15 min
            </div>
            <div class="pipeline-code">
def check_hs_mismatch(invoice_data, hs_db):
    declared_hs = invoice_data['declared_hs']
    keywords = invoice_data['keywords_extracted']
    
    # Query HS database for keyword matches
    candidates = hs_db.search(keywords, limit=5)
    
    # Calculate semantic similarity
    declared_desc = hs_db.get(declared_hs).description
    invoice_desc = invoice_data['line_items'][0]['description']
    
    similarity = cosine_similarity(declared_desc, invoice_desc)
    
    # Flag if best match differs from declared
    best_match = candidates[0]
    if best_match.hs_code != declared_hs:
        confidence = 1.0 - similarity
        return Mismatch(
            declared=declared_hs,
            expected=best_match.hs_code,
            confidence=confidence
        )
            </div>
            <div class="pipeline-result">
              <span class="pipeline-label">Result:</span> Mismatch detected, confidence 92%
            </div>
          </div>
        </div>
        """)

    st.markdown("<div style='height:24px'></div>", unsafe_allow_html=True)

    _md("""
    <div class="pipeline-flow">
      <div class="pipeline-flow-title">Data Flow Architecture</div>
      <div class="pipeline-flow-diagram">
        <div class="flow-step">
          <div class="flow-box">Shipper ERP</div>
          <div class="flow-arrow">→</div>
        </div>
        <div class="flow-step">
          <div class="flow-box">SFTP Drop</div>
          <div class="flow-arrow">→</div>
        </div>
        <div class="flow-step">
          <div class="flow-box">OCR Scanner</div>
          <div class="flow-arrow">→</div>
        </div>
        <div class="flow-step">
          <div class="flow-box">JSON Parser</div>
          <div class="flow-arrow">→</div>
        </div>
        <div class="flow-step">
          <div class="flow-box highlight">Comparison Engine</div>
          <div class="flow-arrow">→</div>
        </div>
        <div class="flow-step">
          <div class="flow-box">Diagnostic DB</div>
          <div class="flow-arrow">→</div>
        </div>
        <div class="flow-step">
          <div class="flow-box">Dashboard</div>
        </div>
      </div>
      <div class="pipeline-flow-note">
        Pipeline runs every 15 minutes. Each batch is checked against HS code database, 
        historical patterns, and carrier accuracy scores. Mismatches are scored by confidence 
        and routed to the appropriate ops team based on severity.
      </div>
    </div>
    """)

    st.markdown("<div style='height:32px'></div>", unsafe_allow_html=True)

    _md("""
    <div class="section">
      <div class="section-head">
        <div class="left"><h2>Data Cleaning <span class="muted">— normalizing messy input</span></h2></div>
        <div class="right">Country of origin case study</div>
      </div>
    </div>
    """)

    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

    _md("""
    <div class="pipeline-detail-text" style="margin-bottom:20px;">
      Before classification can begin, raw data must be cleaned. Country of origin is a critical field — it determines
      preferential duty rates, trade agreements, and sanctions checks. But carriers submit origin in dozens of formats:
      full names, abbreviations, city+country strings, even typos. Regex-based normalization maps all variants to ISO 2-letter codes.
    </div>
    """)

    col_raw, col_clean = st.columns([1, 1])

    with col_raw:
        _md("""
        <div class="clean-card raw">
          <div class="clean-header">
            <span class="clean-title">Raw Input (from carriers)</span>
          </div>
          <div class="clean-body">
            <div class="clean-row">
              <span class="clean-carrier">DHL Express</span>
              <span class="clean-value">"People's Republic of China"</span>
            </div>
            <div class="clean-row">
              <span class="clean-carrier">DSV Air & Sea</span>
              <span class="clean-value">"CN"</span>
            </div>
            <div class="clean-row">
              <span class="clean-carrier">Kuehne+Nagel</span>
              <span class="clean-value">"Shenzhen, China"</span>
            </div>
            <div class="clean-row">
              <span class="clean-carrier">DB Schenker</span>
              <span class="clean-value">"CHN"</span>
            </div>
            <div class="clean-row">
              <span class="clean-carrier">PostNord</span>
              <span class="clean-value">"china"</span>
            </div>
            <div class="clean-row">
              <span class="clean-carrier">Maersk</span>
              <span class="clean-value">"P.R. China"</span>
            </div>
            <div class="clean-row">
              <span class="clean-carrier">DHL Express</span>
              <span class="clean-value">"Turkey (TR)"</span>
            </div>
            <div class="clean-row">
              <span class="clean-carrier">DSV Air & Sea</span>
              <span class="clean-value">"Turkiye"</span>
            </div>
          </div>
        </div>
        """)

    with col_clean:
        _md("""
        <div class="clean-card clean">
          <div class="clean-header">
            <span class="clean-title">Normalized Output (ISO 3166-1 alpha-2)</span>
          </div>
          <div class="clean-body">
            <div class="clean-row">
              <span class="clean-carrier">DHL Express</span>
              <span class="clean-code">CN</span>
              <span class="clean-name">China</span>
            </div>
            <div class="clean-row">
              <span class="clean-carrier">DSV Air & Sea</span>
              <span class="clean-code">CN</span>
              <span class="clean-name">China</span>
            </div>
            <div class="clean-row">
              <span class="clean-carrier">Kuehne+Nagel</span>
              <span class="clean-code">CN</span>
              <span class="clean-name">China</span>
            </div>
            <div class="clean-row">
              <span class="clean-carrier">DB Schenker</span>
              <span class="clean-code">CN</span>
              <span class="clean-name">China</span>
            </div>
            <div class="clean-row">
              <span class="clean-carrier">PostNord</span>
              <span class="clean-code">CN</span>
              <span class="clean-name">China</span>
            </div>
            <div class="clean-row">
              <span class="clean-carrier">Maersk</span>
              <span class="clean-code">CN</span>
              <span class="clean-name">China</span>
            </div>
            <div class="clean-row">
              <span class="clean-carrier">DHL Express</span>
              <span class="clean-code">TR</span>
              <span class="clean-name">Turkey</span>
            </div>
            <div class="clean-row">
              <span class="clean-carrier">DSV Air & Sea</span>
              <span class="clean-code">TR</span>
              <span class="clean-name">Turkey</span>
            </div>
          </div>
        </div>
        """)

    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

    _md("""
    <div class="pipeline-details">
      <div class="pipeline-details-title">Regex Normalization Patterns</div>
      
      <div class="pipeline-detail-section">
        <div class="pipeline-detail-label">Pattern 1 — Full name variants</div>
        <div class="pipeline-code">
r"(?i)(people's republic of china|p\\.?r\\.? china|china|chn)" → "CN"
r"(?i)(turkey|turkiye|türkiye|tr)" → "TR"
r"(?i)(united states|usa|u\\.?s\\.?a?|united states of america)" → "US"
        </div>
        <div class="pipeline-detail-text">
          Case-insensitive matching handles capitalization variants. Escaped dots handle "P.R." vs "PR" vs "P.R".
          Ordered from most specific to least specific to avoid partial matches.
        </div>
      </div>
      
      <div class="pipeline-detail-section">
        <div class="pipeline-detail-label">Pattern 2 — City + country extraction</div>
        <div class="pipeline-code">
r"(?i)(shenzhen|guangzhou|shanghai|beijing|ningbo)[,\\s]+(china|cn)" → "CN"
r"(?i)(istanbul|izmir|ankara|mersin)[,\\s]+(turkey|turkiye|tr)" → "TR"
        </div>
        <div class="pipeline-detail-text">
          When origin includes a city name, extract the country portion. Major port cities are whitelisted
          to avoid false positives (e.g., "Paris, Texas" should not match France).
        </div>
      </div>
      
      <div class="pipeline-detail-section">
        <div class="pipeline-detail-label">Pattern 3 — Already-clean codes</div>
        <div class="pipeline-code">
r"^[A-Z]{2}$" → pass through (already ISO 2-letter)
r"^[A-Z]{3}$" → lookup in ISO 3166-1 alpha-3 table
        </div>
        <div class="pipeline-detail-text">
          If input is already a 2-letter code, validate against ISO table and pass through.
          3-letter codes (CHN, TUR, USA) are mapped via lookup table to 2-letter equivalents.
        </div>
      </div>
      
      <div class="pipeline-detail-section">
        <div class="pipeline-detail-label">Fallback — no match</div>
        <div class="pipeline-detail-text">
          If no regex matches, flag as "origin_unknown" and route to manual review queue.
          Never guess — wrong origin can trigger sanctions violations or incorrect duty calculations.
          Log the raw value for pattern analysis (may reveal new variants to add to regex library).
        </div>
      </div>
    </div>
    """)

    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

    _md("""
    <div class="pipeline-flow">
      <div class="pipeline-flow-title">Why Origin Matters</div>
      <div class="pipeline-flow-note">
        Country of origin directly affects: (1) <strong>Preferential duty rates</strong> — EU-Turkey customs union = 0% on many goods,
        EU-China MFN = standard rates. (2) <strong>Trade sanctions</strong> — Russia, Belarus, North Korea require immediate stop.
        (3) <strong>Anti-dumping duties</strong> — specific products from specific countries face additional tariffs (e.g., steel from China).
        (4) <strong>Certificate requirements</strong> — EUR.1 for preferential origin, Form A for GSP.
        Wrong origin = wrong duty = customs penalty or shipment seizure.
      </div>
    </div>
    """)

    st.markdown("<div style='height:24px'></div>", unsafe_allow_html=True)

    _md("""
    <div class="pipeline-details">
      <div class="pipeline-details-title">HS Code Tree Classification</div>
      <div class="pipeline-detail-text" style="margin-bottom:16px;">
        The tree-based approach: start with the 2-digit chapter (family), then narrow by heading (material/function),
        then subheading (specific type). This is how customs officers classify goods — and how the comparison engine
        walks the tree to find the correct code.
      </div>
    </div>
    """)

    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

    col_declared, col_expected = st.columns([1, 1])

    with col_declared:
        _md("""
        <div class="hs-tree-card wrong">
          <div class="hs-tree-header">
            <span class="hs-tree-title">Declared Path (WRONG)</span>
            <span class="hs-tree-code">3926.90</span>
          </div>
          <div class="hs-tree-body">
            <div class="hs-tree-node level-0">
              <span class="hs-tree-code-tag">VII</span>
              <span class="hs-tree-label">Plastics and articles thereof; rubber</span>
            </div>
            <div class="hs-tree-connector"></div>
            <div class="hs-tree-node level-1">
              <span class="hs-tree-code-tag">39</span>
              <span class="hs-tree-label">Plastics and articles thereof</span>
            </div>
            <div class="hs-tree-connector"></div>
            <div class="hs-tree-node level-2">
              <span class="hs-tree-code-tag">3926</span>
              <span class="hs-tree-label">Other articles of plastics</span>
            </div>
            <div class="hs-tree-connector"></div>
            <div class="hs-tree-node level-3 active-wrong">
              <span class="hs-tree-code-tag">3926.90</span>
              <span class="hs-tree-label">Other</span>
              <span class="hs-tree-duty">6.5%</span>
            </div>
            <div class="hs-tree-verdict wrong">
              Invoice says "electronic voltage regulators, integrated circuits" —
              this branch is for plastic articles. Wrong chapter entirely.
            </div>
          </div>
        </div>
        """)

    with col_expected:
        _md("""
        <div class="hs-tree-card correct">
          <div class="hs-tree-header">
            <span class="hs-tree-title">Expected Path (CORRECT)</span>
            <span class="hs-tree-code">8542.31</span>
          </div>
          <div class="hs-tree-body">
            <div class="hs-tree-node level-0">
              <span class="hs-tree-code-tag">XVI</span>
              <span class="hs-tree-label">Machinery; electrical equipment</span>
            </div>
            <div class="hs-tree-connector"></div>
            <div class="hs-tree-node level-1">
              <span class="hs-tree-code-tag">85</span>
              <span class="hs-tree-label">Electrical machinery and equipment</span>
            </div>
            <div class="hs-tree-connector"></div>
            <div class="hs-tree-node level-2">
              <span class="hs-tree-code-tag">8542</span>
              <span class="hs-tree-label">Electronic integrated circuits</span>
            </div>
            <div class="hs-tree-connector"></div>
            <div class="hs-tree-node level-3 active-correct">
              <span class="hs-tree-code-tag">8542.31</span>
              <span class="hs-tree-label">Processors and controllers</span>
              <span class="hs-tree-duty">0%</span>
            </div>
            <div class="hs-tree-verdict correct">
              Keywords "electronic", "integrated circuits", "voltage regulators"
              all point to this branch. 6.5% duty difference flagged.
            </div>
          </div>
        </div>
        """)

    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

    _md("""
    <div class="pipeline-details">
      <div class="pipeline-details-title">How Tree-Based Classification Works</div>
      
      <div class="pipeline-detail-section">
        <div class="pipeline-detail-label">Step 1 — Chapter (2 digits)</div>
        <div class="pipeline-detail-text">
          The first two digits define the chapter (broad family of goods). Chapter 85 = electrical machinery.
          Chapter 39 = plastics. The comparison engine extracts keywords from the invoice and scores each chapter
          by keyword density. "Electronic", "integrated circuits", "voltage" all score high on Chapter 85,
          zero on Chapter 39.
        </div>
      </div>
      
      <div class="pipeline-detail-section">
        <div class="pipeline-detail-label">Step 2 — Heading (4 digits)</div>
        <div class="pipeline-detail-text">
          Within the winning chapter, the engine walks the heading level. Chapter 85 has headings for
          telephones (8517), monitors (8528), semiconductors (8541), integrated circuits (8542), and
          conductors (8544). "Integrated circuits" matches heading 8542 directly.
        </div>
      </div>
      
      <div class="pipeline-detail-section">
        <div class="pipeline-detail-label">Step 3 — Subheading (6 digits)</div>
        <div class="pipeline-detail-text">
          Within heading 8542, subheadings split by function: processors (8542.31), memories (8542.32),
          amplifiers (8542.33), other (8542.39). "Voltage regulators" and "control board" suggest
          processors/controllers → 8542.31.
        </div>
      </div>
      
      <div class="pipeline-detail-section">
        <div class="pipeline-detail-label">Step 4 — TARIC extension (8-10 digits)</div>
        <div class="pipeline-detail-text">
          EU-specific TARIC codes extend beyond the 6-digit international standard. These add
          surveillance measures, anti-dumping duties, and preferential rates. The engine checks
          TARIC extensions for additional restrictions (dual-use export controls, sanctions, etc.).
        </div>
      </div>
      
      <div class="pipeline-detail-section">
        <div class="pipeline-detail-label">Why the Tree Approach Matters</div>
        <div class="pipeline-detail-text">
          Flat keyword search can miss context — "plastic" appears in electronics packaging descriptions too.
          The tree approach forces hierarchical reasoning: first eliminate wrong chapters, then narrow within
          the correct family. This mirrors how customs officers actually classify goods and catches errors
          that flat search misses (like declaring electronics under the plastics chapter).
        </div>
      </div>
    </div>
    """)

    st.markdown("<div style='height:32px'></div>", unsafe_allow_html=True)

    _md("""
    <div class="section">
      <div class="section-head">
        <div class="left"><h2>Pre-Classification <span class="muted">— when no HS code is provided</span></h2></div>
        <div class="right">4 input scenarios</div>
      </div>
    </div>
    """)

    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

    _md("""
    <div class="pipeline-detail-text" style="margin-bottom:20px;">
      Not every importer provides a standard 6-digit HS code. The system must handle four common input types
      before the tree walk can begin. Each requires a different pre-classification step to produce a valid
      starting point for the HS tree.
    </div>
    """)

    col_a, col_b = st.columns([1, 1])

    with col_a:
        _md("""
        <div class="scenario-card">
          <div class="scenario-header">
            <div class="scenario-num">A</div>
            <div class="scenario-title">CN Code Only (8-digit EU)</div>
          </div>
          <div class="scenario-body">
            <div class="scenario-label">Input</div>
            <div class="scenario-code">CN: 8542 31 90</div>

            <div class="scenario-steps">
              <div class="scenario-step">
                <span class="step-num">1</span>
                <span class="step-text">Strip to 6-digit HS root: <strong>8542.31</strong></span>
              </div>
              <div class="scenario-step">
                <span class="step-num">2</span>
                <span class="step-text">Validate against HS tree: 8542.31 exists in tree under Chapter 85</span>
              </div>
              <div class="scenario-step">
                <span class="step-num">3</span>
                <span class="step-text">Re-extend to TARIC: query TARIC API for 8542 31 90 measures</span>
              </div>
              <div class="scenario-step">
                <span class="step-num">4</span>
                <span class="step-text">Check for surveillance, anti-dumping, preferential rates</span>
              </div>
            </div>

            <div class="scenario-result ok">
              <span class="scenario-result-label">Result:</span>
              CN 8542 31 90 → HS 8542.31 → TARIC 8542 31 90 00
              <br>Duty: 0% | No restrictions | Dual-use check: pass
            </div>
          </div>
        </div>
        """)

    with col_b:
        _md("""
        <div class="scenario-card">
          <div class="scenario-header">
            <div class="scenario-num">B</div>
            <div class="scenario-title">National Tariff Line (Country-Specific)</div>
          </div>
          <div class="scenario-body">
            <div class="scenario-label">Input</div>
            <div class="scenario-code">US HTS: 8542.31.0050</div>

            <div class="scenario-steps">
              <div class="scenario-step">
                <span class="step-num">1</span>
                <span class="step-text">Identify origin: US HTS (10-digit national code)</span>
              </div>
              <div class="scenario-step">
                <span class="step-num">2</span>
                <span class="step-text">Crosswalk lookup: US HTS 8542.31.0050 → HS 8542.31</span>
              </div>
              <div class="scenario-step">
                <span class="step-num">3</span>
                <span class="step-text">Validate: HS 8542.31 exists in tree (processors/controllers)</span>
              </div>
              <div class="scenario-step">
                <span class="step-num">4</span>
                <span class="step-text">Extend to EU TARIC: 8542 31 → query for EU-specific measures</span>
              </div>
            </div>

            <div class="scenario-result warn">
              <span class="scenario-result-label">Result:</span>
              US HTS 8542.31.0050 → HS 8542.31 → TARIC 8542 31 90 00
              <br>Duty: 0% | Crosswalk confidence: 94% | Flagged for manual verify
            </div>
          </div>
        </div>
        """)

    st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)

    col_c, col_d = st.columns([1, 1])

    with col_c:
        _md("""
        <div class="scenario-card">
          <div class="scenario-header">
            <div class="scenario-num">C</div>
            <div class="scenario-title">No Code — Text Description Only</div>
          </div>
          <div class="scenario-body">
            <div class="scenario-label">Input</div>
            <div class="scenario-code">"Ceramic coffee mugs, glazed, 350ml, made in China"</div>

            <div class="scenario-steps">
              <div class="scenario-step">
                <span class="step-num">1</span>
                <span class="step-text">Extract keywords: ceramic, coffee, mugs, glazed, 350ml</span>
              </div>
              <div class="scenario-step">
                <span class="step-num">2</span>
                <span class="step-text">Score chapters: Ch.69 (ceramics) = 0.87, Ch.70 (glass) = 0.21</span>
              </div>
              <div class="scenario-step">
                <span class="step-num">3</span>
                <span class="step-text">Walk tree: 69 → 6911 (tableware of porcelain) → 6911.10</span>
              </div>
              <div class="scenario-step">
                <span class="step-num">4</span>
                <span class="step-text">Material check: "ceramic" + "glazed" → porcelain subheading confirmed</span>
              </div>
            </div>

            <div class="scenario-result warn">
              <span class="scenario-result-label">Result:</span>
              Suggested: 6911.10 (Tableware of porcelain or china)
              <br>Confidence: 87% | Duty: 12% | Flagged for customs officer review
            </div>
          </div>
        </div>
        """)

    with col_d:
        _md("""
        <div class="scenario-card">
          <div class="scenario-header">
            <div class="scenario-num">D</div>
            <div class="scenario-title">Internal SKU — Historical Lookup</div>
          </div>
          <div class="scenario-body">
            <div class="scenario-label">Input</div>
            <div class="scenario-code">Shipper SKU: SHENZ-ELEC-IC7805</div>

            <div class="scenario-steps">
              <div class="scenario-step">
                <span class="step-num">1</span>
                <span class="step-text">Query shipment history: SKU SHENZ-ELEC-IC7805 seen 14 times</span>
              </div>
              <div class="scenario-step">
                <span class="step-num">2</span>
                <span class="step-text">Last classification: 8542.31 (2026-04-12, cleared by customs)</span>
              </div>
              <div class="scenario-step">
                <span class="step-num">3</span>
                <span class="step-text">Validate: HS 8542.31 still active in current TARIC version</span>
              </div>
              <div class="scenario-step">
                <span class="step-num">4</span>
                <span class="step-text">Check: no duty rate changes or new restrictions since last clearance</span>
              </div>
            </div>

            <div class="scenario-result ok">
              <span class="scenario-result-label">Result:</span>
              SKU SHENZ-ELEC-IC7805 → HS 8542.31 (from history)
              <br>Confidence: 96% | Duty: 0% | Auto-approved (repeat shipper, clean record)
            </div>
          </div>
        </div>
        """)

    st.markdown("<div style='height:24px'></div>", unsafe_allow_html=True)

    _md("""
    <div class="pipeline-flow">
      <div class="pipeline-flow-title">Pre-Classification Decision Tree</div>
      <div class="pipeline-flow-diagram" style="flex-wrap:wrap;gap:12px;">
        <div class="flow-step">
          <div class="flow-box">Raw Input</div>
          <div class="flow-arrow">→</div>
        </div>
        <div class="flow-step">
          <div class="flow-box highlight">Input Type Detection</div>
          <div class="flow-arrow">→</div>
        </div>
      </div>
      <div class="pipeline-flow-diagram" style="flex-wrap:wrap;gap:12px;margin-top:8px;">
        <div class="flow-step">
          <div class="flow-box">8-digit → CN strip</div>
          <div class="flow-arrow">|</div>
        </div>
        <div class="flow-step">
          <div class="flow-box">10-digit → crosswalk</div>
          <div class="flow-arrow">|</div>
        </div>
        <div class="flow-step">
          <div class="flow-box">text → keyword classify</div>
          <div class="flow-arrow">|</div>
        </div>
        <div class="flow-step">
          <div class="flow-box">SKU → history lookup</div>
        </div>
      </div>
      <div class="pipeline-flow-diagram" style="flex-wrap:wrap;gap:12px;margin-top:8px;">
        <div class="flow-step">
          <div class="flow-arrow" style="margin-right:0;">→</div>
          <div class="flow-box highlight">6-digit HS Code</div>
          <div class="flow-arrow">→</div>
        </div>
        <div class="flow-step">
          <div class="flow-box">Tree Walk</div>
          <div class="flow-arrow">→</div>
        </div>
        <div class="flow-step">
          <div class="flow-box">TARIC Extension</div>
          <div class="flow-arrow">→</div>
        </div>
        <div class="flow-step">
          <div class="flow-box">Diagnostic Output</div>
        </div>
      </div>
      <div class="pipeline-flow-note">
        Input type detection uses regex patterns: 8-digit numeric = CN, 10-digit with dots = national tariff,
        alphabetic string = text description, alphanumeric with dashes = internal SKU.
        All paths converge at a validated 6-digit HS code before entering the tree walk.
        Confidence below 80% triggers manual review queue regardless of input type.
      </div>
    </div>
    """)

    st.markdown("<div style='height:32px'></div>", unsafe_allow_html=True)

    _md("""
    <div class="section">
      <div class="section-head">
        <div class="left"><h2>LLM vs Regex <span class="muted">— where AI outperforms pattern matching</span></h2></div>
        <div class="right">6 test cases</div>
      </div>
    </div>
    """)

    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

    _md("""
    <div class="pipeline-detail-text" style="margin-bottom:20px;">
      Regex-based classification works well for structured data but struggles with ambiguity, context, and multi-language inputs.
      Large Language Models (LLMs) understand semantics, infer context, and handle natural language descriptions.
      Below are real-world cases where LLM classification outperforms regex pattern matching.
    </div>
    """)

    # Import LLM classifier
    from llm_classifier import get_all_test_cases, get_statistics

    stats = get_statistics()
    test_cases = get_all_test_cases()

    # Statistics summary
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Test Cases", stats["total_cases"])
    with col2:
        st.metric("Regex Accuracy", f"{stats['regex_accuracy']:.0%}")
    with col3:
        st.metric("LLM Accuracy", f"{stats['llm_accuracy']:.0%}")
    with col4:
        improvement = stats['llm_accuracy'] - stats['regex_accuracy']
        st.metric("LLM Improvement", f"+{improvement:.0%}")

    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

    # Test cases
    for i, case in enumerate(test_cases, 1):
        with st.expander(f"Case {i}: {case['description']}", expanded=(i == 1)):
            col_desc, col_context = st.columns([1, 1])
            with col_desc:
                st.markdown("**Description:**")
                st.code(case['description'])
            with col_context:
                st.markdown("**Context:**")
                st.info(case['context'])

            st.markdown("---")

            col_regex, col_llm = st.columns([1, 1])

            with col_regex:
                regex = case['regex_result']
                status_icon = "✓" if regex.correct else "✗"
                status_color = "green" if regex.correct else "red"

                st.markdown(f"**Regex Classification** <span style='color:{status_color}'>{status_icon}</span>", unsafe_allow_html=True)
                st.markdown(f"**HS Code:** `{regex.hs_code}`")
                st.markdown(f"**Confidence:** {regex.confidence:.0%}")
                st.markdown("**Reasoning:**")
                st.caption(regex.reasoning)

                if not regex.correct:
                    st.error("Incorrect classification")

            with col_llm:
                llm = case['llm_result']
                status_icon = "✓" if llm.correct else "✗"
                status_color = "green" if llm.correct else "red"

                st.markdown(f"**LLM Classification** <span style='color:{status_color}'>{status_icon}</span>", unsafe_allow_html=True)
                st.markdown(f"**HS Code:** `{llm.hs_code}`")
                st.markdown(f"**Confidence:** {llm.confidence:.0%}")
                st.markdown("**Reasoning:**")
                st.caption(llm.reasoning)

                if llm.correct:
                    st.success("Correct classification")

    st.markdown("<div style='height:24px'></div>", unsafe_allow_html=True)

    _md("""
    <div class="pipeline-details">
      <div class="pipeline-details-title">Why LLMs Excel at Classification</div>
      
      <div class="pipeline-detail-section">
        <div class="pipeline-detail-label">1. Semantic Understanding</div>
        <div class="pipeline-detail-text">
          LLMs understand that "plastic housing for electronic device" is primarily an electronic component,
          not a plastic article. They grasp the concept of "essential character" — a key principle in HS classification.
          Regex only sees keywords and cannot infer relationships between them.
        </div>
      </div>
      
      <div class="pipeline-detail-section">
        <div class="pipeline-detail-label">2. Multi-Language Support</div>
        <div class="pipeline-detail-text">
          LLMs are trained on multilingual data and can classify descriptions in German, Chinese, French, etc.
          without separate regex patterns for each language. They understand that "Elektronische Spannungsregler"
          (German) and "电子稳压器" (Chinese) both mean "electronic voltage regulators".
        </div>
      </div>
      
      <div class="pipeline-detail-section">
        <div class="pipeline-detail-label">3. Context Awareness</div>
        <div class="pipeline-detail-text">
          When given context like "for repair of industrial control system", LLMs apply General Rule of Interpretation 3(b):
          kits are classified by their essential character. Regex cannot make this inference from keywords alone.
        </div>
      </div>
      
      <div class="pipeline-detail-section">
        <div class="pipeline-detail-label">4. Natural Language Explanations</div>
        <div class="pipeline-detail-text">
          LLMs provide human-readable reasoning that customs officers can understand and verify.
          This builds trust and enables faster dispute resolution. Regex provides only confidence scores
          without explanation of why a classification was chosen.
        </div>
      </div>
      
      <div class="pipeline-detail-section">
        <div class="pipeline-detail-label">5. Handling Ambiguity</div>
        <div class="pipeline-detail-text">
          Products like "ceramic coffee mug with electronic heating element" are genuinely ambiguous.
          LLMs can weigh competing factors (ceramic vs electronic) and apply classification rules.
          Regex would need explicit rules for every possible combination, which is impractical.
        </div>
      </div>
    </div>
    """)

    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

    _md("""
    <div class="pipeline-flow">
      <div class="pipeline-flow-title">Hybrid Approach: Best of Both Worlds</div>
      <div class="pipeline-flow-note">
        <strong>Production Strategy:</strong> Use regex for high-confidence structured data (HS codes, CN codes, SKUs).
        Route ambiguous descriptions, multi-language inputs, and low-confidence regex results to LLM.
        This balances speed (regex: ~10ms) with accuracy (LLM: ~500ms) and cost (LLM API calls are expensive).
        <br><br>
        <strong>Confidence Thresholds:</strong> Regex confidence > 90% → accept. 70-90% → LLM verification. < 70% → LLM classification.
        This ensures fast processing for clear cases while leveraging AI for complex scenarios.
      </div>
    </div>
    """)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    render_topbar()

    st.markdown("<div style='height:24px'></div>", unsafe_allow_html=True)

    tab_dashboard, tab_pipeline, tab_api, tab_docs = st.tabs(["Dashboard", "Data Pipeline", "API", "Documents"])

    with tab_dashboard:
        df, lane_statuses = render_data_input()
        
        # Apply filters
        df, lane_statuses = render_filters(df, lane_statuses)

        kpis = compute_kpis(df, lane_statuses, today=TODAY)
        render_kpi_strip(kpis)

        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

        render_exceptions_first(df, lane_statuses)
        render_diagnostics()
        render_lane_matrix(df, lane_statuses)
        render_data_freshness()
        render_footer()

    with tab_pipeline:
        render_pipeline_showcase()

    with tab_api:
        render_api_showcase()

    with tab_docs:
        render_documents_showcase()


def render_api_showcase() -> None:
    """Render API documentation and example requests/responses."""
    st.markdown("## API Layer")
    st.markdown("REST API for programmatic access to parcel operations data.")
    
    st.info("**Note:** The API server runs separately from this dashboard. On Streamlit Cloud, this tab shows documentation only. For local development, run `uvicorn api:app --reload --port 8000` to start the API server.")
    
    # Configurable base URL
    api_base_url = st.text_input(
        "API Base URL",
        value="http://localhost:8000",
        help="Base URL for API examples. Change this if your API runs on a different host/port.",
        key="api_base_url"
    )
    
    st.markdown("### Base URL")
    st.code(api_base_url, language="text")
    
    st.markdown("### Authentication")
    st.markdown("Mock API — no authentication required. Production would use API keys or OAuth2.")
    
    st.markdown("---")
    
    # Health check
    st.markdown("### `GET /health`")
    st.markdown("Health check endpoint.")
    
    with st.expander("Example", expanded=False):
        st.markdown("**Request:**")
        st.code(f"curl {api_base_url}/health", language="bash")
        
        st.markdown("**Response:**")
        st.code("""{
  "status": "ok",
  "version": "0.1.0",
  "timestamp": "2026-05-28T15:30:45.123456"
}""", language="json")
    
    st.markdown("---")
    
    # List batches
    st.markdown("### `GET /api/batches`")
    st.markdown("List all shipment batches with optional filters.")
    
    st.markdown("**Query Parameters:**")
    st.markdown("- `carrier` (optional): Filter by carrier name")
    st.markdown("- `origin` (optional): Filter by origin country code")
    st.markdown("- `priority` (optional): Filter by priority (normal, high, critical)")
    
    with st.expander("Example: List all batches", expanded=False):
        st.markdown("**Request:**")
        st.code(f"curl {api_base_url}/api/batches", language="bash")
        
        st.markdown("**Response:**")
        st.code("""[
  {
    "batch_id": "FI-2026-001",
    "carrier": "DHL Express",
    "origin": "CN",
    "expected_arrival": "2026-05-28",
    "parcel_count": 450,
    "hs_code": "3926.90",
    "planned_owner": "Customs",
    "priority": "high",
    "notes": "HS code mismatch suspected"
  },
  {
    "batch_id": "FI-2026-002",
    "carrier": "DSV Air & Sea",
    "origin": "DE",
    "expected_arrival": "2026-05-29",
    "parcel_count": 320,
    "hs_code": "8542.31",
    "planned_owner": "Operations",
    "priority": "normal",
    "notes": ""
  }
]""", language="json")
    
    with st.expander("Example: Filter by carrier", expanded=False):
        st.markdown("**Request:**")
        st.code(f"curl '{api_base_url}/api/batches?carrier=DHL%20Express'", language="bash")
        
        st.markdown("**Response:**")
        st.code("""[
  {
    "batch_id": "FI-2026-001",
    "carrier": "DHL Express",
    "origin": "CN",
    "expected_arrival": "2026-05-28",
    "parcel_count": 450,
    "hs_code": "3926.90",
    "planned_owner": "Customs",
    "priority": "high",
    "notes": "HS code mismatch suspected"
  }
]""", language="json")
    
    st.markdown("---")
    
    # Get batch detail
    st.markdown("### `GET /api/batch/{batch_id}`")
    st.markdown("Get detailed information for a specific batch including lane statuses.")
    
    with st.expander("Example", expanded=False):
        st.markdown("**Request:**")
        st.code(f"curl {api_base_url}/api/batch/FI-2026-001", language="bash")
        
        st.markdown("**Response:**")
        st.code("""{
  "batch_id": "FI-2026-001",
  "carrier": "DHL Express",
  "origin": "CN",
  "expected_arrival": "2026-05-28",
  "parcel_count": 450,
  "hs_code": "3926.90",
  "planned_owner": "Customs",
  "priority": "high",
  "notes": "HS code mismatch suspected",
  "lane_statuses": [
    {"lane": "Arrival", "status": "ok"},
    {"lane": "Documents", "status": "warning"},
    {"lane": "H7", "status": "critical"},
    {"lane": "ICS2/ENS", "status": "ok"},
    {"lane": "Last-mile", "status": "pending"},
    {"lane": "Stopped", "status": "critical"},
    {"lane": "EU Trucks", "status": "ok"},
    {"lane": "Support", "status": "ok"}
  ]
}""", language="json")
    
    st.markdown("---")
    
    # Get diagnostics
    st.markdown("### `GET /api/batch/{batch_id}/diagnostics`")
    st.markdown("Get diagnostics for a specific batch.")
    
    with st.expander("Example", expanded=False):
        st.markdown("**Request:**")
        st.code(f"curl {api_base_url}/api/batch/FI-2026-001/diagnostics", language="bash")
        
        st.markdown("**Response:**")
        st.code("""[
  {
    "batch_id": "FI-2026-001",
    "issue_type": "hs_mismatch",
    "severity": "critical",
    "confidence": 0.92,
    "declared": "3926.90",
    "expected": "8542.31",
    "source": "Document Store",
    "detail": "Invoice describes electronic components but HS code is for plastic articles",
    "suggested_action": "Contact shipper for clarification and request amended invoice",
    "duty_impact": "Potential 6.5% duty difference"
  }
]""", language="json")
    
    st.markdown("---")
    
    # Submit amendment
    st.markdown("### `POST /api/batch/{batch_id}/amend`")
    st.markdown("Submit an amendment request for a batch field.")
    
    st.markdown("**Request Body:**")
    st.code("""{
  "batch_id": "FI-2026-001",
  "field": "hs_code",
  "old_value": "3926.90",
  "new_value": "8542.31",
  "reason": "Invoice describes electronic components, not plastic articles"
}""", language="json")
    
    with st.expander("Example", expanded=False):
        st.markdown("**Request:**")
        st.code(f"""curl -X POST {api_base_url}/api/batch/FI-2026-001/amend \\
  -H "Content-Type: application/json" \\
  -d '{{
    "batch_id": "FI-2026-001",
    "field": "hs_code",
    "old_value": "3926.90",
    "new_value": "8542.31",
    "reason": "Invoice describes electronic components, not plastic articles"
  }}'""", language="bash")
        
        st.markdown("**Response:**")
        st.code("""{
  "status": "submitted",
  "amendment_id": "AMD-A1B2C3D4",
  "message": "Amendment request AMD-A1B2C3D4 submitted for batch FI-2026-001. Field 'hs_code' will be updated from '3926.90' to '8542.31'."
}""", language="json")
    
    st.markdown("---")
    
    # Get HS code tree
    st.markdown("### `GET /api/hs-code/{code}/tree`")
    st.markdown("Get the HS code tree path for a given code.")
    
    with st.expander("Example", expanded=False):
        st.markdown("**Request:**")
        st.code(f"curl {api_base_url}/api/hs-code/8542.31/tree", language="bash")
        
        st.markdown("**Response:**")
        st.code("""[
  {
    "code": "Section XVI",
    "label": "Machinery and mechanical appliances; electrical equipment",
    "duty": null,
    "highlight": null
  },
  {
    "code": "85",
    "label": "Electrical machinery and equipment and parts thereof",
    "duty": null,
    "highlight": null
  },
  {
    "code": "8542",
    "label": "Electronic integrated circuits",
    "duty": null,
    "highlight": null
  },
  {
    "code": "8542.31",
    "label": "Processors and controllers",
    "duty": "0%",
    "highlight": "expected"
  }
]""", language="json")
    
    st.markdown("---")
    
    # List all diagnostics
    st.markdown("### `GET /api/diagnostics`")
    st.markdown("List all diagnostics across all batches.")
    
    with st.expander("Example", expanded=False):
        st.markdown("**Request:**")
        st.code(f"curl {api_base_url}/api/diagnostics", language="bash")
        
        st.markdown("**Response:**")
        st.code("""[
  {
    "batch_id": "FI-2026-001",
    "issue_type": "hs_mismatch",
    "severity": "critical",
    "confidence": 0.92,
    "declared": "3926.90",
    "expected": "8542.31",
    "source": "Document Store",
    "detail": "Invoice describes electronic components but HS code is for plastic articles",
    "suggested_action": "Contact shipper for clarification and request amended invoice",
    "duty_impact": "Potential 6.5% duty difference"
  },
  {
    "batch_id": "FI-2026-004",
    "issue_type": "missing_documents",
    "severity": "warning",
    "confidence": 0.85,
    "declared": "",
    "expected": "Commercial Invoice",
    "source": "Document Store",
    "detail": "Required document missing from batch",
    "suggested_action": "Contact shipper to obtain missing documents",
    "duty_impact": "Cannot assess duty impact without documents"
  }
]""", language="json")
    
    st.markdown("---")
    
    # Interactive docs
    st.markdown("### Interactive Documentation")
    st.markdown("Start the API server to access interactive Swagger UI:")
    st.code("uvicorn api:app --reload --port 8000", language="bash")
    st.markdown(f"Then visit: [{api_base_url}/docs]({api_base_url}/docs)")
    
    st.markdown("---")
    
    # Integration example
    st.markdown("### Integration Example")
    st.markdown("Python client using the API:")
    
    st.code(f"""import requests

# List all batches
response = requests.get("{api_base_url}/api/batches")
batches = response.json()

# Get diagnostics for a specific batch
batch_id = "FI-2026-001"
response = requests.get(f"{api_base_url}/api/batch/{{batch_id}}/diagnostics")
diagnostics = response.json()

for diag in diagnostics:
    print(f"Issue: {{diag['issue_type']}}")
    print(f"Severity: {{diag['severity']}}")
    print(f"Action: {{diag['suggested_action']}}")
    print()

# Submit an amendment
amendment = {{
    "batch_id": batch_id,
    "field": "hs_code",
    "old_value": "3926.90",
    "new_value": "8542.31",
    "reason": "Corrected based on invoice description"
}}
response = requests.post(
    f"{api_base_url}/api/batch/{{batch_id}}/amend",
    json=amendment
)
print(response.json())
""", language="python")


def render_documents_showcase() -> None:
    """Render PDF document generation showcase."""
    from pdf_generator import generate_amendment_letter, generate_carrier_notification, generate_escalation_report
    
    st.markdown("## Document Generator")
    st.markdown("Automated PDF generation for customs operations workflows.")
    
    st.markdown("### Use Cases")
    st.markdown("- **Amendment Letters**: Formal requests to customs authorities for declaration changes")
    st.markdown("- **Carrier Notifications**: Issue alerts and action requests to carriers")
    st.markdown("- **Escalation Reports**: Internal reports for operations management")
    
    st.markdown("---")
    
    # Amendment Letter
    st.markdown("### Amendment Request Letter")
    st.markdown("Formal letter to customs authorities requesting declaration amendment.")
    
    col1, col2 = st.columns([1, 1])
    
    with col1:
        st.markdown("**Example: HS Code Correction**")
        st.markdown("- **Batch**: FI-2026-001")
        st.markdown("- **Field**: HS Code")
        st.markdown("- **Current**: 3926.90 (Plastic articles)")
        st.markdown("- **Proposed**: 8542.31 (Electronic integrated circuits)")
        st.markdown("- **Reason**: Invoice describes electronic components, not plastic articles")
    
    with col2:
        if st.button("Generate Amendment Letter", key="gen_amendment"):
            pdf_buffer = generate_amendment_letter(
                batch_id="FI-2026-001",
                field="HS Code",
                old_value="3926.90 (Plastic articles)",
                new_value="8542.31 (Electronic integrated circuits)",
                reason="Commercial invoice describes electronic voltage regulators and integrated circuits. HS code 3926.90 is for plastic articles. Correct classification is 8542.31 based on product description and TARIC database lookup.",
                carrier="DHL Express",
                origin="CN",
                parcel_count=450,
                hs_code="3926.90",
            )
            st.download_button(
                label="Download PDF",
                data=pdf_buffer,
                file_name="amendment_FI-2026-001.pdf",
                mime="application/pdf",
            )
    
    st.markdown("---")
    
    # Carrier Notification
    st.markdown("### Carrier Notification")
    st.markdown("Notification letter to carrier regarding shipment issues.")
    
    col1, col2 = st.columns([1, 1])
    
    with col1:
        st.markdown("**Example: Missing Documents Alert**")
        st.markdown("- **Batch**: FI-2026-004")
        st.markdown("- **Carrier**: Kuehne+Nagel")
        st.markdown("- **Issue**: Missing commercial invoice")
        st.markdown("- **Severity**: High")
        st.markdown("- **Deadline**: 48 hours")
    
    with col2:
        if st.button("Generate Carrier Notification", key="gen_carrier"):
            pdf_buffer = generate_carrier_notification(
                batch_id="FI-2026-004",
                carrier="Kuehne+Nagel",
                issue_type="missing_documents",
                severity="high",
                detail="Commercial invoice is missing from shipment documentation. This document is required for customs clearance and duty assessment. Without it, the shipment cannot proceed through H7 clearance.",
                required_action="Please provide the commercial invoice including: shipper details, consignee information, complete product descriptions, HS codes, unit values, and total shipment value. Submit via document portal or email to docs@parcel-ops.example.com.",
                deadline="2026-05-30 17:00",
                contact_email="ops@parcel-ops.example.com",
            )
            st.download_button(
                label="Download PDF",
                data=pdf_buffer,
                file_name="notification_FI-2026-004.pdf",
                mime="application/pdf",
            )
    
    st.markdown("---")
    
    # Escalation Report
    st.markdown("### Internal Escalation Report")
    st.markdown("Internal report for operations management review.")
    
    col1, col2 = st.columns([1, 1])
    
    with col1:
        st.markdown("**Example: Critical Batch Escalation**")
        st.markdown("- **Batch**: FI-2026-007")
        st.markdown("- **Carrier**: DHL Express")
        st.markdown("- **Critical Lanes**: H7, Stopped")
        st.markdown("- **Diagnostics**: HS code mismatch (92% confidence)")
        st.markdown("- **Assigned To**: Operations Manager")
    
    with col2:
        if st.button("Generate Escalation Report", key="gen_escalation"):
            pdf_buffer = generate_escalation_report(
                batch_id="FI-2026-007",
                carrier="DHL Express",
                origin="CN",
                parcel_count=150,
                hs_code="3926.90",
                critical_lanes=["H7", "Stopped"],
                diagnostics=[
                    {
                        "issue_type": "hs_mismatch",
                        "severity": "critical",
                        "confidence": 0.92,
                        "detail": "Invoice describes electronic components but HS code is for plastic articles. Potential duty evasion or misclassification.",
                        "suggested_action": "Contact shipper immediately for clarification. Request amended invoice with correct HS code 8542.31.",
                    }
                ],
                escalation_reason="Batch has critical lane statuses (H7 and Stopped) with high-confidence HS code mismatch diagnostic. Requires immediate management review to determine if customs hold is justified or if amendment request should be submitted. Risk of shipment delay and potential penalties if not resolved within 72 hours.",
                assigned_to="Operations Manager",
            )
            st.download_button(
                label="Download PDF",
                data=pdf_buffer,
                file_name="escalation_FI-2026-007.pdf",
                mime="application/pdf",
            )
    
    st.markdown("---")
    
    # Technical details
    st.markdown("### Technical Implementation")
    
    with st.expander("PDF Generation Architecture", expanded=False):
        st.markdown("**Library**: ReportLab (Python PDF generation)")
        st.markdown("**Page Format**: A4 (210mm × 297mm)")
        st.markdown("**Margins**: 2cm all sides")
        st.markdown("**Fonts**: Helvetica (standard PDF fonts, no embedding required)")
        
        st.markdown("**Document Structure**:")
        st.markdown("- Header with title and metadata")
        st.markdown("- Tabular data for batch information")
        st.markdown("- Structured sections with headings")
        st.markdown("- Signature blocks for formal documents")
        st.markdown("- Footer with generation timestamp")
        
        st.markdown("**Styling**:")
        st.markdown("- Custom paragraph styles for consistent formatting")
        st.markdown("- Color-coded severity indicators")
        st.markdown("- Professional business document layout")
        st.markdown("- Responsive table layouts")
    
    with st.expander("Integration Points", expanded=False):
        st.markdown("**API Integration**:")
        st.code("""# Generate amendment letter from API response
API_BASE = "http://localhost:8000"  # Configure for your environment
response = requests.get(f"{API_BASE}/api/batch/FI-2026-001/diagnostics")
diagnostics = response.json()

if diagnostics:
    diag = diagnostics[0]
    pdf = generate_amendment_letter(
        batch_id=diag['batch_id'],
        field="HS Code",
        old_value=diag['declared'],
        new_value=diag['expected'],
        reason=diag['detail'],
        carrier="DHL Express",
        origin="CN",
        parcel_count=450,
        hs_code=diag['declared'],
    )
    
    # Save or email PDF
    with open(f"amendment_{diag['batch_id']}.pdf", "wb") as f:
        f.write(pdf.read())
""", language="python")
        
        st.markdown("**Workflow Automation**:")
        st.markdown("1. Diagnostic detected → Generate amendment letter")
        st.markdown("2. Carrier issue identified → Generate notification")
        st.markdown("3. Critical escalation triggered → Generate report")
        st.markdown("4. Documents attached to batch record in system")
        st.markdown("5. Email notifications sent to relevant parties")
    
    st.markdown("---")
    
    st.markdown("### Document Templates")
    st.markdown("All documents use standardized templates for consistency:")
    
    st.markdown("**Amendment Letter Template**:")
    st.markdown("- Formal business letter format")
    st.markdown("- Customs authority address block")
    st.markdown("- Batch reference and shipment details")
    st.markdown("- Amendment details (field, old value, new value)")
    st.markdown("- Reason and justification")
    st.markdown("- Declaration of accuracy")
    st.markdown("- Signature block")
    
    st.markdown("**Carrier Notification Template**:")
    st.markdown("- Priority header with severity color")
    st.markdown("- Shipment identification")
    st.markdown("- Issue description and impact")
    st.markdown("- Required action with deadline")
    st.markdown("- Contact information")
    st.markdown("- Acknowledgment request")
    
    st.markdown("**Escalation Report Template**:")
    st.markdown("- Internal report header")
    st.markdown("- Batch summary with all details")
    st.markdown("- Critical lanes list")
    st.markdown("- Diagnostics with confidence scores")
    st.markdown("- Escalation reason and context")
    st.markdown("- Recommended action items")
    st.markdown("- Assignment to operations manager")


if __name__ == "__main__":
    main()
