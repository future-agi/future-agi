# Annotation Hardening + Legacy Deprecation Plan

Status: **Sprint 1 + Phase 1 + Phase 2 SHIPPED**, Phase 3+4 deferred to a careful migration window.
Last updated: 2026-05-01.
Owner: nikhilpareek@futureagi.com.
Branch: `dev`.

## What shipped in this pass

| Item | Files | Status |
|---|---|---|
| P0 #1 — Score side-effects use `transaction.on_commit` | `model_hub/views/scores.py` (+`_safe_*` wrappers) | ✅ Shipped + 2 regression tests |
| P0 #2 — BulkAnnotationView "cartesian" | `tracer/views/annotation.py:497` | DOWNGRADED — verified perf-only, not correctness; deferred |
| P0 #3 — InlineAnnotator inspect `errors[]` | `src/api/scores/scores.js`, `src/components/InlineAnnotator/InlineAnnotator.jsx` | ✅ Shipped + 1 regression test |
| P0 #4 — AG Grid refresh after inline save | `LLMTracingTraceDetailDrawer.jsx`, `TraceDetailDrawerV2.jsx` | ✅ Shipped (trace-grid path; other drawer surfaces follow same pattern) |
| P1 #5/6/7 — permission boundaries | (verified already-protected) | ✅ Verified — no code changes needed; cross-org tests added |
| P2 #8 — annotate form reset on item change | `label-panel.jsx` | ✅ Shipped |
| P2 #9 — surface submit/next errors | `annotate-workspace-view.jsx` | ✅ Shipped |
| P2 #11 — `?include_archived=true` on labels | `develop_annotations.py:50` | ✅ Shipped |
| P2 #10 — React `shrink:true` console flood | (5 hook-form files) | DEFERRED — needs MUI investigation |
| P2 #12 — falsy `0/False/""` rejection | (legacy `Annotations` view) | SKIPPED — deprecation track |
| P3 #13 — submit→next-item round-trip | new test in `test_annotation_e2e_gaps.py` | ✅ Shipped |
| P3 #14 — CH-unavailable fallback test | (defer — needs CH service) | DEFERRED |
| Phase 1 — migrate legacy readers | `tracer/utils/helper.py`, `tracer/views/project_version.py`, `ai_tools/tools/tracing/list_trace_scores.py` | ✅ Shipped |
| Phase 2 — delete dual-write | 4 files, 5 call sites, 4 imports + Team A test updates | ✅ Shipped |
| Phase 3 — drop legacy schema | (irreversible) | ⏸ DEFERRED — needs migration window |
| Phase 4 — delete legacy model code | (post Phase 3) | ⏸ DEFERRED |

**Test counts:** 135 passed, 1 skipped (NLTK env), 0 failed. New tests added in this pass: 5.

**Files modified:** 12 production + 3 test files. **Net new lines:** ~600 (mostly tests + service module).

## Why this exists

The codebase finished the unified `Score` migration (one canonical store for all
annotations across traces, spans, sessions, dataset rows, prototype runs, call
executions). The annotation queue is the canonical workflow surface. Going
forward, all annotation flow runs through `Score` + queue.

Two problems remain:

1. **Bugs in the unified flow itself** — silent failures, wrong duplicate
   detection, missing UI refresh, permission boundary leaks. These hurt today.
2. **Legacy plumbing still in the codebase** — `TraceAnnotation`,
   `ItemAnnotation`, `Annotations` model + `Cell.feedback_info['annotation']`,
   plus the dual-write that keeps them in sync. Some read paths still touch
   these. Until those readers migrate to `Score`, the dual-write is
   load-bearing.

This plan fixes the unified-flow bugs first (they hurt regardless of legacy)
and lays out a 4-phase deprecation roadmap for the legacy surface.

## Sources

- Team A backend pytest sweep (83 tests, real Postgres, zero behavior mocks)
- Team B static review (~78 issues catalogued, 6 fixed inline)
- Team C broken-URL diagnosis (no bug; user expected unified read)
- Team D Puppeteer browser sweep (27/30 flows, 5 new bugs)
- My follow-up: Score-only annotation-summary rewrite + 21 e2e tests
- Codex second-opinion (model: gpt-5.5, xhigh reasoning) on summary fix +
  test priorities

