"""Terminal grading refusals must not replay successful judges or provider calls."""

import asyncio
import os
from uuid import uuid4

import pytest

pytest.importorskip("optuna")  # Optional agent-optimizer extra.
from temporalio import activity
from temporalio.worker import (
    Interceptor,
    ActivityInboundInterceptor,
    Worker,
    UnsandboxedWorkflowRunner,
)
from temporalio.testing import WorkflowEnvironment


class AttemptInterceptor(Interceptor):
    def __init__(self, observed):
        self.observed = observed

    def intercept_activity(self, next):
        observed = self.observed

        class Inbound(ActivityInboundInterceptor):
            async def execute_activity(self, input):
                info = activity.info()
                if info.activity_type != "dataset_optimization_run_activity":
                    return await super().execute_activity(input)
                observed["attempts"].append(info.attempt)
                try:
                    return await super().execute_activity(input)
                except Exception as exc:
                    observed["failures"].append(
                        {
                            "type": type(exc).__name__,
                            "code": getattr(exc, "code", None),
                            "non_retryable": getattr(exc, "non_retryable", False),
                            "message": str(exc),
                        }
                    )
                    raise

        return Inbound(next)


def make_run(organization, workspace):
    from model_hub.models.develop_dataset import Dataset, Row, Column, Cell
    from model_hub.models.evals_metric import EvalTemplate, UserEvalMetric
    from model_hub.models.optimize_dataset import OptimizeDataset
    from model_hub.utils.dataset_optimization import create_dataset_optimization_steps

    dataset = Dataset.no_workspace_objects.create(
        name="Synthetic retry dataset",
        organization=organization,
        workspace=workspace,
    )
    column = Column.no_workspace_objects.create(
        name="answer",
        dataset=dataset,
        data_type="text",
        source="OTHERS",
    )
    row = Row.no_workspace_objects.create(dataset=dataset, order=0)
    Cell.no_workspace_objects.create(
        dataset=dataset, row=row, column=column, value="baseline synthetic"
    )
    run = OptimizeDataset.no_workspace_objects.create(
        name="Synthetic denial retry",
        organization=organization,
        workspace=workspace,
        column=column,
        optimizer_algorithm="random_search",
        optimizer_config={"num_variations": 1, "model_name": "gpt-4o-mini"},
        status="pending",
        optimize_type="PromptTemplate",
        environment="Training",
        version="1.0",
    )
    for index in range(2):
        template = EvalTemplate.no_workspace_objects.create(
            name=f"Synthetic independently limited judge {index}",
            organization=organization,
            workspace=workspace,
            model="gpt-4o-mini",
            config={"output": "float", "eval_type_id": f"SyntheticJudge{index}"},
            output_type_normalized="percentage",
        )
        metric = UserEvalMetric.no_workspace_objects.create(
            name=f"Synthetic metric {index}",
            organization=organization,
            workspace=workspace,
            dataset=dataset,
            template=template,
            config={"mapping": {"output": "output"}},
        )
        run.user_eval_template_ids.add(metric)
    # Use the activity's query order; first succeeds, second refuses.
    metrics = list(run.user_eval_template_ids.select_related("template").all())
    create_dataset_optimization_steps(str(run.id))
    return run, str(metrics[0].template_id), str(metrics[1].template_id)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("code", ["rate_limited", "BUDGET_PAUSED"])
