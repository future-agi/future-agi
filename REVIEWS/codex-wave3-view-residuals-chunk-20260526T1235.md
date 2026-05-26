# Codex review — wave-3 view residuals: observation_span.py + trace.py

**Commits reviewed**: `d559432829aa1e970c0ed1cc0950ab01d6c1e5a4` (observation_span.py)
+ `f02e7e00a` (trace.py)

**Branch**: `feat/ch25-spans-migration`

**Reviewer prompt focus**:
1. `has_voice_traces` migration semantic preservation against the prior ORM
   `.filter(trace__project_id=, parent_span_id__isnull=True,
   observation_type='conversation').exists()` call.
2. `prev_next_*_by_start_time` comment accuracy for wave-3 reader signatures
   and the filter-aware gap.
3. Any stale D-027 comments still mis-describing the file.
4. Tenant scope preservation on the `has_voice_traces` site.
5. Wave-2 P0/P1 anti-patterns (Project.organization filter, silent missing-
   row drops, N+1, Django FK fields receiving CHSpan dataclass).

## P0

None.

## P1

None. The `has_voice_traces` migration preserves the intended CH semantic:
the site first tenant-gates the project through
`_project_queryset_for_request(...)` before calling CH
(`tracer/views/trace.py:3401`, `tracer/views/trace.py:384`), and
`has_root_spans_of_type` filters active CH rows by denormalized
`project_id`, `observation_type`, and root `parent_span_id = ''`
(`tracer/services/clickhouse/v2/span_reader.py:1021`). That matches the old
ORM check assuming the required invariant `ObservationSpan.project_id ==
ObservationSpan.trace.project_id`; CH cannot exactly reproduce corrupted-
row behavior from the old `trace__project_id` join without joining
traces. The denormalization invariant is the same one wave-2 codex review
already accepted for other CH-routed reads in this file.

## P2

None. I did not see the wave-2 P0/P1 anti-patterns introduced in these
hunks: no `Project.organization`-only replacement at the
`has_voice_traces` site, no silent CH list drops, no N+1 loop, and no
Django FK assignment from `CHSpan`. The new path is one CH existence call
after a scoped project gate (`tracer/views/trace.py:3417`).

## P3

Stale comment line reference: `compare_traces` says `get_observation_spans()`
is at `observation_span.py L3019-3059`, but current `get_observation_spans`
starts at line 3065 and the orphan/root helpers it describes are later
(`tracer/views/trace.py:2139`,
`tracer/views/observation_span.py:3065`,
`tracer/views/observation_span.py:3237`). Behavior is unaffected, but the
breadcrumb is misleading.

**Triage**: addressed in-place. The compare_traces breadcrumb now points to
`get_observation_spans` by symbol name rather than a brittle line range.

## Other observations

I did not find stale D-027 comments still mis-describing behavior in the
reviewed files. The `prev_next_*_by_start_time` comments accurately describe
the wave-3 signatures and the filter-aware gap: the readers are unfiltered,
while these endpoints apply eval, annotation, and span-attribute filters
before walking (`tracer/views/trace.py:2406`,
`tracer/views/observation_span.py:2532`,
`tracer/services/clickhouse/v2/span_reader.py:1123`).

No tests run; this was a static review.

## Verdict

Two clean commits land cleanly. One P3 fixed in-place. No follow-up needed.
