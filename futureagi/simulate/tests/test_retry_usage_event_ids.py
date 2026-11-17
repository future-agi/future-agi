"""A retried call's error localization and call cost are charged once; a rerun
a person starts is charged again.

Error localization. An evaluation of a call whose config has error localization
on makes two localization tasks: the playground task run_eval_func makes on the
evaluation's call log, and the call's own task. The error localizer schedule
localizes each pending task, and each localization emits an error_localizer
usage event. When Temporal runs the evaluation activity again after a lost
worker, the retry makes a new playground task and sets the call's task pending
again, so both are localized again. Their events are keyed to the evaluation
step (the call, the workflow run that evaluates it and the config), so the
consumer, which keeps one event per event id, charges the retry nothing more
than the first attempt.

Call cost. The deduct_call_cost activity of a call's workflow emits the call's
voice_call or text_call usage event, under DB_RETRY_POLICY. An attempt can be
lost after its event reached the stream (a worker that stops, an attempt past
its timeout), and Temporal runs the activity again. The event is keyed to the
call and the workflow run, never the attempt, so the consumer keeps one.

A rerun a person starts is a new workflow run, also when it reuses the call's
row and the workflow's id, so its events are new and are charged. An event
whose workflow run cannot be read gets a fresh event id and is charged.
"""

import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import pytest
from asgiref.sync import sync_to_async
from temporalio import activity, workflow
from temporalio.common import WorkflowIDReusePolicy
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from model_hub.models.choices import OwnerChoices
from model_hub.models.error_localizer_model import (
    ErrorLocalizerStatus,
    ErrorLocalizerTask,
)
from model_hub.models.evals_metric import EvalTemplate
from simulate.models.eval_config import SimulateEvalConfig
from simulate.models.test_execution import CallExecution
from simulate.services.call_rerun import clear_call_for_rerun
from simulate.temporal.retry_policies import DB_RETRY_POLICY, EVAL_RETRY_POLICY
from simulate.temporal.types.activities import DeductCostInput
from simulate.tests.test_eval_resolver_behavior import (  # noqa: F401 - fixtures
    agent_definition as agent_definition,
)
from simulate.tests.test_eval_resolver_behavior import agent_version as agent_version
from simulate.tests.test_eval_resolver_behavior import call_execution as call_execution
from simulate.tests.test_eval_resolver_behavior import (
    chat_call_execution as chat_call_execution,
)
from simulate.tests.test_eval_resolver_behavior import (
    dataset_for_scenario as dataset_for_scenario,
)
from simulate.tests.test_eval_resolver_behavior import run_test as run_test
from simulate.tests.test_eval_resolver_behavior import scenario as scenario
from simulate.tests.test_eval_resolver_behavior import (
    simulator_agent as simulator_agent,
)
from simulate.tests.test_eval_resolver_behavior import test_execution as test_execution
from simulate.tests.test_eval_usage_event_ids import (
    _TRANSCRIPT,
    _activity_info,
    _evaluate,
    _kept_by_the_consumer,
)
from tfc.constants.api_calls import APICallStatusChoices, APICallTypeChoices

pytest_plugins = ["evaluations.tests.usage_step_support"]
pytestmark = pytest.mark.requires_ee

_LOCALIZER = APICallTypeChoices.ERROR_LOCALIZER.value
# What a stand-in localization costs.
_LOCALIZATION_COST = 0.003
# What the stand-in judge scores the call: under its template's pass threshold
# of 0.5, so the evaluation fails and its error localization runs.
_FAILING_SCORE = 0.2
# How long the call a rerun dials again lasts: four started minutes.
_RERUN_SECONDS = 185


def _ids(events):
    return {event.event_id for event in events}


# ============================================================================
# ERROR LOCALIZATION OF A CALL'S EVALUATION
# ============================================================================


