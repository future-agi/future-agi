"""Grouped scores use the same evaluation bindings as individual call rows."""

import pytest

from model_hub.models.evals_metric import EvalTemplate
from simulate.models import CallExecution, Scenarios, SimulateEvalConfig, TestExecution
from simulate.models.run_test import RunTest
from simulate.services.run_results_v3 import build_call_rows
from simulate.services.run_results_v3_queries import group_run_calls, run_calls_queryset


@pytest.fixture
def grouped_execution(organization, workspace):
    run = RunTest.objects.create(
        name="Grouped score run", organization=organization, workspace=workspace
    )
    return TestExecution.objects.create(
        run_test=run, status=TestExecution.ExecutionStatus.COMPLETED
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    "output_type,template_choices,binding,outputs,expected_scores",
    [
        ("percentage", {}, {"reverse_output": True}, [0.9, 0.7], [0.1, 0.3]),
        (
            "deterministic",
            {"safe": 1.0, "unsafe": 0.0},
            {"choice_scores": {"safe": 0.3, "unsafe": 0.8}},
            ["safe", "unsafe"],
            [0.3, 0.8],
        ),
        (
            "deterministic",
            {"safe": 1.0},
            {"reverse_output": True},
            ["Passed", "Failed"],
            [1.0, 0.0],
        ),
        ("percentage", {}, {"pass_threshold": "bad"}, [0.9, 0.7], [None, None]),
    ],
)
def test_group_scores_match_configured_rows_across_pages(
    grouped_execution,
    organization,
    workspace,
    output_type,
    template_choices,
    binding,
    outputs,
    expected_scores,
):
    template = EvalTemplate.objects.create(
        name="Configured group evaluation",
        organization=organization,
        output_type_normalized=output_type,
        pass_threshold=0.7,
        choice_scores=template_choices,
    )
    config = SimulateEvalConfig.objects.create(
        name="Configured group evaluation",
        run_test=grouped_execution.run_test,
        eval_template=template,
        config=binding,
    )
    columns = [{"id": str(config.id), "name": config.name}]
    scenario = Scenarios.objects.create(
        name="Shared goal",
        source="test",
        scenario_type=Scenarios.ScenarioTypes.SCRIPT,
        organization=organization,
        workspace=workspace,
    )
    for output in outputs:
        CallExecution.objects.create(
            test_execution=grouped_execution,
            scenario=scenario,
            status="completed",
            eval_outputs={
                str(config.id): {
                    "status": "completed",
                    "output_type": (
                        "Pass/Fail" if output in ("Passed", "Failed") else output_type
                    ),
                    "output": output,
                }
            },
        )
    queryset = run_calls_queryset(grouped_execution).order_by("id")
    rows, _ = build_call_rows(
        grouped_execution, list(queryset), columns, {str(config.id)}
    )
    scores = [row["evaluations"][0]["score"] for row in rows]
    assert sorted(scores, key=lambda score: score or 0) == sorted(
        expected_scores, key=lambda score: score or 0
    )
    measured = [score for score in scores if score is not None]
    for row in rows:
        groups = group_run_calls(
            queryset, "goal", [row], columns, execution=grouped_execution
        )
        assert len(groups) == 1
        assert groups[0]["total"] == len(outputs)
        assert groups[0]["result_ids"] == [row["id"]]
        aggregate = groups[0]["aggregates"]["evaluations"][str(config.id)]
        assert aggregate["scored"] == len(measured)
        assert aggregate["score_sum"] == pytest.approx(sum(measured))


@pytest.mark.django_db
def test_native_group_scores_preserve_row_fallback(
    grouped_execution, organization, workspace
):
    scenario = Scenarios.objects.create(
        name="Native check goal",
        source="test",
        scenario_type=Scenarios.ScenarioTypes.SCRIPT,
        organization=organization,
        workspace=workspace,
    )
    for score in (80, 40):
        CallExecution.objects.create(
            test_execution=grouped_execution,
            scenario=scenario,
            status="completed",
            eval_outputs={
                "native-check": {
                    "source": "harness",
                    "status": "completed",
                    "output": {"score": score},
                }
            },
        )
    queryset = run_calls_queryset(grouped_execution)
    columns = [{"id": "native-check", "name": "Native check"}]
    rows, _ = build_call_rows(grouped_execution, list(queryset), columns, set())
    groups = group_run_calls(
        queryset, "goal", rows[:1], columns, execution=grouped_execution
    )
    aggregate = groups[0]["aggregates"]["evaluations"]["native-check"]
    assert aggregate["scored"] == 2
    assert aggregate["score_sum"] == pytest.approx(
        sum(row["evaluations"][0]["score"] for row in rows)
    )
    assert aggregate["score_sum"] == pytest.approx(1.2)
