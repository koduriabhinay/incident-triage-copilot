# Incident Triage Copilot

A multi-agent incident triage tool for on-call engineers. Given an incident
report, four agents classify it, retrieve the relevant runbook sections, draft a
remediation plan and write a structured summary. It runs offline with no API
keys. An optional LLM step can refine the classification and plan when a key is
configured; without one, the rule-based and extractive paths are used.

## Architecture

```
                        ┌─────────────────┐
                        │  Incident report │
                        │ (title + details)│
                        └────────┬────────┘
                                 ▼
                   ┌─────────────────────────┐
                   │ 1. INTAKE agent         │
                   │ rule-based classifier   │
                   │ SEV1-4, category,       │
                   │ confidence + signals    │
                   └────────────┬────────────┘
                                ▼
                   ┌─────────────────────────┐
                   │ 2. RETRIEVAL agent      │
                   │ BM25 + hashed TF-IDF,   │
                   │ fused with reciprocal   │
                   │ rank fusion over        │
                   │ runbooks/*.md           │
                   └────────────┬────────────┘
                                ▼
                   ┌─────────────────────────┐
                   │ 3. ACTION agent         │
                   │ extractive remediation  │
                   │ plan: steps, ranked     │
                   │ causes, escalation,     │
                   │ citations               │
                   └────────────┬────────────┘
                                ▼
                   ┌─────────────────────────┐
                   │ 4. REPORTER agent       │
                   │ structured summary:     │
                   │ what / impact / cause / │
                   │ actions / follow-ups    │
                   └─────────────────────────┘
```

**The agents.** The intake agent scores weighted keyword signals with a
negation guard ("no outage", "avoid an outage" do not count) and returns the
matched signals so every classification is explainable. The retrieval agent
builds the query from the report plus the predicted category and severity,
then fuses a BM25 ranking with a hashed TF-IDF cosine ranking using reciprocal
rank fusion (no score normalization needed, robust when the signals disagree).
The action agent extracts mitigation steps and likely causes from the
retrieved sections and cites every item back to its runbook section. The
reporter composes the final summary purely from pipeline outputs, so it never
invents facts.

## Quickstart

```bash
git clone https://github.com/koduriabhinay/incident-triage-copilot.git
cd incident-triage-copilot
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Triage a bundled sample incident
python cli.py --file data_samples/incident-04.json

# Triage your own report
python cli.py --text "Checkout API returning 503 for all users since 14:00 UTC"

# Start the API
uvicorn api.app:app --reload
curl -X POST localhost:8000/triage \
  -H 'Content-Type: application/json' \
  -d '{"title":"Pods crashlooping after deploy","description":"6 of 10 pods in CrashLoopBackOff"}'
```

## Sample triage output

`python cli.py --file data_samples/incident-04.json` (trimmed):

```
Severity   : SEV2  (confidence 0.81)
Category   : data
Signals    : consumer lag, delayed, kafka
Retrieval  : rrf (BM25 + hashed TF-IDF, reciprocal rank fusion)

RETRIEVED RUNBOOK SECTIONS
[0.033] Kafka Consumer Lag :: Immediate mitigation  (kafka-consumer-lag#immediate-mitigation)
[0.033] Kafka Consumer Lag :: Symptoms              (kafka-consumer-lag#symptoms)
[0.031] Kafka Consumer Lag :: Likely causes         (kafka-consumer-lag#likely-causes)
...

REMEDIATION PLAN
Immediate steps:
  1. Identify the lagging consumer group: `kafka-lag --group order-processor`.
  2. Check whether consumers are crashlooping or stuck; restart unhealthy consumer pods.
  3. Temporarily scale up consumer replicas.
  4. If a poison-pill message is blocking a partition, skip it via the dead-letter tool.
  5. Pause non-critical producers to let consumers catch up.
Likely causes:
  - Consumer pods crashlooping on a poison-pill message
  - Traffic spike exceeding provisioned consumer throughput
  - Slow downstream dependency (database) throttling consume speed
  - Rebalance storm from unstable consumer group membership
Escalation : Notify the owning team's on-call in the team channel within 30 minutes.
Citations  : kafka-consumer-lag#immediate-mitigation, kafka-consumer-lag#likely-causes

INCIDENT SUMMARY
What happened : Kafka consumer lag growing on order-processor group...
Impact        : Major impact: degraded service or elevated errors affecting a subset of users.
Suspected     : Consumer pods crashlooping on a poison-pill message
Follow-ups    : Confirm the fix against cited sections; schedule a blameless
                post-incident review; publish a customer-facing status update.

Latency: 5.2 ms total (intake 3.6 / retrieval 1.5 / action 0.1 / reporter 0.0)
Mode: offline
```

