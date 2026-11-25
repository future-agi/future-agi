import pytest
from django.http import QueryDict

from simulate.models import HostedHarnessScenario
from simulate.services.harness_scenarios import (
    ScenarioRows,
    apply_filters,
    index_scenarios,
)
from simulate.services.hosted_harness import create_hosted_job
from simulate.tests.test_hosted_harness_channels import _payload

_DOCS = [
    {
        "scenario_key": "cancel-order",
        "name": "Cancel_order_late",
        "instruction": "Cancel the order placed yesterday.",
        "use_case": "orders",
    },
    {
        "scenario_key": "track-order",
        "name": "track_order",
        "instruction": "Ask where the parcel is.",
        "use_case": "orders",
    },
    {
        "scenario_key": "refund",
        "name": "refund_after_cancel",
        "instruction": "Ask for money back.",
        "use_case": "refunds",
    },
]


@pytest.fixture
def suite(organization, workspace):
    job, _ = create_hosted_job(
        organization,
        _payload(scenario_count=len(_DOCS)),
        idempotency_key="filter-operators",
        workspace=workspace,
    )
    index_scenarios(job, _DOCS)
    return HostedHarnessScenario.no_workspace_objects.filter(job=job)


def _keys(found):
    return sorted(one.scenario_key for one in found)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("name__contains=c", ["cancel-order", "refund", "track-order"]),
        ("name__contains=CANCEL", ["cancel-order", "refund"]),
        (
            "name__contains=cancel&name__contains=track",
            ["cancel-order", "refund", "track-order"],
        ),
        ("name__not_contains=cancel", ["track-order"]),
        ("name__starts_with=cancel", ["cancel-order"]),
        ("name=track_order", ["track-order"]),
        ("name=track", []),
        ("name_not=track_order", ["cancel-order", "refund"]),
        ("instruction__contains=parcel", ["track-order"]),
        ("instruction__not_contains=order", ["refund", "track-order"]),
        ("instruction__starts_with=ask", ["refund", "track-order"]),
        ("use_case__contains=ord", ["cancel-order", "refund", "track-order"]),
        ("name__bogus=cancel", ["cancel-order", "refund", "track-order"]),
    ],
)
def test_each_text_operator_filters_the_suite_the_same_way_in_both_paths(
    suite, query, expected
):
    params = QueryDict(query)
    from_rows = _keys(apply_filters(ScenarioRows(suite), params))
    from_queryset = _keys(apply_filters(suite, params))

    assert from_rows == from_queryset
    assert from_queryset == expected
