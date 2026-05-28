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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    render_topbar()

    st.markdown("<div style='height:24px'></div>", unsafe_allow_html=True)

    tab_dashboard, tab_pipeline = st.tabs(["Dashboard", "Data Pipeline"])

    with tab_dashboard:
        df, lane_statuses = render_data_input()

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


if __name__ == "__main__":
    main()
