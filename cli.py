#!/usr/bin/env python3
"""Command-line interface for the incident triage copilot.

Usage:
    python cli.py --file data_samples/incident-01.json
    python cli.py --text "Checkout API returning 503 for all users since 14:00 UTC"
    echo "Pods crashlooping after deploy" | python cli.py
    python cli.py --file report.json --json   # raw JSON output
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from agents import IncidentReport, TriageOrchestrator  # noqa: E402


def load_report(args: argparse.Namespace) -> IncidentReport:
    if args.file:
        payload = json.loads(Path(args.file).read_text(encoding="utf-8"))
        return IncidentReport(
            id=payload.get("id", "cli-report"),
            title=payload["title"],
            description=payload.get("description", ""),
        )
    if args.text:
        title, _, description = args.text.partition("\n")
        return IncidentReport(id="cli-report", title=title, description=description)
    # Fall back to stdin.
    piped = sys.stdin.read().strip()
    if not piped:
        raise SystemExit("No input: pass --file, --text, or pipe a report via stdin.")
    title, _, description = piped.partition("\n")
    return IncidentReport(id="cli-report", title=title, description=description)


def render_human(result) -> str:
    c = result.classification
    lines = [
        "=" * 64,
        f"TRIAGE  {result.report.id}",
        "=" * 64,
        "",
        f"Severity   : {c.severity.value}  (confidence {c.confidence:.2f})",
        f"Category   : {c.category.value}",
        f"Signals    : {', '.join(c.signals) if c.signals else 'none'}",
        f"Retrieval  : {result.retrieval.method} (BM25 + hashed TF-IDF, reciprocal rank fusion)",
        "",
        "RETRIEVED RUNBOOK SECTIONS",
        "-" * 64,
    ]
    for s in result.retrieval.sections:
        lines.append(f"[{s.score:.3f}] {s.runbook_title} :: {s.section_title}  ({s.citation})")
    lines += ["", "REMEDIATION PLAN", "-" * 64, "Immediate steps:"]
    for i, step in enumerate(result.plan.immediate_steps, 1):
        lines.append(f"  {i}. {step}")
    lines.append("Likely causes:")
    for cause, rationale in result.plan.likely_causes:
        lines.append(f"  - {cause} [{rationale}]")
    lines += ["", f"Escalation : {result.plan.escalation}"]
    if result.plan.citations:
        lines.append(f"Citations  : {', '.join(result.plan.citations)}")
    s = result.summary
    lines += [
        "",
        "INCIDENT SUMMARY",
        "-" * 64,
        f"What happened : {s.what_happened}",
        f"Impact        : {s.impact}",
        f"Suspected     : {s.suspected_cause}",
        "Follow-ups    :",
    ]
    for f in s.follow_ups:
        lines.append(f"  - {f}")
    t = result.timing
    lines += [
        "",
        f"Latency: {t.total_ms:.1f} ms total "
        f"(intake {t.intake_ms:.1f} / retrieval {t.retrieval_ms:.1f} / "
        f"action {t.action_ms:.1f} / reporter {t.reporter_ms:.1f})",
        f"Mode: {'offline' if result.offline_mode else 'LLM-assisted'}",
    ]
    return "\n".join(lines)


def render_json(result) -> str:
    return json.dumps(
        {
            "id": result.report.id,
            "severity": result.classification.severity.value,
            "category": result.classification.category.value,
            "confidence": result.classification.confidence,
            "signals": result.classification.signals,
            "retrieval_method": result.retrieval.method,
            "sections": [
                {
                    "citation": s.citation,
                    "runbook": s.runbook_title,
                    "section": s.section_title,
                    "score": s.score,
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
        },
        indent=2,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Triage an incident report offline.")
    parser.add_argument("--file", help="Path to a JSON incident report")
    parser.add_argument("--text", help="Raw incident text (first line = title)")
    parser.add_argument("--top-k", type=int, default=5, help="Runbook sections to retrieve")
    parser.add_argument("--json", action="store_true", help="Emit raw JSON")
    args = parser.parse_args()

    report = load_report(args)
    orchestrator = TriageOrchestrator(
        PROJECT_ROOT / "runbooks",
        top_k=args.top_k,
    )
    result = orchestrator.triage(report)
    print(render_json(result) if args.json else render_human(result))


if __name__ == "__main__":
    main()
