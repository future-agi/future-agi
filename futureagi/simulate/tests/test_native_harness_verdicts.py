"""Keep native harness verdicts consistent across rows and database summaries."""

import pytest

from model_hub.models.evals_metric import EvalTemplate
from simulate.models import CallExecution, Scenarios, SimulateEvalConfig, TestExecution
from simulate.models.run_test import RunTest
from simulate.services.run_results_v3 import call_outcome
from simulate.services.run_results_v3_queries import (
    apply_run_call_query,
    run_calls_queryset,
    summarize_run_calls,
)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "native,metadata,call_status,expected",
    [
        ({"output": False}, {}, "completed", "failed"),
        ({"output": " Failed "}, {}, "completed", "failed"),
        ({"output": " unsuccessful "}, {}, "completed", "failed"),
        ({"output": "SUCCESS"}, {}, "completed", "passed"),
        ({"output": "false"}, {}, "completed", "failed"),
        ({"output": "failure"}, {}, "completed", "failed"),
        ({"output": True, "status": " error "}, {}, "completed", "inconclusive"),
        ({"output": False, "status": "FAILED"}, {}, "completed", "inconclusive"),
        ({"output": False, "status": "pending"}, {}, "completed", "passed"),
        ({"output": False, "status": "skipped"}, {}, "completed", "passed"),
        ({"output": False, "source": "provider"}, {}, "completed", "passed"),
        ({"output": {"failure": True}}, {}, "completed", "passed"),
        (
            {"output": False},
            {"harness_outcome_status": "passed"},
            "completed",
            "failed",
        ),
        ({"output": False}, {}, "running", "inconclusive"),
        ({"output": False}, {}, "failed", "error"),
    ],
)
def test_native_check_rows_filters_and_counts_agree(
    organization, workspace, native, metadata, call_status, expected
):
    run = RunTest.objects.create(
        name="Native checks", organization=organization, workspace=workspace
    )
    execution = TestExecution.objects.create(run_test=run)
    scenario = Scenarios.objects.create(
        name="Native check",
        source="Test",
        organization=organization,
        workspace=workspace,
    )
    template = EvalTemplate.objects.create(
        name="Quality",
        organization=organization,
        output_type_normalized="pass_fail",
        config={"output": "Pass/Fail"},
    )
    config = SimulateEvalConfig.objects.create(
        name="Quality", run_test=run, eval_template=template
    )
    call = CallExecution.objects.create(
        test_execution=execution,
        scenario=scenario,
        status=call_status,
        call_metadata=metadata,
        eval_outputs={
            str(config.id): {"output": "Passed", "status": "completed"},
            "native-check": {"source": "harness", "status": "completed", **native},
        },
    )
    queryset = run_calls_queryset(execution)
    assert call_outcome(call, {str(config.id): config}) == expected
    assert queryset.get(pk=call.pk).result_outcome == expected
    for outcome in ("passed", "failed", "error", "inconclusive"):
        filtered = apply_run_call_query(queryset, {"filters": {"status": [outcome]}})
        assert filtered.count() == int(outcome == expected)
    summary = summarize_run_calls(queryset, include_percentiles=False)
    assert summary["outcomes"][expected] == 1
    assert sum(summary["outcomes"].values()) == 1


@pytest.mark.django_db
@pytest.mark.parametrize("deleted", [False, True])
def test_source_marked_configured_choices_are_not_judged_twice(
    organization, workspace, deleted
):
    run = RunTest.objects.create(
        name="Choices", organization=organization, workspace=workspace
    )
    execution = TestExecution.objects.create(run_test=run)
    scenario = Scenarios.objects.create(
        name="Choice check",
        source="Test",
        organization=organization,
        workspace=workspace,
    )
    template = EvalTemplate.objects.create(
        name="Choice",
        organization=organization,
        output_type_normalized="deterministic",
        config={"output": "choices"},
        choice_scores={"false": 1.0},
    )
    config = SimulateEvalConfig.objects.create(
        name="Choice", run_test=run, eval_template=template, deleted=deleted
    )
    call = CallExecution.objects.create(
        test_execution=execution,
        scenario=scenario,
        status="completed",
        eval_outputs={
            str(config.id): {
                "source": "harness",
                "output": "false",
                "status": "completed",
            }
        },
    )
    live_configs = {} if deleted else {str(config.id): config}
    expected = "failed" if deleted else "passed"
    assert call_outcome(call, live_configs) == expected
    assert run_calls_queryset(execution).get(pk=call.pk).result_outcome == expected
