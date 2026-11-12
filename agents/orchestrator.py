"""Orchestrator: runs the four agents as a linear pipeline.

intake -> retrieval -> action -> reporter

Each stage is timed individually so latency can be attributed (and the eval
harness can report per-stage cost). The orchestrator is deliberately
hand-rolled rather than framework-based: the pipeline is linear, so a few
explicit calls are clearer and have zero extra dependencies.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from .action import ActionAgent
from .base import IncidentReport, StageTiming, TriageResult
from .intake import IntakeAgent
from .reporter import ReporterAgent
from .retrieval import RetrievalAgent

logger = logging.getLogger(__name__)


class TriageOrchestrator:
    """Coordinates the four triage agents end to end."""

    def __init__(
        self,
        runbooks_dir: str | Path,
        use_llm: bool | None = None,
        top_k: int = 5,
    ) -> None:
        self.top_k = top_k
        self.intake = IntakeAgent(use_llm=use_llm)
        self.retrieval = RetrievalAgent(runbooks_dir)
        self.action = ActionAgent(use_llm=use_llm)
        self.reporter = ReporterAgent()
        self.offline_mode = not (
            self.intake.requested_llm and self._llm_configured()
        )

    @staticmethod
    def _llm_configured() -> bool:
        import os

        return bool(os.getenv("OPENAI_API_KEY", ""))

    def triage(self, report: IncidentReport) -> TriageResult:
        timing = StageTiming()

        start = time.perf_counter()
        classification = self.intake.classify(report)
        timing.intake_ms = (time.perf_counter() - start) * 1000

        start = time.perf_counter()
        retrieval = self.retrieval.retrieve(report, classification, top_k=self.top_k)
        timing.retrieval_ms = (time.perf_counter() - start) * 1000

        start = time.perf_counter()
        plan = self.action.plan(classification, retrieval)
        timing.action_ms = (time.perf_counter() - start) * 1000

        start = time.perf_counter()
        summary = self.reporter.summarize(report, classification, retrieval, plan)
        timing.reporter_ms = (time.perf_counter() - start) * 1000

        llm_used = classification.llm_used or plan.llm_used
        return TriageResult(
            report=report,
            classification=classification,
            retrieval=retrieval,
            plan=plan,
            summary=summary,
            timing=timing,
            offline_mode=not llm_used,
        )
