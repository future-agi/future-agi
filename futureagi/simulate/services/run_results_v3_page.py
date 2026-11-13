"""The v3 calls page, built from one pass over a run's calls.

The verdict and score expressions cost about a millisecond per call, so the
page reads every per-call value it needs in a single query and derives the
summary, the groups and the page slice from those rows in Python. A completed
run's rows are cached until the run or its eval configs change.
"""

from __future__ import annotations

import functools
import hashlib
import json
import logging
import uuid
from collections.abc import Callable
from typing import Any

from django.core.cache import cache
from django.db.models import Count, Func, JSONField, QuerySet

from simulate.models import CallExecution, SimulateEvalConfig, TestExecution
from simulate.services.harness_scenarios import level_label
from simulate.services.run_results_v3 import (
    CALL_ROW_FIELDS,
    CALL_ROW_METADATA_KEYS,
    CALL_ROW_SCENARIO_FIELDS,
    OUTCOME_LABELS,
)
from simulate.services.run_results_v3_queries import (
    EVALUATED_OUTCOMES,
    GROUP_FIELDS,
    LIST_AXES,
    ORDERING_FIELDS,
    OUTCOMES,
    UNGROUPED,
    _aggregate_expressions,
    _configured_eval_verdict,
    _eval_score,
    _group_keys,
    _summary_from_values,
    apply_run_call_query,
    run_calls_queryset,
)
from simulate.services.run_results_v3_scan import scan_call_values
from simulate.services.run_results_v3_scoring import (
    LiveEvals,
    live_eval_specs,
    live_eval_specs_by_run_test,
)
from simulate.services.scoring_status import SCORING_ROW_FIELDS, scoring_values

logger = logging.getLogger(__name__)

CALL_VALUE_FIELDS = (
    "id",
    "duration_seconds",
    "result_latency_ms",
    "result_turn_count",
    "result_tokens",
    "result_cost_cents",
    "result_csat",
    "avg_stop_time_after_interruption_ms",
    "ai_interruption_count",
    "result_goal",
    *dict.fromkeys(GROUP_FIELDS.values()),
    "result_persona_name",
    "result_scenario_name",
    "result_situation",
)
METRIC_FIELDS = {
    "duration": "duration_seconds",
    "latency": "result_latency_ms",
    "tokens": "result_tokens",
    "cost_cents": "result_cost_cents",
}
# Orderings the scan does not reproduce; the ORM path serves them.
SQL_ORDERED_FIELDS = frozenset(ORDERING_FIELDS) - {"started_at"}
SUBSET_PARAMS = ("search", "filters", "group_by", "group_key", "ordering")
STATUS_FILTERS = ("status", "status_not")
CONFIG_KEY_FIELDS = ("id", "updated_at", "config", "eval_template__updated_at")
# CSAT and other per-call metrics can still land after the run completes
# without moving anything in the cache key, so a cached pass stays short-lived.
CACHE_TIMEOUT = 5 * 60
# Compare's facts derive from a run's whole-run rows and hold the stamp those
# rows were cached with; rows cached again under a new stamp drop them.
COMPARE_FACTS_SHAPE = "compare-facts:v5"


def _score_expressions(
    execution: TestExecution,
    columns: list[dict[str, str]],
    live_evals: LiveEvals | None = None,
) -> dict[str, Any]:
    if columns and live_evals is None:
        live_evals = live_eval_specs(execution.run_test_id)
    expressions = {}
    for index, column in enumerate(columns):
        eval_id = str(column["id"])
        live = live_evals.get(eval_id) if live_evals is not None else None
        expressions[f"score_{index}"] = (
            _configured_eval_verdict(eval_id, live.config, live.spec)[0]
            if live is not None
            else _eval_score(eval_id)
        )
    return expressions


