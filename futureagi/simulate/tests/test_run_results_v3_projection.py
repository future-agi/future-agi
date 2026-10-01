"""Outcome projection preserves ORM consumers without repeating verdict SQL."""

import json
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.db import connection
from django.db.models import Count, OuterRef, Q, Subquery

from simulate.models import CallExecution
from simulate.services.run_results_v3_expressions import project_annotation
from simulate.services.run_results_v3_queries import (
    OUTCOMES,
    _aggregate_expressions,
    apply_run_call_query,
    run_call_rows_queryset,
    run_calls_queryset,
)


def _queryset(config_count=1, *, scalar_only=False):
    configs = [
        SimpleNamespace(
            id=f"eval-{index}",
            config={"pass_threshold": 0.7, "reverse_output": bool(index % 2)},
            eval_template=SimpleNamespace(
                output_type_normalized="percentage",
                choice_scores=None,
                config={"output": "score"},
                pass_threshold=0.5,
            ),
        )
        for index in range(config_count)
    ]
    execution_id = uuid.UUID(int=1)
    with patch(
        "simulate.services.run_results_v3_queries.SimulateEvalConfig.objects.filter"
    ) as filtered:
        filtered.return_value.select_related.return_value = configs
        queryset = run_calls_queryset(
            SimpleNamespace(run_test=None), [execution_id]
        ).order_by()
    if not scalar_only:
        return queryset
    # Keep the real outcome/metric expressions, without unrelated relation joins.
    outcome = queryset.query.annotations["result_outcome"]
    projection = queryset.query.alias_map[outcome.alias]
    fields = {
        key: value
        for key, value in queryset.query.annotations.items()
        if key
        in {
            "result_latency_ms",
            "result_tokens",
            "result_cost_cents",
            "result_csat",
            "result_turn_count",
            "result_eval_outcome",
        }
    }
    return project_annotation(
        CallExecution.objects.filter(test_execution_id=execution_id)
        .annotate(result_outcome=projection.expression, **fields)
        .order_by(),
        "result_outcome",
    )


def _unproject(queryset):
    baseline = queryset.all()
    query = baseline.query
    alias = query.annotations["result_outcome"].alias
    projection = query.alias_map.pop(alias)
    query.alias_refcount.pop(alias)
    query.table_map[projection.table_name].remove(alias)
    query.annotations["result_outcome"] = projection.expression
    return baseline


def test_many_evaluations_project_once_for_filtered_group_aggregates():
    queryset = _queryset(20)
    baseline = _unproject(queryset)
    counters = {
        outcome: Count("id", filter=Q(result_outcome=outcome)) for outcome in OUTCOMES
    }

    def grouped(query):
        return (
            query.filter(result_outcome__in=OUTCOMES)
            .values("result_outcome")
            .annotate(**counters)
        )

    sql, params = grouped(queryset).query.sql_with_params()
    baseline_sql, baseline_params = grouped(baseline).query.sql_with_params()

    assert sql.count("CROSS JOIN LATERAL") == 1
    assert sql.count("OFFSET 0") == 1
    assert len(sql) < len(baseline_sql) / 3
    assert len(params) < len(baseline_params) / 3
    cloned_sql, cloned_params = grouped(queryset).all().query.sql_with_params()
    assert cloned_sql == sql
    assert repr(cloned_params) == repr(params)


def test_projection_relabels_correlated_subquery_sources():
    queryset = _queryset()
    nested = CallExecution.objects.annotate(
        verdict=Subquery(
            queryset.filter(pk=OuterRef("pk")).values("result_outcome")[:1]
        )
    ).values("verdict")

    sql, _ = nested.query.sql_with_params()
    parent_alias = nested.query.annotations["verdict"].query.base_table

    assert sql.count("CROSS JOIN LATERAL") == 1
    assert f'{parent_alias}."eval_outputs"' in sql
    assert f'"{CallExecution._meta.db_table}"."eval_outputs"' not in sql
    assert f'{parent_alias}."id"' in sql


def test_call_row_query_masks_only_unused_verdicts_and_keeps_status_filters():
    queryset = _queryset()
    filtered = apply_run_call_query(queryset, {"filters": {"status": ["failed"]}})
    rows = run_call_rows_queryset(filtered)

    assert "result_outcome" in filtered.query.annotation_select
    assert "result_eval_outcome" in filtered.query.annotation_select
    assert set(rows.query.annotation_select) == set(
        filtered.query.annotation_select
    ) - {"result_outcome", "result_eval_outcome"}
    assert rows.query.where == filtered.query.where
    assert rows.query.order_by == filtered.query.order_by
    sql, _ = rows.query.sql_with_params()
    assert sql.count("CROSS JOIN LATERAL") == 1
    assert '"simulation_call_verdict"."value" IN' in sql


