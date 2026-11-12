"""Tests for the eval harness metric functions and smoke test."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from eval.evaluate import percentile, precision_at_k  # noqa: E402


def test_precision_at_k_basic():
    retrieved = ["a", "b", "c"]
    assert precision_at_k(retrieved, ["a", "x"], 1) == 1.0
    assert precision_at_k(retrieved, ["x", "b"], 3) == 1 / 3
    assert precision_at_k(retrieved, ["x", "y"], 3) == 0.0


def test_precision_at_k_edge_cases():
    assert precision_at_k([], ["a"], 3) == 0.0
    assert precision_at_k(["a"], [], 3) == 0.0
    assert precision_at_k(["a", "b"], ["a"], 0) == 0.0


def test_percentile():
    values = [10.0, 20.0, 30.0, 40.0]
    assert percentile(values, 50) == 30.0
    assert percentile([], 95) == 0.0


def test_evaluate_smoke():
    # Full harness over the real samples.
    from eval.evaluate import evaluate

    out = evaluate(
        PROJECT_ROOT / "data_samples",
        PROJECT_ROOT / "runbooks",
        top_k=3,
        verbose=False,
    )
    summary = out["summary"]
    assert summary["n_samples"] == 10
    assert 0.0 <= summary["severity_accuracy"] <= 1.0
    assert 0.0 <= summary["category_accuracy"] <= 1.0
    # 8 of 10 samples carry runbook labels (2 have none).
    assert summary["retrieval_eval_samples"] == 8
    assert summary["latency_mean_ms"] > 0
    assert len(out["rows"]) == 10
