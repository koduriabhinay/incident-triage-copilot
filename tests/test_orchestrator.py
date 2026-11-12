"""End-to-end orchestrator tests (BM25-only for determinism)."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from agents import IncidentReport, Severity, TriageOrchestrator  # noqa: E402


def make_orchestrator(**kwargs) -> TriageOrchestrator:
    return TriageOrchestrator(PROJECT_ROOT / "runbooks", **kwargs)


def test_full_pipeline_produces_complete_result():
    orch = make_orchestrator()
    report = IncidentReport(
        id="e2e-1",
        title="Checkout API returning 503 for all users",
        description="Complete outage since 14:02 UTC. All users affected, no orders can be placed.",
    )
    result = orch.triage(report)

    assert result.classification.severity == Severity.SEV1
    assert result.retrieval.sections, "expected retrieved sections"
    assert result.plan.immediate_steps, "expected mitigation steps"
    assert result.plan.escalation, "expected an escalation path"
    assert result.summary.what_happened, "expected a summary"
    assert result.summary.follow_ups, "expected follow-ups"
    assert result.timing.total_ms > 0
    assert result.offline_mode is True


def test_plan_citations_reference_retrieved_sections():
    orch = make_orchestrator()
    report = IncidentReport(
        id="e2e-2",
        title="Kafka consumer lag growing",
        description="Consumer lag past 200k messages on the order-processor group.",
    )
    result = orch.triage(report)
    retrieved = {s.citation for s in result.retrieval.sections}
    for citation in result.plan.citations:
        assert citation in retrieved, f"citation {citation} not in retrieved sections"


def test_sev1_escalation_mentions_paging():
    orch = make_orchestrator()
    report = IncidentReport(
        id="e2e-3",
        title="Production down",
        description="Complete outage, all users affected.",
    )
    result = orch.triage(report)
    assert result.classification.severity == Severity.SEV1
    assert "page" in result.plan.escalation.lower()


def test_orchestrator_top_k_flows_through():
    orch = make_orchestrator(top_k=1)
    report = IncidentReport(id="e2e-4", title="API errors", description="5xx spike")
    result = orch.triage(report)
    assert len(result.retrieval.sections) == 1