## Evaluation

`python -m eval.evaluate` runs the full pipeline over 10 labeled sample
incidents and reports triage accuracy, runbook retrieval precision, and
latency. Results on the bundled samples:

| Metric | Value |
|---|---|
| Severity accuracy | 1.00 (10/10) |
| Category accuracy | 1.00 (10/10) |
| Retrieval precision@1 | 1.00 |
| Retrieval precision@3 | 0.833 |
| Mean end-to-end latency | 2.1 ms |
| p95 end-to-end latency | 5.4 ms |

Retrieval precision is measured against labeled relevant runbooks on the 8
samples that have one (two samples intentionally have no matching runbook).
Run the test suite with `pytest tests/` (runs offline).

## Configuration

| Variable | Default | Effect |
|---|---|---|
| `TRIAGE_USE_LLM` | `0` | Set to `1` to enable the optional LLM override/rewrite path |
| `OPENAI_API_KEY` | unset | Required for the LLM path; any OpenAI-compatible key |
| `TRIAGE_LLM_MODEL` | `gpt-4o-mini` | Model used when the LLM path is active |
| `TRIAGE_TOP_K` | `5` | Runbook sections retrieved per triage (API default) |

The LLM path is optional: classification falls back to the
rule-based classifier and planning falls back to the extractive plan whenever
the key or client library is missing.

## Project structure

```
agents/          the four agents plus the hand-rolled orchestrator
runbooks/        5 fictional runbooks (API errors, DB failover, Kafka lag,
                 pod crashloop, node disk pressure)
data_samples/    10 labeled incident reports (JSON) used by the eval harness
eval/            offline eval: accuracy, precision@k, latency table
api/             FastAPI service: POST /triage, GET /health, GET /runbooks
tests/           pytest suite for classifier, retrieval, orchestrator, eval
cli.py           terminal triage from a file, --text, or stdin
```

## Adding dense embeddings

The retrieval agent currently fuses BM25 with hashed TF-IDF vectors, which
keeps the project dependency-light and fully offline with no model downloads.
If you want true semantic retrieval, `sentence-transformers`
(`all-MiniLM-L6-v2`) can be swapped in as a third ranking signal: encode the
runbook sections once at index time, encode the query at retrieval time, and
add the cosine-similarity ranking to the reciprocal rank fusion list in
`RetrievalAgent.retrieve`. The fusion function already accepts any number of
rankings. The BM25 + TF-IDF path stays
as the offline fallback.

## Limitations

* The intake classifier is keyword based. It is explainable and fast, but it
  will miss incidents described with vocabulary outside its signal lists.
* The runbooks are fictional and small (5 documents, 30 sections). Retrieval
  quality on a real, large runbook corpus would need re-tuning and a larger
  eval set.
* The action agent is extractive: it can only recommend what the runbooks
  already say. Novel failure modes get generic escalation guidance.
* Sample eval numbers above are measured on 10 hand-written incidents, so
  treat them as a smoke test, not a benchmark.

## License

MIT, see [LICENSE](LICENSE).
