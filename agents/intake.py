"""Intake agent: classifies an incident report by severity and category.

The classifier is rule/keyword based and fully offline. Each severity tier and
category has a weighted keyword list; the tier with the highest weighted score
wins. Confidence is the winning score's share of the total score mass, which
keeps it calibrated and explainable: the matched signals are returned so an
on-call engineer can see *why* the classification was made.

An optional LLM override is supported through the ``TRIAGE_USE_LLM`` env var.
It only activates when an OpenAI-compatible client library is importable *and*
an API key is present; otherwise the agent logs a warning and stays offline.
"""

from __future__ import annotations

import logging
import os
import re

from .base import Category, Classification, IncidentReport, Severity

logger = logging.getLogger(__name__)

# Weighted keyword signals. Weights reflect how strongly a phrase indicates
# the tier; multi-word phrases are matched before single words.
SEVERITY_SIGNALS: dict[Severity, list[tuple[str, float]]] = {
    Severity.SEV1: [
        ("complete outage", 3.0),
        ("production down", 3.0),
        ("all users", 2.5),
        ("data loss", 3.0),
        ("breach", 2.5),
        ("outage", 2.0),
        ("down", 1.5),
        ("unavailable", 1.5),
        ("critical", 1.5),
        ("sev1", 3.0),
        ("p0", 2.5),
        ("kernel panic", 2.5),
        ("connection refused", 2.0),
    ],
    Severity.SEV2: [
        ("degraded", 2.0),
        ("elevated error", 2.0),
        ("failover", 2.0),
        ("partial", 1.5),
        ("spike", 1.5),
        ("crashloop", 2.0),
        ("crashloopbackoff", 2.0),
        ("sev2", 3.0),
        ("p1", 2.5),
        ("major", 1.5),
        ("delayed", 3.0),
        ("5xx", 1.5),
    ],
    Severity.SEV3: [
        ("warning", 1.5),
        ("elevated", 1.0),
        ("lag", 1.5),
        ("slow", 1.0),
        ("retry", 1.0),
        ("intermittent", 1.5),
        ("sev3", 3.0),
        ("p2", 2.5),
        ("minor degradation", 2.0),
    ],
    Severity.SEV4: [
        ("cosmetic", 2.0),
        ("typo", 2.0),
        ("low priority", 2.0),
        ("minor", 1.0),
        ("suggestion", 1.0),
        ("sev4", 3.0),
        ("p3", 2.5),
        ("p4", 2.5),
    ],
}

CATEGORY_SIGNALS: dict[Category, list[tuple[str, float]]] = {
    Category.INFRA: [
        ("kubernetes", 2.0),
        ("pod", 1.5),
        ("node", 1.5),
        ("cpu", 1.5),
        ("memory", 1.5),
        ("disk", 1.5),
        ("dns", 2.0),
        ("network", 1.5),
        ("load balancer", 2.0),
        ("tls", 1.5),
        ("certificate", 1.5),
        ("k8s", 1.5),
        ("crashloop", 2.5),
        ("crashloopbackoff", 2.5),
        ("environment variable", 1.5),
        ("oomkilled", 2.0),
    ],
    Category.APP: [
        ("api", 1.5),
        ("endpoint", 1.5),
        ("500", 2.0),
        ("502", 2.0),
        ("503", 2.0),
        ("error rate", 2.0),
        ("timeout", 1.5),
        ("deploy", 1.5),
        ("release", 1.0),
        ("service", 1.0),
        ("http", 1.0),
    ],
    Category.DATA: [
        ("kafka", 2.5),
        ("consumer lag", 2.5),
        ("database", 2.0),
        ("postgres", 2.0),
        ("replication", 2.0),
        ("query", 1.0),
        ("pipeline", 1.5),
        ("etl", 1.5),
        ("warehouse", 1.5),
        ("table", 1.0),
    ],
    Category.SECURITY: [
        ("breach", 3.0),
        ("unauthorized", 2.5),
        ("vulnerability", 2.5),
        ("cve", 2.5),
        ("injection", 2.5),
        ("exfiltration", 3.0),
        ("malicious", 2.0),
        ("suspicious login", 2.5),
        ("phishing", 2.0),
        ("credential stuffing", 3.0),
        ("brute force", 2.5),
        ("brute-force", 2.5),
        ("failed login", 2.0),
        ("login attempts", 1.5),
        ("ddos", 2.5),
        ("attack", 1.5),
    ],
}

# Negation guard: a signal preceded by "no", "not", "without", "avoid", etc.
# should not count ("no outage detected", "to avoid an outage").
_NEGATION_RE = re.compile(
    r"\b(no|not|without|isn't|aren't|wasn't|weren't|avoid|avoids|prevent|prevents)"
    r"\b[\w\s]{0,20}$"
)


