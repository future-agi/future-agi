"""A retried simulation evaluation is charged once; a rerun a person starts is
charged again.

A call's evaluations run in the run_simulate_evaluations activity of the call's
workflow. The usage event of each evaluation is keyed to the call, the workflow
run that evaluates it and the evaluation config. When Temporal runs the
activity again after a lost worker, every evaluation emits the event it emitted
before, so the consumer, which keeps one event per event id, charges it once. A
rerun a person starts is a new workflow run, also when it reuses the call's row
and the workflow's id, so its events are new and are charged. A tool-call
evaluation is keyed the same way, to the tool call.
"""

import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from asgiref.sync import sync_to_async
from temporalio import activity, workflow
from temporalio.common import WorkflowIDReusePolicy
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from model_hub.models.choices import OwnerChoices
from model_hub.models.evals_metric import EvalTemplate
from simulate.models import AgentDefinition
from simulate.models.eval_config import SimulateEvalConfig
from simulate.models.run_test import RunTest
from simulate.models.test_execution import CallExecution
from simulate.services.call_rerun import clear_call_for_rerun
from simulate.temporal.retry_policies import EVAL_RETRY_POLICY
from simulate.temporal.types.activities import (
    RunSimulateEvaluationsInput,
    RunToolCallEvaluationInput,
)
from simulate.tests.test_eval_resolver_behavior import (  # noqa: F401 - fixtures
    agent_definition as agent_definition,
)
from simulate.tests.test_eval_resolver_behavior import agent_version as agent_version
from simulate.tests.test_eval_resolver_behavior import call_execution as call_execution
from simulate.tests.test_eval_resolver_behavior import (
    dataset_for_scenario as dataset_for_scenario,
)
from simulate.tests.test_eval_resolver_behavior import run_test as run_test
from simulate.tests.test_eval_resolver_behavior import scenario as scenario
from simulate.tests.test_eval_resolver_behavior import (
    simulator_agent as simulator_agent,
)
from simulate.tests.test_eval_resolver_behavior import test_execution as test_execution
from tfc.constants.api_calls import APICallStatusChoices, APICallTypeChoices
from tfc.temporal.common.heartbeat import Heartbeater

pytest_plugins = ["evaluations.tests.usage_step_support"]
pytestmark = pytest.mark.requires_ee

_TRANSCRIPT = {
    "transcript": "Hello. Yes, order 123 shipped.",
    "voice_recording": "",
    "assistant_recording": "",
    "customer_recording": "",
    "stereo_recording": "",
    "user_chat_transcript": "",
    "assistant_chat_transcript": "",
}
# What the stand-in judges cost: an evaluation, and one tool call.
_EVALUATION_COST = 0.002
_TOOL_CALL_COST = 0.001
# The per-run fee the billing_config fixture charges.
_PER_RUN_FEE = 0.0005


@pytest.fixture
def eval_configs(run_test, organization, workspace):
    """Two evaluations of the run, each with its own template."""
    return [
        SimulateEvalConfig.objects.create(
            name=name,
            eval_template=EvalTemplate.no_workspace_objects.create(
                name=name,
                organization=organization,
                workspace=workspace,
                owner=OwnerChoices.USER.value,
                config={"output": "score", "eval_type_id": "DeterministicEvaluator"},
                output_type_normalized="percentage",
                pass_threshold=0.5,
            ),
            run_test=run_test,
            mapping={"input": "call.transcript"},
            config={"config": {}, "params": {}},
        )
        for name in ("charged-evaluation-a", "charged-evaluation-b")
    ]


