# Error Feed v2: backend E2E handoff

Branch: `fix/TH-7781-investigation-handoff`

Status: integration in progress; the full ingest-to-Feed path is not yet verified.

## Target flow

1. A selected project's OTLP trace finishes ingestion. ClickHouse holds the spans; a completed-trace notification identifies the project and trace. The notification is a trigger, not the trace payload.
2. Django records the notification, applies project configuration/admission, and creates or updates one `TraceInvestigationJob` for that trace. Retries must not produce duplicate current reports.
3. The Node worker claims a due job through Django's internal API, reads bounded project-scoped evidence from ClickHouse, and runs Omega. The worker returns an investigation result to Django; it does not write Feed tables directly.
4. Django validates the result and stores one `TraceInvestigationReport` plus normalized findings, requirement checks, evidence receipts, and accounting rows. Only the active report for a trace is projected into the Feed.
5. Grouping attaches each actionable `TraceInvestigationFinding` to a `TraceErrorGroup` and records membership. This is Atharva's integration point. A saved report with `grouping_status=pending` is **not** yet a visible Feed issue.
6. The existing `/tracer/feed/issues/*` read APIs serve the group, counts, trace list, and investigation evidence. The current UI flow remains Feed list → issue detail → traces/evidence → action. RCA must read the same current findings.
7. Model-cost receipts and tenant credit deduction are separate checks. Verify the billing consumer and a balance mutation; a `cost_usd` value alone does not prove charging.

## What is on this branch

- Investigation control, publication, and normalized models: `futureagi/tracer/services/trace_investigation.py`, `futureagi/tracer/models/trace_investigation.py`.
- Internal notification, claim, attempt, and report routes: `futureagi/tracer/urls.py` and `futureagi/tracer/views/trace_investigation.py`.
- Schema reconciliation and legacy backfill: `futureagi/tracer/migrations/0099_*` through `0102_reconcile_report_flags.py`. Historical scan flags and findings are copied to the new tables; do not dual-write new investigations to `TraceScanResult`/`TraceScanIssue`.
- Feed selectors now read current investigation reports/findings: `futureagi/tracer/queries/feed.py`. The UI makes a small evidence-label adjustment in `frontend/src/pages/dashboard/error-feed/components/OverviewTab.jsx`; no new Feed route is required for this cutover.

The RCA adapter edits in `futureagi/ee/agenthub/cluster_rca/` are **not yet committed or pushed**. Do not treat RCA as complete from this branch alone.

## Evidence so far

- A migration replay on a clone of the existing local database applied 0100–0102 and preserved 16,471 legacy scans and 1,479 legacy findings. The copied `has_issues` flags matched the source scans.
- Two fresh OTLP traces produced current Omega reports with one finding each in the local stack. They did not become visible Feed issues because grouping was not connected in that run.
- Focused normalized-evidence projection tests pass. A historical grouped issue can be read through the Feed selectors. Neither check is a full browser-to-worker E2E test.

## E2E acceptance run

Use the existing local stack and a selected project. Record the project ID, trace ID, job ID, attempt ID, report ID, finding ID, group ID, and timestamps at each boundary. Run these cases:

1. **Failure trace:** send OTLP spans, observe one completed-trace notification, one admitted job, a worker claim, Omega's report callback, a current report/finding, grouping, then the issue and its evidence in both Feed API and UI.
2. **Healthy trace:** confirm the report has no finding and does not create a false Feed issue; its `has_issues` flag must be false.
3. **Duplicate and retry:** replay the notification and retry a worker attempt. Confirm idempotent publication and one current report; stale attempts must not replace it or regroup obsolete findings.
4. **Isolation and configuration:** a non-selected project must not enter the v2 flow; one tenant must not read or charge another tenant's report. Check the project's sampling configuration explicitly.
5. **Legacy history:** after migration, an existing grouped issue still appears with its trace counts/evidence using the new normalized source. Check RCA on both a backfilled finding and a new Omega finding.
6. **Accounting:** reconcile gateway usage, stored accounting rows, the existing usage emitter, and the actual tenant credit balance change. Record failed/retried calls separately.

For each failure, identify the broken boundary (notification, admission, claim, evidence read, Omega, publication, grouping, Feed read, UI, or billing) instead of marking the whole algorithm as failed. Keep a single E2E trace log with the IDs above so the run can be reproduced.

## Remaining ownership and decisions

- **Atharva:** connect grouping to current findings and memberships; verify the resulting issue through the existing Feed APIs. Treat the experiment `results-for-clustering.json` as a test artifact, not the database contract.
- **Kartik/backend:** finish and verify the RCA current-finding adapter, complete the local E2E run, and fix any boundary failures. The current branch does not prove grouping, RCA, UI, or tenant balance deduction end to end.
- **Later:** add a managed Playwright E2E flow once its harness can start the Omega worker and control the model response. The current harness does not provide that control, so this branch uses the agreed harness-gap exemption.
