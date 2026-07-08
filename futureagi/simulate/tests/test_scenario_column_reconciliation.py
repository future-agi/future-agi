"""Regression tests for name-based scenario dataset columns.

Covers the fix that keys scenario dataset columns by canonical name across
datasets: the reconciliation service, the cross-dataset filter path, the
grouping path, the legacy `scenario_<id>_dataset_<uuid>` fallback, and the
SQL-identifier sanitisation on the grouping alias.
"""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from model_hub.models.choices import DatasetSourceChoices, SourceChoices
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from simulate.models import CallExecution, RunTest, Scenarios, TestExecution
from simulate.utils.test_execution_utils import (
    TestExecutionUtils,
    reconcile_scenario_column_order,
)

pytestmark = pytest.mark.django_db


def _make_dataset_with_column(organization, workspace, user, name, column_name="outcome"):
    dataset = Dataset.no_workspace_objects.create(
        name=f"ds-{name}",
        organization=organization,
        workspace=workspace,
        user=user,
        source=DatasetSourceChoices.SCENARIO.value,
    )
    column = Column.objects.create(
        dataset=dataset,
        name=column_name,
        data_type="text",
        source=SourceChoices.OTHERS.value,
    )
    dataset.column_order = [str(column.id)]
    dataset.save()
    return dataset, column


def _make_scenario(organization, workspace, user, name, column_name="outcome"):
    dataset, column = _make_dataset_with_column(
        organization, workspace, user, name, column_name
    )
    scenario = Scenarios.objects.create(
        name=name,
        source="src",
        scenario_type=Scenarios.ScenarioTypes.DATASET,
        organization=organization,
        workspace=workspace,
        dataset=dataset,
    )
    return scenario, dataset, column


def _make_row_with_cell(dataset, column, value, order=0):
    row = Row.objects.create(dataset=dataset, order=order)
    Cell.objects.create(dataset=dataset, column=column, row=row, value=value)
    return row


@pytest.fixture
def run_test(organization, workspace):
    return RunTest.objects.create(
        name="rt", organization=organization, workspace=workspace
    )


@pytest.fixture
def test_execution(run_test):
    return TestExecution.objects.create(
        run_test=run_test,
        status=TestExecution.ExecutionStatus.COMPLETED,
    )


class TestReconcileScenarioColumnOrder:
    def test_collapses_outcome_across_datasets(
        self, organization, workspace, user
    ):
        s1, _, c1 = _make_scenario(organization, workspace, user, "scenario-a")
        s2, _, c2 = _make_scenario(organization, workspace, user, "scenario-b")

        column_order, changed = reconcile_scenario_column_order(
            scenarios=[s1, s2],
            call_executions=CallExecution.objects.none(),
            column_order=[],
        )

        scenario_cols = [
            c for c in column_order if c.get("type") == "scenario_dataset_column"
        ]
        assert len(scenario_cols) == 1, column_order
        entry = scenario_cols[0]
        assert entry["column_name"] == "Ideal Outcome"
        assert entry["id"] == "Ideal Outcome"
        assert set(entry["dataset_column_ids"]) == {str(c1.id), str(c2.id)}
        assert changed is True

    def test_preserves_existing_visibility(self, organization, workspace, user):
        s1, _, _ = _make_scenario(organization, workspace, user, "scenario-a")
        seed = [
            {
                "type": "scenario_dataset_column",
                "id": "Ideal Outcome",
                "column_name": "Ideal Outcome",
                "visible": False,
                "dataset_column_ids": ["stale"],
            }
        ]

        column_order, _ = reconcile_scenario_column_order(
            scenarios=[s1],
            call_executions=CallExecution.objects.none(),
            column_order=seed,
        )

        entry = next(
            c for c in column_order if c.get("type") == "scenario_dataset_column"
        )
        assert entry["visible"] is False

    def test_preserves_scenario_block_position(
        self, organization, workspace, user
    ):
        s1, _, _ = _make_scenario(organization, workspace, user, "scenario-a")
        seed = [
            {"type": "metadata", "id": "call_details", "column_name": "Call"},
            {
                "type": "scenario_dataset_column",
                "id": "Ideal Outcome",
                "column_name": "Ideal Outcome",
                "visible": True,
                "dataset_column_ids": ["stale"],
            },
            {"type": "evaluation", "id": "eval-1", "column_name": "Eval"},
        ]

        column_order, _ = reconcile_scenario_column_order(
            scenarios=[s1],
            call_executions=CallExecution.objects.none(),
            column_order=seed,
        )

        types = [c["type"] for c in column_order]
        # The scenario block stays between the metadata and evaluation columns.
        assert types == ["metadata", "scenario_dataset_column", "evaluation"]