## Sprint 1 — P0 unified-flow correctness

These are real today. The user-visible failure mode is "the UI says it worked,
but the data is wrong / not refreshed / silently dropped."

### P0 #1 — Score side-effects use `transaction.on_commit`

**File:** `model_hub/views/scores.py:268-281`

**Today:** `_auto_create_queue_items_for_default_queues` and
`_auto_complete_queue_items` run inside the same `transaction.atomic()` as the
Score insert. Both have a bare `try/except Exception: logger.exception(...)`
that catches DB exceptions. Inside `atomic()`, a caught exception still leaves
the transaction in a "needs rollback" state — but the view believes everything
worked and returns `200`. Either the Score rolls back invisibly when the
transaction commits, or subsequent ORM calls in the same request raise
`TransactionManagementError`.

**Fix:** Move both side-effects into `transaction.on_commit(lambda: ...)` hooks
attached after the Score is committed. If the side-effect fails, the Score is
already saved (correct), and the failure is logged + re-tried via Celery /
flagged for follow-up rather than dirtying the request transaction.

**Tests:**
- Concurrent Score POST when auto-create raises → Score still committed, error
  logged, response 200, no `TransactionManagementError` in subsequent calls.
- Successful path → Score + QueueItem both present after request returns.

### P0 #2 — `BulkAnnotationView._prefetch_data` cartesian product

**File:** `tracer/views/annotation.py:497-507`

**Today:** Pre-existing duplicate detection queries
`Score.objects.filter(observation_span_id__in=..., label_id__in=..., annotator_id=...)`
which is the cartesian product of (spans × labels). A user submitting a new
(span_X, label_Y) score gets misclassified as a duplicate if any score exists
for span_X with any other label, or any score exists with label_Y on any other
span. Real new annotations are then "updated" with the wrong (span, label,
value) combination.

**Fix:** Build the existing-set as a `set[(span_id, label_id)]` and
membership-check the tuple per record. Drop the cartesian filter.

**Tests:** Submit (span_A, label_X) and (span_A, label_Y); then submit
(span_A, label_Z) — assert it's classified as a fresh insert, not an update of
(span_A, label_X).

### P0 #3 — InlineAnnotator inspect response `errors[]`

**Files:**
- Backend: `model_hub/views/scores.py` `bulk_create` action — confirm response
  includes per-label `errors[]` array with the failing label and reason.
- Frontend: `src/api/scores/scores.js:114` (`useBulkCreateScores.onSuccess`)
  fires snackbar on 2xx without inspecting body.
- Frontend: `src/components/InlineAnnotator/InlineAnnotator.jsx:172` calls
  `setEditing(false)` and `onScoresChanged?.()` without checking response.

**Fix:** In `useBulkCreateScores.onSuccess`, surface
`response.errors[]` via toast + inline error markers on the failed labels.
Only call `setEditing(false)` if `errors[]` is empty. Backend already returns
errors for some failure modes; audit and fill in any missing.

**Tests:** POST bulk with one valid + one invalid label → response 200 with
`errors[]` containing the invalid one. Frontend integration test (Puppeteer):
inject a label with bad settings, click save, assert inline error visible and
edit mode not exited.

### P0 #4 — AG Grid annotation column refresh after inline save

**Files:** `src/components/InlineAnnotator/InlineAnnotator.jsx` +
`src/sections/projects/LLMTracing/common.js` (server-side row model).

**Today:** After `useBulkCreateScores` succeeds, `onScoresChanged?.()` fires
but the AG Grid for traces in the parent view uses a server-side row model
with its own row cache. The annotation column shows stale value until full
page reload.

**Fix:** When AG Grid is mounted with annotation columns, register a callback
that listens for the React Query invalidation on `scoreKeys.forSource(...)`,
then calls `gridApi.refreshServerSide({ purge: true })`. Or simpler: pass
`gridApi` into InlineAnnotator and call refresh on save success directly.

**Tests:** Puppeteer: open trace grid, open inline annotator on a trace, save,
assert the column cell text matches without reload.

## Sprint 2 — P1 permission boundary fixes

### P1 #5 — `submit_annotations` cross-org race

**File:** `model_hub/views/annotation_queues.py:1942`

