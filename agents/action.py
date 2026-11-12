"""Action agent: drafts a remediation plan from the retrieved runbook sections.

Offline mode is extractive: it pulls numbered/bulleted steps out of sections
titled like "Immediate mitigation", ranks likely causes from "Likely causes"
sections, and derives the escalation path from the severity. Every item is
cited back to its runbook section so an on-call engineer can verify it.

An optional LLM rewrite (``TRIAGE_USE_LLM=1`` + API key) may rephrase the
plan, but the extractive plan is always produced first and used as fallback.
"""

from __future__ import annotations

import logging
import os
import re

from .base import Classification, RemediationPlan, RetrievalResult, Severity

logger = logging.getLogger(__name__)

_STEP_RE = re.compile(r"^\s*(?:\d+[.)]|[-*])\s+(.+)$")

# Section titles (lowercased, substring match) that hold mitigation steps.
MITIGATION_TITLES = ("immediate mitigation", "mitigation", "response steps", "what to do")
CAUSE_TITLES = ("likely causes", "root causes", "possible causes", "common causes")
ESCALATION_TITLES = ("escalation", "escalate", "page", "notify")
VERIFY_TITLES = ("verification", "verify", "confirm recovery", "post-incident")

ESCALATION_BY_SEVERITY: dict[Severity, str] = {
    Severity.SEV1: (
        "Page the primary on-call immediately, declare an incident, assign an "
        "incident commander, and open a dedicated war-room channel. Notify "
        "engineering leadership within 15 minutes."
    ),
    Severity.SEV2: (
        "Notify the owning team's on-call in the team channel within 30 minutes. "
        "Declare an incident if customer impact is confirmed or growing."
    ),
    Severity.SEV3: (
        "File a ticket for the owning team and mention the on-call in the team "
        "channel. No incident declaration required unless impact grows."
    ),
    Severity.SEV4: (
        "Log as a low-priority ticket for the owning team's backlog. No paging."
    ),
}


def _section_matches(section_title: str, keywords: tuple[str, ...]) -> bool:
    lowered = section_title.lower()
    return any(k in lowered for k in keywords)


def _extract_steps(text: str) -> list[str]:
    """Pull numbered/bulleted steps out of a runbook section body."""
    steps: list[str] = []
    for line in text.splitlines():
        m = _STEP_RE.match(line)
        if m:
            step = m.group(1).strip()
            if step and step not in steps:
                steps.append(step)
    return steps


class ActionAgent:
    """Builds a remediation plan from retrieved runbook evidence."""

    def __init__(self, use_llm: bool | None = None) -> None:
        if use_llm is None:
            use_llm = os.getenv("TRIAGE_USE_LLM", "0") == "1"
        self.requested_llm = use_llm

    def plan(
        self, classification: Classification, retrieval: RetrievalResult
    ) -> RemediationPlan:
        sections = retrieval.sections

        immediate_steps: list[str] = []
        likely_causes: list[tuple[str, str]] = []
        citations: list[str] = []

        for section in sections:
            if _section_matches(section.section_title, MITIGATION_TITLES):
                for step in _extract_steps(section.text):
                    if step not in immediate_steps:
                        immediate_steps.append(step)
                citations.append(section.citation)
            elif _section_matches(section.section_title, CAUSE_TITLES):
                for cause in _extract_steps(section.text):
                    rationale = f"Listed as a likely cause in {section.runbook_title}"
                    if (cause, rationale) not in likely_causes:
                        likely_causes.append((cause, rationale))
                citations.append(section.citation)

        # Fall back to verification steps if no mitigation section was retrieved.
        if not immediate_steps:
            for section in sections:
                if _section_matches(section.section_title, VERIFY_TITLES):
                    for step in _extract_steps(section.text):
                        if step not in immediate_steps:
                            immediate_steps.append(step)
                    citations.append(section.citation)
                    break

        # Rank causes: prefer causes from the top-scoring section.
        likely_causes = likely_causes[:5]
        immediate_steps = immediate_steps[:8]
        citations = sorted(set(citations))

        escalation = self._escalation_for(classification, sections)

        plan = RemediationPlan(
            immediate_steps=immediate_steps,
            likely_causes=likely_causes,
            escalation=escalation,
            citations=citations,
            llm_used=False,
        )

        if self.requested_llm:
            rewritten = self._try_llm_rewrite(classification, plan)
            if rewritten is not None:
                return rewritten
            logger.warning("LLM rewrite requested but unavailable; using extractive plan.")
        return plan

    @staticmethod
    def _escalation_for(
        classification: Classification, sections: list
    ) -> str:
        # Prefer an explicit escalation section from the runbooks when present.
        for section in sections:
            if _section_matches(section.section_title, ESCALATION_TITLES):
                first_line = section.text.strip().splitlines()[0].strip()
                if first_line:
                    return f"{first_line} (source: {section.citation})"
        return ESCALATION_BY_SEVERITY[classification.severity]

    def _try_llm_rewrite(
        self, classification: Classification, plan: RemediationPlan
    ) -> RemediationPlan | None:
        api_key = os.getenv("OPENAI_API_KEY", "")
        if not api_key:
            return None
        try:
            from openai import OpenAI  # type: ignore[import-not-found]
        except ImportError:
            return None
        try:
            client = OpenAI(api_key=api_key)
            steps = "\n".join(f"- {s}" for s in plan.immediate_steps)
            causes = "\n".join(f"- {c}" for c, _ in plan.likely_causes)
            prompt = (
                "You are an SRE assistant. Rewrite this incident remediation plan "
                "to be clear and actionable. Keep every factual step; do not invent "
                "new infrastructure details. Keep the same sections.\n\n"
                f"Severity: {classification.severity.value}\n"
                f"Immediate steps:\n{steps}\nLikely causes:\n{causes}\n"
                f"Escalation: {plan.escalation}"
            )
            resp = client.chat.completions.create(
                model=os.getenv("TRIAGE_LLM_MODEL", "gpt-4o-mini"),
                messages=[{"role": "user", "content": prompt}],
                max_tokens=600,
                temperature=0.2,
            )
            text = resp.choices[0].message.content.strip()
            # Keep structure: put the rewrite in as refined steps, preserve citations.
            refined = [line.strip("- ").strip() for line in text.splitlines() if line.strip()]
            return RemediationPlan(
                immediate_steps=refined[:10] or plan.immediate_steps,
                likely_causes=plan.likely_causes,
                escalation=plan.escalation,
                citations=plan.citations,
                llm_used=True,
            )
        except Exception as exc:
            logger.warning("LLM rewrite failed (%s); using extractive plan.", exc)
            return None
