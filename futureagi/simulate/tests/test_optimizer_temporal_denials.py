"""Scoreless policy refusals survive scenario serialization and optimizer persistence."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

pytest.importorskip("optuna")  # Optional agent-optimizer extra.

pytestmark = pytest.mark.django_db(transaction=True)


def denial_for(code):
    from ee.usage.exceptions import UsageLimitExceeded
    from ee.usage.schemas.events import CheckResult
    from tfc.utils.evaluation_denials import EvaluationRateLimited

    if code == "rate_limited":
        return EvaluationRateLimited()
    return UsageLimitExceeded(
        CheckResult(
            allowed=False,
            error_code=code,
            reason="Synthetic organization budget paused; resume budget before retrying.",
        )
    )


@pytest.fixture
def temporal_case(monkeypatch, organization, workspace):
    from ee.agenthub.fix_your_agent import fix_your_agent as optimizer
    from model_hub.models.evals_metric import EvalTemplate
    from simulate.models import RunTest, SimulateEvalConfig
    from tfc.temporal.agent_prompt_optimiser import eval_activities as tasks

    template = EvalTemplate.no_workspace_objects.create(
        name="Synthetic grading",
        organization=organization,
        workspace=workspace,
        model="gpt-4o-mini",
        config={
            "output": "float",
            "eval_type_id": "Synthetic",
            "required_keys": ["conversation"],
        },
    )
    run = RunTest.no_workspace_objects.create(
        name="Synthetic optimizer run",
        organization=organization,
        workspace=workspace,
    )
    config = SimulateEvalConfig.no_workspace_objects.create(
        name="Synthetic grading",
        eval_template=template,
        run_test=run,
        model="gpt-4o-mini",
        mapping={"conversation": "transcript"},
        config={},
    )
    wire = {}
    observations = {"raw": [], "runner_calls": [], "candidate_generations": []}

    def delay(scenario, evaluator_config):
        # Exercise the real task, including reconstruction and denial handling.
        raw = tasks.evaluate_scenario_task._original_func(
            json.loads(json.dumps(scenario)), json.loads(json.dumps(evaluator_config))
        )
        observations["raw"].append(raw)
        key = str(len(wire))
        # Exact two-level result envelope consumed by evaluate_scenarios_parallel.
        wire[key] = json.loads(
            json.dumps({"result": {"result": raw, "status": "completed"}})
        )
        return SimpleNamespace(id=key)

    monkeypatch.setattr(tasks.evaluate_scenario_task, "delay", delay)
    monkeypatch.setattr(tasks, "get_workflow_result_sync", lambda key, **_: wire[key])
    monkeypatch.setattr(
        "tfc.temporal.common.client.cancel_workflow_sync", Mock(return_value=True)
    )
    monkeypatch.setattr(
        optimizer.SimulationEvaluator,
        "_run_real_simulation",
        lambda self, prompt, scenario: {"transcript": scenario["existing_transcript"]},
    )
    monkeypatch.setattr(optimizer, "update_agent_optimiser_run_step", Mock())
    return SimpleNamespace(
        config=config,
        optimizer=optimizer,
        observations=observations,
        organization=organization,
        workspace=workspace,
    )


def install_judge(monkeypatch, case, code):
    def judge(**kwargs):
        transcript = kwargs["mappings"]["conversation"]
        case.observations["runner_calls"].append(transcript)
        if transcript.startswith("DENIED"):
            raise denial_for(code)
        return {"output": 1.0, "reason": "Synthetic allowed grading"}

    monkeypatch.setattr(case.optimizer, "run_eval_func", judge)


def scenarios(mixed):
    return [
        {
            "call_execution_id": "synthetic-1",
            "agent_prompt": "base",
            "existing_transcript": "DENIED synthetic transcript",
        },
        {
            "call_execution_id": "synthetic-2",
            "agent_prompt": "base",
            "existing_transcript": ("OK" if mixed else "DENIED")
            + " synthetic transcript",
        },
    ]


@pytest.mark.parametrize("code", ["rate_limited", "BUDGET_PAUSED"])
def test_temporal_denial_must_not_be_numeric(monkeypatch, temporal_case, code):
    """Both real result adapters must preserve a scoreless refusal."""
    from tfc.utils.evaluation_denials import EvaluationDenied

    case = temporal_case
    install_judge(monkeypatch, case, code)
    evaluator = case.optimizer.SimulationEvaluator(
        user_eval_configs=[case.config],
        use_synthetic=False,
        initial_agent_prompt="base",
        use_temporal_evaluation=True,
        organization=case.organization,
        workspace=case.workspace,
    )
    results = None
    denial = None
    try:
        results = evaluator.evaluate(scenarios(False))
    except EvaluationDenied as exc:
        denial = exc
    assert case.observations["runner_calls"], (
        "Fixture did not reach the shared evaluation boundary"
    )
    numeric = [
        r.score
        for r in (results or [])
        if isinstance(getattr(r, "score", None), (int, float))
    ]
    print(
        "CANDIDATE1", code, "numeric_scores=", numeric, "raw=", case.observations["raw"]
    )
    assert not numeric, (
        f"Denied scenario tasks became numeric EvaluationResults: {numeric}; raw={case.observations['raw']}"
    )
    assert denial is not None and denial.code == code, (
        "Refusal did not reach the parent evaluator"
    )
    assert all("score" not in raw for raw in case.observations["raw"])


@pytest.mark.parametrize("code", ["rate_limited", "BUDGET_PAUSED"])
@pytest.mark.parametrize(
    "mixed", [False, True], ids=["all-denied", "mixed-success-denied"]
)
def test_denial_blocks_baseline_and_candidate_generation(
    monkeypatch, temporal_case, code, mixed
):
    """Drive the real optimizer through its baseline and trial callbacks."""
    from ee.agent_opt.optimizers import random_search
    from tfc.utils.evaluation_denials import EvaluationDenied

    case = temporal_case
    install_judge(monkeypatch, case, code)
    persisted = []
    from simulate.models import (
        AgentOptimiser,
        AgentOptimiserRun,
        AgentPromptOptimiserRun,
        CallExecution,
        Scenarios,
        TestExecution,
    )
    from simulate.models.prompt_trial import PromptTrial
    from simulate.models.trial_item_result import TrialItemResult
    from simulate.utils.agent_prompt_optimiser import store_single_trial

    agent = AgentOptimiser.no_workspace_objects.create(
        name="Synthetic optimizer", configuration={}
    )
    analysis = AgentOptimiserRun.no_workspace_objects.create(
        agent_optimiser=agent, status="completed", input_data={}, result={}
    )
    execution = TestExecution.no_workspace_objects.create(
        run_test=case.config.run_test, status="completed"
    )
    run = AgentPromptOptimiserRun.no_workspace_objects.create(
        name="Synthetic prompt optimization",
        agent_optimiser=agent,
        agent_optimiser_run=analysis,
        test_execution=execution,
        optimiser_type="random_search",
        model="gpt-4o-mini",
        status="running",
        configuration={},
    )
    inputs = scenarios(mixed)
    for index, item in enumerate(inputs):
        scenario = Scenarios.no_workspace_objects.create(
            name=f"Synthetic scenario {index}",
            source="synthetic",
            organization=case.organization,
            workspace=case.workspace,
        )
        call = CallExecution.no_workspace_objects.create(
            test_execution=execution, scenario=scenario, status="completed"
        )
        item["call_execution_id"] = str(call.id)

    def persist(**trial):
        persisted.append(trial)
        store_single_trial(prompt_optimiser_run=run, **trial)

    def variations(*args, **kwargs):
        case.observations["candidate_generations"].append("candidate")
        return ["synthetic candidate"]

    monkeypatch.setattr(random_search, "_generate_variations", variations)
    monkeypatch.setattr(random_search, "update_agent_optimiser_run_step", Mock())
    outcome = None
    denied = None
    try:
        outcome = case.optimizer.FixYourAgent().optimize(
            initial_agent_prompt="base",
            scenarios=inputs,
            eval_configs=[case.config],
            optimizer_type="random_search",
            optimizer_config={"num_variations": 1},
            use_synthetic=False,
            optimization_model="gpt-4o-mini",
            api_key="synthetic-not-a-real-key",
            use_temporal_evaluation=True,
            organization=case.organization,
            workspace=case.workspace,
            on_trial_callback=persist,
        )
    except EvaluationDenied as exc:
        denied = exc
    assert len(case.observations["runner_calls"]) >= 1, (
        "Shared evaluator was not reached"
    )
    saved_trials = list(
        PromptTrial.no_workspace_objects.filter(
            agent_prompt_optimiser_run=run
        ).values_list("average_score", flat=True)
    )
    saved_items = list(
        TrialItemResult.no_workspace_objects.filter(
            prompt_trial__agent_prompt_optimiser_run=run
        ).values_list("score", flat=True)
    )
    print(
        "CANDIDATE4",
        code,
        "mixed=",
        mixed,
        "persisted_scores=",
        saved_trials,
        "persisted_item_scores=",
        saved_items,
        "candidate_generations=",
        len(case.observations["candidate_generations"]),
        "returned_normally=",
        outcome is not None,
    )
    assert not saved_trials and not saved_items, (
        f"Denied grading persisted real scored trials/items: trials={saved_trials}, items={saved_items}"
    )
    assert not persisted, (
        f"Denied grading reached scored-trial persistence callback: {persisted}"
    )
    assert not case.observations["candidate_generations"], (
        "Generated candidates after baseline refusal"
    )
    assert denied is not None and denied.code == code, (
        "Typed denial did not reach optimizer boundary"
    )


def test_control_allowed_temporal_scores_remain_numeric(monkeypatch, temporal_case):
    case = temporal_case
    install_judge(monkeypatch, case, "rate_limited")
    inputs = scenarios(False)
    for item in inputs:
        item["existing_transcript"] = "OK synthetic transcript"
    evaluator = case.optimizer.SimulationEvaluator(
        user_eval_configs=[case.config],
        use_synthetic=False,
        initial_agent_prompt="base",
        use_temporal_evaluation=True,
        organization=case.organization,
        workspace=case.workspace,
    )
    assert [r.score for r in evaluator.evaluate(inputs)] == [1.0, 1.0]


@pytest.mark.parametrize("code", ["rate_limited", "BUDGET_PAUSED"])
def test_simulation_optimizer_activity_denial_is_terminal(
    monkeypatch, temporal_case, code, temporal_test_server_options
):
    """Run the real activity/workflow to prove the agent path also stops retries."""
    import asyncio
    from uuid import uuid4

    from temporalio import activity
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import UnsandboxedWorkflowRunner, Worker

    from simulate.models import (
        AgentOptimiser,
        AgentOptimiserRun,
        AgentPromptOptimiserRun,
        TestExecution,
    )
    from tfc.temporal.agent_prompt_optimiser import activities
    from tfc.temporal.agent_prompt_optimiser.types import (
        RunAgentPromptOptimiserWorkflowInput,
    )
    from tfc.temporal.agent_prompt_optimiser.workflows import (
        AgentPromptOptimiserWorkflow,
    )

    case = temporal_case
    agent = AgentOptimiser.no_workspace_objects.create(
        name="Synthetic optimizer", configuration={}
    )
    analysis = AgentOptimiserRun.no_workspace_objects.create(
        agent_optimiser=agent, status="completed", input_data={}, result={}
    )
    execution = TestExecution.no_workspace_objects.create(
        run_test=case.config.run_test, status="completed"
    )
    run = AgentPromptOptimiserRun.no_workspace_objects.create(
        name="Synthetic terminal refusal",
        agent_optimiser=agent,
        agent_optimiser_run=analysis,
        test_execution=execution,
        optimiser_type="random_search",
        model="gpt-4o-mini",
        configuration={"num_variations": 1},
    )
    attempts = []

    def refuse(*args, **kwargs):
        attempts.append(activity.info().attempt)
        raise denial_for(code)

    monkeypatch.setattr(activities, "get_full_test_execution_data", lambda _: {})
    monkeypatch.setattr(
        activities, "get_api_key_for_model", lambda **_: "synthetic-key"
    )
    monkeypatch.setattr(activities, "fetch_agent_level_issues_from_run", lambda _: [])
    monkeypatch.setattr(activities.FixYourAgent, "optimize_from_execution", refuse)

    async def execute():
        async with await WorkflowEnvironment.start_time_skipping(
            **temporal_test_server_options
        ) as env:
            queue = "synthetic-agent-denial-" + uuid4().hex
            async with Worker(
                env.client,
                task_queue=queue,
                workflows=[AgentPromptOptimiserWorkflow],
                activities=activities.ALL_ACTIVITIES,
                workflow_runner=UnsandboxedWorkflowRunner(),
            ):
                return await asyncio.wait_for(
                    env.client.execute_workflow(
                        AgentPromptOptimiserWorkflow.run,
                        RunAgentPromptOptimiserWorkflowInput(
                            run_id=str(run.id), task_queue=queue
                        ),
                        id=queue,
                        task_queue=queue,
                    ),
                    timeout=180,
                )

    result = asyncio.run(execute())
    run.refresh_from_db()
    print("AGENT_DENIAL", code, "attempts=", attempts, "error=", result.error)
    assert attempts == [1]
    assert result.status == run.status == "failed"
    assert code in result.error
    assert code in run.error_message
    assert str(denial_for(code)) in run.error_message


@pytest.mark.parametrize("code", ["rate_limited", "BUDGET_PAUSED"])
def test_temporal_result_adapter_rejects_denial_before_scoring(
    monkeypatch, temporal_case, code
):
    from tfc.temporal.agent_prompt_optimiser import eval_activities as tasks
    from tfc.utils.evaluation_denials import EvaluationDenied

    denial = denial_for(code)
    monkeypatch.setattr(
        tasks,
        "evaluate_scenarios_parallel",
        lambda **_: [
            {"score": 1.0, "reason": "Synthetic successful scenario"},
            {"denial": denial.as_dict()},
        ],
    )
    evaluator = temporal_case.optimizer.SimulationEvaluator(
        user_eval_configs=[temporal_case.config],
        use_synthetic=False,
        initial_agent_prompt="base",
        use_temporal_evaluation=True,
        organization=temporal_case.organization,
        workspace=temporal_case.workspace,
    )
    with pytest.raises(EvaluationDenied) as caught:
        evaluator.evaluate(scenarios(True))
    assert caught.value.as_dict() == denial.as_dict()


TRIAL_DENIAL_CONFIGS = {
    "random_search": {"num_variations": 3},
    "metaprompt": {"num_rounds": 3},
    "bayesian": {"n_trials": 3, "min_examples": 1, "max_examples": 1},
}


@pytest.mark.parametrize("code", ["rate_limited", "BUDGET_PAUSED"])
@pytest.mark.parametrize("optimiser_type", sorted(TRIAL_DENIAL_CONFIGS))
def test_trial_denial_escapes_every_optimizer(
    monkeypatch, temporal_case, optimiser_type, code
):
    """A graded baseline, then refused trials: no later scenario batch starts."""
    from ee.agent_opt.generators.litellm_generator import LiteLLMGenerator
    from ee.agent_opt.optimizers import random_search
    from tfc.utils.evaluation_denials import EvaluationDenied

    case = temporal_case
    seen = {"phase": "baseline", "denials": 0, "batches": [], "callbacks": []}

    def event():
        return {"phase": seen["phase"], "after_denial": seen["denials"] > 0}

    def judge(**kwargs):
        if seen["phase"] == "trial":
            seen["denials"] += 1
            raise denial_for(code)
        return {"output": 1.0, "reason": "Synthetic allowed grading"}

    real_batch = case.optimizer.SimulationEvaluator._evaluate_via_temporal

    def batch(self, inputs):
        # Every scenario of a batch is dispatched before results are collected,
        # so only a batch that *starts* after a refusal is repeated paid work.
        seen["batches"].append(event())
        return real_batch(self, inputs)

    def on_trial(**trial):
        seen["callbacks"].append({**event(), "is_baseline": trial["is_baseline"]})
        if trial["is_baseline"]:
            seen["phase"] = "trial"

    monkeypatch.setattr(case.optimizer, "run_eval_func", judge)
    monkeypatch.setattr(
        case.optimizer.SimulationEvaluator, "_evaluate_via_temporal", batch
    )
    monkeypatch.setattr(
        LiteLLMGenerator,
        "generate",
        lambda self, prompt_vars, **kw: json.dumps(
            {"hypothesis": "synthetic", "improved_prompt": "synthetic candidate"}
        ),
    )
    monkeypatch.setattr(
        random_search, "_generate_variations", lambda *a, **kw: ["synthetic candidate"]
    )
    inputs = scenarios(False)
    for item in inputs:
        item["existing_transcript"] = "OK synthetic transcript"

    outcome = None
    denied = None
    try:
        outcome = case.optimizer.FixYourAgent().optimize(
            initial_agent_prompt="base",
            scenarios=inputs,
            eval_configs=[case.config],
            optimizer_type=optimiser_type,
            optimizer_config=dict(TRIAL_DENIAL_CONFIGS[optimiser_type]),
            use_synthetic=False,
            optimization_model="gpt-4o-mini",
            api_key="synthetic-not-a-real-key",
            use_temporal_evaluation=True,
            organization=case.organization,
            workspace=case.workspace,
            on_trial_callback=on_trial,
        )
    except EvaluationDenied as exc:
        denied = exc

    trial_batches = [b for b in seen["batches"] if b["phase"] == "trial"]
    later_batches = [b for b in seen["batches"] if b["after_denial"]]
    print(
        "TRIAL_DENIAL",
        optimiser_type,
        code,
        "returned_normally=",
        outcome is not None,
        "denials=",
        seen["denials"],
        "trial_batches=",
        len(trial_batches),
        "batches_after_denial=",
        len(later_batches),
    )
    assert seen["callbacks"] and seen["callbacks"][0]["is_baseline"], (
        "Baseline was not graded and persisted"
    )
    assert seen["denials"], "Fixture never reached the trial-phase refusal"
    assert denied is not None and denied.code == code, (
        f"{optimiser_type}: trial-phase {code} was swallowed; optimize() returned "
        f"normally after {len(trial_batches)} trial batches "
        f"({len(later_batches)} started after the first refusal)"
    )
    assert len(trial_batches) == 1
    assert later_batches == []
    assert not any(c["after_denial"] for c in seen["callbacks"])