@pytest.fixture
def charges(monkeypatch, billing_config):
    """What the evaluations charge: the usage events they emit and the call
    log rows they write. The judge, the usage check and the call log are
    stand-ins; the activities and run_eval_func run as they are."""
    import model_hub.views.utils.evals as evals_module

    seen = SimpleNamespace(events=[], log_rows=[])

    class StandInEvaluator:
        cost = {"total_cost": _EVALUATION_COST}
        token_usage = {}

        def run(self, **_kwargs):
            return SimpleNamespace(
                eval_results=[
                    {
                        "data": {"input": _TRANSCRIPT["transcript"]},
                        "failure": None,
                        "reason": "ok",
                        "runtime": 0.01,
                        "model": "turing_large",
                        "metrics": None,
                        "metadata": {},
                    }
                ]
            )

    def _call_log(**kwargs):
        seen.log_rows.append(kwargs)
        return SimpleNamespace(
            log_id=uuid4(),
            config=json.dumps({}),
            status=APICallStatusChoices.PROCESSING.value,
            input_token_count=0,
            save=MagicMock(),
        )

    monkeypatch.setattr(evals_module, "log_and_deduct_cost_for_api_request", _call_log)
    monkeypatch.setattr(
        "ee.usage.utils.usage_entries.log_and_deduct_cost_for_api_request", _call_log
    )
    monkeypatch.setattr(
        "ee.usage.services.metering.check_usage",
        lambda *_args, **_kwargs: SimpleNamespace(allowed=True, reason=""),
    )
    monkeypatch.setattr("ee.usage.services.emitter.emit", seen.events.append)
    monkeypatch.setattr(
        "model_hub.views.utils.evals.EvaluationRunner._create_eval_instance",
        lambda *_args, **_kwargs: StandInEvaluator(),
    )
    monkeypatch.setattr(
        "model_hub.views.utils.evals.EvaluationRunner.map_fields",
        lambda *_args, **_kwargs: {"input": _TRANSCRIPT["transcript"]},
    )
    monkeypatch.setattr(
        "model_hub.views.utils.evals.EvaluationRunner.format_output",
        lambda *_args, **_kwargs: 0.7,
    )
    monkeypatch.setattr(
        "simulate.temporal.activities.xl._build_transcript_data",
        lambda _call_execution: dict(_TRANSCRIPT),
    )
    return seen


def _activity_info(activity_type, workflow_id, workflow_run_id, attempt):
    return SimpleNamespace(
        activity_id="1",
        activity_type=activity_type,
        attempt=attempt,
        heartbeat_timeout=None,
        task_queue="tasks_xl",
        workflow_id=workflow_id,
        workflow_namespace="default",
        workflow_run_id=workflow_run_id,
        workflow_type="CallExecutionWorkflow",
    )


async def _attempt(run_activity, activity_input, charges, info):
    """One attempt of an activity inside the workflow run ``info`` names. Gives
    the usage events the attempt emitted."""
    before = len(charges.events)
    with (
        patch("temporalio.activity.info", return_value=info),
        patch("temporalio.activity.is_cancelled", return_value=False),
        patch.object(
            Heartbeater,
            "__aenter__",
            new=AsyncMock(return_value=MagicMock(details=None)),
        ),
        patch.object(Heartbeater, "__aexit__", new=AsyncMock(return_value=None)),
    ):
        result = await run_activity(activity_input)
    assert result.success is True, result.error
    return charges.events[before:]


async def _evaluate(call_execution, charges, *, workflow_run_id, attempt=1):
    """One attempt of the call's evaluation activity. A first run and a
    whole-run rerun both run the call under the workflow id of its row."""
    from simulate.temporal.activities.xl import run_simulate_evaluations

    events = await _attempt(
        run_simulate_evaluations,
        RunSimulateEvaluationsInput(call_execution_id=str(call_execution.id)),
        charges,
        _activity_info(
            "run_simulate_evaluations",
            f"call-exec-{call_execution.id}",
            workflow_run_id,
            attempt,
        ),
    )
    assert await sync_to_async(_failed_evaluations)(call_execution) == {}
    return events


async def _evaluate_tool_calls(call_execution, charges, *, workflow_run_id, attempt=1):
    """One attempt of the call's tool-call evaluation activity."""
    from simulate.temporal.activities.xl import run_tool_call_evaluation

    return await _attempt(
        run_tool_call_evaluation,
        RunToolCallEvaluationInput(call_execution_id=str(call_execution.id)),
        charges,
        _activity_info(
            "run_tool_call_evaluation",
            f"call-exec-{call_execution.id}",
            workflow_run_id,
            attempt,
        ),
    )


def _failed_evaluations(call_execution):
    """Why each evaluation of the call that failed did, by config id."""
    call_execution.refresh_from_db()
    return {
        config_id: output.get("reason")
        for config_id, output in (call_execution.eval_outputs or {}).items()
        if output.get("error")
    }


