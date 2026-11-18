"""A run whose calls ran with no pass or fail reads as No score: the goal breakdown's headline and an unscored call's
reason say so from the server."""

from __future__ import annotations

import pytest

from simulate.models import RunTest, Scenarios, TestExecution
from simulate.models.test_execution import CallExecution
from simulate.services.run_dashboard_v3 import _breakdown
from simulate.services.run_results_v3 import _unscored_reason


@pytest.fixture
def scenario(organization, workspace):
    return Scenarios.objects.create(
        name="No-score scenario",
        source="test",
        organization=organization,
        workspace=workspace,
    )


@pytest.fixture
def test_execution(organization, workspace, scenario):
    run_test = RunTest.objects.create(
        name="No-score run", organization=organization, workspace=workspace
    )
    run_test.scenarios.add(scenario)
    return TestExecution.objects.create(
        run_test=run_test, status=TestExecution.ExecutionStatus.COMPLETED
    )


class _Counts:
    """The grouped-count chain `_breakdown` reads from a queryset, over fixed rows."""

    def __init__(self, counts: dict[str, int]):
        self.rows = [
            {"dashboard_goal": label, "count": count} for label, count in counts.items()
        ]

    def order_by(self, *fields):
        return self

    def values(self, *fields):
        return self

    def annotate(self, **expressions):
        return self

    def __iter__(self):
        return iter(sorted(self.rows, key=lambda row: -row["count"]))


def _headline(counts: dict[str, int]) -> str:
    chart = _breakdown(
        _Counts(counts),
        "dashboard_goal",
        "goal_outcome",
        "Goal outcome breakdown",
        sum(counts.values()),
    )
    return chart["headline"]["label"]


@pytest.mark.parametrize(
    ("counts", "headline"),
    [
        # Only unscored calls: no score, not "Passed 0%".
        ({"no_score": 3}, "no_score"),
        # Escalated and inconclusive calls do not outrank it.
        ({"no_score": 2, "escalated": 1, "inconclusive": 1}, "no_score"),
        # Anything passed, failed, errored or still to run keeps passed.
        ({"no_score": 2, "passed": 1}, "passed"),
        ({"no_score": 2, "failed": 1}, "passed"),
        ({"no_score": 2, "error": 1}, "passed"),
        ({"no_score": 2, "queued": 1}, "passed"),
        ({"no_score": 2, "in_progress": 1}, "passed"),
        ({"failed": 2}, "passed"),
    ],
)
def test_the_goal_headline(counts, headline):
    assert _headline(counts) == headline


def test_another_breakdown_keeps_its_first_segment():
    chart = _breakdown(
        _Counts({"no_score": 3}),
        "dashboard_goal",
        "disconnection",
        "How calls ended",
        3,
    )

    assert chart["headline"]["label"] == "no_score"
    assert chart["headline"] is chart["segments"][0]


def test_an_unscored_call_says_what_it_has_instead():
    results = [
        {"name": "a", "result": "not_applicable"},
        {"name": "b", "result": "not_applicable"},
        {"name": "c", "result": "inconclusive"},
        {"name": "d", "result": "unmeasured"},
        {"name": "e", "passed": True},
    ]

    assert (
        _unscored_reason("no_score", results)
        == "2 not applicable, 1 inconclusive, 1 not measured"
    )


def test_only_an_unscored_call_gets_that_reason():
    results = [{"name": "a", "result": "not_applicable"}]

    assert _unscored_reason("passed", results) is None
    assert _unscored_reason("no_score", []) is None


def _pass_rate_metric(auth_client, test_execution):
    response = auth_client.get(
        f"/simulate/v3/test-executions/{test_execution.id}/analytics/"
    )
    assert response.status_code == 200, response.content
    return next(
        item
        for item in response.json()["dashboard"]["metrics"]
        if item["key"] == "pass_rate"
    )


def _call(test_execution, scenario, outcome):
    CallExecution.objects.create(
        test_execution=test_execution,
        scenario=scenario,
        phone_number="+1230001111",
        status="completed",
        call_metadata={"harness_outcome_status": outcome},
    )


@pytest.mark.django_db
def test_the_pass_rate_of_only_unscored_calls_is_no_score(
    auth_client, test_execution, scenario
):
    _call(test_execution, scenario, "no_score")
    _call(test_execution, scenario, "no_score")

    metric = _pass_rate_metric(auth_client, test_execution)

    assert (metric["value"], metric["no_score"]) == (None, True)


@pytest.mark.django_db
def test_a_scored_pass_rate_is_not_no_score(auth_client, test_execution, scenario):
    _call(test_execution, scenario, "no_score")
    _call(test_execution, scenario, "passed")

    metric = _pass_rate_metric(auth_client, test_execution)

    assert metric["value"] == 100
    assert metric["no_score"] is False


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("outcomes", "verdict"),
    [
        (("no_score", "no_score"), "no_score"),
        (("no_score", "passed"), "passed"),
        (("no_score", "failed"), "failed"),
    ],
)
def test_the_analytics_summary_carries_the_run_verdict(
    auth_client, test_execution, scenario, outcomes, verdict
):
    for outcome in outcomes:
        _call(test_execution, scenario, outcome)

    response = auth_client.get(
        f"/simulate/v3/test-executions/{test_execution.id}/analytics/"
    )

    assert response.status_code == 200, response.content
    assert response.json()["summary"]["outcome_status"] == verdict
