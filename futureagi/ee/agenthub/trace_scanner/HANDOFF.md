# Error Feed v2 Python port

Branch: `feat/TH-7781-error-feed-scanner-port`, based on `origin/dev`.

This branch ports the evaluated Omega investigation graph into the existing
scanner, gateway, persistence, and ingestion flow. Existing full-benchmark
scores belong to the Omega implementation; backend-specific live checks are
listed below.

## Implemented

- `TraceScanner.scan_batch` runs an adaptive controller, optional child with
  read-only evidence tools, controller consolidation, then a mandatory verifier.
- Existing `ScanResult`, `ScanIssue`, `ScanMeta`, and `KeyMoment` are retained.
  `ScanResult` adds `outcome`, `investigation`, and the executing `scan_version`.
- Internal model reports and strict parsing are in `investigation_types.py`.
  Controller/child/verifier instructions are in `investigation_prompt.py`.
- `evidence_checks.py` supports scoped read, collect, equality, set difference,
  and sum. No shell, network, database execution, or recursive delegation.
- `investigation_provider.py` uses the existing metered gateway. Default model:
  `vertex_ai/gemini-3.8-flash`; reasoning is not forcibly disabled.
- Persistence saves the report and outcome under result metadata. Parent and
  issue writes are atomic. Operational failures remain retryable and unwritten.
- Unknown results are excluded from healthy embeddings and passing counts.
- The trace adapter reads heavy fields and preserves typed attributes, recorded
  metadata/events, timestamps, and parent IDs. No inference-input summarization.
- Ingestion and sweep dispatch one trace per Temporal activity. V2 performs at
  least two lossless model passes, so the old serial batch of 15 could exceed
  the activity deadline before later traces were persisted.
- The cumulative input budget is 2.1M estimated tokens and reserves the
  mandatory verifier before making a paid controller call. This is separate
  from the provider's per-request context limit.

## Atharva: grouping boundary

Detection does **not** map findings into the old static taxonomy. The current
compatibility adapter leaves `category`, `group`, and `fix_layer` empty; `brief`
contains the verified finding description. This is temporary integration work,
not a finalized grouping contract. Existing category-partitioned clustering must
be reconciled with your TH-7782 implementation before deployment.

`has_issues` reflects published violated findings. Final task `outcome` is
separate: a recovered process mistake can coexist with a successful outcome.
Evidence IDs are trace-local `event:N` coordinates, not issue or cluster IDs.
Breadcrumbs resolve those IDs to captured span content; they do not establish
the exact originating failure step.

## Verification

- 180 focused scanner, dispatch, PostgreSQL persistence, and project-scoped
  read tests pass locally.
- A protected 54-span trace downloaded from the development host completed the
  continuous `scan_and_write` path through Gemini 3.8 Flash and real local
  Postgres. It persisted a `v2-adaptive-2` violated outcome, its accepted
  verifier report, and issue rows. Runtime: 46.4 seconds.
- A protected 80-span trace that previously exhausted the cumulative budget
  completed after the reservation/budget fix: 669,094 provider tokens, $0.525,
  three grounded findings, no retry or operational error.
- The prompt correction was replayed on the same 54-span trace before and after:
  it changed a false success into an unmet booking outcome while retaining that
  the agent's escalation conduct was compliant.
- A 24-case AppWorld diagnostic produced 4 TP, 0 FP, 12 FN, and 8 TN, with zero
  verdict changes from the prior prompt. It is deliberately enriched for prior
  misses and is not comparable to the full 59.9% recall run. Separately, 44 of
  52 dropped-state cases are byte-identical to their healthy counterpart because
  the trace excludes post-state observations; no trace-only detector can
  distinguish those pairs.
- No development trace, AppWorld input, model call ledger, or credential is
  committed to this repository.

## Known boundaries

- `confidence="M"` and blank `category`, `group`, and `fix_layer` are compatibility
  placeholders. TH-7782 owns issue grouping/taxonomy; do not add a static
  detector taxonomy here.
- The old V7 private methods remain in `scanner.py` for helper/test compatibility,
  but `scan_batch` no longer invokes them. Remove them only in a dedicated
  caller-audited cleanup.
- Hidden persisted-state failures require authoritative post-state observations
  or replay. More deliberation over byte-identical traces cannot recover them.
- The local E2E proves ingestion-ready service composition through persistence;
  it does not exercise the downstream TH-7782 clustering activity or frontend.

Run focused tests from `futureagi/` with Python 3.13:

```sh
.venv/bin/python -m pytest ee/agenthub/trace_scanner/tests -q
.venv/bin/python -m pytest tracer/tests/test_scan_unresolved.py tracer/tests/test_scan_project_scoped_read.py -q
```

Use the repository's local test services in `docker-compose.test.yml`; never
point pytest at the development or production databases.
