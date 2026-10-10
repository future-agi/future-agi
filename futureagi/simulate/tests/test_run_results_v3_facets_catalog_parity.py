"""Facets, the evaluation catalog and page groups match their full-scan forms."""

from __future__ import annotations

from collections import Counter
from datetime import timedelta
from typing import Any

import pytest
from django.core.cache import cache
from django.db import connection
from django.db.models import Count, QuerySet
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from model_hub.models.evals_metric import EvalTemplate
from simulate.models import CallExecution, Scenarios, SimulateEvalConfig, TestExecution
from simulate.models.run_test import RunTest
from simulate.services import run_results_v3 as catalog_module
from simulate.services.run_results_v3 import (
    build_evaluation_catalog,
    receipt_sub_goal_names,
)
from simulate.services.run_results_v3_page import (
    _call_values,
    _score_expressions,
    group_call_values,
    page_groups,
    run_calls_page,
)
from simulate.services.run_results_v3_queries import (
    _result_sub_goals_expression,
    apply_run_call_query,
    run_call_facets,
    run_calls_queryset,
)


def _reference_facets(queryset: QuerySet) -> dict[str, list[dict[str, Any]]]:
    """The facets as the database computed them before the scan rows were reused."""
    facets = {}
    for name, field in (("goal", "result_goal"), ("status", "result_outcome")):
        facets[name] = [
            {"value": row[field], "count": row["count"]}
            for row in queryset.values(field)
            .annotate(count=Count("id"))
            .order_by("-count", field)
        ]
    sub_goals = Counter()
    for values in (
        queryset.order_by()
        .annotate(result_sub_goals=_result_sub_goals_expression())
        .values_list("result_sub_goals", flat=True)
    ):
        for value in values if isinstance(values, list) else []:
            name = value.get("name") if isinstance(value, dict) else value
            if name:
                sub_goals[str(name)] += 1
    facets["sub_goal"] = [
        {"value": value, "count": count}
        for value, count in sorted(
            sub_goals.items(), key=lambda item: (-item[1], item[0].lower())
        )
    ]
    return facets


def _reference_harness_columns(execution: TestExecution) -> list[dict[str, str]]:
    """The harness columns as the per-call Python scan found them."""
    seen = set()
    columns = []
    outputs = (
        CallExecution.objects.filter(test_execution=execution)
        .order_by("created_at", "id")
        .values_list("eval_outputs", "call_metadata")
    )
    for eval_outputs, metadata in outputs:
        if not isinstance(eval_outputs, dict):
            continue
        sub_goal_names = receipt_sub_goal_names(metadata)
        for eval_id, data in eval_outputs.items():
            eval_id = str(eval_id)
            if (
                eval_id in seen
                or not isinstance(data, dict)
                or data.get("source") != "harness"
            ):
                continue
            name = str(data.get("name") or eval_id)
            columns.append(
                {
                    "id": eval_id,
                    "name": name,
                    "kind": "sub_goal" if name in sub_goal_names else "evaluation",
                }
            )
            seen.add(eval_id)
    return columns


@pytest.fixture
def execution(organization, workspace) -> TestExecution:
    run = RunTest.objects.create(
        name="Parity run", organization=organization, workspace=workspace
    )
    return TestExecution.objects.create(
        run_test=run, status=TestExecution.ExecutionStatus.RUNNING
    )


@pytest.fixture
def scenario_factory(organization, workspace):
    def create(name: str) -> Scenarios:
        return Scenarios.objects.create(
            name=name,
            source="test",
            scenario_type=Scenarios.ScenarioTypes.SCRIPT,
            organization=organization,
            workspace=workspace,
        )

    return create


def _call(execution: TestExecution, **fields: Any) -> CallExecution:
    if "scenario" not in fields:
        run = execution.run_test
        fields["scenario"] = Scenarios.objects.create(
            name="Default goal",
            source="test",
            scenario_type=Scenarios.ScenarioTypes.SCRIPT,
            organization=run.organization,
            workspace=run.workspace,
        )
    return CallExecution.objects.create(test_execution=execution, **fields)


