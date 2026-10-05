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
from collections.abc import Callable
from typing import Any

from django.core.cache import cache
from django.db.models import QuerySet

from simulate.models import CallExecution, SimulateEvalConfig, TestExecution
from simulate.services.harness_scenarios import level_label
from simulate.services.run_results_v3 import OUTCOME_LABELS
from simulate.services.run_results_v3_queries import (
    EVALUATED_OUTCOMES,
    GROUP_FIELDS,
    LIST_AXES,
    OUTCOMES,
    UNGROUPED,
    _configured_eval_verdict,
    _eval_score,
    _group_keys,
    _summary_from_values,
    apply_run_call_query,
)

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
)
METRIC_FIELDS = {
    "duration": "duration_seconds",
    "latency": "result_latency_ms",
    "tokens": "result_tokens",
    "cost_cents": "result_cost_cents",
}
SUBSET_PARAMS = ("search", "filters", "group_by", "group_key", "ordering")
# CSAT and other per-call metrics can still land after the run completes
# without moving anything in the cache key, so a cached pass stays short-lived.
CACHE_TIMEOUT = 5 * 60


def _score_expressions(
    execution: TestExecution, columns: list[dict[str, str]]
) -> dict[str, Any]:
    configs = (
        {
            str(config.id): config
            for config in SimulateEvalConfig.objects.filter(
                run_test=execution.run_test, deleted=False
            ).select_related("eval_template")
        }
        if columns
        else {}
    )
    expressions = {}
    for index, column in enumerate(columns):
        eval_id = str(column["id"])
        config = configs.get(eval_id)
        expressions[f"score_{index}"] = (
            _configured_eval_verdict(eval_id, config)[0]
            if config is not None
            else _eval_score(eval_id)
        )
    return expressions


def _call_values(
    queryset: QuerySet, columns: list[dict[str, str]], scores: dict[str, Any]
) -> list[dict[str, Any]]:
    rows = []
    for values in queryset.annotate(**scores).values(*CALL_VALUE_FIELDS, *scores):
        row = {field: values[field] for field in CALL_VALUE_FIELDS}
        row["id"] = str(row["id"])
        row["scores"] = {
            str(column["id"]): values[f"score_{index}"]
            for index, column in enumerate(columns)
        }
        rows.append(row)
    return rows


def _cache_key(
    execution: TestExecution,
    subset: dict[str, Any],
    columns: list[dict[str, str]],
) -> str | None:
    if execution.status != TestExecution.ExecutionStatus.COMPLETED:
        return None
    version = execution.completed_at or execution.updated_at
    # The live configs by content, not by their latest updated_at: a removal
    # drops one from the set, a template edit moves its own stamp, and a
    # save(update_fields=[...]) that skips updated_at still changes the config.
    configs = list(
        SimulateEvalConfig.objects.filter(run_test=execution.run_test, deleted=False)
        .order_by("id")
        .values_list("id", "updated_at", "config", "eval_template__updated_at")
    )
    fingerprint = hashlib.sha1(
        json.dumps(
            {
                "subset": subset,
                "columns": [str(column["id"]) for column in columns],
                "configs": configs,
            },
            sort_keys=True,
            default=str,
        ).encode()
    ).hexdigest()
    return f"simulate:v3:calls:{execution.id}:{version.timestamp()}:{fingerprint}"


def _cached_call_values(
    execution: TestExecution,
    subset: dict[str, Any],
    columns: list[dict[str, str]],
    read_rows: Callable[[], list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    key = _cache_key(execution, subset, columns)
    if key and (cached := cache.get(key)) is not None:
        return cached
    rows = read_rows()
    if key:
        cache.set(key, rows, timeout=CACHE_TIMEOUT)
    return rows


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
    labelled_axis = group_by in {"sub_goal", "attack", "task"}
    groups = []
    for key, result_ids in ids_by_key.items():
        members = [row for row in rows if _in_group(group_by, row[field], key)]
        values = _aggregate_values(members, columns)
        summary = _summary_from_values(values)
        groups.append(
            {
                "key": key,
                "label": (
                    level_label(key)
                    if labelled_axis and key != UNGROUPED
                    else OUTCOME_LABELS.get(key, key)
                ),
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
        )
    return sorted(groups, key=lambda group: (-group["total"], group["label"].lower()))


def run_calls_page(
    execution: TestExecution,
    query: dict[str, Any],
    columns: list[dict[str, str]],
    base_queryset: Callable[[], QuerySet],
) -> dict[str, Any]:
    scores = functools.cache(lambda: _score_expressions(execution, columns))
    subset = {name: query.get(name) for name in SUBSET_PARAMS}
    rows = _cached_call_values(
        execution,
        subset,
        columns,
        lambda: _call_values(
            apply_run_call_query(base_queryset(), query), columns, scores()
        ),
    )
    has_subset = bool(
        query.get("search")
        or query.get("filters")
        or query.get("group_key") is not None
    )
    execution_rows = (
        _cached_call_values(
            execution,
            {},
            columns,
            lambda: _call_values(base_queryset(), columns, scores()),
        )
        if has_subset
        else rows
    )
    start = (query["page"] - 1) * query["page_size"]
    return {
        "rows": rows,
        "page_ids": [row["id"] for row in rows[start : start + query["page_size"]]],
        "summary": summarize_call_values(rows),
        "execution_summary": summarize_call_values(execution_rows),
        "count": len(rows),
    }


def page_calls(page: dict[str, Any]) -> list[CallExecution]:
    by_id = {
        str(call.id): call
        for call in CallExecution.objects.filter(
            id__in=page["page_ids"]
        ).select_related("scenario", "test_execution__agent_definition")
    }
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
) -> list[dict[str, Any]]:
    rows = page["rows"]
    if rows and any(str(column["id"]) not in rows[0]["scores"] for column in columns):
        rows = _call_values(
            apply_run_call_query(base_queryset(), query),
            columns,
            _score_expressions(execution, columns),
        )
    return group_call_values(rows, query.get("group_by"), page["page_ids"], columns)
