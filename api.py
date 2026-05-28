"""Parcel Ops Control Tower — Mock API layer.

FastAPI application providing REST endpoints for batch management,
diagnostics, and HS code lookups. Run with:

    uvicorn api:app --reload --port 8000

Interactive docs available at http://localhost:8000/docs
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from parcel_schema import (
    demo_shipments,
    demo_lane_statuses,
    demo_diagnostics,
    hs_tree_path,
)


app = FastAPI(
    title="Parcel Ops Control Tower API",
    description="Mock API for parcel operations dashboard. Provides endpoints for batch management, diagnostics, and HS code lookups.",
    version="0.1.0",
)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class Batch(BaseModel):
    batch_id: str
    carrier: str
    origin: str
    expected_arrival: date
    parcel_count: int
    hs_code: str
    planned_owner: str
    priority: str
    notes: str


class LaneStatus(BaseModel):
    lane: str
    status: str


class BatchDetail(Batch):
    lane_statuses: list[LaneStatus]


class Diagnostic(BaseModel):
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


class AmendmentRequest(BaseModel):
    batch_id: str
    field: str
    old_value: str
    new_value: str
    reason: str


class AmendmentResponse(BaseModel):
    status: str
    amendment_id: str
    message: str


class HSCodeNode(BaseModel):
    code: str
    label: str
    duty: Optional[str] = None
    highlight: Optional[str] = None


class HealthResponse(BaseModel):
    status: str
    version: str
    timestamp: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health", response_model=HealthResponse)
def health_check():
    """Health check endpoint."""
    from datetime import datetime
    return HealthResponse(
        status="ok",
        version="0.1.0",
        timestamp=datetime.now().isoformat(),
    )


@app.get("/api/batches", response_model=list[Batch])
def list_batches(
    carrier: Optional[str] = Query(None, description="Filter by carrier name"),
    origin: Optional[str] = Query(None, description="Filter by origin country code"),
    priority: Optional[str] = Query(None, description="Filter by priority (normal, high, critical)"),
):
    """List all shipment batches with optional filters."""
    df = demo_shipments(today=date.today())
    
    if carrier:
        df = df[df["carrier"] == carrier]
    if origin:
        df = df[df["origin"] == origin]
    if priority:
        df = df[df["priority"] == priority]
    
    return [Batch(**row) for row in df.to_dict("records")]


@app.get("/api/batch/{batch_id}", response_model=BatchDetail)
def get_batch(batch_id: str):
    """Get detailed information for a specific batch including lane statuses."""
    df = demo_shipments(today=date.today())
    batch_rows = df[df["batch_id"] == batch_id]
    
    if batch_rows.empty:
        raise HTTPException(status_code=404, detail=f"Batch {batch_id} not found")
    
    batch = Batch(**batch_rows.iloc[0].to_dict())
    all_statuses = demo_lane_statuses([batch_id])
    lane_statuses = [
        LaneStatus(lane=lane, status=status)
        for lane, status in all_statuses.get(batch_id, {}).items()
    ]
    
    return BatchDetail(**batch.dict(), lane_statuses=lane_statuses)


@app.get("/api/batch/{batch_id}/diagnostics", response_model=list[Diagnostic])
def get_batch_diagnostics(batch_id: str):
    """Get diagnostics for a specific batch."""
    all_diagnostics = demo_diagnostics()
    batch_diagnostics = [d for d in all_diagnostics if d.batch_id == batch_id]
    
    if not batch_diagnostics:
        raise HTTPException(status_code=404, detail=f"No diagnostics found for batch {batch_id}")
    
    return [Diagnostic(**d.__dict__) for d in batch_diagnostics]


@app.post("/api/batch/{batch_id}/amend", response_model=AmendmentResponse)
def submit_amendment(batch_id: str, request: AmendmentRequest):
    """Submit an amendment request for a batch field."""
    df = demo_shipments(today=date.today())
    if df[df["batch_id"] == batch_id].empty:
        raise HTTPException(status_code=404, detail=f"Batch {batch_id} not found")
    
    if request.batch_id != batch_id:
        raise HTTPException(status_code=400, detail="Batch ID mismatch")
    
    # Mock amendment submission
    import uuid
    amendment_id = f"AMD-{uuid.uuid4().hex[:8].upper()}"
    
    return AmendmentResponse(
        status="submitted",
        amendment_id=amendment_id,
        message=f"Amendment request {amendment_id} submitted for batch {batch_id}. Field '{request.field}' will be updated from '{request.old_value}' to '{request.new_value}'.",
    )


@app.get("/api/hs-code/{code}/tree", response_model=list[HSCodeNode])
def get_hs_code_tree(code: str):
    """Get the HS code tree path for a given code."""
    path = hs_tree_path(code)
    
    if not path:
        raise HTTPException(status_code=404, detail=f"HS code {code} not found in tree")
    
    return [
        HSCodeNode(
            code=node["code"],
            label=node["label"],
            duty=node.get("duty"),
            highlight=node.get("highlight"),
        )
        for node in path
    ]


@app.get("/api/diagnostics", response_model=list[Diagnostic])
def list_all_diagnostics():
    """List all diagnostics across all batches."""
    all_diagnostics = demo_diagnostics()
    return [Diagnostic(**d.__dict__) for d in all_diagnostics]


# ---------------------------------------------------------------------------
# Run with: uvicorn api:app --reload --port 8000
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)
