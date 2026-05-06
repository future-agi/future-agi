# `user_id` filter — stage-1 / stage-2 duplication

**Status:** backlog. Defer cleanup until we touch the 11 affected callers for
other reasons (or schedule a focused refactor).

## TL;DR

`user_id` filter resolution (frontend string like `"customer_001"` →
`tracer_enduser` UUID) happens in **two places** today:

1. **Stage 1 — view-level rewrite** in
   `tracer/views/observation_span.py:2012-2049` (for `list_spans_observe`)
   and `tracer/views/trace.py:4736-4771` (for `list_traces_of_session`).
2. **Stage 2 — defensive reroute** in
   `tracer/services/clickhouse/query_builders/filters.py:388-413`
   (inside `_build_condition`).

Stage 2 is the **safety net** for every other `ClickHouseFilterBuilder` caller
that doesn't run stage 1. Removing stage 2 today crashes 11 endpoints. The
duplication is the smell, not either stage individually.

## How they differ

|  | Stage 1 (view rewrite) | Stage 2 (inline reroute) |
|---|---|---|
| Where | `observation_span.py:2012`, `trace.py:4736` | `filters.py:388-413` |
| When | Before `ClickHouseFilterBuilder` is constructed | Inside `_build_condition`, on every call |
| Resolves via | PG query `EndUser.objects.filter(...)` | CH subquery on `tracer_enduser` (CDC copy) |
| Org/project scope | ✅ `organization=org`, optional `project_id=...` | ❌ scope-blind (relies on outer query's `project_id`) |
| Span vs trace mode aware | ✅ rewrites to `NORMAL` (span list) or `TRACE_END_USER` (trace list) | ❌ always wraps in `trace_id IN (...)` |
| Empty resolution sentinel | ✅ substitutes `00000000-0000-0000-0000-000000000000` so SQL still parses but matches nothing | n/a — SQL handles empty lists naturally |

## When each fires (call site matrix)

| Caller | Runs stage 1? | Without stage 2 |
|---|---|---|
| `list_spans_observe` (CH path) | ✅ yes | works; stage 2 unreachable |
| `list_traces_of_session` (CH path) | ✅ yes | works; stage 2 unreachable |
| `session_list.py` builder | ❌ no | crash: `Unknown identifier 'user_id'` |
| `voice_call_list.py` builder | ❌ no | crash |
| `time_series.py` builder | ❌ no | crash |
| `user_time_series.py` builder | ❌ no | crash |
| `session_time_series.py` builder | ❌ no | crash |
| `eval_metrics.py` builder | ❌ no | crash |
| `error_analysis.py` builder | ❌ no | crash |
| `monitor_metrics.py` builder | ❌ no | crash |
| `agent_graph.py` builder | ❌ no | crash |
| `dashboard.py` builder | ❌ no | crash |
| `simulation_dashboard.py` builder | ❌ no | crash |

13 query builders use `ClickHouseFilterBuilder`. Only the 2 list endpoints run
stage 1. The other 11 rely on stage 2 to not crash when a `user_id` filter
arrives — typically from a saved view, an AI filter, or a dashboard widget
config.

## The contract stage 2 emits

```sql
trace_id IN (
  SELECT trace_id FROM spans
  WHERE end_user_id IN (
    SELECT id FROM tracer_enduser FINAL
    WHERE user_id IN %(uid_s)s AND _peerdb_is_deleted = 0
  )
  AND _peerdb_is_deleted = 0
)
```

This is correct **as long as the outer query is project-scoped** (which the
list endpoints' WHERE clauses always are). The weakness vs stage 1: the inner
`WHERE user_id IN ...` doesn't filter `tracer_enduser` by organization, so if
two orgs ever shared the EndUser table this would leak. In this codebase
orgs are isolated (each EndUser belongs to one org), so the leak is
theoretical, not actual. Stage 1's PG-side `organization=org` filter is
defense-in-depth that stage 2 lacks.

## Cleanup options

### Option A — Promote stage 1 to a shared helper (recommended)

Extract the rewrite into a shared module:

```python
# tracer/services/clickhouse/filter_rewrites.py
def resolve_user_id_filter(filters, *, organization, project_id=None, query_mode):
    """Rewrite `user_id` string filters to `end_user_id` UUID filters."""
    out = []
    for f in filters:
        col = f.get("column_id") or f.get("columnId")
        cfg = f.get("filter_config") or f.get("filterConfig") or {}
        col_type = cfg.get("col_type") or cfg.get("colType") or "NORMAL"
        if col != "user_id" or col_type != "NORMAL":
            out.append(f)
            continue
        vals = cfg.get("filter_value") or cfg.get("filterValue") or []
        vals = vals if isinstance(vals, list) else [vals]
        vals = [v for v in vals if v]
        if not vals:
            out.append(f)
            continue
        eu_qs = EndUser.objects.filter(
            user_id__in=vals, organization=organization, deleted=False
        )
        if project_id:
            eu_qs = eu_qs.filter(project_id=project_id)
        ids = [str(u) for u in eu_qs.values_list("id", flat=True)] or [
            "00000000-0000-0000-0000-000000000000"
        ]
        out.append({
            "column_id": "end_user_id",
            "filter_config": {
                "col_type": "TRACE_END_USER" if query_mode == "trace" else "NORMAL",
                "filter_type": "text",
                "filter_op": "in",
                "filter_value": ids,
            },
        })
    return out
```

Then **every CH endpoint** that builds a filter list calls this helper before
constructing the builder. Once all 13 callers are migrated, delete stage 2
from `filters.py:388-413`.

**Pros**: minimal architectural change. Behavior matches the existing
list-endpoint stage 1 (org/project scoped, mode-aware).

**Cons**: must touch all 13 callers. Easy to miss one — leaving the safety
net deletion until last is sensible.

### Option B — Push resolution into `ClickHouseFilterBuilder` itself

Give the builder access to PG via an injected resolver:

```python
class ClickHouseFilterBuilder:
    def __init__(
        self,
        ...,
        user_id_resolver: Optional[Callable[[List[str]], List[str]]] = None,
    ):
        self.user_id_resolver = user_id_resolver
        ...
```

Each endpoint constructs the builder with a resolver bound to its
`organization` / `project_id`. Stage 2 becomes the only path; it now has
the context it needs.

**Pros**: architecturally cleanest. Builder owns user_id resolution end-to-end.

**Cons**: introduces a callback-shaped dependency in a module that
otherwise has no Django imports. Larger blast radius for the refactor.

## Recommendation

Ship **Option A**. Mechanical, low risk, fits the existing layering.
Until then, leave both stages in place — the duplication is annoying but
it's load-bearing for 11 callers.

## Score.project_id denormalisation — deferred

`EvalLogger.project_id` was denormalised in tracer migration **0074** so the
ClickHouse `has_eval` filter can scope by project without an INNER JOIN to
`spans`. The same change for `model_hub.Score` (used by `has_annotation`)
was deferred for one specific reason worth documenting.

`Score.project` already exists as a `ForeignKey("model_hub.DevelopAI", ...)`
field (`model_hub/models/score.py:120`). But the trace-annotation writer at
`tracer/views/annotation.py:824-825` populates it with `span.project` —
which is a `tracer.Project`, NOT a `model_hub.DevelopAI`:

```python
score = Score(
    observation_span=span,
    ...
    organization=span.project.organization,
    project=span.project,                # tracer.Project, not DevelopAI
)
```

`tracer.Project` and `model_hub.DevelopAI` use different DB tables
(`tracer_project` vs `model_hub_developai`). Either the FK constraint is
disabled, or this writer happens to escape constraint violations because
the column is never read against the DevelopAI table. Either way, adding
a new `tracer_project` field to `Score` for the CH filter would conflict
with the existing inconsistency.

Two ways to clean this up — both need DBA-level review:

1. **Repurpose `Score.project`** — change the FK target to
   `tracer.Project`. Risk: existing rows with DevelopAI UUIDs become
   dangling. Need a backfill that nulls or remaps them.
2. **Add a new `Score.tracer_project` FK** — clean separation from
   the existing `project` field. Backfill from `Score.observation_span.
   project_id` for source_type="observation_span" rows; from
   `Score.trace.project_id` for source_type="trace"; null for prototype
   /dataset rows.

Until this is resolved, `_build_has_annotation_condition` keeps the
existing `LEFT JOIN tracer_observation_span` pattern. It's slower but
correct, and the row count for annotations is small enough that the
JOIN cost rarely dominates.

Track separately under a new ticket; do not bundle with the user_id
refactor or the EvalLogger denormalisation.

## Related quirks worth knowing while you're in there

- The view-level rewrites (`observation_span.py:2038`, `trace.py:4758`)
  hard-code `_ids = ["00000000-0000-0000-0000-000000000000"]` when the
  EndUser lookup returns nothing. This is **intentional**: emitting a "matches
  nothing" predicate keeps the filter present in the AND chain. Returning
  `None` would silently drop the filter and widen the result. Don't
  "simplify" this without understanding the count-query consistency
  requirement.
- Operators currently honored at stage 2: `equals`/`in` (positive),
  `not_equals`/`not_in` (negative). Other ops (`contains`, `starts_with`)
  collapse to membership. Matches the frontend's `userScopeFilter` which
  always sends `equals`. If we ever support partial user_id matching in the
  picker, the resolver needs an `ILIKE` path.