def _ids(events):
    """The event id of each evaluation, by the template it ran."""
    ids = {event.properties["source_id"]: event.event_id for event in events}
    assert len(ids) == len(events)
    return ids


def _kept_by_the_consumer(events):
    """The events the consumer keeps: one per event id."""
    return {event.event_id: event for event in events}


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_retried_evaluation_is_charged_once(
    call_execution, eval_configs, charges
):
    first = await _evaluate(call_execution, charges, workflow_run_id="run-1")
    retried = await _evaluate(
        call_execution, charges, workflow_run_id="run-1", attempt=2
    )

    assert len(first) == len(retried) == 2
    assert _ids(retried) == _ids(first)
    assert len(_kept_by_the_consumer(charges.events)) == 2
    # Every attempt logs its own calls.
    assert len(charges.log_rows) == 4


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_rerun_a_person_starts_is_charged_again(
    call_execution, eval_configs, charges
):
    """A whole-run rerun resets the call's row and runs it again under the
    same workflow id, in a new workflow run."""
    first = await _evaluate(call_execution, charges, workflow_run_id="run-1")
    await sync_to_async(clear_call_for_rerun)(call_execution)
    rerun = await _evaluate(call_execution, charges, workflow_run_id="run-2")

    assert len(first) == len(rerun) == 2
    assert set(_ids(rerun)) == set(_ids(first))
    assert len(_kept_by_the_consumer(charges.events)) == 4
    assert len(charges.log_rows) == 4


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_first_run_charges_each_evaluation_once(
    call_execution, eval_configs, charges, organization
):
    events = await _evaluate(call_execution, charges, workflow_run_id="run-1")

    assert len(events) == 2
    assert len(_kept_by_the_consumer(events)) == 2
    cost = _EVALUATION_COST + _PER_RUN_FEE
    by_template = {event.properties["source_id"]: event for event in events}
    template_ids = {str(config.eval_template_id) for config in eval_configs}
    assert set(by_template) == template_ids
    for template_id, event in by_template.items():
        assert event.org_id == str(organization.id)
        assert event.event_type == APICallTypeChoices.TURING_LARGE_EVALUATOR.value
        assert event.amount == pytest.approx(cost * 100)
        assert event.properties == {
            "source": "simulate",
            "source_id": template_id,
            "raw_cost_usd": str(cost),
        }
    assert len(charges.log_rows) == 2


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_an_evaluation_whose_workflow_run_is_unknown_is_still_charged(
    call_execution, eval_configs, charges
):
    """Each attempt is charged under fresh ids: an evaluation is never left
    uncharged because its workflow run cannot be read."""
    first = await _evaluate(call_execution, charges, workflow_run_id="")
    again = await _evaluate(call_execution, charges, workflow_run_id="", attempt=2)

    assert len(first) == len(again) == 2
    assert len(_kept_by_the_consumer(charges.events)) == 4
    for event in charges.events:
        assert UUID(event.event_id).version == 4
        assert event.event_type == APICallTypeChoices.TURING_LARGE_EVALUATOR.value
        assert event.amount == pytest.approx((_EVALUATION_COST + _PER_RUN_FEE) * 100)


class _ToolCallJudge:
    """Stands in for ToolEvalAgent: the call made one tool twice, and both
    calls pass."""

    llm = SimpleNamespace(
        model_name="turing_large",
        cost={"total_cost": _TOOL_CALL_COST},
        token_usage={},
    )

    def evaluate_tool_calls(self, _messages):
        return {
            "tool_calls_data": [
                {"tool_name": "lookup_order", "tool_call_id": "tool-call-1"},
                {"tool_name": "lookup_order", "tool_call_id": "tool-call-2"},
            ],
            "conversation_context": "",
        }

    def _evaluate_single_tool_call(self, **_kwargs):
        return {"result": True, "summary": "ok"}


class _ToolCallTranscript:
    """Stands in for ChatToolCallAdapter."""

    def get_tool_call_transcript(self, _call_execution):
        return []