@pytest.fixture
def tied_calls(execution, scenario_factory) -> list[CallExecution]:
    """Equal counts across goals and statuses whose order depends on collation."""
    calls = []
    sub_goal_sets = [
        {"hosted_harness_receipt": {"sub_goals": [{"name": "Greets"}, "pin"]}},
        {"sub_goals": ["pin", {"name": "verify"}]},
        {"sub_goals": []},
        {},
    ]
    goals = ["apple", "Banana", "in_progress", "inconclusive", "a b", "ab", "Ab"]
    statuses = ["ongoing", "completed", "pending", "failed", "completed", "ongoing"]
    for index, goal in enumerate(goals):
        scenario = scenario_factory(goal)
        for repeat in range(2):
            calls.append(
                _call(
                    execution,
                    scenario=scenario,
                    status=statuses[(index + repeat) % len(statuses)],
                    call_metadata=sub_goal_sets[(index + repeat) % len(sub_goal_sets)],
                )
            )
    calls.append(_call(execution, status="completed"))
    return calls


def _facets_from_rows(
    execution: TestExecution, call_ids: list[str] | None = None
) -> dict[str, list[dict[str, Any]]]:
    rows = _call_values(run_calls_queryset(execution), [], {})
    calls = CallExecution.objects.filter(test_execution=execution)
    if call_ids:
        scoped = {call_id.lower() for call_id in call_ids}
        rows = [row for row in rows if row["id"] in scoped]
        calls = calls.filter(id__in=call_ids)
    return run_call_facets(lambda: rows, lambda: calls)


@pytest.mark.django_db
class TestFacetsFromRows:
    def test_whole_run_matches_database_facets(self, execution, tied_calls):
        facets = _facets_from_rows(execution)

        assert facets == _reference_facets(run_calls_queryset(execution))
        assert len({facet["count"] for facet in facets["goal"]}) < len(facets["goal"])

    def test_hand_off_scopes_counts_like_the_database(self, execution, tied_calls):
        call_ids = [str(call.id).upper() for call in tied_calls[::3]]

        facets = _facets_from_rows(execution, call_ids)

        expected = _reference_facets(
            apply_run_call_query(
                run_calls_queryset(execution),
                {"filters": {"call_execution_id": call_ids}},
            )
        )
        assert facets == expected
        assert sum(facet["count"] for facet in facets["status"]) == len(call_ids)

    def test_empty_run(self, execution):
        assert _facets_from_rows(execution) == {
            "goal": [],
            "status": [],
            "sub_goal": [],
        }

    def test_missing_goal_sorts_after_a_tied_goal(self, execution):
        # Real calls always fall back to a scenario name, so the rows are built here.
        rows = [
            {"result_goal": None, "result_outcome": "passed"},
            {"result_goal": "A", "result_outcome": "passed"},
        ]

        facets = run_call_facets(
            lambda: rows,
            lambda: CallExecution.objects.filter(test_execution=execution),
        )

        # ``ORDER BY -count, value`` puts NULL last.
        assert facets["goal"] == [
            {"value": "A", "count": 1},
            {"value": None, "count": 1},
        ]

    def test_no_ties_skips_the_collation_query(self, execution, scenario_factory):
        _call(execution, scenario=scenario_factory("solo"), status="completed")
        rows = _call_values(run_calls_queryset(execution), [], {})

        with CaptureQueriesContext(connection) as queries:
            run_call_facets(
                lambda: rows,
                lambda: CallExecution.objects.filter(test_execution=execution),
            )

        assert not any("unnest(" in query["sql"] for query in queries)

    def test_cached_facets_are_served_without_queries(self, execution):
        key = f"test:facets:{execution.id}"
        cache.set(key, {"goal": [], "status": [], "sub_goal": []})
        try:
            with CaptureQueriesContext(connection) as queries:
                facets = run_call_facets(
                    lambda: [], lambda: CallExecution.objects.all(), key
                )
        finally:
            cache.delete(key)

        assert facets == {"goal": [], "status": [], "sub_goal": []}
        assert len(queries) == 0