@pytest.fixture
def localized_config(run_test, organization, workspace):
    """An evaluation of the run with error localization on. Its template's
    criteria is the rule prompt both localization tasks take."""
    return SimulateEvalConfig.objects.create(
        name="localized-evaluation",
        eval_template=EvalTemplate.no_workspace_objects.create(
            name="localized-evaluation",
            organization=organization,
            workspace=workspace,
            owner=OwnerChoices.USER.value,
            config={"output": "score", "eval_type_id": "DeterministicEvaluator"},
            criteria="The agent tells the customer where the order is.",
            output_type_normalized="percentage",
            pass_threshold=0.5,
        ),
        run_test=run_test,
        mapping={"input": "call.transcript"},
        config={"config": {}, "params": {}},
        error_localizer=True,
    )


@pytest.fixture
def charges(monkeypatch, billing_config):
    """What a call's evaluation and its error localization charge: the usage
    events they emit, the call log rows they write and the localizations that
    run. The judge, the localizer, the usage check and the call log are
    stand-ins; the evaluation activity, run_eval_func, both localization
    triggers and process_single_error_localization run as they are."""
    import model_hub.tasks.user_evaluation as localization_module
    import model_hub.views.utils.evals as evals_module

    seen = SimpleNamespace(events=[], log_rows={}, localizations=[], refunds=[])

    class StandInEvaluator:
        cost = {"total_cost": 0.002}
        token_usage = {}

        def run(self, **_kwargs):
            return SimpleNamespace(
                eval_results=[
                    {
                        "data": {"input": _TRANSCRIPT["transcript"]},
                        "failure": None,
                        "reason": "The agent did not say where the order is.",
                        "runtime": 0.01,
                        "model": "turing_large",
                        "metrics": None,
                        "metadata": {},
                    }
                ]
            )

    class StandInLocalizer:
        """Stands in for the localizer (a Claude Agent SDK session). Each one
        made is a localization that runs; each finds the cause."""

        cost = {"total_cost": _LOCALIZATION_COST}

        def __init__(self, **kwargs):
            seen.localizations.append(kwargs)

        def localize_errors(self):
            return SimpleNamespace(
                analysis={"input": [{"rank": "1", "text": "order 123 shipped"}]},
                selected_key="input",
                skip_reason=None,
            )

    def _call_log(**kwargs):
        """One call log row (an evaluation's or a localization's), as
        log_and_deduct_cost_for_api_request writes it."""
        row = SimpleNamespace(
            log_id=uuid4(),
            organization=kwargs.get("organization"),
            workspace=kwargs.get("workspace"),
            api_call_type=kwargs.get("api_call_type"),
            config=json.dumps({}),
            status=APICallStatusChoices.PROCESSING.value,
            input_token_count=0,
            save=MagicMock(),
        )
        seen.log_rows[row.log_id] = row
        return row

    # The call log table: the localization of a playground task writes its
    # result back to the row of the evaluation's call log.
    call_log_table = SimpleNamespace(
        objects=SimpleNamespace(get=lambda log_id: seen.log_rows[log_id]),
        DoesNotExist=KeyError,
    )

    monkeypatch.setattr(evals_module, "log_and_deduct_cost_for_api_request", _call_log)
    monkeypatch.setattr(
        localization_module, "log_and_deduct_cost_for_api_request", _call_log
    )
    monkeypatch.setattr(
        "ee.usage.utils.usage_entries.log_and_deduct_cost_for_api_request", _call_log
    )
    monkeypatch.setattr(
        localization_module, "refund_cost_for_api_call", seen.refunds.append
    )
    monkeypatch.setattr(localization_module, "APICallLog", call_log_table)
    monkeypatch.setattr(localization_module, "ErrorLocalizer", StandInLocalizer)
    monkeypatch.setattr(localization_module, "close_old_connections", lambda: None)
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
        lambda *_args, **_kwargs: _FAILING_SCORE,
    )
    monkeypatch.setattr(
        "simulate.temporal.activities.xl._build_transcript_data",
        lambda _call_execution: dict(_TRANSCRIPT),
    )
    return seen


def _localize_pending():
    """What the error localizer schedule (error_localizer_task, every 30
    seconds) does: each pending task is marked running and localized once.
    Gives the tasks it localized."""
    from model_hub.tasks.user_evaluation import process_single_error_localization

    pending = list(
        ErrorLocalizerTask.objects.filter(status=ErrorLocalizerStatus.PENDING)
    )
    for task in pending:
        task.mark_as_running()
        process_single_error_localization._original_func(str(task.id))
    return pending


