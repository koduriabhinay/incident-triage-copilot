"""Retrieval agent: finds the most relevant runbook sections for an incident.

Lightweight offline stack (no torch, no model downloads):

* **BM25** via ``rank_bm25``: lexical match over runbook sections.
* **Hashed TF-IDF** via numpy: each section is a unit-norm TF-IDF vector in a
  hashed feature space (deterministic md5 hashing, so identical across runs);
  cosine similarity gives a vector-space "dense-style" signal.
* **Reciprocal rank fusion (RRF)**: the two rankings are fused with
  ``score(d) = sum(1 / (k + rank))``, k=60. RRF needs no score normalization
  and is robust when the two signals disagree.

A ``sentence-transformers`` dense encoder can be swapped in later as an
optional upgrade (see README); the fusion interface already supports it.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
from pathlib import Path

from rank_bm25 import BM25Okapi

from .base import Category, Classification, IncidentReport, RetrievalResult, RunbookSection, Severity

logger = logging.getLogger(__name__)

# RRF constant: higher k dampens the advantage of top ranks.
RRF_K = 60
# Hashed feature space size for the TF-IDF vectors.
HASH_DIM = 2**14

# Extra query terms per category so retrieval benefits from the intake agent's
# classification even when the report text is terse.
CATEGORY_TERMS: dict[Category, str] = {
    Category.INFRA: "pod node cpu memory disk kubernetes network dns",
    Category.APP: "api endpoint error rate 500 timeout service http",
    Category.DATA: "kafka consumer lag database postgres replication pipeline",
    Category.SECURITY: "breach unauthorized vulnerability suspicious login",
}

SEVERITY_TERMS: dict[Severity, str] = {
    Severity.SEV1: "outage down critical",
    Severity.SEV2: "degraded failover elevated",
    Severity.SEV3: "warning lag intermittent",
    Severity.SEV4: "minor",
}

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def _hash_token(token: str) -> int:
    """Deterministic bucket index for a token (md5, not salted hash())."""
    digest = hashlib.md5(token.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") % HASH_DIM


class RetrievalAgent:
    """RRF-fused BM25 + hashed TF-IDF retrieval over markdown runbooks."""

    def __init__(self, runbooks_dir: str | Path) -> None:
        self.runbooks_dir = Path(runbooks_dir)
        self.sections: list[RunbookSection] = self._load_sections()
        if not self.sections:
            raise ValueError(f"No runbook sections found in {self.runbooks_dir}")
        tokenized = [tokenize(f"{s.section_title} {s.text}") for s in self.sections]
        self._bm25 = BM25Okapi(tokenized)
        self._tfidf_matrix, self._idf = self._build_tfidf(tokenized)
        logger.info(
            "Retrieval index built: %d sections from %s.",
            len(self.sections),
            self.runbooks_dir,
        )

    # ------------------------------------------------------------------ #
    # Index construction
    # ------------------------------------------------------------------ #
    def _load_sections(self) -> list[RunbookSection]:
        sections: list[RunbookSection] = []
        for path in sorted(self.runbooks_dir.glob("*.md")):
            title, parsed = self._parse_runbook(path)
            sections.extend(parsed)
        return sections

    @staticmethod
    def _parse_runbook(path: Path) -> tuple[str, list[RunbookSection]]:
        """Split a runbook into sections on ``##`` headers."""
        lines = path.read_text(encoding="utf-8").splitlines()
        title = path.stem.replace("-", " ").title()
        sections: list[RunbookSection] = []
        current_heading: str | None = None
        current_body: list[str] = []
        for line in lines:
            if line.startswith("# "):
                title = line[2:].strip()
            elif line.startswith("## "):
                if current_heading is not None:
                    sections.append(
                        RunbookSection(
                            runbook_id=path.stem,
                            runbook_title=title,
                            section_title=current_heading,
                            text="\n".join(current_body).strip(),
                        )
                    )
                current_heading = line[3:].strip()
                current_body = []
            else:
                current_body.append(line)
        if current_heading is not None:
            sections.append(
                RunbookSection(
                    runbook_id=path.stem,
                    runbook_title=title,
                    section_title=current_heading,
                    text="\n".join(current_body).strip(),
                )
            )
        return title, [s for s in sections if s.text]

    def _build_tfidf(self, tokenized: list[list[str]]):
        """Build unit-norm hashed TF-IDF vectors for every section."""
        import numpy as np

        n_docs = len(tokenized)
        doc_freq = np.zeros(HASH_DIM, dtype=np.float64)
        tf_matrix = np.zeros((n_docs, HASH_DIM), dtype=np.float64)
        for i, tokens in enumerate(tokenized):
            counts: dict[int, int] = {}
            for tok in tokens:
                b = _hash_token(tok)
                counts[b] = counts.get(b, 0) + 1
            for b, c in counts.items():
                # Sublinear TF dampens the effect of repeated terms.
                tf_matrix[i, b] = 1.0 + math.log(c)
                doc_freq[b] += 1
        # Smoothed IDF.
        idf = np.log((1.0 + n_docs) / (1.0 + doc_freq)) + 1.0
        tfidf = tf_matrix * idf
        norms = np.linalg.norm(tfidf, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return tfidf / norms, idf

    # ------------------------------------------------------------------ #
    # Scoring
    # ------------------------------------------------------------------ #
    def _tfidf_scores(self, query_tokens: list[str]) -> list[float]:
        import numpy as np

        q = np.zeros(HASH_DIM, dtype=np.float64)
        counts: dict[int, int] = {}
        for tok in query_tokens:
            b = _hash_token(tok)
            counts[b] = counts.get(b, 0) + 1
        for b, c in counts.items():
            q[b] = (1.0 + math.log(c)) * self._idf[b]
        norm = np.linalg.norm(q)
        if norm == 0:
            return [0.0] * len(self.sections)
        q /= norm
        sims = (self._tfidf_matrix @ q).tolist()
        return [float(x) for x in sims]

    @staticmethod
    def _rrf_fuse(rankings: list[list[int]], k: int = RRF_K) -> dict[int, float]:
        """Reciprocal rank fusion over one or more ranked doc-id lists."""
        fused: dict[int, float] = {}
        for ranking in rankings:
            for rank, doc_id in enumerate(ranking, start=1):
                fused[doc_id] = fused.get(doc_id, 0.0) + 1.0 / (k + rank)
        return fused

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def build_query(self, report: IncidentReport, classification: Classification) -> str:
        parts = [
            report.title,
            report.description,
            CATEGORY_TERMS[classification.category],
            SEVERITY_TERMS[classification.severity],
        ]
        return " ".join(parts)

    def retrieve(
        self,
        report: IncidentReport,
        classification: Classification,
        top_k: int = 3,
    ) -> RetrievalResult:
        query = self.build_query(report, classification)
        query_tokens = tokenize(query)

        bm25_scores = self._bm25.get_scores(query_tokens)
        tfidf_scores = self._tfidf_scores(query_tokens)

        bm25_ranking = sorted(
            range(len(self.sections)), key=lambda i: bm25_scores[i], reverse=True
        )
        tfidf_ranking = sorted(
            range(len(self.sections)), key=lambda i: tfidf_scores[i], reverse=True
        )
        fused = self._rrf_fuse([bm25_ranking, tfidf_ranking])

        top_ids = sorted(fused, key=lambda i: fused[i], reverse=True)[:top_k]
        sections = [
            RunbookSection(
                runbook_id=self.sections[i].runbook_id,
                runbook_title=self.sections[i].runbook_title,
                section_title=self.sections[i].section_title,
                text=self.sections[i].text,
                score=round(fused[i], 4),
            )
            for i in top_ids
        ]
        return RetrievalResult(sections=sections, method="rrf")
