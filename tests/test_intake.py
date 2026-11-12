"""Tests for the intake (classification) agent. All offline."""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from agents import Category, IncidentReport, IntakeAgent, Severity  # noqa: E402


@pytest.fixture
def intake() -> IntakeAgent:
    return IntakeAgent(use_llm=False)


def report(title: str, description: str = "") -> IncidentReport:
    return IncidentReport(id="test", title=title, description=description)


def test_sev1_outage_classification(intake: IntakeAgent):
    r = report(
        "Complete outage: checkout API down",
        "Production is down. All users affected, 100% of checkout requests failing.",
    )
    c = intake.classify(r)
    assert c.severity == Severity.SEV1
    assert c.category == Category.APP
    assert 0.0 < c.confidence <= 1.0
    assert c.signals, "expected explanatory signals"


def test_database_category(intake: IntakeAgent):
    r = report(
        "Postgres primary failover",
        "The database primary failed over. Replication lag on the old primary.",
    )
    c = intake.classify(r)
    assert c.category == Category.DATA
    assert c.severity in (Severity.SEV1, Severity.SEV2)


def test_security_category(intake: IntakeAgent):
    r = report(
        "Credential stuffing attack detected",
        "2000 failed logins from one IP range, pattern matches credential stuffing.",
    )
    c = intake.classify(r)
    assert c.category == Category.SECURITY


def test_infra_category(intake: IntakeAgent):
    r = report(
        "Node disk pressure",
        "Kubernetes nodes reporting disk pressure, pods evicted.",
    )
    c = intake.classify(r)
    assert c.category == Category.INFRA


def test_sev4_cosmetic(intake: IntakeAgent):
    r = report("Typo in footer", "Cosmetic typo on the admin page. Low priority.")
    c = intake.classify(r)
    assert c.severity == Severity.SEV4


def test_empty_report_falls_back_gracefully(intake: IntakeAgent):
    c = intake.classify(report("", ""))
    assert c.severity == Severity.SEV3  # safe default
    assert c.confidence == pytest.approx(0.25)


def test_negation_guard(intake: IntakeAgent):
    r = report("Routine check", "No outage detected. Service is not down.")
    c = intake.classify(r)
    assert c.severity != Severity.SEV1


def test_llm_requested_but_unavailable_stays_offline(monkeypatch):
    monkeypatch.setenv("TRIAGE_USE_LLM", "1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    agent = IntakeAgent()  # reads env var
    c = agent.classify(report("API down", "outage"))
    assert c.llm_used is False
    assert c.severity == Severity.SEV1
