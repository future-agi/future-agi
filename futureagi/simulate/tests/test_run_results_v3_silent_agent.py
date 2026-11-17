"""A call the agent never answered shows no pass on any surface fed by the receipt.

The sealed receipt still says passed for such a call; the readers must not.
"""

import pytest
from django.utils import timezone

from simulate.models import CallExecution, RunTest, Scenarios, TestExecution
from simulate.services.run_results_v3 import build_call_rows, call_outcome
from simulate.services.run_results_v3_queries import (
    build_run_comparison,
    run_calls_queryset,
)
from simulate.utils.processing_outcomes import (
    HARNESS_VERDICT_WITHHELD_KEY,
    SILENT_AGENT_SKIP_REASON,
    set_processing_skip_metadata,
)

KEY = "scenario-one"


@pytest.fixture
def run(organization, workspace):
    run_test = RunTest.objects.create(
        name="Silent run", organization=organization, workspace=workspace
    )
    execution = TestExecution.objects.create(
        run_test=run_test, status=TestExecution.ExecutionStatus.COMPLETED
    )
    scenario = Scenarios.objects.create(
        name="Silent scenario",
        source="test",
        scenario_type=Scenarios.ScenarioTypes.SCRIPT,
        organization=organization,
        workspace=workspace,
    )
    return execution, scenario


def _sealed_pass(*, skipped=False, reason=SILENT_AGENT_SKIP_REASON):
    metadata = {
        "harness_scenario_key": KEY,
        "harness_outcome_status": "passed",
        "hosted_harness_receipt": {
            "scenario_key": KEY,
            "status": "passed",
            "sub_goals": [{"name": "booked", "held": True}],
        },
    }
    if skipped:
        metadata = set_processing_skip_metadata(metadata, skipped=True, reason=reason)
        metadata[HARNESS_VERDICT_WITHHELD_KEY] = reason == SILENT_AGENT_SKIP_REASON
    return metadata


def _call(execution, scenario, metadata):
    return CallExecution.objects.create(
        test_execution=execution,
        scenario=scenario,
        status="completed",
        started_at=timezone.now(),
        call_metadata=metadata,
        eval_outputs={},
    )


@pytest.mark.django_db
class TestCallRows:
    def row(self, execution, call):
        rows, _ = build_call_rows(execution, [call], [], set())
        return rows[0]

    def test_silent_call_shows_no_pass_for_its_sub_goals_or_harness_status(self, run):
        execution, scenario = run
        call = _call(execution, scenario, _sealed_pass(skipped=True))

        row = self.row(execution, call)

        assert row["outcome"] == "inconclusive"
        assert row["harness_outcome_status"] is None
        assert row["sub_goal_results"] == [{"name": "booked", "passed": None}]

    def test_answered_call_keeps_its_receipt_verdict(self, run):
        execution, scenario = run
        call = _call(execution, scenario, _sealed_pass())

        row = self.row(execution, call)

        assert row["harness_outcome_status"] == "passed"
        assert row["sub_goal_results"] == [{"name": "booked", "passed": True}]

    def test_executor_skip_keeps_the_receipt_verdict(self, run):
        execution, scenario = run
        call = _call(
            execution,
            scenario,
            _sealed_pass(skipped=True, reason="Call too short to process."),
        )

        row = self.row(execution, call)

        assert row["harness_outcome_status"] == "passed"
        assert row["sub_goal_results"] == [{"name": "booked", "passed": True}]


@pytest.mark.django_db
class TestRunComparison:
    def current(self, execution):
        comparison = build_run_comparison(
            execution, run_calls_queryset(execution), with_pairs=True
        )
        (pair,) = comparison["pairs"]
        return pair["current"]

    def test_silent_trial_is_undecided_not_a_pass(self, run):
        execution, scenario = run
        _call(execution, scenario, _sealed_pass())
        _call(execution, scenario, _sealed_pass(skipped=True))

        side = self.current(execution)

        assert (side["passed"], side["trials"], side["undecided"]) == (1, 1, 1)

    def test_run_of_only_silent_trials_has_no_pass_rate(self, run):
        execution, scenario = run
        call = _call(execution, scenario, _sealed_pass(skipped=True))
        assert call_outcome(call, {}) == "inconclusive"

        side = self.current(execution)

        assert (side["passed"], side["trials"], side["undecided"]) == (0, 0, 1)
        assert side["pass_rate"] is None