async def _localize(charges):
    """One pass of the error localizer schedule. Gives the error_localizer
    usage events its localizations emitted."""
    before = len(charges.events)
    await sync_to_async(_localize_pending)()
    return [
        event for event in charges.events[before:] if event.event_type == _LOCALIZER
    ]


def _localization_tasks(organization):
    """The organization's localization tasks: the source of each, by id."""
    return {
        str(task.id): task.source
        for task in ErrorLocalizerTask.objects.filter(organization=organization)
    }


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_retried_evaluation_charges_its_error_localization_once(
    call_execution, localized_config, charges
):
    """The schedule runs every 30 seconds and the evaluation's retry waits at
    least 30 seconds, so the first attempt's tasks are usually localized
    before the retry runs."""
    await _evaluate(call_execution, charges, workflow_run_id="run-1")
    first = await _localize(charges)
    await _evaluate(call_execution, charges, workflow_run_id="run-1", attempt=2)
    retried = await _localize(charges)

    # Both tasks are localized again: the retry's new playground task, and the
    # call's task, which the retry set pending again.
    assert len(first) == len(retried) == 2
    assert _ids(retried) <= _ids(first)
    kept_first = _kept_by_the_consumer(first)
    assert len(_kept_by_the_consumer(first + retried)) == len(kept_first)


def _localize_each_again():
    """What a localization task's own retry does after an attempt lost after
    its emit: the task runs again. Gives the ids of the tasks run again."""
    from model_hub.tasks.user_evaluation import process_single_error_localization

    task_ids = [str(task.id) for task in ErrorLocalizerTask.objects.all()]
    for task_id in task_ids:
        process_single_error_localization._original_func(task_id)
    return task_ids


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_retried_error_localization_task_is_charged_once(
    call_execution, localized_config, charges
):
    """Each localization task emits under the id stored on it when it was
    made, so its own retry emits that id again and the consumer keeps one."""
    await _evaluate(call_execution, charges, workflow_run_id="run-1")
    first = await _localize(charges)
    before = len(charges.events)
    await sync_to_async(_localize_each_again)()
    again = [
        event for event in charges.events[before:] if event.event_type == _LOCALIZER
    ]

    assert len(again) == len(first) == 2
    assert _ids(again) == _ids(first)
    kept_first = _kept_by_the_consumer(first)
    assert len(_kept_by_the_consumer(first + again)) == len(kept_first)


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_rerun_a_person_starts_charges_its_error_localization_again(
    call_execution, localized_config, charges
):
    """A whole-run rerun resets the call's row and evaluates it again under
    the same workflow id, in a new workflow run."""
    await _evaluate(call_execution, charges, workflow_run_id="run-1")
    first = await _localize(charges)
    await sync_to_async(clear_call_for_rerun)(call_execution)
    await _evaluate(call_execution, charges, workflow_run_id="run-2")
    rerun = await _localize(charges)

    assert len(first) == len(rerun) == 2
    assert not _ids(rerun) & _ids(first)
    kept_first = _kept_by_the_consumer(first)
    assert len(_kept_by_the_consumer(first + rerun)) == 2 * len(kept_first)


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_first_run_charges_each_error_localization_task_once(
    call_execution, localized_config, charges, organization
):
    """An evaluation with error localization on makes two tasks, the
    playground task run_eval_func makes and the call's own task, and each is
    localized and charged once, with its type, amount and properties."""
    await _evaluate(call_execution, charges, workflow_run_id="run-1")
    events = await _localize(charges)
    tasks = await sync_to_async(_localization_tasks)(organization)

    assert sorted(tasks.values()) == ["playground", "simulate"]
    assert len(events) == len(charges.localizations) == 2
    assert {event.properties["source_id"] for event in events} == set(tasks)
    assert charges.refunds == []
    for event in events:
        assert event.org_id == str(organization.id)
        assert event.event_type == _LOCALIZER
        assert event.amount == pytest.approx(_LOCALIZATION_COST * 100)
        assert event.properties == {
            "source": "error_localizer",
            "source_id": event.properties["source_id"],
            "raw_cost_usd": str(_LOCALIZATION_COST),
        }


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_an_error_localization_whose_workflow_run_is_unknown_is_still_charged(
    call_execution, localized_config, charges
):
    """Each attempt's localizations are charged under fresh ids: a
    localization is never left uncharged because its workflow run cannot be
    read."""
    await _evaluate(call_execution, charges, workflow_run_id="")
    first = await _localize(charges)
    await _evaluate(call_execution, charges, workflow_run_id="", attempt=2)
    again = await _localize(charges)

    assert len(first) == len(again) == 2
    assert len(_kept_by_the_consumer(first + again)) == 4
    for event in first + again:
        assert UUID(event.event_id).version == 4
        assert event.event_type == _LOCALIZER
        assert event.amount == pytest.approx(_LOCALIZATION_COST * 100)