def _call_values(
    queryset: QuerySet, columns: list[dict[str, str]], scores: dict[str, Any]
) -> list[dict[str, Any]]:
    rows = []
    # The same query reads each call's stored scoring state, so the scoring
    # counts cost no second pass. A cached row holds that state, never a status:
    # the scoring clocks move with no write, so each request derives from it.
    fields = [field for field in CALL_VALUE_FIELDS if field != "id"]
    for values in scoring_values(queryset.annotate(**scores), *fields, *scores):
        row = {field: values[field] for field in CALL_VALUE_FIELDS}
        row["id"] = str(row["id"])
        row["scores"] = {
            str(column["id"]): values[f"score_{index}"]
            for index, column in enumerate(columns)
        }
        row.update({field: values[field] for field in SCORING_ROW_FIELDS})
        rows.append(row)
    return rows


def _cache_key(
    execution: TestExecution,
    subset: dict[str, Any],
    columns: list[dict[str, str]],
    *,
    configs: list[tuple[Any, ...]] | None = None,
    calls: int | None = None,
    shape: str = "calls:v4",
) -> str | None:
    if execution.status != TestExecution.ExecutionStatus.COMPLETED:
        return None
    version = execution.completed_at or execution.updated_at
    if configs is None:
        # The live configs by content, not by their latest updated_at: a removal
        # drops one from the set, a template edit moves its own stamp, and a
        # save(update_fields=[...]) that skips updated_at still changes the config.
        configs = list(
            SimulateEvalConfig.objects.filter(
                run_test=execution.run_test, deleted=False
            )
            .order_by("id")
            .values_list(*CONFIG_KEY_FIELDS)
        )
    if calls is None:
        # A call leaves a finished run only by deletion, which moves nothing else here.
        calls = CallExecution.objects.filter(test_execution=execution).count()
    fingerprint = hashlib.sha1(
        json.dumps(
            {
                "subset": subset,
                "columns": [str(column["id"]) for column in columns],
                "configs": configs,
                "calls": calls,
            },
            sort_keys=True,
            default=str,
        ).encode()
    ).hexdigest()
    # The shape names what is cached; an entry written in another shape must miss.
    return f"simulate:v3:{shape}:{execution.id}:{version.timestamp()}:{fingerprint}"


def _run_key_inputs(
    executions: list[TestExecution],
) -> tuple[dict[Any, list[tuple[Any, ...]]], dict[Any, int]]:
    """Each run test's configs and each run's call count, read once for all runs."""
    configs: dict[Any, list[tuple[Any, ...]]] = {}
    for run_test_id, *config in (
        SimulateEvalConfig.objects.filter(
            run_test_id__in={execution.run_test_id for execution in executions},
            deleted=False,
        )
        .order_by("id")
        .values_list("run_test_id", *CONFIG_KEY_FIELDS)
    ):
        configs.setdefault(run_test_id, []).append(tuple(config))
    calls = dict(
        CallExecution.objects.filter(
            test_execution_id__in=[execution.id for execution in executions]
        )
        .order_by()
        .values("test_execution_id")
        .annotate(count=Count("id"))
        .values_list("test_execution_id", "count")
    )
    return configs, calls


def _stamp_key(key: str) -> str:
    """The key of the stamp a run's whole-run rows are cached with."""
    return f"{key}:stamp"


