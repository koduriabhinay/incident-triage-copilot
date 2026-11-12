"""Tests for the retrieval agent (RRF of BM25 + hashed TF-IDF, fully offline)."""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from agents import Category, Classification, IncidentReport, RetrievalAgent, Severity  # noqa: E402
from agents.retrieval import _hash_token, tokenize  # noqa: E402


@pytest.fixture
def retrieval() -> RetrievalAgent:
    return RetrievalAgent(PROJECT_ROOT / "runbooks")


def classification(sev=Severity.SEV2, cat=Category.APP) -> Classification:
    return Classification(severity=sev, category=cat, confidence=0.8, signals=[])


def report(title: str, description: str = "") -> IncidentReport:
    return IncidentReport(id="test", title=title, description=description)


def test_loads_all_runbooks(retrieval: RetrievalAgent):
    runbook_ids = {s.runbook_id for s in retrieval.sections}
    assert runbook_ids == {
        "api-high-error-rate",
        "database-failover",
        "kafka-consumer-lag",
        "pod-crashloop",
        "node-disk-pressure",
    }
    assert all(s.text for s in retrieval.sections)


def test_api_incident_retrieves_api_runbook(retrieval: RetrievalAgent):
    r = report(
        "Elevated 5xx on checkout API",
        "The checkout API is returning 503 errors for 20% of requests.",
    )
    result = retrieval.retrieve(r, classification(), top_k=3)
    assert result.sections, "expected at least one section"
    assert result.sections[0].runbook_id == "api-high-error-rate"
    assert result.method == "rrf"


def test_kafka_incident_retrieves_kafka_runbook(retrieval: RetrievalAgent):
    r = report(
        "Consumer lag growing",
        "Kafka consumer lag on the order-processor group is past 200k messages.",
    )
    result = retrieval.retrieve(r, classification(cat=Category.DATA), top_k=3)
    top_ids = [s.runbook_id for s in result.sections]
    assert "kafka-consumer-lag" in top_ids


def test_scores_are_sorted_descending(retrieval: RetrievalAgent):
    r = report("Pods crashlooping", "Kubernetes pods in CrashLoopBackOff after deploy.")
    result = retrieval.retrieve(r, classification(cat=Category.INFRA), top_k=5)
    scores = [s.score for s in result.sections]
    assert scores == sorted(scores, reverse=True)
    assert all(s > 0 for s in scores)


def test_top_k_respected(retrieval: RetrievalAgent):
    r = report("Something is wrong", "errors everywhere")
    result = retrieval.retrieve(r, classification(), top_k=2)
    assert len(result.sections) == 2


def test_citations_are_well_formed(retrieval: RetrievalAgent):
    r = report("Database failover", "postgres primary failed over")
    result = retrieval.retrieve(r, classification(Severity.SEV1, Category.DATA), top_k=3)
    for s in result.sections:
        assert s.citation.startswith(s.runbook_id + "#")
        assert " " not in s.citation


def test_empty_runbook_dir_raises(tmp_path):
    with pytest.raises(ValueError, match="No runbook sections"):
        RetrievalAgent(tmp_path)


def test_hash_token_is_deterministic():
    assert _hash_token("kafka") == _hash_token("kafka")
    assert 0 <= _hash_token("kafka") < 2**14


def test_tokenize_basic():
    assert tokenize("API 503 Errors!") == ["api", "503", "errors"]


def test_rrf_fusion_prefers_consensus():
    # Doc 0 ranks first in both rankings; doc 2 ranks first in only one.
    # RRF should rank the consensus doc highest.
    fused = RetrievalAgent._rrf_fuse([[0, 1, 2], [0, 2, 1]])
    assert fused[0] > fused[1]
    assert fused[0] > fused[2]