@pytest.mark.django_db
class TestHarnessColumns:
    def test_first_seen_order_names_and_kinds_match_the_scan(self, execution):
        now = timezone.now()
        receipt = {"hosted_harness_receipt": {"sub_goals": [{"name": "Greets"}]}}
        harness = {"source": "harness", "status": "completed", "output": True}
        first = _call(
            execution,
            status="completed",
            call_metadata=receipt,
            eval_outputs={
                "zz-long-key": {**harness, "name": "Greets"},
                "b": {**harness, "name": 7},
                "a": {**harness, "name": ""},
                "configured": {"status": "completed", "output": True},
                "not-object": "harness",
            },
        )
        tied = [
            _call(
                execution,
                status="completed",
                eval_outputs={"shared": {**harness, "name": "Greets"}, key: harness},
            )
            for key in ("t1", "t2")
        ]
        later = _call(
            execution,
            status="completed",
            call_metadata=receipt,
            eval_outputs={
                "b": {**harness, "name": "Renamed"},
                "c": {**harness, "name": False},
                "d": {**harness, "source": "Harness"},
            },
        )
        for outputs in (None, [], "text", {"e": {**harness, "name": {"x": 1}}}):
            odd = _call(execution, status="completed")
            CallExecution.objects.filter(pk=odd.pk).update(eval_outputs=outputs)
        CallExecution.objects.filter(pk=first.pk).update(
            created_at=now - timedelta(minutes=5)
        )
        CallExecution.objects.filter(pk__in=[call.pk for call in tied]).update(
            created_at=now - timedelta(minutes=3)
        )
        CallExecution.objects.filter(pk=later.pk).update(
            created_at=now - timedelta(minutes=1)
        )

        columns = catalog_module._harness_columns(execution)

        assert columns == _reference_harness_columns(execution)
        assert [column["id"] for column in columns[:3]] == ["a", "b", "zz-long-key"]
        assert columns[2]["kind"] == "sub_goal"
        assert {column["id"] for column in columns} >= {"shared", "t1", "t2", "c", "e"}

    def test_catalog_keeps_configured_columns_first(
        self, execution, organization, workspace
    ):
        template = EvalTemplate.objects.create(
            name="Configured template", organization=organization
        )
        config = SimulateEvalConfig.objects.create(
            name="Configured", run_test=execution.run_test, eval_template=template
        )
        _call(
            execution,
            status="completed",
            eval_outputs={
                str(config.id): {"source": "harness", "name": "Dup"},
                "native": {"source": "harness", "name": "Native"},
            },
        )

        columns, live_eval_ids = build_evaluation_catalog(execution)

        assert columns == [
            {"id": str(config.id), "name": "Configured", "kind": "evaluation"},
            {"id": "native", "name": "Native", "kind": "evaluation"},
        ]
        assert live_eval_ids == {str(config.id)}

    def test_empty_run_has_no_harness_columns(self, execution):
        assert catalog_module._harness_columns(execution) == []


def _reference_page_groups(execution, page, query, columns, base_queryset):
    rows = page["rows"]
    if rows and any(str(column["id"]) not in rows[0]["scores"] for column in columns):
        rows = _call_values(
            apply_run_call_query(base_queryset(), query),
            columns,
            _score_expressions(execution, columns),
        )
    return group_call_values(rows, query.get("group_by"), page["page_ids"], columns)