def cached_run_values(
    executions: list[TestExecution],
    read: Callable[[list[TestExecution]], dict[Any, Any]],
    *,
    shape: str,
    columns_by_run: dict[Any, list[dict[str, str]]] | None = None,
    key_inputs: tuple[dict[Any, list[tuple[Any, ...]]], dict[Any, int]] | None = None,
    valid: Callable[[Any, Any], bool] | None = None,
    stamps: dict[Any, str] | None = None,
) -> dict[Any, Any]:
    """Per-run values cached under the page's invalidation inputs, read in one batch.

    ``read`` reads the runs the cache missed and returns their values by run id.
    ``key_inputs`` is ``_run_key_inputs(executions)`` when the caller already has it;
    a cached value ``valid(run_id, value)`` rejects is read again. With ``stamps``,
    each value is cached with a fresh stamp, a value cached without one is read
    again, and each run's stamp goes in ``stamps``.
    """
    configs, calls = key_inputs or _run_key_inputs(executions)
    keys = {
        execution.id: _cache_key(
            execution,
            {},
            (columns_by_run or {}).get(execution.id, []),
            configs=configs.get(execution.run_test_id, []),
            calls=calls.get(execution.id, 0),
            shape=shape,
        )
        for execution in executions
    }
    wanted = [key for key in keys.values() if key]
    if stamps is not None:
        wanted += [_stamp_key(key) for key in wanted]
    cached = cache.get_many(wanted) if wanted else {}
    values = {
        execution_id: cached[key]
        for execution_id, key in keys.items()
        if key is not None
        and key in cached
        and (stamps is None or _stamp_key(key) in cached)
        and (valid is None or valid(execution_id, cached[key]))
    }
    if stamps is not None:
        stamps.update(
            {
                execution_id: cached[_stamp_key(keys[execution_id])]
                for execution_id in values
            }
        )
    missed = [execution for execution in executions if execution.id not in values]
    if missed:
        fresh = read(missed)
        values.update(fresh)
        to_cache = {}
        for execution_id, value in fresh.items():
            if stamps is not None:
                stamps[execution_id] = uuid.uuid4().hex
            if key := keys.get(execution_id):
                to_cache[key] = value
                if stamps is not None:
                    to_cache[_stamp_key(key)] = stamps[execution_id]
        try:
            # One transaction, so no reader sees new rows under the old stamp.
            cache.set_many(to_cache, timeout=CACHE_TIMEOUT)
        except Exception:
            # The values are already read; a refused write must not fail the page.
            logger.warning(
                "Could not cache %s runs' %s", len(to_cache), shape, exc_info=True
            )
    return values


def cached_execution_rows(
    executions: list[TestExecution],
    columns_by_run: dict[Any, list[dict[str, str]]],
    live_evals: LiveEvals | None = None,
    key_inputs: tuple[dict[Any, list[tuple[Any, ...]]], dict[Any, int]] | None = None,
    stamps: dict[Any, str] | None = None,
) -> dict[Any, list[dict[str, Any]]]:
    """Each run's unfiltered rows, from the entry the default calls page caches.

    Each run's rows stamp goes in ``stamps``.
    """

    return cached_run_values(
        executions,
        lambda missed: {
            execution.id: scan_call_values(
                execution,
                columns_by_run[execution.id],
                "-started_at",
                live_evals=live_evals,
            )
            for execution in missed
        },
        shape="calls:v4",
        columns_by_run=columns_by_run,
        key_inputs=key_inputs,
        stamps={} if stamps is None else stamps,
    )


def _cached_call_values(
    execution: TestExecution,
    subset: dict[str, Any],
    columns: list[dict[str, str]],
    read_rows: Callable[[], list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], bool]:
    """The rows, and whether they were read now rather than served from cache."""
    key = _cache_key(execution, subset, columns)
    if key and (cached := cache.get(key)) is not None:
        return cached, False
    rows = read_rows()
    if key:
        try:
            # The whole-run rows carry a new stamp, which compare's facts check.
            cache.set_many(
                {key: rows, **({} if subset else {_stamp_key(key): uuid.uuid4().hex})},
                timeout=CACHE_TIMEOUT,
            )
        except Exception:
            # The rows are already read; a refused write must not fail the page.
            logger.warning(
                "Could not cache %s calls of run %s",
                len(rows),
                execution.id,
                exc_info=True,
            )
    return rows, True


def _average(values: list[Any]) -> float | None:
    present = [value for value in values if value is not None]
    return sum(present) / len(present) if present else None


def _total(values: list[Any]) -> float | None:
    present = [value for value in values if value is not None]
    return sum(present) if present else None