def _execute_on_rows(queryset, rows, *, explain=False):
    sql, params = queryset.query.sql_with_params()
    table = connection.ops.quote_name(CallExecution._meta.db_table)
    marker = f"FROM {table} "
    before, after = sql.split(marker, 1)
    recordset = (
        "jsonb_to_recordset(%s::jsonb) AS "
        f"{table}(id uuid, test_execution_id uuid, deleted boolean, "
        "status text, call_metadata jsonb, eval_outputs jsonb, "
        "duration_seconds double precision, avg_agent_latency_ms double precision, "
        "conversation_metrics_data jsonb, customer_cost_cents double precision) "
    )
    insert_at = before.count("%s")
    prefix = "EXPLAIN (ANALYZE, FORMAT JSON) " if explain else ""
    with connection.cursor() as cursor:
        cursor.execute(
            f"{prefix}{before}FROM {recordset}{after}",
            [*params[:insert_at], json.dumps(rows), *params[insert_at:]],
        )
        return cursor.fetchall()


@pytest.mark.django_db
def test_projected_rows_filters_pagination_and_summary_match_original():
    queryset = _queryset(2, scalar_only=True)
    baseline = _unproject(queryset)
    examples = [
        ("completed", {}, {"eval-0": 0.9, "eval-1": 0.4}),
        ("completed", {}, {"eval-0": 0.4, "eval-1": 0.4}),
        ("completed", {}, {"eval-0": 0.9, "eval-1": 0.9}),
        ("completed", {}, {}),
        ("failed", {"harness_outcome_status": "passed"}, {}),
        ("completed", {"harness_outcome_status": "failed"}, {"eval-0": 0.9}),
        ("in_progress", {}, {"eval-0": 0.9}),
    ]
    rows = [
        {
            "id": str(uuid.UUID(int=index + 2)),
            "test_execution_id": str(uuid.UUID(int=1)),
            "deleted": False,
            "status": status,
            "call_metadata": metadata,
            "eval_outputs": {
                key: {"status": "completed", "output": value}
                for key, value in outputs.items()
            },
            "duration_seconds": 10 + index,
            "avg_agent_latency_ms": 100 + index,
            "conversation_metrics_data": {
                "total_tokens": 10,
                "turn_count": 2,
                "csat_score": 8,
            },
            "customer_cost_cents": 2,
        }
        for index, (status, metadata, outputs) in enumerate(examples)
    ]
    operations = [
        lambda query: query.order_by("id").values("id", "result_outcome"),
        lambda query: apply_run_call_query(
            query, {"filters": {"status": ["failed"]}, "ordering": "outcome"}
        ).values("id", "result_outcome"),
        lambda query: query.order_by("id").values("id", "result_outcome")[1:4],
        lambda query: (
            query.values("result_outcome")
            .annotate(**_aggregate_expressions())
            .order_by("result_outcome")
        ),
        lambda query: query.values("test_execution_id").annotate(
            **_aggregate_expressions()
        ),
        lambda query: query.values("test_execution_id").annotate(count=Count("id")),
        lambda query: run_call_rows_queryset(
            apply_run_call_query(
                query, {"filters": {"status": ["failed"]}, "ordering": "outcome"}
            )
        ).values("id"),
    ]

    for operation in operations:
        assert _execute_on_rows(operation(queryset), rows) == _execute_on_rows(
            operation(baseline), rows
        )

    result = _execute_on_rows(operations[0](queryset), rows)
    assert [outcome for _, outcome in result] == [
        "passed",
        "failed",
        "failed",
        "inconclusive",
        "error",
        "failed",
        "inconclusive",
    ]

    plan = _execute_on_rows(operations[0](queryset), rows, explain=True)[0][0][0][
        "Plan"
    ]

    def nodes(node):
        yield node
        for child in node.get("Plans", []):
            yield from nodes(child)

    projections = [
        node
        for node in nodes(plan)
        if node["Node Type"] == "Result" and node.get("Parent Relationship") == "Inner"
    ]
    assert len(projections) == 1
    assert 0 < projections[0]["Actual Loops"] <= len(rows)
    masked = run_call_rows_queryset(queryset).order_by("duration_seconds", "id")
    masked_plan = _execute_on_rows(masked.values("id")[:1], rows, explain=True)[0][0][
        0
    ]["Plan"]
    assert not any(
        node.get("Function Name") == "jsonb_each" for node in nodes(masked_plan)
    )