# ============================================================================
# CALL COST
# ============================================================================


class _AttemptLost(RuntimeError):
    """An attempt lost right after its usage event reached the stream: a
    worker that stops, or an attempt past its start-to-close timeout. Temporal
    runs the activity again: DB_RETRY_POLICY retries any error but a
    ValueError and the validation errors it names."""


@pytest.fixture
def call_charges(monkeypatch):
    """The usage events the call cost activity emits. The emitter stands in
    for the usage stream; ``lose_next_attempt`` loses the attempt right after
    its event reached it."""
    seen = SimpleNamespace(events=[], lose_next_attempt=False)

    def _emit(event):
        seen.events.append(event)
        if seen.lose_next_attempt:
            seen.lose_next_attempt = False
            raise _AttemptLost("the attempt was lost after its emit")

    monkeypatch.setattr("ee.usage.services.emitter.emit", _emit)
    monkeypatch.setattr(
        "ee.voice.temporal.activities.voice_large.close_old_connections",
        lambda: None,
    )
    return seen


async def _deduct(call, organization, charges, *, workflow_run_id, attempt=1):
    """One attempt of the call's deduct_call_cost activity, in the workflow run
    named, under the workflow id of the call's row. Gives the usage events the
    attempt emitted."""
    from ee.voice.temporal.activities.voice_large import deduct_call_cost

    before = len(charges.events)
    info = _activity_info(
        "deduct_call_cost", f"call-exec-{call.id}", workflow_run_id, attempt
    )
    with patch("temporalio.activity.info", return_value=info):
        await deduct_call_cost(
            DeductCostInput(call_id=str(call.id), org_id=str(organization.id), cost=0)
        )
    return charges.events[before:]


async def _deduct_and_lose(call, organization, charges, *, workflow_run_id):
    """An attempt of deduct_call_cost that is lost right after its emit. Gives
    the usage events it emitted."""
    before = len(charges.events)
    charges.lose_next_attempt = True
    with pytest.raises(_AttemptLost):
        await _deduct(call, organization, charges, workflow_run_id=workflow_run_id)
    return charges.events[before:]