def _aggregate_values(
    rows: list[dict[str, Any]], columns: list[dict[str, str]]
) -> dict[str, Any]:
    outcomes = [row["result_outcome"] for row in rows]
    values: dict[str, Any] = {
        "total": len(rows),
        "measured": sum(outcome in EVALUATED_OUTCOMES for outcome in outcomes),
        "tokens_total_value": _total([row["result_tokens"] for row in rows]),
        "cost_cents_total_value": _total([row["result_cost_cents"] for row in rows]),
        "csat_average": _average([row["result_csat"] for row in rows]),
        "turns_average": _average([row["result_turn_count"] for row in rows]),
        "stop_latency_average": _average(
            [row["avg_stop_time_after_interruption_ms"] for row in rows]
        ),
        "ai_interruptions_average": _average(
            [row["ai_interruption_count"] for row in rows]
        ),
    }
    for outcome in OUTCOMES:
        values[f"outcome_{outcome}"] = outcomes.count(outcome)
    for name, field in METRIC_FIELDS.items():
        measured = [row[field] for row in rows if row[field] is not None]
        values[f"{name}_average"] = _average(measured)
        values[f"{name}_measured"] = len(measured)
    for column in columns:
        eval_id = str(column["id"])
        scored = [
            row["scores"][eval_id]
            for row in rows
            if row["scores"].get(eval_id) is not None
        ]
        values[f"eval_{eval_id}_scored"] = len(scored)
        values[f"eval_{eval_id}_sum"] = sum(scored)
    return values