def _chat_call_with_tool_evaluation(call_execution, agent_definition, run_test):
    """Make the call a chat call whose run evaluates tool calls, with no tool
    results yet."""
    AgentDefinition.objects.filter(id=agent_definition.id).update(
        agent_type=AgentDefinition.AgentTypeChoices.TEXT
    )
    RunTest.objects.filter(id=run_test.id).update(enable_tool_evaluation=True)
    CallExecution.objects.filter(id=call_execution.id).update(
        simulation_call_type=CallExecution.SimulationCallType.TEXT, tool_outputs={}
    )


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_tool_call_evaluation_is_keyed_to_its_tool_call(
    call_execution,
    eval_configs,
    charges,
    agent_definition,
    run_test,
    test_execution,
    organization,
    monkeypatch,
):
    """Each tool call is its own event, apart from the call's evaluations in
    the same workflow run. A retry finds the tool results the first attempt
    stored and evaluates nothing."""
    monkeypatch.setattr(
        "ee.agenthub.tool_eval_agent.tool_eval_agent.ToolEvalAgent", _ToolCallJudge
    )
    monkeypatch.setattr(
        "ee.agenthub.tool_eval_agent.adapters.ChatToolCallAdapter",
        _ToolCallTranscript,
    )
    await sync_to_async(_chat_call_with_tool_evaluation)(
        call_execution, agent_definition, run_test
    )

    evaluations = await _evaluate(call_execution, charges, workflow_run_id="run-1")
    logged = len(charges.log_rows)
    first = await _evaluate_tool_calls(call_execution, charges, workflow_run_id="run-1")
    retried = await _evaluate_tool_calls(
        call_execution, charges, workflow_run_id="run-1", attempt=2
    )

    assert len(evaluations) == len(first) == 2
    assert retried == []
    assert len(_kept_by_the_consumer(evaluations + first)) == 4
    for event in first:
        assert UUID(event.event_id).version == 5
        assert event.org_id == str(organization.id)
        assert event.event_type == APICallTypeChoices.TURING_LARGE_EVALUATOR.value
        assert event.amount == pytest.approx(_TOOL_CALL_COST * 100)
        assert event.properties == {
            "source": "simulate_tool_evaluation",
            "source_id": str(test_execution.id),
            "raw_cost_usd": str(_TOOL_CALL_COST),
        }
    assert len(charges.log_rows) - logged == 2


class _EvaluationProbe:
    """Stands in for the evaluation activity: its first attempt in every
    workflow run is lost, and each attempt records the run it is in."""

    def __init__(self):
        self.seen = []

    @activity.defn(name="probe_simulate_evaluations")
    async def evaluate(self) -> None:
        info = activity.info()
        self.seen.append((info.workflow_id, info.workflow_run_id, info.attempt))
        if info.attempt == 1:
            raise RuntimeError("worker lost")


@workflow.defn
class CallProbeWorkflow:
    """Schedules the probe under the retry policy the call workflow gives its
    evaluations."""

    @workflow.run
    async def run(self) -> None:
        await workflow.execute_activity(
            "probe_simulate_evaluations",
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=EVAL_RETRY_POLICY,
        )


@pytest.mark.asyncio
async def test_temporal_keeps_a_retry_in_its_run_and_starts_a_rerun_in_a_new_one(
    temporal_test_server_options,
):
    """What the key rests on. An activity's retry sees the workflow run of its
    first attempt. A workflow started again under the same workflow id, as a
    call that runs again under the id of its row, is a new run."""
    probe = _EvaluationProbe()
    workflow_id = f"call-exec-{uuid4()}"
    task_queue = f"evaluation-run-probe-{uuid4()}"
    async with await WorkflowEnvironment.start_time_skipping(
        **temporal_test_server_options
    ) as env:
        async with Worker(
            env.client,
            task_queue=task_queue,
            workflows=[CallProbeWorkflow],
            activities=[probe.evaluate],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            for _start in range(2):
                await env.client.execute_workflow(
                    CallProbeWorkflow.run,
                    id=workflow_id,
                    task_queue=task_queue,
                    id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE,
                )

    attempts = [(seen_id, attempt) for seen_id, _run, attempt in probe.seen]
    assert attempts == [(workflow_id, 1), (workflow_id, 2)] * 2
    first, first_retried, second, second_retried = (
        run for _seen_id, run, _attempt in probe.seen
    )
    assert first_retried == first
    assert second_retried == second
    assert second != first