def _score_signals(text: str, signals: list[tuple[str, float]]) -> tuple[float, list[str]]:
    """Return (weighted score, matched signal phrases) for one signal list."""
    score = 0.0
    matched: list[str] = []
    for phrase, weight in signals:
        # Word boundaries stop "down" from matching "downstream".
        pattern = _compiled_pattern(phrase)
        m = pattern.search(text)
        if m is None:
            continue
        # Skip matches that are negated in the preceding context window.
        window_start = max(0, m.start() - 30)
        if _NEGATION_RE.search(text[window_start : m.start()]):
            continue
        score += weight
        matched.append(phrase)
    return score, matched


# Precompiled word-boundary patterns, built once at import so classification
# stays in the sub-millisecond range.
_PATTERN_CACHE: dict[str, re.Pattern] = {}


def _compiled_pattern(phrase: str) -> re.Pattern:
    pattern = _PATTERN_CACHE.get(phrase)
    if pattern is None:
        pattern = re.compile(r"\b" + re.escape(phrase) + r"\b")
        _PATTERN_CACHE[phrase] = pattern
    return pattern


class IntakeAgent:
    """Rule-based incident classifier with optional LLM override."""

    def __init__(self, use_llm: bool | None = None) -> None:
        # Explicit arg wins; otherwise fall back to the env var.
        if use_llm is None:
            use_llm = os.getenv("TRIAGE_USE_LLM", "0") == "1"
        self.requested_llm = use_llm

    def classify(self, report: IncidentReport) -> Classification:
        text = report.full_text().lower()

        severity, sev_conf, sev_signals = self._classify_axis(text, SEVERITY_SIGNALS, Severity.SEV3)
        category, cat_conf, cat_signals = self._classify_axis(
            text, CATEGORY_SIGNALS, Category.APP
        )
        confidence = round((sev_conf + cat_conf) / 2.0, 3)

        classification = Classification(
            severity=severity,
            category=category,
            confidence=confidence,
            signals=sorted(set(sev_signals + cat_signals)),
            llm_used=False,
        )

        if self.requested_llm:
            override = self._try_llm_override(report)
            if override is not None:
                return override
            logger.warning(
                "TRIAGE_USE_LLM=1 but no usable LLM client/key found; "
                "continuing with offline rule-based classification."
            )
        return classification

    @staticmethod
    def _classify_axis(
        text: str,
        signal_map: dict,
        default,
    ) -> tuple:
        """Score one axis (severity or category); return (winner, confidence, signals)."""
        scores: dict = {}
        all_signals: dict = {}
        for label, signals in signal_map.items():
            s, matched = _score_signals(text, signals)
            scores[label] = s
            all_signals[label] = matched

        total = sum(scores.values())
        if total <= 0:
            # No signal at all: fall back to the default with low confidence.
            return default, 0.25, []

        winner = max(scores, key=lambda k: scores[k])  # type: ignore[arg-type]
        confidence = scores[winner] / total
        # Dampen confidence when the margin over the runner-up is thin.
        runner_up = sorted(scores.values(), reverse=True)[1] if len(scores) > 1 else 0.0
        margin = (scores[winner] - runner_up) / total
        confidence = round(min(0.99, confidence * (0.6 + 0.4 * margin + 0.2)), 3)
        return winner, confidence, all_signals[winner]

    def _try_llm_override(self, report: IncidentReport) -> Classification | None:
        """Attempt an LLM classification; return None when unavailable.

        This is intentionally best-effort: the offline classifier is the
        default path and must never depend on this succeeding.
        """
        api_key = os.getenv("OPENAI_API_KEY", "")
        if not api_key:
            return None
        try:
            from openai import OpenAI  # type: ignore[import-not-found]
        except ImportError:
            return None
        try:
            client = OpenAI(api_key=api_key)
            prompt = (
                "Classify this incident report. Reply with exactly two tokens: "
                "SEVERITY (one of SEV1 SEV2 SEV3 SEV4) and CATEGORY "
                f"(one of infra app data security).\n\n{report.full_text()}"
            )
            resp = client.chat.completions.create(
                model=os.getenv("TRIAGE_LLM_MODEL", "gpt-4o-mini"),
                messages=[{"role": "user", "content": prompt}],
                max_tokens=20,
                temperature=0,
            )
            tokens = resp.choices[0].message.content.strip().split()
            severity = Severity(tokens[0].upper())
            category = Category(tokens[1].lower())
            return Classification(
                severity=severity,
                category=category,
                confidence=0.9,
                signals=["llm-override"],
                llm_used=True,
            )
        except Exception as exc:  # never let the LLM path break triage
            logger.warning("LLM override failed (%s); using offline classification.", exc)
            return None
