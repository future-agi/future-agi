"""Number filters on test-execution calls must partition null and populated rows."""

import json

import pytest

from simulate.models import CallExecution, RunTest, Scenarios, TestExecution
from simulate.serializers.test_execution import ExecutionDetailQuerySerializer
from simulate.utils.test_execution_utils import TestExecutionUtils

pytestmark = pytest.mark.django_db

NUMBER_COLUMNS = [
    ("overallScore", "overall_score"),
    ("overall_score", "overall_score"),
    ("duration_seconds", "duration_seconds"),
    ("duration", "duration_seconds"),
    ("avg_agent_latency_ms", "avg_agent_latency_ms"),
    ("latency", "avg_agent_latency_ms"),
    ("latency_ms", "avg_agent_latency_ms"),
    ("responseTime", "response_time_ms"),
    ("response_time", "response_time_ms"),
]
COST_COLUMNS = ["cost_cents", "customer_cost_cents", "cost"]


@pytest.fixture
def make_calls(organization, workspace):
    run_test = RunTest.objects.create(
        name="Number filter regression", organization=organization, workspace=workspace
    )
    execution = TestExecution.objects.create(
        run_test=run_test, status=TestExecution.ExecutionStatus.COMPLETED
    )
    scenario = Scenarios.objects.create(
        name="Number filter scenario",
        source="test",
        scenario_type=Scenarios.ScenarioTypes.DATASET,
        organization=organization,
        workspace=workspace,
    )

    def create(rows):
        calls = CallExecution.objects.bulk_create(
            [
                CallExecution(
                    test_execution=execution,
                    scenario=scenario,
                    status=CallExecution.CallStatus.COMPLETED,
                    **values,
                )
                for values in rows
            ]
        )
        return calls, CallExecution.objects.filter(test_execution=execution)

    return create


def _filter_ids(queryset, column_id, op, **value):
    # Use the endpoint's query contract, including omitted filter_value for null ops.
    serializer = ExecutionDetailQuerySerializer(
        data={
            "filters": json.dumps(
                [
                    {
                        "column_id": column_id,
                        "filter_config": {
                            "filter_type": "number",
                            "filter_op": op,
                            **value,
                        },
                    }
                ]
            )
        }
    )
    assert serializer.is_valid(), serializer.errors
    errors = []
    result = TestExecutionUtils()._apply_filters(
        queryset, serializer.validated_data["filters"], errors, {}
    )
    assert errors == []
    return set(result.values_list("id", flat=True))


@pytest.mark.parametrize("column_id,field", NUMBER_COLUMNS)
@pytest.mark.parametrize("value", [{}, {"filter_value": None}], ids=["omitted", "null"])
def test_number_null_filters_partition_rows(make_calls, column_id, field, value):
    calls, queryset = make_calls([{field: None}, {field: 0}, {field: 25}])

    null_ids = _filter_ids(queryset, column_id, "is_null", **value)
    non_null_ids = _filter_ids(queryset, column_id, "is_not_null", **value)

    assert null_ids == {calls[0].id}
    assert non_null_ids == {calls[1].id, calls[2].id}
    assert null_ids.isdisjoint(non_null_ids)
    assert null_ids | non_null_ids == {call.id for call in calls}


@pytest.mark.parametrize("column_id", [c[0] for c in NUMBER_COLUMNS] + COST_COLUMNS)
def test_all_null_number_column_returns_ten_or_zero_rows(make_calls, column_id):
    calls, queryset = make_calls([{} for _ in range(10)])

    assert _filter_ids(queryset, column_id, "is_not_null") == set()
    assert _filter_ids(queryset, column_id, "is_null") == {call.id for call in calls}


@pytest.mark.parametrize("column_id", COST_COLUMNS)
@pytest.mark.parametrize("value", [{}, {"filter_value": None}], ids=["omitted", "null"])
def test_cost_is_null_only_when_both_fields_are_null(make_calls, column_id, value):
    calls, queryset = make_calls(
        [
            {"customer_cost_cents": None, "cost_cents": None},
            {"customer_cost_cents": 0, "cost_cents": None},
            {"customer_cost_cents": None, "cost_cents": 0},
            {"customer_cost_cents": 25, "cost_cents": None},
            {"customer_cost_cents": None, "cost_cents": 25},
            {"customer_cost_cents": 10, "cost_cents": 25},
        ]
    )

    null_ids = _filter_ids(queryset, column_id, "is_null", **value)
    non_null_ids = _filter_ids(queryset, column_id, "is_not_null", **value)

    assert null_ids == {calls[0].id}
    assert non_null_ids == {call.id for call in calls[1:]}
    assert null_ids.isdisjoint(non_null_ids)
    assert null_ids | non_null_ids == {call.id for call in calls}


