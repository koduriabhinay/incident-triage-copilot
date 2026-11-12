"""Shared data model for the incident triage copilot.

Every agent in the pipeline reads and writes these typed structures, so the
orchestrator can pass results between stages without any hidden coupling.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Severity(str, Enum):
    """Incident severity, SEV1 (critical) through SEV4 (low)."""

    SEV1 = "SEV1"
    SEV2 = "SEV2"
    SEV3 = "SEV3"
    SEV4 = "SEV4"


class Category(str, Enum):
    """High-level incident category used to route the investigation."""

    INFRA = "infra"
    APP = "app"
    DATA = "data"
    SECURITY = "security"


@dataclass
class IncidentReport:
    """A single incoming incident report to triage."""

    id: str
    title: str
    description: str
    source: str = "manual"
    reported_at: Optional[str] = None

    def full_text(self) -> str:
        return f"{self.title}\n{self.description}"


@dataclass
class Classification:
    """Output of the intake agent."""

    severity: Severity
    category: Category
    confidence: float  # 0.0 - 1.0
    signals: list[str] = field(default_factory=list)  # matched keywords/rules
    llm_used: bool = False


@dataclass
class RunbookSection:
    """One retrievable section of a runbook, with a citation identity."""

    runbook_id: str  # e.g. "api-high-error-rate"
    runbook_title: str
    section_title: str  # e.g. "Immediate mitigation"
    text: str
    score: float = 0.0

    @property
    def citation(self) -> str:
        return f"{self.runbook_id}#{self.section_title.lower().replace(' ', '-')}"


@dataclass
class RetrievalResult:
    """Output of the retrieval agent."""

    sections: list[RunbookSection] = field(default_factory=list)
    method: str = "rrf"  # reciprocal rank fusion of BM25 + hashed TF-IDF


@dataclass
class RemediationPlan:
    """Output of the action agent."""

    immediate_steps: list[str] = field(default_factory=list)
    likely_causes: list[tuple[str, str]] = field(default_factory=list)  # (cause, rationale)
    escalation: str = ""
    citations: list[str] = field(default_factory=list)
    llm_used: bool = False


@dataclass
class IncidentSummary:
    """Output of the reporter agent: a timeline-ready structured summary."""

    what_happened: str = ""
    impact: str = ""
    suspected_cause: str = ""
    actions_planned: list[str] = field(default_factory=list)
    follow_ups: list[str] = field(default_factory=list)


@dataclass
class StageTiming:
    intake_ms: float = 0.0
    retrieval_ms: float = 0.0
    action_ms: float = 0.0
    reporter_ms: float = 0.0

    @property
    def total_ms(self) -> float:
        return self.intake_ms + self.retrieval_ms + self.action_ms + self.reporter_ms


@dataclass
class TriageResult:
    """The complete output of one triage run."""

    report: IncidentReport
    classification: Classification
    retrieval: RetrievalResult
    plan: RemediationPlan
    summary: IncidentSummary
    timing: StageTiming = field(default_factory=StageTiming)
    offline_mode: bool = True
