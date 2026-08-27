## Summary

Add a single `filter_combinator` field (`"and"` default, `"or"`) so users can combine query-builder filters with OR across the whole filter list, instead of being forced into AND-only.

The combinator is one operator for the entire list (not per-pair), matching the existing query-builder contract where every filter shares a single combining operator.

### Backend

- **Django ORM** (`FilterEngine.get_filter_conditions_for_system_metrics`, `get_filter_conditions_for_voice_system_metrics`): `reduce(operator.or_)` when `filter_combinator == "or"`, else `reduce(operator.and_)`.
- **Raw SQL / CTE builders** (`get_sql_filter_conditions_for_*`): the OR case is parenthesised — `AND (f1 OR f2 OR ...)` — so a surrounding `project_id = … AND` scope keeps binding to the whole group. AND mode stays as `AND f1 AND f2`.
- **ClickHouse** (`ClickHouseFilterBuilder.translate` and the v2 subclass): same parenthesisation; the OR group is wrapped so caller-supplied surrounding scope is preserved.
- **Eval templates list** (`build_eval_list_queryset`): the advanced filters (`eval_type`, `output_type`, `tags`, `template_type`, `names`, `created_by` and their `*_not` negations) are now combined with the configured combinator. In OR mode the `*_not` conditions are negated before the OR so they keep their exclude semantics (AND mode still applies them via `.exclude()`). The `eval_type` filter, previously applied in the view, now lives inside `build_eval_list_queryset` so the combinator covers it too.

### Frontend

- A reusable AND/OR toggle chip between query-builder chips in the shared `QueryInput` (gated by a new `showCombinator` prop) and in the Evals `QueryInput`. Toggling re-applies through `onApply(tokens, combinator)`.
- Threaded through trace / span / eval list requests (`filter_combinator` on the wire). Absent flag == `"and"`, so every existing caller is unchanged.

## Design decisions (please review)

These are intentional choices that deviate from "OR just works everywhere" — flagging them so a maintainer can object before merge:

1. **One combinator for the whole list, not per-pair.** There is a single `filter_combinator` value, applied across all filters. This keeps the request contract small and matches how the tracing filter model already treats the list.

2. **OR on the Evals filter panel is Query-tab-only.** The Basic and AI tabs are AND-only by design (the issue scopes OR to the free-form query builder). Two panels behave differently on tab switch:

   - **`TraceFilterPanel`** actively resets the combinator back to `"and"` when the user switches to the Basic tab (see `TraceFilterPanel.jsx` tab `onChange`).
   - **`EvalFilterPanel`** does **not** reset on tab switch — the last Query-tab OR value is retained in `combinatorRef` and re-sent by the debounced auto-apply until the user hits Clear or applies an AI filter (both of which force `"and"`).
     These two behaviours are inconsistent by accident, not intent. If maintainers prefer uniform behaviour, the Eval panel should also reset on switch to Basic. **Open question for review — which is correct?**

3. **OR is not persisted to the saved-filter localStorage payload** in `LLMTracingView`. Re-loading a saved trace/span view resets the combinator to `"and"`. This is a pre-existing gap (the save payload predates this PR and never stored the combinator); fixing it is out of scope for #2226 but noted for follow-up.

4. **Non-ClickHouse / non-combinator-aware paths ignore the flag.** Several existing code paths (`get_export_data`, `get_trace_id_by_index`, realtime `socket.py`, `project_version.py` graph queries, and `prompt_metrics.py` CTE builders) call `FilterEngine.get_filter_conditions_for_system_metrics(filters)` without passing `filter_combinator`. None of these are touched by this PR; they were already AND-only before #2226 and remain so. They are listed here only so reviewers know OR is **not** guaranteed on those routes yet.

## Test results

- `tracer/tests/test_filter_combinator.py` — pins the contract at every layer: ORM connectors, raw-SQL/CTE parenthesisation, ClickHouse parenthesisation, absent-flag == `"and"`, project-scope leakage, and a cross-engine (Django vs ClickHouse) equivalence class for the OR query shape.
- `model_hub/tests/test_eval_list.py` — **new `TestEvalListFilterCombinator`** class covering the OR path that previously had no tests: default-is-and, OR union across different filter keys, OR-not-narrowing, `*_not` exclusion semantics under OR, single-`*_not` under OR, and a 3-filter AND-vs-OR contrast.

## Scope

Backend + frontend. No model changes, no gateway/contract changes. Frontend changes are additive (`showCombinator` prop, new `filter_combinator` request field that defaults to `"and"`).

Closes #2226
