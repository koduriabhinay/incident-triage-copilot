"""Offline evaluation harness for the incident triage copilot.

Metrics:
  * Triage accuracy: predicted severity and category vs. labeled samples.
  * Retrieval precision@1 and precision@3: retrieved runbook ids vs. the
    sample's labeled relevant runbooks (samples with no labels are skipped).
  * Latency: mean and p95 end-to-end triage time, plus per-stage means.

Run:  python -m eval.evaluate [--top-k 3]
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from agents import IncidentReport, TriageOrchestrator  # noqa: E402


def load_samples(samples_dir: Path) -> list[dict]:
    samples = []
    for path in sorted(samples_dir.glob("*.json")):
        samples.append(json.loads(path.read_text(encoding="utf-8")))
    return samples


def precision_at_k(retrieved_ids: list[str], relevant_ids: list[str], k: int) -> float:
    if not relevant_ids or k <= 0:
        return 0.0
    hits = sum(1 for rid in retrieved_ids[:k] if rid in relevant_ids)
    return hits / min(k, len(retrieved_ids)) if retrieved_ids else 0.0


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(pct / 100 * len(ordered)))
    return ordered[idx]


def evaluate(
    samples_dir: Path,
    runbooks_dir: Path,
    top_k: int = 5,
    verbose: bool = True,
) -> dict:
    samples = load_samples(samples_dir)
    orchestrator = TriageOrchestrator(runbooks_dir, top_k=top_k)

    sev_correct = cat_correct = 0
    p_at_1, p_at_3 = [], []
    latencies: list[float] = []
    stage_totals = {"intake": [], "retrieval": [], "action": [], "reporter": []}
    rows = []

    for sample in samples:
        report = IncidentReport(
            id=sample["id"],
            title=sample["title"],
            description=sample["description"],
        )
        start = time.perf_counter()
        result = orchestrator.triage(report)
        elapsed_ms = (time.perf_counter() - start) * 1000
        latencies.append(elapsed_ms)
        stage_totals["intake"].append(result.timing.intake_ms)
        stage_totals["retrieval"].append(result.timing.retrieval_ms)
        stage_totals["action"].append(result.timing.action_ms)
        stage_totals["reporter"].append(result.timing.reporter_ms)

        sev_ok = result.classification.severity.value == sample["severity"]
        cat_ok = result.classification.category.value == sample["category"]
        sev_correct += sev_ok
        cat_correct += cat_ok

        retrieved_ids = [s.runbook_id for s in result.retrieval.sections]
        p1 = p3 = None
        if sample.get("relevant_runbooks"):
            p1 = precision_at_k(retrieved_ids, sample["relevant_runbooks"], 1)
            p3 = precision_at_k(retrieved_ids, sample["relevant_runbooks"], 3)
            p_at_1.append(p1)
            p_at_3.append(p3)

        rows.append(
            {
                "id": sample["id"],
                "sev_pred": result.classification.severity.value,
                "sev_true": sample["severity"],
                "sev_ok": sev_ok,
                "cat_pred": result.classification.category.value,
                "cat_true": sample["category"],
                "cat_ok": cat_ok,
                "p@1": p1,
                "p@3": p3,
                "latency_ms": round(elapsed_ms, 1),
                "retrieval": result.retrieval.method,
            }
        )

    n = len(samples)
    summary = {
        "n_samples": n,
        "severity_accuracy": round(sev_correct / n, 3),
        "category_accuracy": round(cat_correct / n, 3),
        "retrieval_precision@1": round(statistics.mean(p_at_1), 3) if p_at_1 else None,
        "retrieval_precision@3": round(statistics.mean(p_at_3), 3) if p_at_3 else None,
        "retrieval_eval_samples": len(p_at_1),
        "latency_mean_ms": round(statistics.mean(latencies), 1),
        "latency_p95_ms": round(percentile(latencies, 95), 1),
        "stage_mean_ms": {
            stage: round(statistics.mean(vals), 2) for stage, vals in stage_totals.items()
        },
        "retrieval_method": rows[0]["retrieval"] if rows else "n/a",
    }

    if verbose:
        print(_format_table(rows, summary))
    return {"rows": rows, "summary": summary}


def _format_table(rows: list[dict], summary: dict) -> str:
    lines = []
    header = (
        f"{'sample':<12}{'sev':<14}{'cat':<14}{'p@1':<6}{'p@3':<6}{'lat_ms':<8}{'retrieval'}"
    )
    lines.append(header)
    lines.append("-" * len(header))
    for r in rows:
        sev = f"{r['sev_pred']}{'' if r['sev_ok'] else '!=' + r['sev_true']}"
        cat = f"{r['cat_pred']}{'' if r['cat_ok'] else '!=' + r['cat_true']}"
        p1 = f"{r['p@1']:.2f}" if r["p@1"] is not None else "n/a"
        p3 = f"{r['p@3']:.2f}" if r["p@3"] is not None else "n/a"
        lines.append(
            f"{r['id']:<12}{sev:<14}{cat:<14}{p1:<6}{p3:<6}"
            f"{r['latency_ms']:<8}{r['retrieval']}"
        )
    lines.append("-" * len(header))
    lines.append("SUMMARY")
    for key, value in summary.items():
        if key == "stage_mean_ms":
            lines.append("  stage_mean_ms:")
            for stage, ms in value.items():
                lines.append(f"    {stage:<10}{ms} ms")
        else:
            lines.append(f"  {key:<24}{value}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate the triage copilot offline.")
    parser.add_argument("--top-k", type=int, default=3)
    args = parser.parse_args()
    evaluate(
        PROJECT_ROOT / "data_samples",
        PROJECT_ROOT / "runbooks",
        top_k=args.top_k,
    )


if __name__ == "__main__":
    main()
