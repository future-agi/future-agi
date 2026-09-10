# Error Feed v2 Python port — work in progress

Branch: `feat/TH-7781-error-feed-scanner-port`, based on `origin/dev`.

This is a collaboration checkpoint, **not ready to merge or deploy**. The
production-data inference replay and complete backend end-to-end verification
have not finished. Existing benchmark scores belong to the Omega implementation,
not this Python port.

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

## Verification so far

- Existing scanner tests plus new harness/provider tests pass locally.
- PostgreSQL tests cover atomic rollback, durable unknown, actual version, and
  healthy-embedding eligibility.
- Twelve traces were downloaded privately from the development host. A local
  adapter check preserved their captured string attributes and metadata.
  They are simulator-project voice traces, not an accuracy-labelled production
  benchmark. No raw captures or credentials are committed here.

## Remaining before merge

1. Complete live inference and persistence/readback replay through the backend
   gateway; verify provider model routing, usage, deadlines, and retries.
2. Compare Python host behavior against frozen Omega responses. A blank
   requirement now correctly prevents success; record this deliberate fix.
3. Finish output integration: metadata population, uncalibrated legacy confidence
   (`M` is currently a compatibility placeholder), requirement-only violations,
   typed persisted reports/API serialization, and grouping ownership.
4. Remove the obsolete scanner decision methods after auditing helper callers.
   They remain in `scanner.py` for now but `scan_batch` bypasses them.
5. Finish trace-tree edge cases, large-project read bounds, timeout accounting,
   source-field completeness, and deployment/rollback configuration.
6. Review effective prompts and their production-specific revision separately
   from benchmark parity. Do not restore the old no-evidence-means-PASS rule.

Run focused tests from `futureagi/` with Python 3.13:

```sh
.venv/bin/python -m pytest ee/agenthub/trace_scanner/tests -q
.venv/bin/python -m pytest tracer/tests/test_scan_unresolved.py tracer/tests/test_scan_project_scoped_read.py -q
```

Use the repository's local test services in `docker-compose.test.yml`; never
point pytest at the development or production databases.
