"""A Prompt Template run is never platform-paid and never pays twice.

RED at 15514f0d:
- A run with no traced rows goes on to its criteria call and loop.
- Saving the results is outside the runner's handler, so a failed save after
  settled calls is re-raised for a Temporal retry, which would pay again.
- pd.DataFrame(data) runs before the handler, so a setup error leaves the run
  RUNNING with the raw error re-raised.
- An empty cached criteria breakdown reaches PromptOptimizer, which then
  reads ``None.dataset`` (agent_task_v2.py), an error re-raised for a retry.

GREEN (tasks/prompt_template_optimizer.py runner_topk_optimizer):
- No rows: refused before any model call (NO_ROWS_MESSAGE).
- Any error after a sent call, the results' save included: FAILED with the
  fixed STOPPED_AFTER_MODEL_CALLS and no retry, even a database error. (r6,
  RED at 973212bf: when marking the run failed raises too, the
  non-retryable error is still raised; the run may go on showing RUNNING.)
- A setup error before the first call: FAILED with the fixed
  COULD_NOT_START_MESSAGE and no retry. An empty criteria breakdown:
  FAILED with CRITERIA_UNAVAILABLE_MESSAGE.
- Only a transient database error (django.db OperationalError or
  InterfaceError) before any call is left to retry. That test is a guard and
  passes before and after.
- The loop's rows are sent from PromptOptimizer's own worker threads, each
  call held and settled. That test is a guard on the real threaded path.

r7, RED at f55fdaf1 (get_topk_prompts, retried by the drop-in runner up to
three times):
- A retry after a lost worker reads the traced rows again and runs the whole
  loop again, each call held and settled a second time: nothing records that
  an earlier attempt of the same execution had started its model calls.
- Everything before runner_topk_optimizer (the ClickHouse read, the node walk,
  the chat flattening, the metric read) is outside any handler, so an error
  there is re-raised raw for a retry and leaves the run RUNNING.

GREEN (r7, as amended in r7b):
- Immediately before the run's first model call (the criteria call, else,
  with stored criteria, the loop's first call once PromptOptimizer is built)
  the runner commits a marker in the run's optimizer_config. It is always
  written: with the Temporal workflow id and run id when an activity's can be
  read, else with none. It goes on the instance saved last, so a completed
  run keeps it. A failed write ends the run before any call
  (COULD_NOT_START_MESSAGE), or is left to retry if it is transient.
- get_topk_prompts first loads the run. If it carries the marker at all,
  whatever ids it holds, it is never run again and no read, hold or call is
  made: RUNNING is FAILED with STOPPED_AFTER_MODEL_CALLS; COMPLETED returns
  and keeps its results; FAILED (or any other status) is left as it is; all
  but COMPLETED raise the non-retryable error. No marker: the run goes on.
- An error before the runner: FAILED with COULD_NOT_START_MESSAGE and no
  retry. A transient database error is still left to retry. The no-marker and
  the transient-read tests are guards and pass before and after.
- The identity helper, unpatched, reads a real activity context's ids across
  the drop-in's thread hop (otel_sync_to_async), and none outside one.

Doubles as in test_prompt_template_optimizer_loop_metered.py, except that the
worker-thread test keeps PromptOptimizer's real ThreadPoolExecutor. In the r7
tests the traced rows come from a stand-in for the shared ClickHouse client
and from real conversation nodes. Where a test needs readable ids, the task
module's one identity helper (_activity_execution) is patched; the probe test
runs it unpatched in temporalio's ActivityEnvironment. No test reaches a
provider, RED or GREEN.
"""

import asyncio
import threading
import uuid
from dataclasses import replace
from decimal import Decimal
from types import MappingProxyType
from unittest.mock import patch

import pandas as pd
import pytest
from django.db import OperationalError
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from agentic_eval.core.llm import platform_model
from ee.agenthub.prompt_optimizer_agent import agent_task_v2
from model_hub.models.conversations import Conversation, Message, Node
from model_hub.models.optimize_dataset import OptimizeDataset
from model_hub.services import credit_ledger_port as credit_ledger
from model_hub.services import model_route
from model_hub.tasks import prompt_template_optimizer as template_task
from model_hub.tests.test_model_route_support import (
    OPENAI_CHAT_MODEL,
    FakeGatewayClient,
    eligible_port,
)
from model_hub.tests.test_prompt_template_optimization_runs import (
    CRITERIA,
    CRITERIA_ANSWER,
    OPTIMISED,
    TEMPLATE,
    _ai_model,
    _metric,
    _run,
)
from model_hub.tests.test_prompt_template_optimizer_loop_metered import (
    CRITERIA_UNAVAILABLE,
    RUNNER_DATA,
    CallingOptimizer,
    _ops,
    _priced,
    _stored_criteria_run,
)
from model_hub.tests.test_prompt_template_optimizer_one_route import (
    COULD_NOT_START,
    STOPPED_AFTER_MODEL_CALLS,
)
from tfc.telemetry import otel_sync_to_async

pytestmark = pytest.mark.usefixtures("urlconf_loaded_without_workspace_context")