def _dial_again(call_execution):
    """A rerun a person starts dials the call again: its row is reset and the
    new call's result is stored on it."""
    clear_call_for_rerun(call_execution)
    CallExecution.objects.filter(id=call_execution.id).update(
        status=CallExecution.CallStatus.COMPLETED, duration_seconds=_RERUN_SECONDS
    )


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_call_cost_attempt_lost_after_its_emit_is_charged_once(
    call_execution, organization, call_charges
):
    """Attempt 1 emits the call's cost and is lost; Temporal runs attempt 2 in
    the same workflow run, and it emits the cost again."""
    lost = await _deduct_and_lose(
        call_execution, organization, call_charges, workflow_run_id="run-1"
    )
    retried = await _deduct(
        call_execution, organization, call_charges, workflow_run_id="run-1", attempt=2
    )

    assert len(lost) == len(retried) == 1
    assert retried[0].event_id == lost[0].event_id
    assert len(_kept_by_the_consumer(call_charges.events)) == 1


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_call_a_person_reruns_is_charged_again(
    call_execution, organization, call_charges
):
    """A whole-run rerun dials the call again under the workflow id of its
    row, in a new workflow run; the new call is charged by its own minutes."""
    first = await _deduct(
        call_execution, organization, call_charges, workflow_run_id="run-1"
    )
    await sync_to_async(_dial_again)(call_execution)
    rerun = await _deduct(
        call_execution, organization, call_charges, workflow_run_id="run-2"
    )

    assert len(first) == len(rerun) == 1
    assert rerun[0].event_id != first[0].event_id
    assert rerun[0].amount == 4
    assert len(_kept_by_the_consumer(call_charges.events)) == 2


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_first_voice_call_is_charged_once_by_its_minutes(
    call_execution, organization, call_charges
):
    events = await _deduct(
        call_execution, organization, call_charges, workflow_run_id="run-1"
    )

    assert len(events) == 1
    (event,) = events
    assert event.org_id == str(organization.id)
    assert event.event_type == APICallTypeChoices.VOICE_CALL.value
    # 120 seconds: two started minutes.
    assert event.amount == 2
    assert event.properties == {
        "source": "simulate",
        "source_id": str(call_execution.id),
        "duration_seconds": 120,
    }


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_first_chat_call_is_charged_once(
    chat_call_execution, organization, call_charges
):
    events = await _deduct(
        chat_call_execution, organization, call_charges, workflow_run_id="run-1"
    )

    assert len(events) == 1
    (event,) = events
    assert event.org_id == str(organization.id)
    assert event.event_type == APICallTypeChoices.TEXT_CALL.value
    assert event.amount == 1
    assert event.properties == {
        "source": "simulate",
        "source_id": str(chat_call_execution.id),
    }


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_call_cost_whose_workflow_run_is_unknown_is_still_charged(
    call_execution, organization, call_charges
):
    """Each attempt is charged under a fresh id: a call is never left
    uncharged because its workflow run cannot be read."""
    lost = await _deduct_and_lose(
        call_execution, organization, call_charges, workflow_run_id=""
    )
    again = await _deduct(
        call_execution, organization, call_charges, workflow_run_id="", attempt=2
    )

    assert len(lost) == len(again) == 1
    assert len(_kept_by_the_consumer(call_charges.events)) == 2
    for event in call_charges.events:
        assert UUID(event.event_id).version == 4
        assert event.event_type == APICallTypeChoices.VOICE_CALL.value
        assert event.amount == 2


# ============================================================================
# WHAT THE KEY RESTS ON
# ============================================================================

# The retry policy the call workflow gives each charged activity: the
# evaluation, whose retry makes the localization tasks again, and the call
# cost.
_POLICIES = {"evaluation": EVAL_RETRY_POLICY, "call_cost": DB_RETRY_POLICY}


class _RetryProbe:
    """Stands in for a charged activity: its first attempt in every workflow
    run is lost, and each attempt records the run it is in."""

    def __init__(self):
        self.seen = []

    @activity.defn(name="probe_retried_usage_activity")
    async def attempt(self) -> None:
        info = activity.info()
        self.seen.append((info.workflow_id, info.workflow_run_id, info.attempt))
        if info.attempt == 1:
            raise _AttemptLost("worker lost")


@workflow.defn
class RetryPolicyProbeWorkflow:
    """Schedules the probe under the retry policy the call workflow gives the
    activity it is named for."""

    @workflow.run
    async def run(self, policy: str) -> None:
        await workflow.execute_activity(
            "probe_retried_usage_activity",
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=_POLICIES[policy],
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", sorted(_POLICIES))
async def test_temporal_keeps_a_retry_of_a_charged_activity_in_its_run(
    policy, temporal_test_server_options
):
    """What the key rests on, under each real retry policy. An activity's retry
    sees the workflow run of its first attempt. A workflow started again under
    the same workflow id, as a call that runs again under the id of its row,
    is a new run."""
    probe = _RetryProbe()
    workflow_id = f"call-exec-{uuid4()}"
    task_queue = f"retry-usage-probe-{uuid4()}"
    async with await WorkflowEnvironment.start_time_skipping(
        **temporal_test_server_options
    ) as env:
        async with Worker(
            env.client,
            task_queue=task_queue,
            workflows=[RetryPolicyProbeWorkflow],
            activities=[probe.attempt],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            for _start in range(2):
                await env.client.execute_workflow(
                    RetryPolicyProbeWorkflow.run,
                    policy,
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