@pytest.mark.parametrize("phase", ["baseline", "trial"])
def test_terminal_denial_must_not_retry(
    monkeypatch, organization, workspace, code, phase
):
    from ee.agenthub.fix_your_agent.direct_evaluator import DirectEvaluator
    from ee.agent_opt.optimizers import random_search
    from model_hub.views.utils import evals
    from model_hub.models.dataset_optimization_trial import DatasetOptimizationTrial
    from tfc.temporal.dataset_optimization import activities
    from tfc.temporal.dataset_optimization.workflows import DatasetOptimizationWorkflow
    from tfc.temporal.dataset_optimization.types import DatasetOptimizationWorkflowInput
    from tfc.utils.evaluation_denials import EvaluationRateLimited
    from ee.usage.exceptions import UsageLimitExceeded
    from ee.usage.schemas.events import CheckResult

    run, allowed_template, denied_template = make_run(organization, workspace)
    observed = {
        "attempts": [],
        "failures": [],
        "provider_calls": [],
        "usage_events": [],
        "denials": [],
    }

    async def provider(self, prompt):
        observed["provider_calls"].append(prompt)
        return "synthetic output: " + prompt

    def judge(**kwargs):
        template_id = str(kwargs["template"].id)
        output = kwargs["mappings"]["output"]
        prompt_phase = "baseline" if "baseline synthetic" in output else "trial"
        should_deny = template_id == denied_template and (
            phase == "baseline" or prompt_phase == "trial"
        )
        if should_deny:
            observed["denials"].append({"code": code, "phase": prompt_phase})
            if code == "rate_limited":
                raise EvaluationRateLimited()
            raise UsageLimitExceeded(
                CheckResult(
                    allowed=False,
                    error_code="BUDGET_PAUSED",
                    reason="Synthetic budget paused; resume before grading.",
                )
            )
        observed["usage_events"].append(
            {"template": template_id, "phase": prompt_phase}
        )
        return {"output": 0.8, "reason": "Synthetic successful judge usage"}

    monkeypatch.setattr(DirectEvaluator, "_call_llm_async", provider)
    monkeypatch.setattr(evals, "run_eval_func", judge)
    monkeypatch.setattr(
        random_search, "_generate_variations", lambda *a, **kw: ["trial synthetic"]
    )
    monkeypatch.setattr(
        activities, "get_api_key_for_model", lambda **kw: "synthetic-not-a-real-key"
    )
    monkeypatch.setattr("tfc.ee_gating.check_ee_feature", lambda *a, **kw: None)

    async def execute():
        options = {}
        if os.environ.get("TEMPORAL_TEST_SERVER"):
            options["test_server_existing_path"] = os.environ["TEMPORAL_TEST_SERVER"]
        async with await WorkflowEnvironment.start_time_skipping(**options) as env:
            queue = "synthetic-denial-" + uuid4().hex
            async with Worker(
                env.client,
                task_queue=queue,
                workflows=[DatasetOptimizationWorkflow],
                activities=activities.ALL_ACTIVITIES,
                workflow_runner=UnsandboxedWorkflowRunner(),
                interceptors=[AttemptInterceptor(observed)],
            ):
                return await asyncio.wait_for(
                    env.client.execute_workflow(
                        DatasetOptimizationWorkflow.run,
                        DatasetOptimizationWorkflowInput(
                            run_id=str(run.id), task_queue=queue
                        ),
                        id=queue,
                        task_queue=queue,
                    ),
                    timeout=180,
                )

    result = asyncio.run(execute())
    run.refresh_from_db()
    trials = list(
        DatasetOptimizationTrial.no_workspace_objects.filter(
            optimization_run=run
        ).values("trial_number", "average_score", "is_baseline")
    )
    successful_first = [
        event
        for event in observed["usage_events"]
        if event["template"] == allowed_template and event["phase"] == phase
    ]
    providers_in_phase = [
        prompt
        for prompt in observed["provider_calls"]
        if ("baseline" if prompt == "baseline synthetic" else "trial") == phase
    ]
    print(
        "CANDIDATE3",
        code,
        phase,
        "attempts=",
        observed["attempts"],
        "provider_calls_in_denied_phase=",
        len(providers_in_phase),
        "successful_first_judge_executions_in_denied_phase=",
        len(successful_first),
        "denials=",
        observed["denials"],
        "failures=",
        observed["failures"],
        "saved_trials=",
        trials,
        "workflow_status=",
        result.status,
        "workflow_error=",
        result.error,
    )
    assert observed["denials"], "Test did not reach requested typed evaluation refusal"
    assert all(f["code"] == code or f["non_retryable"] for f in observed["failures"]), (
        observed
    )
    assert len(observed["attempts"]) == 1, (
        f"Terminal {code} retried {len(observed['attempts'])} times; "
        f"provider_calls={len(providers_in_phase)}, successful_first_judge={len(successful_first)}, "
        f"failures={observed['failures']}"
    )
    assert len(providers_in_phase) == 1
    assert len(successful_first) == 1
    assert run.status in {"failed", "blocked", "denied"}
    assert len(trials) == (1 if phase == "trial" else 0), (
        "Denied trial was persisted as scored work"
    )

    assert code in result.error
    assert code in run.error_message
    assert "before" in result.error
