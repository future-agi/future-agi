import pytest

from simulate.models import CallExecution, RunTest, Scenarios
from simulate.models import TestExecution as SimulationTestExecution
from simulate.utils.test_execution_utils import TestExecutionUtils

pytestmark = pytest.mark.django_db


@pytest.fixture
def test_execution(organization, workspace):
    run_test = RunTest.objects.create(
        name="Number filter test", organization=organization, workspace=workspace
    )
    return SimulationTestExecution.objects.create(run_test=run_test)


@pytest.fixture
def scenario(organization, workspace):
    return Scenarios.objects.create(
        name="Number filter scenario",
        source="Test source",
        scenario_type=Scenarios.ScenarioTypes.GRAPH,
        organization=organization,
        workspace=workspace,
    )


@pytest.fixture
def calls(test_execution, scenario):
    values = {
        "all-null": {},
        "customer-cost": {
            "avg_agent_latency_ms": 10,
            "customer_cost_cents": 100,
            "response_time_ms": 1000,
        },
        "legacy-cost": {"cost_cents": 200},
        "both-costs": {
            "avg_agent_latency_ms": 20,
            "customer_cost_cents": 300,
            "cost_cents": 400,
            "response_time_ms": 2000,
        },
    }
    return {
        name: CallExecution.objects.create(
            test_execution=test_execution,
            scenario=scenario,
            call_metadata={"test_name": name},
            **field_values,
        )
        for name, field_values in values.items()
    }


def _filter_calls(queryset, column_id, operator, value=None, *, include_value=False):
    filter_config = {"filter_type": "number", "filter_op": operator}
    if include_value:
        filter_config["filter_value"] = value
    errors = []
    result = TestExecutionUtils()._apply_filters(
        queryset,
        [{"column_id": column_id, "filter_config": filter_config}],
        errors,
        {},
    )
    assert errors == []
    return result


@pytest.mark.parametrize(
    ("column_id", "null_names", "not_null_names"),
    [
        (
            "avg_agent_latency_ms",
            {"all-null", "legacy-cost"},
            {"customer-cost", "both-costs"},
        ),
        (
            "cost",
            {"all-null"},
            {"customer-cost", "legacy-cost", "both-costs"},
        ),
        (
            "responseTime",
            {"all-null", "legacy-cost"},
            {"customer-cost", "both-costs"},
        ),
    ],
)
def test_number_null_operators_are_complementary(
    calls, column_id, null_names, not_null_names
):
    queryset = CallExecution.objects.filter(id__in=[call.id for call in calls.values()])

    null_ids = set(
        _filter_calls(queryset, column_id, "is_null").values_list("id", flat=True)
    )
    not_null_ids = set(
        _filter_calls(queryset, column_id, "is_not_null").values_list("id", flat=True)
    )

    assert null_ids == {calls[name].id for name in null_names}
    assert not_null_ids == {calls[name].id for name in not_null_names}
    assert null_ids.isdisjoint(not_null_ids)
    assert null_ids | not_null_ids == {call.id for call in calls.values()}


def test_number_null_operators_when_every_row_is_null(test_execution, scenario):
    CallExecution.objects.bulk_create(
        [
            CallExecution(test_execution=test_execution, scenario=scenario)
            for _ in range(10)
        ]
    )
    queryset = CallExecution.objects.filter(test_execution=test_execution)

    assert _filter_calls(queryset, "avg_agent_latency_ms", "is_null").count() == 10
    assert _filter_calls(queryset, "avg_agent_latency_ms", "is_not_null").count() == 0


@pytest.mark.parametrize(
    ("operator", "value", "expected_names"),
    [
        ("equals", 10, {"customer-cost"}),
        ("not_equals", 10, {"both-costs"}),
        ("in", [10, 20], {"customer-cost", "both-costs"}),
        ("not_in", [10], {"both-costs"}),
        ("greater_than", 10, {"both-costs"}),
        ("less_than", 20, {"customer-cost"}),
        ("greater_than_or_equal", 10, {"customer-cost", "both-costs"}),
        ("less_than_or_equal", 20, {"customer-cost", "both-costs"}),
        ("between", [10, 19], {"customer-cost"}),
        ("not_between", [10, 19], {"both-costs"}),
    ],
)
def test_existing_number_operators_are_unchanged(
    calls, operator, value, expected_names
):
    queryset = CallExecution.objects.filter(
        id__in=[call.id for call in calls.values()],
        avg_agent_latency_ms__isnull=False,
    )

    result_ids = set(
        _filter_calls(
            queryset,
            "avg_agent_latency_ms",
            operator,
            value,
            include_value=True,
        ).values_list("id", flat=True)
    )

    assert result_ids == {calls[name].id for name in expected_names}