class TestScenarioDatasetColumnFilter:
    def test_matches_across_datasets(
        self, organization, workspace, user, test_execution
    ):
        s1, ds1, c1 = _make_scenario(organization, workspace, user, "scenario-a")
        s2, ds2, c2 = _make_scenario(organization, workspace, user, "scenario-b")
        r1 = _make_row_with_cell(ds1, c1, "win")
        r2 = _make_row_with_cell(ds2, c2, "win")
        ce1 = CallExecution.objects.create(
            test_execution=test_execution, scenario=s1, row_id=r1.id
        )
        ce2 = CallExecution.objects.create(
            test_execution=test_execution, scenario=s2, row_id=r2.id
        )

        column_order = [
            {
                "type": "scenario_dataset_column",
                "id": "Ideal Outcome",
                "column_name": "Ideal Outcome",
                "dataset_column_ids": [str(c1.id), str(c2.id)],
            }
        ]
        filters = [
            {
                "column_id": "Ideal Outcome",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "win",
                },
            }
        ]

        with CaptureQueriesContext(connection) as ctx:
            result = list(
                TestExecutionUtils()._apply_filters(
                    CallExecution.objects.filter(test_execution=test_execution),
                    filters,
                    [],
                    {},
                    column_order=column_order,
                )
            )
        assert len(ctx.captured_queries) == 1, ctx.captured_queries

        ids = {str(ce.id) for ce in result}
        assert ids == {str(ce1.id), str(ce2.id)}

    def test_legacy_scenario_dataset_column_id_still_filters(
        self, organization, workspace, user, test_execution
    ):
        s1, ds1, c1 = _make_scenario(organization, workspace, user, "scenario-a")
        r1 = _make_row_with_cell(ds1, c1, "win")
        ce1 = CallExecution.objects.create(
            test_execution=test_execution, scenario=s1, row_id=r1.id
        )

        legacy_id = f"scenario_{s1.id}_dataset_{c1.id}"
        filters = [
            {
                "column_id": legacy_id,
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "win",
                },
            }
        ]

        result = TestExecutionUtils()._apply_filters(
            CallExecution.objects.filter(test_execution=test_execution),
            filters,
            [],
            {},
            column_order=[],
        )

        assert {str(ce.id) for ce in result} == {str(ce1.id)}


class TestScenarioDatasetGrouping:
    def test_groups_across_datasets(
        self, organization, workspace, user, test_execution
    ):
        s1, ds1, c1 = _make_scenario(organization, workspace, user, "scenario-a")
        s2, ds2, c2 = _make_scenario(organization, workspace, user, "scenario-b")
        r1 = _make_row_with_cell(ds1, c1, "win")
        r2 = _make_row_with_cell(ds2, c2, "win")
        CallExecution.objects.create(
            test_execution=test_execution, scenario=s1, row_id=r1.id
        )
        CallExecution.objects.create(
            test_execution=test_execution, scenario=s2, row_id=r2.id
        )

        default_columns = [
            {
                "type": "scenario_dataset_column",
                "id": "Ideal Outcome",
                "column_name": "Ideal Outcome",
                "dataset_column_ids": [str(c1.id), str(c2.id)],
            }
        ]

        results = TestExecutionUtils()._apply_scenario_dataset_grouping(
            CallExecution.objects.filter(test_execution=test_execution),
            row_groups=[],
            group_keys=[],
            default_columns=default_columns,
        )

        # Both calls carry "win" for their own dataset's column, so they
        # collapse into a single group across the two datasets.
        assert len(results) == 1, results
        assert results[0]["Ideal_Outcome"] == "win"
        assert results[0]["count"] == 2

    def test_grouping_alias_rejects_sql_injection(
        self, organization, workspace, user, test_execution
    ):
        s1, ds1, c1 = _make_scenario(organization, workspace, user, "scenario-a")
        r1 = _make_row_with_cell(ds1, c1, "win")
        ce1 = CallExecution.objects.create(
            test_execution=test_execution, scenario=s1, row_id=r1.id
        )

        # A column name that would break out of a quoted SQL identifier if the
        # alias were not allowlisted to [A-Za-z0-9_].
        hostile_name = 'foo"; DROP TABLE simulate_call_execution; --'
        default_columns = [
            {
                "type": "scenario_dataset_column",
                "id": hostile_name,
                "column_name": hostile_name,
                "dataset_column_ids": [str(c1.id)],
            }
        ]

        # Must run without a SQL error and without dropping the table.
        results = TestExecutionUtils()._apply_scenario_dataset_grouping(
            CallExecution.objects.filter(test_execution=test_execution),
            row_groups=[],
            group_keys=[],
            default_columns=default_columns,
        )

        assert isinstance(results, list)
        assert CallExecution.objects.filter(id=ce1.id).exists()
