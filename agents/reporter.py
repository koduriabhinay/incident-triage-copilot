"""Reporter agent: produces a structured, timeline-ready incident summary.

It synthesizes the classification, the retrieved evidence, and the remediation
plan into five fixed fields. This agent is purely compositional (no LLM needed):
it never invents facts, it only reorganizes what the earlier agents produced.
"""

from __future__ import annotations

from .base import (
    Classification,
    IncidentReport,
    IncidentSummary,
    RemediationPlan,
    RetrievalResult,
)


class ReporterAgent:
    """Composes the final structured summary from pipeline outputs."""

    def summarize(
        self,
        report: IncidentReport,
        classification: Classification,
        retrieval: RetrievalResult,
        plan: RemediationPlan,
    ) -> IncidentSummary:
        top_section = retrieval.sections[0] if retrieval.sections else None

        what_happened = report.title.strip()
        if report.description.strip() and report.description.strip() not in what_happened:
            first_sentence = report.description.strip().split(".")[0].strip()
            if first_sentence:
                what_happened = f"{what_happened}. {first_sentence}."

        impact = self._impact_line(classification)

        if plan.likely_causes:
            suspected_cause = plan.likely_causes[0][0]
            if len(plan.likely_causes) > 1:
                suspected_cause += (
                    f" (alternatives: {', '.join(c for c, _ in plan.likely_causes[1:3])})"
                )
        elif top_section:
            suspected_cause = (
                f"Under investigation; see {top_section.runbook_title} "
                f"({top_section.citation})."
            )
        else:
            suspected_cause = "Under investigation; no matching runbook found."

        follow_ups = [
            f"Confirm the fix against {s.citation}" for s in retrieval.sections[:2]
        ]
        follow_ups.append(
            "Schedule a blameless post-incident review within 2 business days "
            f"({classification.severity.value})."
        )
        if classification.severity.value in ("SEV1", "SEV2"):
            follow_ups.append("Publish a customer-facing status update.")

        return IncidentSummary(
            what_happened=what_happened,
            impact=impact,
            suspected_cause=suspected_cause,
            actions_planned=list(plan.immediate_steps),
            follow_ups=follow_ups,
        )

    @staticmethod
    def _impact_line(classification: Classification) -> str:
        sev = classification.severity.value
        cat = classification.category.value
        scope = {
            "SEV1": "Critical impact: service unavailable or data at risk for all or most users.",
            "SEV2": "Major impact: degraded service or elevated errors affecting a subset of users.",
            "SEV3": "Minor impact: degraded performance or warnings; limited user impact.",
            "SEV4": "Negligible impact: cosmetic or low-priority issue.",
        }[sev]
        return f"{scope} Category: {cat}."
