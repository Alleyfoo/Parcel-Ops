"""Parcel Ops schema, normalization helpers, and synthetic demo data.

This module owns:
  - Column contracts for uploaded data (normalize_shipment_data)
  - Synthetic demo data generator (demo_shipments)
  - KPI aggregation helpers
  - Lane status enums and color mapping
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
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
# Synthetic demo data
# ---------------------------------------------------------------------------

_CARRIERS = [
    "DHL Express",
    "DB Schenker",
    "Kuehne+Nagel",
    "DSV Air & Sea",
    "Maersk Logistics",
    "PostNord",
]

_ORIGINS = ["CN", "DE", "PL", "TR", "SE", "NL", "US", "KR"]

_PRIORITIES = ["normal", "normal", "normal", "high", "critical"]

random.seed(42)


def demo_shipments(today: date | None = None, n: int = 8) -> pd.DataFrame:
    """Generate n synthetic shipment batches for demo mode."""
    today = today or date.today()
    rows = []
    for i in range(n):
        arrival_offset = random.randint(-3, 7)
        rows.append({
            "batch_id": f"FI-{today.year}-{str(i + 1).zfill(3)}",
            "carrier": random.choice(_CARRIERS),
            "origin": random.choice(_ORIGINS),
            "expected_arrival": today + timedelta(days=arrival_offset),
            "parcel_count": random.randint(20, 800),
            "hs_code": f"{random.randint(1000, 9999)}.{random.randint(10, 99)}",
            "planned_owner": random.choice(["Ops-A", "Ops-B", "Customs"]),
            "priority": random.choice(_PRIORITIES),
            "notes": "",
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Lane status generator (synthetic)
# ---------------------------------------------------------------------------

def demo_lane_statuses(batch_ids: list[str]) -> dict[str, dict[str, LaneStatus]]:
    """Return a dict of {batch_id: {lane: status}} for demo mode."""
    rng = random.Random(7)
    result: dict[str, dict[str, LaneStatus]] = {}
    for bid in batch_ids:
        statuses: dict[str, LaneStatus] = {}
        for lane in LANES:
            r = rng.random()
            if r < 0.60:
                statuses[lane] = "ok"
            elif r < 0.80:
                statuses[lane] = "pending"
            elif r < 0.92:
                statuses[lane] = "warning"
            else:
                statuses[lane] = "critical"
        result[bid] = statuses
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
    """Return synthetic freshness entries for demo mode."""
    return [
        FreshnessEntry("Carrier API", "fresh", "2 min ago", "DHL, DSV, PostNord connected"),
        FreshnessEntry("Customs API", "stale", "47 min ago", "H7 feed delayed — retry pending"),
        FreshnessEntry("ICS2 Portal", "fresh", "8 min ago", "EU ENS filing status synced"),
        FreshnessEntry("Tracking DB", "fresh", "1 min ago", "Last-mile events streaming"),
        FreshnessEntry("Document Store", "partial", "12 min ago", "3 batches awaiting upload"),
        FreshnessEntry("EU Truck Schedule", "missing", "—", "Integration not yet configured"),
    ]