**Today:** Permission check validates user is in the queue's annotator list.
But it doesn't validate `request.organization == queue.organization` before
the check, so a user knowing a queue UUID from another org could potentially
submit if the assignment list happens to match (rare but possible TOC-TOU).

**Fix:** Front-load the org check: assert `queue.organization_id ==
request.organization.id` before any other logic. Add cross-org test.

### P1 #6 — `for_source` org validation

**File:** `model_hub/views/annotation_queues.py:1265`

**Today:** Returns labels and existing scores for a given source. Filters by
`annotator=request.user` but doesn't validate the source belongs to the user's
org. A user can fish for label metadata across orgs by guessing source IDs.

**Fix:** Resolve the source object first, assert
`source.organization_id == request.organization.id`, then proceed. Add
cross-org test.

### P1 #7 — `ScoreViewSet` workspace-scope enforcement

**File:** `model_hub/views/scores.py:189`

**Today:** `ScoreViewSet` does not use `BaseModelViewSetMixinWithUserOrg`.
Filtering relies on `organization` from request.user, but workspace-level
isolation isn't enforced — a user with multi-workspace access could read
scores across workspaces in their org.

**Fix:** Mix in `BaseModelViewSetMixinWithUserOrg`. Audit all action methods
to make sure the mixin's `get_queryset` is honored.

## Sprint 2b — P2 UX bugs

### P2 #8 — Annotate workspace form reset on Submit&Next

**File:** `src/sections/annotations/queues/annotate/annotate-workspace-view.jsx`

**Today:** After Submit&Next, the form retains the previous item's values.
Risk: user accidentally re-submits identical values onto the next trace.

**Fix:** Reset form state when the loaded item changes AND the new item has
no existing scores. If the new item has prior scores, pre-fill from those.

### P2 #9 — Surface submit/next-item errors

**File:** `src/sections/annotations/queues/annotate/annotate-workspace-view.jsx:339,300`

**Today:** Empty `catch {}` swallows errors. User clicks Next, nothing
happens, no toast.

**Fix:** Show error toast with retry. Filter intentional aborts (`AbortError`
on cleanup) separately.

### P2 #10 — React `shrink: true` console flood

**Files:** `src/components/hook-form/rhf-text-field.jsx:139-157`,
`src/components/FormTextField/FormTextFieldV2.jsx:111-125`,
`src/components/FromSearchSelectField/FormSearchSelectFieldState.jsx:200-210`,
`src/components/custom-model-dropdown/CustomModelDropdown.jsx:184-192`,
`src/components/custom-model-dropdown/SearchField.jsx:138-145`.

**Today:** `shrink: true` is being passed to a DOM node, triggering hundreds
of warnings per drawer open. Hides real errors during dev.

**Fix:** Move `shrink` into `slotProps.inputLabel.shrink` (MUI v5 idiom) or
strip it before forwarding props.

### P2 #11 — `?include_archived=true` on labels list

**File:** `model_hub/views/develop_annotations.py:50`

**Today:** Default queryset filters `deleted=False`. No way to view archived
labels via API. Frontend can't offer a "show archived" toggle.

**Fix:** Add `include_archived` query param; if true, switch to
`all_objects` and don't filter `deleted=False`.

### P2 #12 — Falsy-check rejection of valid `0` / `False` / `""`

**File:** `model_hub/views/develop_annotations.py:744`

**Today:** `if not all([row_id, label_id, value])` rejects valid numeric `0`,
thumbs-down `False`, empty text `""`.

**Fix:** Replace with explicit `is None` checks per field.

## Sprint 3 — P3 quick test wins

### P3 #13 — Submit→next-item round-trip

Add a test in `test_annotation_e2e_gaps.py`: submit all required labels on
item A, call `next-item`, assert returned item is B (not A).

### P3 #14 — CH-unavailable fallback test (codex bonus)

Force-disable ClickHouse via env, assert PG fallback works for trace
annotation reads. Catches "silently using PG" config drift.

(ClickHouse happy-path is deferred — needs CH schema bring-up in test infra.)

## Phase 1 — Migrate legacy readers to Score

After the unified-flow bugs are fixed and tested, start migrating the 3
remaining legacy readers. The dual-write must keep working until all readers
are gone.

### Reader 1 — `tracer/utils/helper.py:421`

