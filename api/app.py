"""FastAPI service exposing the incident triage copilot.

Endpoints:
  GET  /health    service and retrieval-mode status
  GET  /runbooks  list of bundled runbooks
  POST /triage    triage one incident report
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from agents import IncidentReport, TriageOrchestrator

PROJECT_ROOT = Path(__file__).resolve().parent.parent

app = FastAPI(
    title="Incident Triage Copilot",
    description="Multi-agent incident triage: classify, retrieve runbooks, draft remediation.",
    version="1.0.0",
)

_orchestrator: TriageOrchestrator | None = None


def get_orchestrator() -> TriageOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = TriageOrchestrator(
            PROJECT_ROOT / "runbooks",
            top_k=int(os.getenv("TRIAGE_TOP_K", "5")),
        )
    return _orchestrator


class TriageRequest(BaseModel):
    title: str = Field(..., min_length=3, description="Short incident title")
    description: str = Field(..., min_length=3, description="Incident details")
    top_k: int = Field(5, ge=1, le=10, description="Runbook sections to retrieve")


class SectionOut(BaseModel):
    runbook_id: str
    runbook_title: str
    section_title: str
    citation: str
    score: float
    text: str


class TriageResponse(BaseModel):
    severity: str
    category: str
    confidence: float
    signals: list[str]
    retrieval_method: str
    sections: list[SectionOut]
    immediate_steps: list[str]
    likely_causes: list[dict]
    escalation: str
    citations: list[str]
    summary: dict
    timing_ms: dict
    offline_mode: bool


@app.get("/health")
def health() -> dict:
    orch = get_orchestrator()
    return {
        "status": "ok",
        "retrieval_method": "rrf",
        "runbooks": len({s.runbook_id for s in orch.retrieval.sections}),
    }


@app.get("/runbooks")
def list_runbooks() -> dict:
    orch = get_orchestrator()
    seen: dict[str, str] = {}
    for s in orch.retrieval.sections:
        seen.setdefault(s.runbook_id, s.runbook_title)
    return {"runbooks": [{"id": rid, "title": t} for rid, t in sorted(seen.items())]}


@app.post("/triage", response_model=TriageResponse)
def triage(req: TriageRequest) -> dict:
    try:
        orch = get_orchestrator()
        report = IncidentReport(id="api-request", title=req.title, description=req.description)
        result = orch.triage(report)
        # Honor a per-request top_k by re-running retrieval only if it differs.
        if req.top_k != orch.top_k:
            result.retrieval = orch.retrieval.retrieve(
                report, result.classification, top_k=req.top_k
            )
            result.plan = orch.action.plan(result.classification, result.retrieval)
            result.summary = orch.reporter.summarize(
                report, result.classification, result.retrieval, result.plan
            )
    except Exception as exc:  # noqa: BLE001 - surface as a 500 with context
        raise HTTPException(status_code=500, detail=f"Triage failed: {exc}") from exc

    return {
        "severity": result.classification.severity.value,
        "category": result.classification.category.value,
        "confidence": result.classification.confidence,
        "signals": result.classification.signals,
        "retrieval_method": result.retrieval.method,
        "sections": [
            {
                "runbook_id": s.runbook_id,
                "runbook_title": s.runbook_title,
                "section_title": s.section_title,
                "citation": s.citation,
                "score": s.score,
                "text": s.text,
            }
            for s in result.retrieval.sections
        ],
        "immediate_steps": result.plan.immediate_steps,
        "likely_causes": [
            {"cause": c, "rationale": r} for c, r in result.plan.likely_causes
        ],
        "escalation": result.plan.escalation,
        "citations": result.plan.citations,
        "summary": {
            "what_happened": result.summary.what_happened,
            "impact": result.summary.impact,
            "suspected_cause": result.summary.suspected_cause,
            "actions_planned": result.summary.actions_planned,
            "follow_ups": result.summary.follow_ups,
        },
        "timing_ms": {
            "intake": round(result.timing.intake_ms, 2),
            "retrieval": round(result.timing.retrieval_ms, 2),
            "action": round(result.timing.action_ms, 2),
            "reporter": round(result.timing.reporter_ms, 2),
            "total": round(result.timing.total_ms, 2),
        },
        "offline_mode": result.offline_mode,
    }
