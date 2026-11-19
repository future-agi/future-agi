"""A run's pass is one server value: the runs list, the run's header and its analytics read the same trial counts."""

from __future__ import annotations

import pytest

from simulate.models import RunTest, Scenarios, TestExecution
from simulate.models.test_execution import CallExecution

TRIAL_KEYS = (
    "trials_passed",
    "trials_evaluated",
    "trials_inconclusive",
    "trials_excluded_not_comparable",
    "trial_pass_rate",
)


@pytest.fixture
def run(organization, workspace):
    scenario = Scenarios.objects.create(
        name="Refund", source="test", organization=organization, workspace=workspace
    )
    run_test = RunTest.objects.create(
        name="Trial pass run", organization=organization, workspace=workspace
    )
    run_test.scenarios.add(scenario)
    execution = TestExecution.objects.create(
        run_test=run_test, status=TestExecution.ExecutionStatus.COMPLETED
    )
    return execution, scenario


def _trials(run, outcomes):
    execution, scenario = run
    for outcome in outcomes:
        CallExecution.objects.create(
            test_execution=execution,
            scenario=scenario,
            phone_number="+1230001111",
            status="completed",
            call_metadata={"harness_outcome_status": outcome},
        )
    return execution


def _surfaces(auth_client, execution):
    listed = auth_client.get(f"/simulate/run-tests/{execution.run_test_id}/executions/")
    analytics = auth_client.get(
        f"/simulate/v3/test-executions/{execution.id}/analytics/"
    )
    assert listed.status_code == 200, listed.content
    assert analytics.status_code == 200, analytics.content
    [row] = [one for one in listed.json()["results"] if one["id"] == str(execution.id)]
    return row, analytics.json()


@pytest.mark.django_db
def test_a_run_with_an_inconclusive_trial_reads_the_same_on_the_list_the_header_and_analytics(
    auth_client, run
):
    execution = _trials(run, ["passed", "passed", "passed", "failed", "inconclusive"])

    row, analytics = _surfaces(auth_client, execution)

    served = {key: row[key] for key in TRIAL_KEYS}
    assert served == {key: analytics["summary"][key] for key in TRIAL_KEYS}
    header = analytics["reliability"]
    assert (
        header["trials_passed"],
        header["trials_evaluated"],
        header["inconclusive"],
    ) == (
        served["trials_passed"],
        served["trials_evaluated"],
        served["trials_inconclusive"],
    )
    assert (
        served["trials_passed"],
        served["trials_evaluated"],
        served["trials_inconclusive"],
    ) == (3, 4, 1)
    tiles = {metric["key"]: metric for metric in analytics["dashboard"]["metrics"]}
    assert (tiles["trial_pass"]["label"], tiles["trial_pass"]["value"]) == (
        "Trials passed",
        served["trial_pass_rate"],
    )
    assert tiles["pass_rate"]["label"] == "Calls passed"


@pytest.mark.django_db
def test_a_run_with_no_inconclusive_trial_keeps_the_pass_rate_it_always_had(
    auth_client, run
):
    execution = _trials(run, ["passed", "passed", "passed", "failed"])

    row, analytics = _surfaces(auth_client, execution)

    # The analytics pass rate as it always read; the list's own pass_rate keeps its meaning, untouched.
    assert (
        row["trial_pass_rate"]
        == analytics["summary"]["trial_pass_rate"]
        == analytics["summary"]["pass_rate"]
        == 75
    )


def _queries_for(executions):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    from simulate.services.run_reliability_v3 import trial_passes

    with CaptureQueriesContext(connection) as captured:
        passes = trial_passes(executions)
    return len(captured.captured_queries), passes


@pytest.mark.django_db
def test_listing_more_runs_reads_their_trial_pass_in_the_same_number_of_queries(run):
    first = _trials(run, ["passed", "failed"])
    others = [
        TestExecution.objects.create(
            run_test=first.run_test, status=TestExecution.ExecutionStatus.COMPLETED
        )
        for _ in range(3)
    ]
    for other in others:
        _trials((other, run[1]), ["passed", "passed", "inconclusive"])

    one, _ = _queries_for([first])
    four, passes = _queries_for([first, *others])

    assert four == one
    assert passes[first.id]["trial_pass_rate"] == 50
    assert [passes[other.id]["trials_inconclusive"] for other in others] == [1, 1, 1]
