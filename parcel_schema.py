"""Parcel Ops schema, normalization helpers, and synthetic demo data.

This module owns:
  - Column contracts for uploaded data (normalize_shipment_data)
  - Synthetic demo data generator (demo_shipments)
  - KPI aggregation helpers
  - Lane status enums and color mapping
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal

import pandas as pd


# ---------------------------------------------------------------------------
# Lane definitions
# ---------------------------------------------------------------------------

LANES: list[str] = [
    "Arrival",
    "ICS2/ENS",
    "H7",
    "Documents",
    "Stopped",
    "Last-mile",
    "EU Trucks",
    "Support",
]

LANE_LABELS: dict[str, str] = {
    "Arrival":    "Arrival / Terminal",
    "ICS2/ENS":  "ICS2 / ENS",
    "H7":         "H7 Clearance",
    "Documents":  "Documents",
    "Stopped":    "Stopped Shipments",
    "Last-mile":  "Last-mile Handover",
    "EU Trucks":  "EU Trucks",
    "Support":    "Support / SLA",
}

LaneStatus = Literal["ok", "warning", "critical", "pending", "n/a"]

LANE_CSS: dict[LaneStatus, str] = {
    "ok":       "ok",
    "warning":  "warning",
    "critical": "critical",
    "pending":  "",
    "n/a":      "",
}

LANE_LABEL_TEXT: dict[LaneStatus, str] = {
    "ok":       "OK",
    "warning":  "WARN",
    "critical": "CRIT",
    "pending":  "PENDING",
    "n/a":      "N/A",
}


# ---------------------------------------------------------------------------
# Shipment batch schema
# ---------------------------------------------------------------------------

REQUIRED_COLUMNS = [
    "batch_id",       # str — unique identifier, e.g. "FI-2026-06-001"
    "carrier",        # str — carrier / freight forwarder name
    "origin",         # str — country code (ISO 2)
    "expected_arrival",  # date
    "parcel_count",   # int
]

OPTIONAL_COLUMNS = [
    "hs_code",        # str — primary HS code for the batch
    "planned_owner",  # str — ops team responsible
    "priority",       # str: "normal" | "high" | "critical"
    "notes",          # str
]


def normalize_shipment_data(
    raw: pd.DataFrame,
    default_source: str = "upload",
) -> tuple[pd.DataFrame | None, list[str]]:
    """Normalize an uploaded DataFrame to the shipment schema.

    Returns (normalized_df, errors). If critical columns are missing,
    returns (None, [errors]).
    """
    errors: list[str] = []
    missing = [c for c in REQUIRED_COLUMNS if c not in raw.columns]
    if missing:
        errors.append(f"Missing required columns: {', '.join(missing)}")
        return None, errors

    df = raw.copy()

    # Parse date
    if not pd.api.types.is_datetime64_any_dtype(df["expected_arrival"]):
        try:
            df["expected_arrival"] = pd.to_datetime(df["expected_arrival"]).dt.date
        except Exception as exc:
            errors.append(f"Could not parse expected_arrival: {exc}")

    # Fill optional columns
    for col in OPTIONAL_COLUMNS:
        if col not in df.columns:
            df[col] = ""

    if "priority" not in df.columns or df["priority"].isna().all():
        df["priority"] = "normal"

    return df, errors


def shipment_template() -> pd.DataFrame:
    """Return an empty DataFrame with the expected column schema."""
    return pd.DataFrame(columns=REQUIRED_COLUMNS + OPTIONAL_COLUMNS)


# ---------------------------------------------------------------------------
# Curated demo data — realistic scenarios with correlated lane statuses
# ---------------------------------------------------------------------------

_SCENARIOS: list[dict] = [
    {
        "suffix": "001",
        "carrier": "DHL Express",
        "origin": "CN",
        "arrival_offset": 0,
        "parcel_count": 450,
        "hs_code": "8471.30",
        "planned_owner": "Customs",
        "priority": "high",
        "notes": "Invoice missing for HS 8471.30 — customs broker notified",
        "lanes": {
            "Arrival": "ok", "ICS2/ENS": "ok", "H7": "critical",
            "Documents": "warning", "Stopped": "critical",
            "Last-mile": "pending", "EU Trucks": "n/a", "Support": "warning",
        },
    },
    {
        "suffix": "002",
        "carrier": "DSV Air & Sea",
        "origin": "DE",
        "arrival_offset": 0,
        "parcel_count": 320,
        "hs_code": "6110.30",
        "planned_owner": "Ops-A",
        "priority": "normal",
        "notes": "",
        "lanes": {
            "Arrival": "ok", "ICS2/ENS": "ok", "H7": "ok",
            "Documents": "ok", "Stopped": "ok",
            "Last-mile": "ok", "EU Trucks": "ok", "Support": "ok",
        },
    },
    {
        "suffix": "003",
        "carrier": "PostNord",
        "origin": "SE",
        "arrival_offset": -1,
        "parcel_count": 180,
        "hs_code": "4202.92",
        "planned_owner": "Ops-B",
        "priority": "high",
        "notes": "Last-mile backlog at Helsinki depot — 48h SLA pressure",
        "lanes": {
            "Arrival": "ok", "ICS2/ENS": "ok", "H7": "ok",
            "Documents": "ok", "Stopped": "ok",
            "Last-mile": "warning", "EU Trucks": "ok", "Support": "warning",
        },
    },
    {
        "suffix": "004",
        "carrier": "Kuehne+Nagel",
        "origin": "TR",
        "arrival_offset": 2,
        "parcel_count": 620,
        "hs_code": "8517.62",
        "planned_owner": "Customs",
        "priority": "high",
        "notes": "ENS filing not submitted — broker awaiting commercial invoice",
        "lanes": {
            "Arrival": "pending", "ICS2/ENS": "critical", "H7": "pending",
            "Documents": "warning", "Stopped": "pending",
            "Last-mile": "pending", "EU Trucks": "pending", "Support": "ok",
        },
    },
    {
        "suffix": "005",
        "carrier": "DB Schenker",
        "origin": "PL",
        "arrival_offset": -2,
        "parcel_count": 290,
        "hs_code": "9403.60",
        "planned_owner": "Ops-A",
        "priority": "normal",
        "notes": "",
        "lanes": {
            "Arrival": "ok", "ICS2/ENS": "ok", "H7": "ok",
            "Documents": "ok", "Stopped": "ok",
            "Last-mile": "ok", "EU Trucks": "warning", "Support": "ok",
        },
    },
    {
        "suffix": "006",
        "carrier": "Maersk Logistics",
        "origin": "KR",
        "arrival_offset": 5,
        "parcel_count": 780,
        "hs_code": "8528.72",
        "planned_owner": "Ops-B",
        "priority": "normal",
        "notes": "Sea freight — ETA 5 days, pre-arrival docs not yet required",
        "lanes": {
            "Arrival": "pending", "ICS2/ENS": "pending", "H7": "pending",
            "Documents": "pending", "Stopped": "pending",
            "Last-mile": "pending", "EU Trucks": "pending", "Support": "ok",
        },
    },
    {
        "suffix": "007",
        "carrier": "DHL Express",
        "origin": "CN",
        "arrival_offset": -1,
        "parcel_count": 150,
        "hs_code": "3926.90",
        "planned_owner": "Customs",
        "priority": "critical",
        "notes": "Customs hold — HS code mismatch, documents rejected, broker escalation",
        "lanes": {
            "Arrival": "ok", "ICS2/ENS": "warning", "H7": "critical",
            "Documents": "critical", "Stopped": "critical",
            "Last-mile": "pending", "EU Trucks": "n/a", "Support": "critical",
        },
    },
    {
        "suffix": "008",
        "carrier": "DSV Air & Sea",
        "origin": "NL",
        "arrival_offset": 0,
        "parcel_count": 95,
        "hs_code": "2204.21",
        "planned_owner": "Ops-A",
        "priority": "normal",
        "notes": "",
        "lanes": {
            "Arrival": "ok", "ICS2/ENS": "ok", "H7": "ok",
            "Documents": "ok", "Stopped": "ok",
            "Last-mile": "ok", "EU Trucks": "ok", "Support": "ok",
        },
    },
    {
        "suffix": "009",
        "carrier": "PostNord",
        "origin": "SE",
        "arrival_offset": -3,
        "parcel_count": 410,
        "hs_code": "6403.99",
        "planned_owner": "Ops-B",
        "priority": "critical",
        "notes": "SLA breached — 72h since arrival, delivery attempts failed",
        "lanes": {
            "Arrival": "ok", "ICS2/ENS": "ok", "H7": "ok",
            "Documents": "ok", "Stopped": "ok",
            "Last-mile": "critical", "EU Trucks": "ok", "Support": "critical",
        },
    },
    {
        "suffix": "010",
        "carrier": "DB Schenker",
        "origin": "US",
        "arrival_offset": 1,
        "parcel_count": 540,
        "hs_code": "8544.42",
        "planned_owner": "Customs",
        "priority": "high",
        "notes": "Pre-arrival — ICS2 and invoice chase, arriving tomorrow",
        "lanes": {
            "Arrival": "pending", "ICS2/ENS": "warning", "H7": "pending",
            "Documents": "warning", "Stopped": "pending",
            "Last-mile": "pending", "EU Trucks": "pending", "Support": "ok",
        },
    },
]


def demo_shipments(today: date | None = None, n: int = 10) -> pd.DataFrame:
    """Return curated shipment batches for demo mode.

    The *n* parameter is accepted for API compatibility but ignored;
    all 10 scenarios are always returned.
    """
    today = today or date.today()
    rows = []
    for s in _SCENARIOS:
        rows.append({
            "batch_id": f"FI-{today.year}-{s['suffix']}",
            "carrier": s["carrier"],
            "origin": s["origin"],
            "expected_arrival": today + timedelta(days=s["arrival_offset"]),
            "parcel_count": s["parcel_count"],
            "hs_code": s["hs_code"],
            "planned_owner": s["planned_owner"],
            "priority": s["priority"],
            "notes": s["notes"],
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Lane status generator (curated)
# ---------------------------------------------------------------------------

def demo_lane_statuses(batch_ids: list[str]) -> dict[str, dict[str, LaneStatus]]:
    """Return correlated lane statuses for curated demo scenarios.

    Matches by batch_id suffix. Falls back to all-pending for unknown IDs
    (e.g. uploaded data).
    """
    scenario_by_suffix = {s["suffix"]: s for s in _SCENARIOS}
    result: dict[str, dict[str, LaneStatus]] = {}
    for bid in batch_ids:
        suffix = bid.rsplit("-", 1)[-1] if "-" in bid else bid
        scenario = scenario_by_suffix.get(suffix)
        if scenario:
            result[bid] = dict(scenario["lanes"])
        else:
            result[bid] = {lane: "pending" for lane in LANES}
    return result


# ---------------------------------------------------------------------------
# KPI aggregation
# ---------------------------------------------------------------------------

@dataclass
class OpsKPIs:
    total_parcels: int = 0
    batches_arriving_today: int = 0
    stopped_count: int = 0
    h7_pending: int = 0
    critical_count: int = 0
    sla_breach_risk: int = 0


def compute_kpis(
    df: pd.DataFrame,
    lane_statuses: dict[str, dict[str, LaneStatus]],
    today: date | None = None,
) -> OpsKPIs:
    today = today or date.today()
    kpis = OpsKPIs()
    kpis.total_parcels = int(df["parcel_count"].sum())
    kpis.batches_arriving_today = int(
        (df["expected_arrival"] == today).sum()
        if "expected_arrival" in df.columns else 0
    )
    for bid, lanes in lane_statuses.items():
        if lanes.get("Stopped") == "critical":
            kpis.stopped_count += 1
        elif lanes.get("Stopped") == "warning":
            kpis.stopped_count += 1
        if lanes.get("H7") in ("warning", "pending"):
            kpis.h7_pending += 1
        if any(v == "critical" for v in lanes.values()):
            kpis.critical_count += 1
        if lanes.get("Support") in ("warning", "critical"):
            kpis.sla_breach_risk += 1
    return kpis


# ---------------------------------------------------------------------------
# Data source freshness (synthetic for demo)
# ---------------------------------------------------------------------------

@dataclass
class FreshnessEntry:
    name: str
    status: Literal["fresh", "stale", "missing", "partial"]
    last_updated: str  # human-readable relative time
    detail: str


def demo_data_freshness() -> list[FreshnessEntry]:
    """Return freshness entries reflecting current demo scenarios."""
    return [
        FreshnessEntry("Carrier API", "fresh", "2 min ago", "DHL, DSV, PostNord, Schenker connected"),
        FreshnessEntry("Customs API", "stale", "47 min ago", "H7 feed delayed — 2 batches unconfirmed (FI-001, FI-007)"),
        FreshnessEntry("ICS2 Portal", "partial", "14 min ago", "ENS gap for FI-004 (TR) — filing not received"),
        FreshnessEntry("Tracking DB", "fresh", "1 min ago", "Last-mile events streaming — FI-009 SLA breach flagged"),
        FreshnessEntry("Document Store", "partial", "8 min ago", "3 batches with doc issues (FI-001, FI-007, FI-010)"),
        FreshnessEntry("EU Truck Schedule", "missing", "—", "Integration not yet configured"),
    ]


# ---------------------------------------------------------------------------
# Diagnostics — automated issue detection and recommendations
# ---------------------------------------------------------------------------

@dataclass
class DiagnosticEntry:
    batch_id: str
    issue_type: str
    severity: str
    confidence: float
    declared: str
    expected: str
    source: str
    detail: str
    suggested_action: str
    duty_impact: str


def demo_diagnostics() -> list[DiagnosticEntry]:
    """Return mock diagnostics for demo scenarios."""
    return [
        DiagnosticEntry(
            batch_id="FI-2026-007",
            issue_type="hs_mismatch",
            severity="critical",
            confidence=0.92,
            declared="3926.90 (Plastic articles)",
            expected="8542.31 (Electronic integrated circuits)",
            source="Document Store",
            detail="Commercial invoice describes 'electronic components, circuit boards, ICs' but HS code 3926.90 is for plastic articles. Mismatch detected via invoice text analysis.",
            suggested_action="Contact shipper immediately. Amend H7 filing before customs inspection. Prepare for potential duty recalculation.",
            duty_impact="3.7% vs 0% = ~€2,100 additional duty",
        ),
        DiagnosticEntry(
            batch_id="FI-2026-001",
            issue_type="doc_missing",
            severity="high",
            confidence=0.85,
            declared="8471.30 (Data processing machines)",
            expected="8471.30 (Data processing machines)",
            source="Document Store",
            detail="Commercial invoice not received. Cannot validate HS code or declared value. Customs broker notified but no response in 4 hours.",
            suggested_action="Escalate to shipper. Request invoice via DHL portal. Consider provisional H7 filing with estimated value.",
            duty_impact="Unknown — invoice required for duty calculation",
        ),
        DiagnosticEntry(
            batch_id="FI-2026-004",
            issue_type="ens_missing",
            severity="high",
            confidence=0.95,
            declared="8517.62 (Telecom equipment)",
            expected="8517.62 (Telecom equipment)",
            source="ICS2 Portal",
            detail="Entry Summary Declaration (ENS) not filed. Shipment arriving in 2 days from Turkey (non-EU). ENS required 24h before arrival.",
            suggested_action="File ENS immediately via ICS2 portal. Contact Kuehne+Nagel for commercial invoice to complete filing.",
            duty_impact="0% duty but €500/day penalty for late ENS filing",
        ),
        DiagnosticEntry(
            batch_id="FI-2026-009",
            issue_type="sla_breach",
            severity="critical",
            confidence=0.98,
            declared="6403.99 (Footwear)",
            expected="6403.99 (Footwear)",
            source="Tracking DB",
            detail="72 hours since arrival. 3 delivery attempts failed. Customer not reachable. SLA breached by 24 hours.",
            suggested_action="Contact customer via alternate channel. Arrange depot pickup or return to sender. Escalate to support manager.",
            duty_impact="N/A — duty already paid",
        ),
        DiagnosticEntry(
            batch_id="FI-2026-010",
            issue_type="doc_incomplete",
            severity="warning",
            confidence=0.78,
            declared="8544.42 (Electric conductors)",
            expected="8544.42 (Electric conductors)",
            source="Document Store",
            detail="Packing list received but commercial invoice missing. Pre-arrival documentation incomplete. Arriving tomorrow from US.",
            suggested_action="Request invoice from DB Schenker. Monitor ICS2 filing status. Prepare for potential customs query on arrival.",
            duty_impact="3.3% duty — cannot calculate exact amount without invoice",
        ),
    ]


# ---------------------------------------------------------------------------
# HS Code tree — hierarchical classification structure
# ---------------------------------------------------------------------------

HS_TREE: dict = {
    "code": "",
    "label": "Harmonized System",
    "duty": None,
    "children": [
        {
            "code": "VII",
            "label": "Section VII — Plastics and articles thereof; rubber and articles thereof",
            "duty": None,
            "children": [
                {
                    "code": "39",
                    "label": "Chapter 39 — Plastics and articles thereof",
                    "duty": None,
                    "children": [
                        {
                            "code": "3926",
                            "label": "Other articles of plastics and articles of other materials of headings 3901 to 3914",
                            "duty": None,
                            "children": [
                                {
                                    "code": "3926.10",
                                    "label": "Office or school supplies",
                                    "duty": "6.5%",
                                    "children": [],
                                },
                                {
                                    "code": "3926.20",
                                    "label": "Articles of apparel and clothing accessories",
                                    "duty": "6.5%",
                                    "children": [],
                                },
                                {
                                    "code": "3926.30",
                                    "label": "Fittings for furniture, coachwork or the like",
                                    "duty": "6.5%",
                                    "children": [],
                                },
                                {
                                    "code": "3926.40",
                                    "label": "Statuettes and other ornamental articles",
                                    "duty": "6.5%",
                                    "children": [],
                                },
                                {
                                    "code": "3926.90",
                                    "label": "Other",
                                    "duty": "6.5%",
                                    "children": [],
                                    "highlight": "declared",
                                },
                            ],
                        },
                    ],
                },
            ],
        },
        {
            "code": "XVI",
            "label": "Section XVI — Machinery and mechanical appliances; electrical equipment",
            "duty": None,
            "children": [
                {
                    "code": "84",
                    "label": "Chapter 84 — Nuclear reactors, boilers, machinery and mechanical appliances",
                    "duty": None,
                    "children": [],
                },
                {
                    "code": "85",
                    "label": "Chapter 85 — Electrical machinery and equipment and parts thereof",
                    "duty": None,
                    "children": [
                        {
                            "code": "8517",
                            "label": "Telephone sets; telephones for cellular networks",
                            "duty": None,
                            "children": [],
                        },
                        {
                            "code": "8528",
                            "label": "Monitors and projectors; reception apparatus for television",
                            "duty": None,
                            "children": [],
                        },
                        {
                            "code": "8536",
                            "label": "Electrical apparatus for switching or protecting electrical circuits",
                            "duty": None,
                            "children": [],
                        },
                        {
                            "code": "8541",
                            "label": "Diodes, transistors and similar semiconductor devices",
                            "duty": None,
                            "children": [],
                        },
                        {
                            "code": "8542",
                            "label": "Electronic integrated circuits",
                            "duty": None,
                            "highlight": "match",
                            "children": [
                                {
                                    "code": "8542.31",
                                    "label": "Processors and controllers, whether or not combined with memories",
                                    "duty": "0%",
                                    "children": [],
                                    "highlight": "expected",
                                },
                                {
                                    "code": "8542.32",
                                    "label": "Memories",
                                    "duty": "0%",
                                    "children": [],
                                },
                                {
                                    "code": "8542.33",
                                    "label": "Amplifiers",
                                    "duty": "0%",
                                    "children": [],
                                },
                                {
                                    "code": "8542.39",
                                    "label": "Other",
                                    "duty": "0%",
                                    "children": [],
                                },
                            ],
                        },
                        {
                            "code": "8544",
                            "label": "Insulated wire, cable and other electric conductors",
                            "duty": None,
                            "children": [],
                        },
                    ],
                },
            ],
        },
    ],
}


def hs_tree_path(target_code: str) -> list[dict]:
    """Return the path from root to a given HS code node."""
    def _find(node: dict, path: list[dict]) -> list[dict] | None:
        if node["code"] == target_code:
            return path + [node]
        for child in node.get("children", []):
            result = _find(child, path + [node])
            if result:
                return result
        return None
    return _find(HS_TREE, []) or []