def summarize_call_values(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return _summary_from_values(_aggregate_values(rows, []))


def run_outcome_summaries(
    executions: list[TestExecution],
) -> dict[Any, dict[str, Any]]:
    """Each run's graded summary, counted as the v3 calls page counts it.

    A completed run's summary is cached under the page's own invalidation inputs.
    """
    completed = [
        execution
        for execution in executions
        if execution.status == TestExecution.ExecutionStatus.COMPLETED
    ]
    keys: dict[Any, str | None] = {}
    if completed:
        configs, calls = _run_key_inputs(completed)
        keys = {
            execution.id: _cache_key(
                execution,
                {},
                [],
                configs=configs.get(execution.run_test_id, []),
                calls=calls.get(execution.id, 0),
                shape="run-summary:v1",
            )
            for execution in completed
        }
    cache_keys = [key for key in keys.values() if key]
    cached = cache.get_many(cache_keys) if cache_keys else {}
    summaries = {
        execution_id: cached[key]
        for execution_id, key in keys.items()
        if key is not None and key in cached
    }
    missed: dict[Any, list[TestExecution]] = {}
    for execution in executions:
        if execution.id not in summaries:
            missed.setdefault(execution.run_test_id, []).append(execution)
    if missed:
        rows = (
            run_calls_queryset(
                next(iter(missed.values()))[0],
                [execution.id for group in missed.values() for execution in group],
                live_evals_by_run_test=live_eval_specs_by_run_test(missed),
            )
            .order_by()
            .values("test_execution_id")
            .annotate(**_aggregate_expressions(include_percentiles=False))
        )
        for row in rows:
            summaries[row["test_execution_id"]] = _summary_from_values(row)
    empty = _summary_from_values({})
    fresh = {
        key: summaries.get(execution.id, empty)
        for group in missed.values()
        for execution in group
        if (key := keys.get(execution.id))
    }
    if fresh:
        try:
            cache.set_many(fresh, timeout=CACHE_TIMEOUT)
        except Exception:
            # The summaries are already read; a refused write must not fail the list.
            logger.warning(
                "Could not cache the summaries of %s runs", len(fresh), exc_info=True
            )
    return {
        execution.id: summaries.get(execution.id, empty) for execution in executions
    }


def _in_group(group_by: str, value: Any, key: str) -> bool:
    if group_by not in LIST_AXES:
        return str(value) == key
    if key == UNGROUPED:
        return value == []
    return isinstance(value, list) and any(
        isinstance(item, str) and item == key for item in value
    )


def group_call_values(
    rows: list[dict[str, Any]],
    group_by: str | None,
    page_ids: list[str],
    columns: list[dict[str, str]],
) -> list[dict[str, Any]]:
    if group_by not in GROUP_FIELDS or not page_ids:
        return []
    field = GROUP_FIELDS[group_by]
    by_id = {row["id"]: row for row in rows}
    ids_by_key: dict[str, list[str]] = {}
    for call_id in page_ids:
        if call_id not in by_id:
            continue
        for key in _group_keys(group_by, by_id[call_id][field]):
            ids_by_key.setdefault(key, []).append(call_id)
    groups = [
        group_entry(rows, group_by, key, result_ids, columns)
        for key, result_ids in ids_by_key.items()
    ]
    return sorted(groups, key=lambda group: (-group["total"], group["label"].lower()))


def group_label(group_by: str, key: str) -> str:
    if group_by in {"sub_goal", "attack", "task"} and key != UNGROUPED:
        return level_label(key)
    return OUTCOME_LABELS.get(key, key)


def group_entry(
    rows: list[dict[str, Any]],
    group_by: str,
    key: str,
    result_ids: list[str],
    columns: list[dict[str, str]],
    *,
    members: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """One group of the rows: the summary and aggregates of its members.

    ``members`` are the group's rows when the caller already picked them.
    """
    if members is None:
        field = GROUP_FIELDS[group_by]
        members = [row for row in rows if _in_group(group_by, row[field], key)]
    values = _aggregate_values(members, columns)
    summary = _summary_from_values(values)
    return {
        "key": key,
        "label": group_label(group_by, key),
        "result_ids": result_ids,
        "aggregates": {
            "csat": values["csat_average"],
            "turns": values["turns_average"],
            "latency_ms": summary["latency"]["average"],
            "avg_stop_time_after_interruption": values["stop_latency_average"],
            "ai_interruptions": values["ai_interruptions_average"],
            "tokens": summary["tokens"]["total_value"],
            "evaluations": {
                str(column["id"]): {
                    "scored": values[f"eval_{column['id']}_scored"],
                    "score_sum": values[f"eval_{column['id']}_sum"],
                }
                for column in columns
            },
        },
        **summary,
    }


def _scan_ordering(query: dict[str, Any]) -> str | None:
    """The scan's ordering when ``apply_run_call_query`` would only order the run."""
    filters = query.get("filters") or {}
    if str(query.get("search") or "").strip() or any(filters.values()):
        return None
    if query.get("group_by") in GROUP_FIELDS and query.get("group_key") is not None:
        return None
    ordering = str(query.get("ordering") or "-started_at")
    # ``apply_run_call_query`` orders any other field by start time.
    if ordering.lstrip("-") in SQL_ORDERED_FIELDS:
        return None
    return "-started_at" if ordering.startswith("-") else "started_at"


def _subset(query: dict[str, Any]) -> dict[str, Any]:
    if not _has_subset(query) and _scan_ordering(query) == "-started_at":
        # The whole run, newest first: the same entry as ``execution_rows``.
        return {}
    subset = {name: query.get(name) for name in SUBSET_PARAMS}
    if subset["group_key"] is None:
        # A grouping with no key filters nothing, so every grouping reads the same rows.
        subset["group_by"] = None
    return subset


def _has_subset(query: dict[str, Any]) -> bool:
    return bool(
        query.get("search")
        or query.get("filters")
        or query.get("group_key") is not None
    )


def run_calls_page(
    execution: TestExecution,
    query: dict[str, Any],
    columns: list[dict[str, str]],
    base_queryset: Callable[[], QuerySet],
    live_evals: LiveEvals | None = None,
) -> dict[str, Any]:
    scores = functools.cache(lambda: _score_expressions(execution, columns, live_evals))
    ordering = _scan_ordering(query)
    rows, rows_fresh = _cached_call_values(
        execution,
        _subset(query),
        columns,
        lambda: (
            scan_call_values(execution, columns, ordering, live_evals=live_evals)
            if ordering
            else _call_values(
                apply_run_call_query(base_queryset(), query), columns, scores()
            )
        ),
    )
    has_subset = _has_subset(query)
    execution_rows, execution_rows_fresh = (
        _cached_call_values(
            execution,
            {},
            columns,
            lambda: scan_call_values(
                execution, columns, "-started_at", live_evals=live_evals
            ),
        )
        if has_subset
        else (rows, rows_fresh)
    )
    filters = query.get("filters") or {}
    if not any(filters.get(key) for key in STATUS_FILTERS):
        status_rows = rows
    else:
        # The status chips count every outcome under the other filters.
        status_query = {
            **query,
            "filters": {
                key: value
                for key, value in filters.items()
                if key not in STATUS_FILTERS
            },
        }
        status_rows = (
            _cached_call_values(
                execution,
                _subset(status_query),
                columns,
                lambda: _call_values(
                    apply_run_call_query(base_queryset(), status_query),
                    columns,
                    scores(),
                ),
            )[0]
            if _has_subset(status_query)
            else execution_rows
        )
    start = (query["page"] - 1) * query["page_size"]
    return {
        "rows": rows,
        "execution_rows": execution_rows,
        # Read in this request rather than served from the page cache.
        "execution_rows_fresh": execution_rows_fresh,
        "page_ids": [row["id"] for row in rows[start : start + query["page_size"]]],
        "summary": summarize_call_values(rows),
        "execution_summary": summarize_call_values(execution_rows),
        "status_counts": summarize_call_values(status_rows)["outcomes"],
        "count": len(rows),
    }


class JsonbKeySubset(Func):
    """A JSON object column cut down to ``keys``; other JSON values pass through."""

    output_field = JSONField()

    def __init__(self, expression: Any, keys: tuple[str, ...]) -> None:
        super().__init__(expression)
        self.keys = list(keys)

    def as_sql(
        self, compiler: Any, connection: Any, **extra_context: Any
    ) -> tuple[str, list[Any]]:
        sql, params = compiler.compile(self.source_expressions[0])
        return (
            f"CASE WHEN jsonb_typeof({sql}) = 'object' THEN COALESCE("
            f"(SELECT jsonb_object_agg(e.key, e.value) FROM jsonb_each({sql}) AS e"
            f" WHERE e.key = ANY(%s)), '{{}}'::jsonb) ELSE {sql} END",
            [*params, *params, self.keys, *params],
        )


def load_row_calls(
    call_ids: list[str], executions: dict[Any, TestExecution]
) -> dict[str, CallExecution]:
    """The calls by id, with only what ``build_call_rows`` reads, in one query."""
    # A run's calls are large (prompts in call_metadata, the run's own metadata
    # per joined row), so a page reads only what build_call_rows uses.
    queryset = (
        CallExecution.objects.filter(id__in=call_ids)
        # Callers order by their own ids.
        .order_by()
        .select_related("scenario")
        .only(
            *CALL_ROW_FIELDS,
            *(f"scenario__{field}" for field in CALL_ROW_SCENARIO_FIELDS),
        )
        # call_metadata itself stays deferred; build_call_rows reads this subset.
        .annotate(row_metadata=JsonbKeySubset("call_metadata", CALL_ROW_METADATA_KEYS))
    )
    by_id = {}
    for call in queryset:
        if (execution := executions.get(call.test_execution_id)) is not None:
            call.test_execution = execution
        by_id[str(call.id)] = call
    return by_id


def page_calls(page: dict[str, Any], execution: TestExecution) -> list[CallExecution]:
    by_id = load_row_calls(page["page_ids"], {execution.id: execution})
    goals = {row["id"]: row["result_goal"] for row in page["rows"]}
    calls = []
    for call_id in page["page_ids"]:
        if (call := by_id.get(call_id)) is None:
            continue
        call.result_goal = goals[call_id]
        calls.append(call)
    return calls


def page_groups(
    execution: TestExecution,
    page: dict[str, Any],
    query: dict[str, Any],
    columns: list[dict[str, str]],
    base_queryset: Callable[[], QuerySet],
    live_evals: LiveEvals | None = None,
) -> list[dict[str, Any]]:
    group_by = query.get("group_by")
    if group_by not in GROUP_FIELDS or not page["page_ids"]:
        return []
    rows = page["rows"]
    # The page rows may add harness columns the catalog had not seen yet; the
    # cached rows predate them too, so re-read the subset whole.
    if any(str(column["id"]) not in rows[0]["scores"] for column in columns):
        rows = _call_values(
            apply_run_call_query(base_queryset(), query),
            columns,
            _score_expressions(execution, columns, live_evals),
        )
    return group_call_values(rows, group_by, page["page_ids"], columns)