NO_ROWS = (
    "The optimization found no traced rows for its model, version, environment "
    "and dates, so it did not run."
)
SAVE_ERROR = "results table unavailable"
MARK_ERROR = "run table unavailable"
MARKER_WRITE_ERROR = "optimizer config unavailable"
READ_ERROR = "events table unavailable"
READ_LOST = "events connection lost"
KEPT = "Answer {{question}} as the first attempt found it."
# The marker as the run stores it: one key in optimizer_config, naming the
# Temporal execution (written out here, so this file imports at f55fdaf1).
MARKER_KEY = "prompt_template_model_calls_started"
EXECUTION = {
    "workflow_id": "task-get_topk_prompts-0a1b2c3d",
    "workflow_run_id": "run-b",
}
# The same workflow id with another run id: another execution.
OTHER_EXECUTION = {
    "workflow_id": "task-get_topk_prompts-0a1b2c3d",
    "workflow_run_id": "run-a",
}
# The marker a direct call writes: no activity, so no ids to read.
NO_IDS = {"workflow_id": None, "workflow_run_id": None}
# The ids the identity probe gives its activity context.
PROBE = {
    "workflow_id": "task-get_topk_prompts-probe001",
    "workflow_run_id": "probe-run-1",
}


@pytest.fixture
def managed(monkeypatch):
    """As in test_prompt_template_optimizer_loop_metered.py: no gateway port
    until a test installs one, the meter's safety state reset, the platform
    features on the OpenAI chat model the gateway doubles price, a fixed hold
    estimate and no ClickHouse usage log."""
    credit_ledger.reset_meter_safety()
    models = {
        feature: (
            replace(
                config,
                provider="openai",
                model_name=OPENAI_CHAT_MODEL,
                vertex_location=None,
            ),
            temperature,
        )
        for feature, (config, temperature) in platform_model.PLATFORM_MODELS.items()
    }
    monkeypatch.setattr(platform_model, "PLATFORM_MODELS", MappingProxyType(models))
    monkeypatch.setattr(
        credit_ledger, "estimate_call_cost", lambda *args, **kwargs: Decimal("0.10")
    )
    with (
        patch("agentic_eval.core.llm.llm.log_to_clickhouse"),
        model_route.managed_routing_port(None),
    ):
        yield model_route
    credit_ledger.reset_meter_safety()