@pytest.mark.django_db
class TestPageGroups:
    @pytest.fixture
    def scored_calls(self, execution, scenario_factory) -> list[CallExecution]:
        calls = []
        for index, (goal, score) in enumerate(
            [("one", 80), ("one", 40), ("two", 10), ("two", None)]
        ):
            outputs = {"known": {"source": "harness", "output": {"score": 50}}}
            if score is not None:
                outputs["late"] = {"source": "harness", "output": {"score": score}}
            calls.append(
                _call(
                    execution,
                    scenario=(
                        scenario_factory(f"{goal}-{index}")
                        if index == 3
                        else scenario_factory(goal)
                    ),
                    status="completed",
                    eval_outputs=outputs,
                )
            )
        return calls

    @pytest.mark.parametrize("group_by", ["", None, "unknown"])
    def test_no_grouping_reads_nothing(self, execution, scored_calls, group_by):
        queryset = run_calls_queryset(execution)
        query = {"page": 1, "page_size": 2, "group_by": group_by}
        known = [{"id": "known", "name": "Known"}]
        page = run_calls_page(execution, query, known, lambda: queryset)

        with CaptureQueriesContext(connection) as queries:
            groups = page_groups(
                execution, page, query, [*known, {"id": "late"}], lambda: queryset
            )

        assert groups == []
        assert len(queries) == 0

    def test_empty_page_reads_nothing(self, execution, scored_calls):
        queryset = run_calls_queryset(execution)
        query = {"page": 9, "page_size": 2, "group_by": "goal"}
        page = run_calls_page(execution, query, [], lambda: queryset)

        with CaptureQueriesContext(connection) as queries:
            groups = page_groups(
                execution, page, query, [{"id": "x"}], lambda: queryset
            )

        assert groups == []
        assert len(queries) == 0

    @pytest.mark.parametrize(
        "group_by", ["goal", "sub_goal", "accent", "age", "attack", "task", "status"]
    )
    @pytest.mark.parametrize("group_key", [None, "one"])
    def test_missing_columns_match_a_full_rescan(
        self, execution, scored_calls, group_by, group_key
    ):
        queryset = run_calls_queryset(execution)
        query = {
            "page": 1,
            "page_size": 3,
            "group_by": group_by,
            "group_key": group_key if group_by == "goal" else None,
        }
        known = [{"id": "known", "name": "Known"}]
        columns = [*known, {"id": "late", "name": "Late"}]
        page = run_calls_page(execution, query, known, lambda: queryset)
        snapshot = [dict(row, scores=dict(row["scores"])) for row in page["rows"]]

        groups = page_groups(execution, page, query, columns, lambda: queryset)

        assert groups == _reference_page_groups(
            execution, page, query, columns, lambda: queryset
        )
        assert groups and all("late" in g["aggregates"]["evaluations"] for g in groups)
        assert page["rows"] == snapshot


@pytest.mark.django_db
def test_missing_column_rescan_reads_rows_newer_than_the_cache(
    execution, scenario_factory
):
    TestExecution.objects.filter(pk=execution.pk).update(
        status=TestExecution.ExecutionStatus.COMPLETED, completed_at=timezone.now()
    )
    execution.refresh_from_db()
    call = _call(execution, scenario=scenario_factory("goal"), status="completed")
    queryset = run_calls_queryset(execution)
    query = {"page": 1, "page_size": 5, "group_by": "status"}
    cache.clear()
    try:
        cached = run_calls_page(execution, query, [], lambda: queryset)
        CallExecution.objects.filter(pk=call.pk).update(
            eval_outputs={
                "native-check": {
                    "source": "harness",
                    "output": "Passed",
                    "status": "completed",
                }
            }
        )
        page = run_calls_page(execution, query, [], lambda: queryset)
        columns = [{"id": "native-check", "name": "Native check"}]

        groups = page_groups(execution, page, query, columns, lambda: queryset)
    finally:
        cache.clear()

    assert page["rows"] == cached["rows"]
    assert groups == _reference_page_groups(
        execution, page, query, columns, lambda: queryset
    )
    scored = {g["key"]: g["aggregates"]["evaluations"]["native-check"] for g in groups}
    assert scored["passed"] == {"scored": 1, "score_sum": 1.0}