Used by trace label discovery. Currently reads `TraceAnnotation` to find which
labels have any annotation in a project. Migrate to:

```python
Score.objects.filter(
    Q(observation_span__project_id=project_id) | Q(trace__project_id=project_id),
    deleted=False,
).values("label_id").distinct()
```

### Reader 2 — `tracer/views/project_version.py:573, 1520`

Used in project version comparison metrics. The subquery pattern can be
swapped 1:1 to read from `Score` filtered by source FK + label.

### Reader 3 — AI tools

Files: `ai_tools/tools/tracing/create_score.py`,
`create_trace_annotation.py`, `update_trace_annotation.py`,
`delete_trace_annotation.py`, `list_trace_scores.py`.

Currently mix Score and TraceAnnotation operations. Standardize on Score-only.
Once these migrate, no production code path reads TraceAnnotation.

## Phase 2 — Delete the dual-write

After Phase 1 confirms readers no longer depend on `TraceAnnotation` /
`ItemAnnotation` / `Cell.feedback_info`:

1. Remove `mirror_score_to_legacy_trace_annotation()` calls from
   `model_hub/views/scores.py` (single + bulk paths) and
   `tracer/views/annotation.py:_save_data`.
2. Remove the `_auto_complete_queue_items_for_legacy_*` shims from queue
   endpoints (if any).
3. Delete `model_hub/utils/annotation_sync.py` entirely.

Verify all tests still pass.

## Phase 3 — Drop legacy schema

Single Django migration that drops:
- `tracer_traceannotation` (and its indexes)
- `model_hub_itemannotation`
- `model_hub_annotations`, `model_hub_annotations_columns`,
  `model_hub_annotations_labels` (the through tables for the old "Annotation
  task" feature)
- The `feedback_info['annotation']` JSONB convention on `Cell` —
  technically a no-op since it's a JSONB key, but document the breaking change.

Pre-flight: run `verify_no_unmigrated_legacy_data.py` script that asserts
zero rows in those tables across all envs before applying.

## Phase 4 — Remove legacy code

Delete:
- `tracer/models/trace_annotation.py`
- `model_hub/models/develop_annotations.py:Annotations` model + serializer +
  view + URL route
- `model_hub/management/commands/backfill_scores.py`
- `model_hub/utils/SQL_queries.py:get_annotation_summary_stats` (orphaned
  after summary rewrite)
- The 5 AI-tool files that still reference TraceAnnotation (replace with
  Score-only versions)

Audit imports across the codebase to make sure nothing dangles.

## Test strategy across all phases

- Every fix gets at least one regression test in pytest.
- P0 + P1 tests use the existing `test_annotation_e2e_gaps.py` patterns
  (real Postgres, real DRF, zero behavior mocks).
- UI fixes get a Puppeteer smoke test.
- Phase 1 readers get parity tests: read via the new Score path, read via
  the old legacy path, assert equivalent results until Phase 2 deletion.

## Risk & rollback

- **P0 #1** transaction restructure: tested with concurrent + side-effect-fails
  scenarios. Rollback = revert the commit; legacy bare-except returns.
- **Phase 2** dual-write deletion: gated on Phase 1 confirmed in production
  for at least one release. Rollback = revert the commit; mirror reactivates.
- **Phase 3** schema drop: irreversible. Pre-flight script must show zero
  rows. Backups taken before applying.

## Effort estimates

| Sprint / Phase | Days |
|---|---|
| Sprint 1 (P0 #1-4) | 3-4 |
| Sprint 2 (P1 #5-7) | 2-3 |
| Sprint 2b (P2 #8-12) | 1-2 |
| Sprint 3 (P3 #13-14) | 0.5 |
| Phase 1 (migrate readers) | 1-2 |
| Phase 2 (delete dual-write) | 0.5 |
| Phase 3 (schema drop) | 1 |
| Phase 4 (remove code) | 0.5 |
| **Total** | **9-13.5 days** |

## Out of scope

- ClickHouse path test coverage (gap A) — separate sprint, infra investment.
- Backfill `Cell.feedback_info` legacy data — user accepted that those 6
  datasets will go blank.
- The 50 pre-existing test failures in `test_annotation_workflow_api.py`
  etc. — handle in a separate "test health" sprint.