class ThreadRecordingLedger(credit_ledger.MockCreditLedger):
    """The in-memory ledger, also recording which thread placed each hold."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.hold_threads = []

    def hold(self, **kwargs):
        self.hold_threads.append(threading.get_ident())
        return super().hold(**kwargs)


class TracedEvents:
    """Stands in for the shared ClickHouse client: records each read and
    answers it with the given rows, or raises the given error."""

    def __init__(self, rows=(), error=None):
        self.rows = list(rows)
        self.error = error
        self.reads = []

    def __call__(self):
        return self

    def execute_read(self, query, params=None, **kwargs):
        self.reads.append((query, params))
        if self.error is not None:
            raise self.error
        return list(self.rows)


def _traced_rows(organization, workspace, *, rag_info=True):
    """One traced conversation, a user turn and its answer, as real nodes, and
    the events row ClickHouse answers for it (the asked node's id, then the
    tag keys and values). Without rag_info the user turn is malformed:
    get_topk_prompts reads content["rag_info"] of every turn."""
    conversation = Conversation.no_workspace_objects.create(
        title=f"Traced conversation {uuid.uuid4()}",
        organization=organization,
        workspace=workspace,
    )
    question = {"parts": ["What is the refund window?"]}
    if rag_info:
        question["rag_info"] = {
            "prompt_template": TEMPLATE,
            "variables": {"question": "What is the refund window?"},
        }
    asked = Node.objects.create(
        conversation=conversation,
        message=Message.no_workspace_objects.create(
            author={"role": "user"}, content=question
        ),
    )
    Node.objects.create(
        conversation=conversation,
        parent_node=asked,
        message=Message.no_workspace_objects.create(
            author={"role": "assistant"},
            content={"parts": ["Thirty days."], "rag_info": {}},
        ),
    )
    return [(str(asked.id), [], [])]


def _attempt_of(monkeypatch, execution):
    """Run what follows as an attempt of ``execution``, through the task
    module's one identity helper. raising=False: f55fdaf1 has no such helper,
    so the RED there is its old behaviour, not a missing name."""
    monkeypatch.setattr(
        template_task,
        "_activity_execution",
        lambda: dict(execution),
        raising=False,
    )


def _topk(run):
    """get_topk_prompts for this run as the view schedules it, without the
    drop-in wrapper's connection handling (as
    test_optimize_dataset_query_parameters.py calls it)."""
    return template_task.get_topk_prompts._original_func(
        str(run.model_id),
        run.environment,
        run.version,
        [str(metric_id) for metric_id in run.metrics.values_list("id", flat=True)],
        run.id,
        "2026-01-01",
        "2026-01-02",
    )


def _stored(run):
    """Every stored column of the run, to show a retry wrote nothing."""
    return OptimizeDataset.no_workspace_objects.filter(id=run.id).values().get()


# 2. no rows -------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_a_run_with_no_rows_is_refused_before_any_call(
    organization, workspace, managed, monkeypatch
):
    gateway = FakeGatewayClient(response=_priced(CRITERIA_ANSWER))
    port = eligible_port(managed, organization, {OPENAI_CHAT_MODEL}, client=gateway)
    ledger = credit_ledger.MockCreditLedger(default_balance=Decimal("1"))
    optimizer = CallingOptimizer(calls=1)
    monkeypatch.setattr(template_task, "PromptOptimizer", optimizer)
    ai_model = _ai_model(organization, workspace)
    run = _run(organization, workspace, ai_model, _metric(ai_model))
    no_rows = {key: [] for key in RUNNER_DATA}

    with credit_ledger.use_credit_ledger(ledger), managed.managed_routing_port(port):
        with pytest.raises(ApplicationError) as refused:
            template_task.runner_topk_optimizer(no_rows, [], run.id)

    assert refused.value.message == NO_ROWS
    assert refused.value.non_retryable is True
    assert gateway.calls == []
    assert ledger.calls == []
    assert port.eligibility_calls == []
    assert port.authorize_calls == []
    assert optimizer.built == []
    run.refresh_from_db()
    assert run.status == run.StatusType.FAILED
    assert run.error_message == NO_ROWS
    assert run.criteria_breakdown == []
    assert run.optimized_k_prompts is None


# 3. after a sent call, the save included ------------------------------------


@pytest.mark.django_db(transaction=True)
def test_a_save_error_after_sent_calls_fails_the_run_without_a_retry(
    organization, workspace, managed, monkeypatch
):
    gateway = FakeGatewayClient(response=_priced(OPTIMISED))
    port = eligible_port(managed, organization, {OPENAI_CHAT_MODEL}, client=gateway)
    ledger = credit_ledger.MockCreditLedger(default_balance=Decimal("1"))
    optimizer = CallingOptimizer(calls=2)
    monkeypatch.setattr(template_task, "PromptOptimizer", optimizer)
    run = _stored_criteria_run(organization, workspace)
    real_save = OptimizeDataset.save

    def save(self, *args, **kwargs):
        # Saving the results fails, even with a transient database error;
        # marking the run failed (a save of named fields) still works.
        if kwargs.get("update_fields") is None and self.optimized_k_prompts:
            raise OperationalError(SAVE_ERROR)
        return real_save(self, *args, **kwargs)

    monkeypatch.setattr(OptimizeDataset, "save", save)

    with credit_ledger.use_credit_ledger(ledger), managed.managed_routing_port(port):
        with pytest.raises(ApplicationError) as failed:
            template_task.runner_topk_optimizer(RUNNER_DATA, [], run.id)

    assert failed.value.message == STOPPED_AFTER_MODEL_CALLS
    assert SAVE_ERROR not in failed.value.message
    assert failed.value.non_retryable is True
    # The two loop calls, each held and settled; no hold after the save.
    assert _ops(ledger) == ["hold", "settle"] * 2
    assert len(gateway.calls) == 2
    run.refresh_from_db()
    assert run.status == run.StatusType.FAILED
    assert run.error_message == STOPPED_AFTER_MODEL_CALLS
    assert SAVE_ERROR not in run.error_message
    assert run.optimized_k_prompts is None


@pytest.mark.django_db(transaction=True)
def test_a_failed_mark_after_sent_calls_still_ends_the_run_without_a_retry(
    organization, workspace, managed, monkeypatch
):
    gateway = FakeGatewayClient(response=_priced(OPTIMISED))
    port = eligible_port(managed, organization, {OPENAI_CHAT_MODEL}, client=gateway)
    ledger = credit_ledger.MockCreditLedger(default_balance=Decimal("1"))
    optimizer = CallingOptimizer(calls=2)
    monkeypatch.setattr(template_task, "PromptOptimizer", optimizer)
    run = _stored_criteria_run(organization, workspace)
    real_save = OptimizeDataset.save

    def save(self, *args, **kwargs):
        # Saving the results fails, as in the test above.
        if kwargs.get("update_fields") is None and self.optimized_k_prompts:
            raise OperationalError(SAVE_ERROR)
        return real_save(self, *args, **kwargs)

    def mark_as_failed(self, error_message=None):
        # Marking the run failed fails too.
        raise OperationalError(MARK_ERROR)

    monkeypatch.setattr(OptimizeDataset, "save", save)
    monkeypatch.setattr(OptimizeDataset, "mark_as_failed", mark_as_failed)

    with credit_ledger.use_credit_ledger(ledger), managed.managed_routing_port(port):
        with pytest.raises(ApplicationError) as failed:
            template_task.runner_topk_optimizer(RUNNER_DATA, [], run.id)

    # The non-retryable error is still raised, so Temporal never retries after
    # the sent calls; the run left showing RUNNING is a display fault only.
    assert failed.value.message == STOPPED_AFTER_MODEL_CALLS
    assert failed.value.non_retryable is True
    assert MARK_ERROR not in failed.value.message
    assert _ops(ledger) == ["hold", "settle"] * 2
    assert len(gateway.calls) == 2
    run.refresh_from_db()
    assert run.status == run.StatusType.RUNNING
    assert run.optimized_k_prompts is None


# 4. errors before the first call ----------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_a_setup_error_before_any_call_fails_the_run_with_a_fixed_reason(
    organization, workspace, managed, monkeypatch
):
    gateway = FakeGatewayClient(response=_priced(CRITERIA_ANSWER))
    port = eligible_port(managed, organization, {OPENAI_CHAT_MODEL}, client=gateway)
    ledger = credit_ledger.MockCreditLedger(default_balance=Decimal("1"))
    optimizer = CallingOptimizer(calls=1)
    monkeypatch.setattr(template_task, "PromptOptimizer", optimizer)
    ai_model = _ai_model(organization, workspace)
    run = _run(organization, workspace, ai_model, _metric(ai_model))
    # Columns of different lengths: pd.DataFrame refuses to build the rows.
    uneven = {"user_chat": ["What is the refund window?", "And returns?"]}
    uneven["model_chat"] = ["Thirty days."]

    with credit_ledger.use_credit_ledger(ledger), managed.managed_routing_port(port):
        with pytest.raises(ApplicationError) as failed:
            template_task.runner_topk_optimizer(uneven, [], run.id)

    assert failed.value.message == COULD_NOT_START
    assert "same length" not in failed.value.message
    assert failed.value.non_retryable is True
    assert gateway.calls == []
    assert ledger.calls == []
    assert optimizer.built == []
    run.refresh_from_db()
    assert run.status == run.StatusType.FAILED
    assert run.error_message == COULD_NOT_START


@pytest.mark.django_db(transaction=True)
def test_an_empty_cached_criteria_breakdown_fails_the_run_before_the_loop(
    organization, workspace, managed, monkeypatch
):
    gateway = FakeGatewayClient(response=_priced(OPTIMISED))
    port = eligible_port(managed, organization, {OPENAI_CHAT_MODEL}, client=gateway)
    ledger = credit_ledger.MockCreditLedger(default_balance=Decimal("1"))
    criteria_calls = []

    def cached_empty(text, **kwargs):
        # The criteria helper's cache answers an empty breakdown, no call.
        criteria_calls.append(kwargs)
        return []

    monkeypatch.setattr(template_task, "create_criteria_text_prompt", cached_empty)
    ai_model = _ai_model(organization, workspace)
    run = _run(organization, workspace, ai_model, _metric(ai_model))

    with (
        credit_ledger.use_credit_ledger(ledger),
        managed.managed_routing_port(port),
        patch.object(agent_task_v2, "LLM") as own_client,
    ):
        with pytest.raises(ApplicationError) as failed:
            template_task.runner_topk_optimizer(RUNNER_DATA, [], run.id)

    own_client.assert_not_called()
    assert len(criteria_calls) == 1
    assert failed.value.message == CRITERIA_UNAVAILABLE
    assert failed.value.non_retryable is True
    assert gateway.calls == []
    assert ledger.calls == []
    run.refresh_from_db()
    assert run.status == run.StatusType.FAILED
    assert run.error_message == CRITERIA_UNAVAILABLE


@pytest.mark.django_db(transaction=True)
def test_a_transient_database_error_before_any_call_is_left_to_retry(
    organization, workspace, managed, monkeypatch
):
    gateway = FakeGatewayClient(response=_priced(CRITERIA_ANSWER))
    port = eligible_port(managed, organization, {OPENAI_CHAT_MODEL}, client=gateway)
    ledger = credit_ledger.MockCreditLedger(default_balance=Decimal("1"))
    ai_model = _ai_model(organization, workspace)
    run = _run(organization, workspace, ai_model, _metric(ai_model))

    def connection_lost(*args):
        raise OperationalError("database connection lost")

    monkeypatch.setattr(template_task, "_run_client", connection_lost)

    with credit_ledger.use_credit_ledger(ledger), managed.managed_routing_port(port):
        with pytest.raises(OperationalError, match="database connection lost"):
            template_task.runner_topk_optimizer(RUNNER_DATA, [], run.id)

    assert gateway.calls == []
    assert ledger.calls == []
    run.refresh_from_db()
    assert run.status == run.StatusType.RUNNING
    assert run.error_message is None


# 6. the real worker-thread path ----------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_the_loops_rows_are_held_and_settled_from_its_worker_threads(
    organization, workspace, managed
):
    gateway = FakeGatewayClient(response=_priced(OPTIMISED))
    port = eligible_port(managed, organization, {OPENAI_CHAT_MODEL}, client=gateway)
    ledger = ThreadRecordingLedger(default_balance=Decimal("2"))

    with (
        credit_ledger.use_credit_ledger(ledger),
        managed.managed_routing_port(port),
        patch.object(agent_task_v2, "LLM") as own_client,
        patch.object(
            agent_task_v2.PromptOptimizer,
            "_calculate_score",
            side_effect=[0.0, 0.0, 1.0],
        ),
        patch.object(
            agent_task_v2.PromptOptimizer,
            "_get_evaluation_feedback",
            return_value=("", 0.0),
        ),
    ):
        run_client = template_task._run_client(organization, workspace)
        optimizer = agent_task_v2.PromptOptimizer(
            prompt=TEMPLATE,
            train_data=pd.DataFrame(RUNNER_DATA),
            criteria_breakdown=CRITERIA,
            client=run_client,
        )
        result = optimizer.get_optimized_prompt(k=1)

    own_client.assert_not_called()
    assert result == [
        {"iteration_num": 1, "train_score": 1.0, "instruction_template": OPTIMISED}
    ]
    assert _ops(ledger) == ["hold", "settle"] * 8
    assert {hold.purpose for hold in ledger.holds.values()} == {managed.OP_RUN_PROMPT}
    assert all(hold.state == "settled" for hold in ledger.holds.values())
    assert run_client.settled == 8
    # Each mini-batch's one row is sent from PromptOptimizer's worker thread;
    # the gradient steps and follow-ups from the loop's own thread.
    test_thread = threading.get_ident()
    worker_holds = [ident for ident in ledger.hold_threads if ident != test_thread]
    assert len(worker_holds) == 2
    assert len(gateway.calls) == 8


# 7. a retry after the model calls had started (r7) ---------------------------


@pytest.mark.django_db(transaction=True)
def test_a_retry_of_an_execution_whose_calls_started_makes_no_read_hold_or_call(
    organization, workspace, managed, monkeypatch
):
    gateway = FakeGatewayClient(response=_priced(OPTIMISED))
    port = eligible_port(managed, organization, {OPENAI_CHAT_MODEL}, client=gateway)
    ledger = credit_ledger.MockCreditLedger(default_balance=Decimal("1"))
    optimizer = CallingOptimizer(calls=2)
    monkeypatch.setattr(template_task, "PromptOptimizer", optimizer)
    events = TracedEvents(rows=_traced_rows(organization, workspace))
    monkeypatch.setattr(template_task, "ClickHouseClientSingleton", events)
    run = _stored_criteria_run(organization, workspace)
    # Attempt 1 committed its marker, then its worker was lost mid-loop: no
    # handler ran and the run still shows RUNNING. The retry's own ids play no
    # part (no identity patch): the marker's presence stops it.
    run.optimizer_config = {MARKER_KEY: dict(EXECUTION)}
    run.save(update_fields=["optimizer_config"])

    with credit_ledger.use_credit_ledger(ledger), managed.managed_routing_port(port):
        with pytest.raises(ApplicationError) as stopped:
            _topk(run)

    assert stopped.value.message == STOPPED_AFTER_MODEL_CALLS
    assert stopped.value.type == "FailedAfterModelCalls"
    assert stopped.value.non_retryable is True
    assert events.reads == []
    assert ledger.calls == []
    assert gateway.calls == []
    assert port.authorize_calls == []
    assert optimizer.built == []
    run.refresh_from_db()
    assert run.status == run.StatusType.FAILED
    assert run.error_message == STOPPED_AFTER_MODEL_CALLS
    assert run.optimized_k_prompts is None


@pytest.mark.django_db(transaction=True)
def test_a_retry_of_a_completed_execution_keeps_its_results_and_makes_no_call(
    organization, workspace, managed, monkeypatch
):
    gateway = FakeGatewayClient(response=_priced(OPTIMISED))
    port = eligible_port(managed, organization, {OPENAI_CHAT_MODEL}, client=gateway)
    ledger = credit_ledger.MockCreditLedger(default_balance=Decimal("1"))
    optimizer = CallingOptimizer(calls=2)
    monkeypatch.setattr(template_task, "PromptOptimizer", optimizer)
    events = TracedEvents(rows=_traced_rows(organization, workspace))
    monkeypatch.setattr(template_task, "ClickHouseClientSingleton", events)
    run = _stored_criteria_run(organization, workspace)
    # Attempt 1 saved its results, then its worker was lost before Temporal
    # heard that it had completed.
    run.optimizer_config = {MARKER_KEY: dict(EXECUTION)}
    run.optimized_k_prompts = [KEPT]
    run.status = OptimizeDataset.StatusType.COMPLETED
    run.save()
    before = _stored(run)

    with credit_ledger.use_credit_ledger(ledger), managed.managed_routing_port(port):
        result = _topk(run)

    assert events.reads == []
    assert ledger.calls == []
    assert gateway.calls == []
    assert port.authorize_calls == []
    assert optimizer.built == []
    assert result is None
    assert _stored(run) == before
    assert before["status"] == OptimizeDataset.StatusType.COMPLETED
    assert before["optimized_k_prompts"] == [KEPT]


@pytest.mark.django_db(transaction=True)
def test_a_retry_of_a_failed_execution_keeps_its_reason_and_makes_no_call(
    organization, workspace, managed, monkeypatch
):
    gateway = FakeGatewayClient(response=_priced(OPTIMISED))
    port = eligible_port(managed, organization, {OPENAI_CHAT_MODEL}, client=gateway)
    ledger = credit_ledger.MockCreditLedger(default_balance=Decimal("1"))
    optimizer = CallingOptimizer(calls=2)
    monkeypatch.setattr(template_task, "PromptOptimizer", optimizer)
    events = TracedEvents(rows=_traced_rows(organization, workspace))
    monkeypatch.setattr(template_task, "ClickHouseClientSingleton", events)
    run = _stored_criteria_run(organization, workspace)
    earlier_reason = credit_ledger.CreditsExhaustedError().message
    # Attempt 1 was refused mid-loop and marked the run failed, then its
    # worker was lost before Temporal heard.
    run.optimizer_config = {MARKER_KEY: dict(EXECUTION)}
    run.status = OptimizeDataset.StatusType.FAILED
    run.error_message = earlier_reason
    run.save()
    before = _stored(run)

    with credit_ledger.use_credit_ledger(ledger), managed.managed_routing_port(port):
        with pytest.raises(ApplicationError) as stopped:
            _topk(run)

    assert stopped.value.message == STOPPED_AFTER_MODEL_CALLS
    assert stopped.value.type == "FailedAfterModelCalls"
    assert stopped.value.non_retryable is True
    assert events.reads == []
    assert ledger.calls == []
    assert gateway.calls == []
    assert port.authorize_calls == []
    assert optimizer.built == []
    assert _stored(run) == before
    assert before["status"] == OptimizeDataset.StatusType.FAILED
    assert before["error_message"] == earlier_reason


@pytest.mark.django_db(transaction=True)
def test_a_retry_with_no_marker_runs_as_a_first_attempt(
    organization, workspace, managed, monkeypatch
):
    gateway = FakeGatewayClient(response=_priced(OPTIMISED))
    port = eligible_port(managed, organization, {OPENAI_CHAT_MODEL}, client=gateway)
    ledger = credit_ledger.MockCreditLedger(default_balance=Decimal("1"))
    optimizer = CallingOptimizer(calls=2)
    monkeypatch.setattr(template_task, "PromptOptimizer", optimizer)
    events = TracedEvents(rows=_traced_rows(organization, workspace))
    monkeypatch.setattr(template_task, "ClickHouseClientSingleton", events)
    run = _stored_criteria_run(organization, workspace)
    # Attempt 1 was lost before its marker: nothing was sent, so nothing paid.
    _attempt_of(monkeypatch, EXECUTION)

    with credit_ledger.use_credit_ledger(ledger), managed.managed_routing_port(port):
        _topk(run)

    assert len(events.reads) == 1
    (built,) = optimizer.built
    assert list(built["train_data"]["user_chat"]) == ["What is the refund window?"]
    assert _ops(ledger) == ["hold", "settle"] * 2
    assert len(gateway.calls) == 2
    run.refresh_from_db()
    assert run.status == run.StatusType.COMPLETED
    assert run.optimized_k_prompts == [OPTIMISED]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "marked_by", [OTHER_EXECUTION, NO_IDS], ids=["other-ids", "no-ids"]
)
def test_a_marker_holding_other_ids_still_stops_the_retry(
    organization, workspace, managed, monkeypatch, marked_by
):
    gateway = FakeGatewayClient(response=_priced(OPTIMISED))
    port = eligible_port(managed, organization, {OPENAI_CHAT_MODEL}, client=gateway)
    ledger = credit_ledger.MockCreditLedger(default_balance=Decimal("1"))
    optimizer = CallingOptimizer(calls=2)
    monkeypatch.setattr(template_task, "PromptOptimizer", optimizer)
    events = TracedEvents(rows=_traced_rows(organization, workspace))
    monkeypatch.setattr(template_task, "ClickHouseClientSingleton", events)
    run = _stored_criteria_run(organization, workspace)
    # The marker names other ids than this attempt's, or none at all: its
    # presence alone says model calls were started for this row.
    run.optimizer_config = {MARKER_KEY: dict(marked_by)}
    run.save(update_fields=["optimizer_config"])
    _attempt_of(monkeypatch, EXECUTION)

    with credit_ledger.use_credit_ledger(ledger), managed.managed_routing_port(port):
        with pytest.raises(ApplicationError) as stopped:
            _topk(run)

    assert stopped.value.message == STOPPED_AFTER_MODEL_CALLS
    assert stopped.value.non_retryable is True
    assert events.reads == []
    assert ledger.calls == []
    assert gateway.calls == []
    assert optimizer.built == []
    run.refresh_from_db()
    assert run.status == run.StatusType.FAILED
    assert run.error_message == STOPPED_AFTER_MODEL_CALLS
    assert run.optimizer_config == {MARKER_KEY: marked_by}


@pytest.mark.django_db(transaction=True)
def test_a_first_attempt_commits_its_marker_before_its_first_hold(
    organization, workspace, managed, monkeypatch
):
    gateway = FakeGatewayClient(response=_priced(CRITERIA_ANSWER))
    port = eligible_port(managed, organization, {OPENAI_CHAT_MODEL}, client=gateway)
    ledger = credit_ledger.MockCreditLedger(default_balance=Decimal("1"))
    optimizer = CallingOptimizer(calls=1)
    monkeypatch.setattr(template_task, "PromptOptimizer", optimizer)
    # No stored criteria: the run's first model call is the criteria call.
    ai_model = _ai_model(organization, workspace)
    run = _run(organization, workspace, ai_model, _metric(ai_model))
    real_save = OptimizeDataset.save
    marker_writes = []

    def save(self, *args, **kwargs):
        # Each marker write records the ledger operations made before it and
        # how many optimizers had been built.
        if kwargs.get("update_fields") == ["optimizer_config"]:
            marker_writes.append((_ops(ledger), len(optimizer.built)))
        return real_save(self, *args, **kwargs)

    monkeypatch.setattr(OptimizeDataset, "save", save)
    _attempt_of(monkeypatch, EXECUTION)

    with credit_ledger.use_credit_ledger(ledger), managed.managed_routing_port(port):
        template_task.runner_topk_optimizer(RUNNER_DATA, [], run.id)

    # One marker write, before any ledger operation and before the optimizer
    # is built; then the criteria call and the loop's one call, each held and
    # settled.
    assert marker_writes == [([], 0)]
    assert _ops(ledger) == ["hold", "settle"] * 2
    assert len(gateway.calls) == 2
    run.refresh_from_db()
    assert run.status == run.StatusType.COMPLETED
    assert run.criteria_breakdown == CRITERIA
    # The criteria's full save and the results' save keep the marker.
    assert run.optimizer_config == {MARKER_KEY: EXECUTION}


@pytest.mark.django_db(transaction=True)
def test_with_stored_criteria_the_marker_is_committed_just_before_the_loop(
    organization, workspace, managed, monkeypatch
):
    gateway = FakeGatewayClient(response=_priced(OPTIMISED))
    port = eligible_port(managed, organization, {OPENAI_CHAT_MODEL}, client=gateway)
    ledger = credit_ledger.MockCreditLedger(default_balance=Decimal("1"))
    optimizer = CallingOptimizer(calls=2)
    monkeypatch.setattr(template_task, "PromptOptimizer", optimizer)
    # Stored criteria: no criteria call, so the loop sends the first call.
    run = _stored_criteria_run(organization, workspace)
    real_save = OptimizeDataset.save
    marker_writes = []

    def save(self, *args, **kwargs):
        # As above: the ledger operations and the optimizers built so far.
        if kwargs.get("update_fields") == ["optimizer_config"]:
            marker_writes.append((_ops(ledger), len(optimizer.built)))
        return real_save(self, *args, **kwargs)

    monkeypatch.setattr(OptimizeDataset, "save", save)
    _attempt_of(monkeypatch, EXECUTION)

    with credit_ledger.use_credit_ledger(ledger), managed.managed_routing_port(port):
        template_task.runner_topk_optimizer(RUNNER_DATA, [], run.id)

    # One marker write, after the optimizer is built and before the loop's
    # first hold; then the loop's two calls, each held and settled.
    assert marker_writes == [([], 1)]
    assert _ops(ledger) == ["hold", "settle"] * 2
    assert len(gateway.calls) == 2
    run.refresh_from_db()
    assert run.status == run.StatusType.COMPLETED
    assert run.optimized_k_prompts == [OPTIMISED]
    # The results' full save keeps the marker, so a completed run carries it.
    assert run.optimizer_config == {MARKER_KEY: EXECUTION}


@pytest.mark.django_db(transaction=True)
def test_a_failed_marker_write_ends_the_run_before_any_call(
    organization, workspace, managed, monkeypatch
):
    gateway = FakeGatewayClient(response=_priced(CRITERIA_ANSWER))
    port = eligible_port(managed, organization, {OPENAI_CHAT_MODEL}, client=gateway)
    ledger = credit_ledger.MockCreditLedger(default_balance=Decimal("1"))
    optimizer = CallingOptimizer(calls=1)
    monkeypatch.setattr(template_task, "PromptOptimizer", optimizer)
    ai_model = _ai_model(organization, workspace)
    run = _run(organization, workspace, ai_model, _metric(ai_model))
    real_save = OptimizeDataset.save

    def save(self, *args, **kwargs):
        # The marker write fails with a plain error; marking the run failed
        # (a save of other named fields) still works.
        if kwargs.get("update_fields") == ["optimizer_config"]:
            raise RuntimeError(MARKER_WRITE_ERROR)
        return real_save(self, *args, **kwargs)

    monkeypatch.setattr(OptimizeDataset, "save", save)
    # No identity patch: the marker is written outside an activity too.

    with credit_ledger.use_credit_ledger(ledger), managed.managed_routing_port(port):
        with pytest.raises(ApplicationError) as failed:
            template_task.runner_topk_optimizer(RUNNER_DATA, [], run.id)

    assert failed.value.message == COULD_NOT_START
    assert MARKER_WRITE_ERROR not in failed.value.message
    assert failed.value.non_retryable is True
    assert gateway.calls == []
    assert ledger.calls == []
    assert port.authorize_calls == []
    assert optimizer.built == []
    run.refresh_from_db()
    assert run.status == run.StatusType.FAILED
    assert run.error_message == COULD_NOT_START
    assert run.criteria_breakdown == []
    assert run.optimizer_config == {}


@pytest.mark.django_db(transaction=True)
def test_a_transient_marker_write_error_is_left_to_retry_before_any_call(
    organization, workspace, managed, monkeypatch
):
    gateway = FakeGatewayClient(response=_priced(OPTIMISED))
    port = eligible_port(managed, organization, {OPENAI_CHAT_MODEL}, client=gateway)
    ledger = credit_ledger.MockCreditLedger(default_balance=Decimal("1"))
    optimizer = CallingOptimizer(calls=2)
    monkeypatch.setattr(template_task, "PromptOptimizer", optimizer)
    # Stored criteria: the write is the one on optim_obj, before the loop.
    run = _stored_criteria_run(organization, workspace)
    real_save = OptimizeDataset.save

    def save(self, *args, **kwargs):
        # The marker write loses its database connection.
        if kwargs.get("update_fields") == ["optimizer_config"]:
            raise OperationalError(READ_LOST)
        return real_save(self, *args, **kwargs)

    monkeypatch.setattr(OptimizeDataset, "save", save)

    with credit_ledger.use_credit_ledger(ledger), managed.managed_routing_port(port):
        with pytest.raises(OperationalError, match=READ_LOST):
            template_task.runner_topk_optimizer(RUNNER_DATA, [], run.id)

    # The optimizer was built but never asked to run: no call, no hold.
    assert len(optimizer.built) == 1
    assert gateway.calls == []
    assert ledger.calls == []
    assert port.authorize_calls == []
    run.refresh_from_db()
    assert run.status == run.StatusType.RUNNING
    assert run.error_message is None
    assert run.optimizer_config == {}


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("criteria_stored", [True, False], ids=["stored", "uncached"])
def test_a_completed_run_keeps_its_marker_and_a_retry_then_makes_no_call(
    organization, workspace, managed, monkeypatch, criteria_stored
):
    gateway = FakeGatewayClient(response=_priced(CRITERIA_ANSWER))
    port = eligible_port(managed, organization, {OPENAI_CHAT_MODEL}, client=gateway)
    ledger = credit_ledger.MockCreditLedger(default_balance=Decimal("1"))
    optimizer = CallingOptimizer(calls=1)
    monkeypatch.setattr(template_task, "PromptOptimizer", optimizer)
    events = TracedEvents(rows=_traced_rows(organization, workspace))
    monkeypatch.setattr(template_task, "ClickHouseClientSingleton", events)
    if criteria_stored:
        run = _stored_criteria_run(organization, workspace)
    else:
        ai_model = _ai_model(organization, workspace)
        run = _run(organization, workspace, ai_model, _metric(ai_model))
    # No identity patch: a direct call writes the marker with no ids.

    with credit_ledger.use_credit_ledger(ledger), managed.managed_routing_port(port):
        _topk(run)
        # Every later save kept the marker: the criteria save (uncached), the
        # reload and the results' full save.
        completed = _stored(run)
        assert completed["status"] == OptimizeDataset.StatusType.COMPLETED
        assert completed["optimizer_config"] == {MARKER_KEY: NO_IDS}
        first = (len(events.reads), _ops(ledger), len(gateway.calls))
        # The worker is lost before Temporal hears; the retry runs.
        retried = _topk(run)

    calls = 1 if criteria_stored else 2
    assert first == (1, ["hold", "settle"] * calls, calls)
    assert retried is None
    assert (len(events.reads), _ops(ledger), len(gateway.calls)) == first
    assert len(optimizer.built) == 1
    assert _stored(run) == completed


# 8. errors before the runner (r7) --------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_a_traced_rows_read_error_ends_the_run_with_a_fixed_reason(
    organization, workspace, managed, monkeypatch
):
    gateway = FakeGatewayClient(response=_priced(OPTIMISED))
    port = eligible_port(managed, organization, {OPENAI_CHAT_MODEL}, client=gateway)
    ledger = credit_ledger.MockCreditLedger(default_balance=Decimal("1"))
    optimizer = CallingOptimizer(calls=1)
    monkeypatch.setattr(template_task, "PromptOptimizer", optimizer)
    events = TracedEvents(error=RuntimeError(READ_ERROR))
    monkeypatch.setattr(template_task, "ClickHouseClientSingleton", events)
    run = _stored_criteria_run(organization, workspace)

    with credit_ledger.use_credit_ledger(ledger), managed.managed_routing_port(port):
        with pytest.raises(ApplicationError) as failed:
            _topk(run)

    assert len(events.reads) == 1
    assert failed.value.message == COULD_NOT_START
    assert READ_ERROR not in failed.value.message
    assert failed.value.non_retryable is True
    assert ledger.calls == []
    assert gateway.calls == []
    assert optimizer.built == []
    run.refresh_from_db()
    assert run.status == run.StatusType.FAILED
    assert run.error_message == COULD_NOT_START


@pytest.mark.django_db(transaction=True)
def test_a_malformed_traced_conversation_ends_the_run_with_a_fixed_reason(
    organization, workspace, managed, monkeypatch
):
    gateway = FakeGatewayClient(response=_priced(OPTIMISED))
    port = eligible_port(managed, organization, {OPENAI_CHAT_MODEL}, client=gateway)
    ledger = credit_ledger.MockCreditLedger(default_balance=Decimal("1"))
    optimizer = CallingOptimizer(calls=1)
    monkeypatch.setattr(template_task, "PromptOptimizer", optimizer)
    # The user turn has no rag_info: flattening the chat raises KeyError.
    events = TracedEvents(rows=_traced_rows(organization, workspace, rag_info=False))
    monkeypatch.setattr(template_task, "ClickHouseClientSingleton", events)
    run = _stored_criteria_run(organization, workspace)

    with credit_ledger.use_credit_ledger(ledger), managed.managed_routing_port(port):
        with pytest.raises(ApplicationError) as failed:
            _topk(run)

    assert len(events.reads) == 1
    assert failed.value.message == COULD_NOT_START
    assert "rag_info" not in failed.value.message
    assert failed.value.non_retryable is True
    assert ledger.calls == []
    assert gateway.calls == []
    assert optimizer.built == []
    run.refresh_from_db()
    assert run.status == run.StatusType.FAILED
    assert run.error_message == COULD_NOT_START


@pytest.mark.django_db(transaction=True)
def test_a_transient_database_error_from_the_read_is_left_to_retry(
    organization, workspace, managed, monkeypatch
):
    gateway = FakeGatewayClient(response=_priced(OPTIMISED))
    port = eligible_port(managed, organization, {OPENAI_CHAT_MODEL}, client=gateway)
    ledger = credit_ledger.MockCreditLedger(default_balance=Decimal("1"))
    events = TracedEvents(error=OperationalError(READ_LOST))
    monkeypatch.setattr(template_task, "ClickHouseClientSingleton", events)
    run = _stored_criteria_run(organization, workspace)

    with credit_ledger.use_credit_ledger(ledger), managed.managed_routing_port(port):
        with pytest.raises(OperationalError, match=READ_LOST):
            _topk(run)

    assert len(events.reads) == 1
    assert ledger.calls == []
    assert gateway.calls == []
    run.refresh_from_db()
    assert run.status == run.StatusType.RUNNING
    assert run.error_message is None


# 9. the identity helper in a real activity context (r7b) ---------------------


def test_the_identity_helper_reads_the_activity_across_the_drop_in_thread_hop():
    # The real helper, not patched. f55fdaf1 has none: AttributeError here.
    helper = template_task._activity_execution
    env = ActivityEnvironment()
    env.info = replace(
        env.info,
        workflow_id=PROBE["workflow_id"],
        workflow_run_id=PROBE["workflow_run_id"],
    )
    threads = []

    def in_the_activity_thread():
        threads.append(threading.get_ident())
        return helper()

    async def attempt():
        # The drop-in activity wrapper runs its body exactly so
        # (tfc/temporal/drop_in/decorator.py:250-252).
        return await otel_sync_to_async(
            in_the_activity_thread, thread_sensitive=False
        )()

    seen = asyncio.run(env.run(attempt))

    assert seen == PROBE
    # The helper ran once, in a thread of its own, as the activity body does.
    assert len(threads) == 1
    assert threads[0] != threading.get_ident()
    # Outside an activity there are no ids to read.
    assert helper() is None