@pytest.mark.parametrize(
    "column_id,field",
    [
        ("overall_score", "overall_score"),
        ("duration", "duration_seconds"),
        ("latency", "avg_agent_latency_ms"),
        ("cost", "customer_cost_cents"),
        ("cost", "cost_cents"),
    ],
)
@pytest.mark.parametrize(
    "op,value,expected",
    [
        ("equals", "10", [2]),
        ("not_equals", "10", [0, 1, 3]),
        ("greater_than", "10", [3]),
        ("less_than", "10", [1]),
        ("greater_than_or_equal", "10", [2, 3]),
        ("less_than_or_equal", "10", [1, 2]),
        ("between", ["0", "10"], [1, 2]),
        ("not_between", ["0", "10"], [0, 3]),
    ],
)
def test_existing_number_comparisons_are_unchanged(
    make_calls, column_id, field, op, value, expected
):
    calls, queryset = make_calls(
        [{field: number} for number in (None, 0, 10, 20)]
    )

    assert _filter_ids(queryset, column_id, op, filter_value=value) == {
        calls[index].id for index in expected
    }


@pytest.mark.parametrize(
    "column_id,field",
    [
        ("overall_score", "overall_score"),
        ("duration", "duration_seconds"),
        ("latency", "avg_agent_latency_ms"),
        ("cost", "customer_cost_cents"),
        ("cost", "cost_cents"),
    ],
)
@pytest.mark.parametrize(
    "op,expected",
    [("in", [1, 2]), ("not_in", [0, 3])],
)
def test_internal_number_membership_comparisons_are_unchanged(
    make_calls, column_id, field, op, expected
):
    calls, queryset = make_calls(
        [{field: number} for number in (None, 0, 10, 20)]
    )

    # Numeric membership is internal compatibility, not a public query operator.
    filters = [
        {
            "column_id": column_id,
            "filter_config": {
                "filter_type": "number",
                "filter_op": op,
                "filter_value": ["0", "10"],
            },
        }
    ]
    errors = []
    result = TestExecutionUtils()._apply_filters(queryset, filters, errors, {})

    assert errors == []
    assert set(result.values_list("id", flat=True)) == {
        calls[index].id for index in expected
    }


@pytest.mark.parametrize("column_id", ["overall_score", "duration", "latency", "cost"])
@pytest.mark.parametrize("op", ["in", "not_in"])
def test_public_number_filters_reject_internal_membership_operators(column_id, op):
    serializer = ExecutionDetailQuerySerializer(
        data={
            "filters": json.dumps(
                [
                    {
                        "column_id": column_id,
                        "filter_config": {
                            "filter_type": "number",
                            "filter_op": op,
                            "filter_value": ["0", "10"],
                        },
                    }
                ]
            )
        }
    )

    assert not serializer.is_valid()
    assert "filters" in serializer.errors
    assert f"Unsupported filter_op {op!r} for filter_type 'number'." in str(
        serializer.errors["filters"]
    )


@pytest.mark.parametrize("column_id", ["responseTime", "response_time"])
@pytest.mark.parametrize(
    "op,expected",
    [
        ("equals", [2]),
        ("greater_than", [3]),
        ("less_than", [1]),
        ("greater_than_or_equal", [2, 3]),
        ("less_than_or_equal", [1, 2]),
    ],
)
def test_response_time_comparisons_still_convert_seconds_to_ms(
    make_calls, column_id, op, expected
):
    calls, queryset = make_calls(
        [{"response_time_ms": number} for number in (None, 0, 1500, 2000)]
    )

    assert _filter_ids(queryset, column_id, op, filter_value="1.5") == {
        calls[index].id for index in expected
    }


def test_null_filter_preserves_existing_queryset_scope(make_calls):
    calls, queryset = make_calls([{}, {}, {"avg_agent_latency_ms": 0}])
    scoped = queryset.filter(id__in=[calls[0].id, calls[2].id])

    assert _filter_ids(scoped, "latency", "is_null") == {calls[0].id}
    assert _filter_ids(scoped, "latency", "is_not_null") == {calls[2].id}
