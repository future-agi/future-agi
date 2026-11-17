"""Each place an agent row reads the Stop before something is sent.

Every test lands the Stop at one point, just before the read it pins, so
removing that read lets something be sent (the test turns red):
- before a step's call (the read right before the runner starts);
- while the step renders its prompt (the runner's last hook before sending);
- after the row began but before any step started;
- before an evaluator's charge (the evaluator's second read);
- a row not started when a Stop that did not reach the workflows lands: its
  worker is refused, visibly.
A step that failed before the Stop keeps its own failure as the row's.

The recorder, the evaluator runner and the Stop view are the real ones; only
the provider, the workflow service and the evaluator engine and billing are
stand-ins.
"""

from unittest.mock import patch

import pytest

from model_hub.models.choices import StatusType
from model_hub.models.experiments import ExperimentsTable
from model_hub.services.experiment_run_writes import RunWriteError
from model_hub.tests.test_experiment_stop_default_path import NOT_REACHED, stop
from model_hub.tests.test_experiment_stop_graph_rows import (  # noqa: F401
    STEP_CALL,
    STOPPED,
    graph_world,
    prepare,
    run_control_off,
    run_row,
    stored_run,
    transports,
)
from model_hub.tests.test_native_prompt_create_bridge import (  # noqa: F401
    native_fixture_evaluator,
)
from model_hub.tests.test_native_prompt_create_bridge import tenants as tenants

pytestmark = pytest.mark.django_db(transaction=True)

RENDER = "agent_playground.services.engine.runners.llm_prompt.LLMPromptRunner._render_messages"


def stopped_by(world):
    """The Stop's first effect every read sees: the experiment is Cancelled."""
    ExperimentsTable.no_workspace_objects.filter(pk=world["experiment"].pk).update(
        status=StatusType.CANCELLED.value
    )


def once_then(world, original):
    calls = []

    def wrapper(*args, **kwargs):
        if not calls:
            stopped_by(world)
        calls.append(1)
        return original(*args, **kwargs)

    return wrapper


def sealed_stopped(world):
    item = stored_run(world).items.get(deleted=False)
    nodes = item.metadata["native_graph"]["node_results"].values()
    return item, [node["status"] for node in nodes]


def test_a_stop_just_before_a_steps_call_sends_and_renders_nothing(tenants):
    from agent_playground.services import engine

    world = graph_world(tenants["a"])
    row_id = world["rows"][0]
    prepared = prepare(world, row_id)
    stack, engine_stub, billing = transports()
    with (
        stack,
        patch(STEP_CALL, autospec=True) as step_call,
        patch(RENDER, autospec=True) as render,
        patch.object(
            engine, "store_node_inputs", once_then(world, engine.store_node_inputs)
        ),
    ):
        run_row(world, row_id, prepared)
    render.assert_not_called()
    step_call.assert_not_called()
    item, statuses = sealed_stopped(world)
    assert statuses == ["SKIPPED", "SKIPPED"]
    assert item.error["type"] == "ExecutionStopped"
    assert billing.call_count == 0


def test_a_stop_while_a_step_renders_its_prompt_sends_nothing(tenants):
    from agent_playground.services.engine.runners.llm_prompt import LLMPromptRunner

    world = graph_world(tenants["a"])
    row_id = world["rows"][0]
    prepared = prepare(world, row_id)
    stack, _, billing = transports()
    with (
        stack,
        patch(STEP_CALL, autospec=True) as step_call,
        patch.object(
            LLMPromptRunner,
            "_render_messages",
            once_then(world, LLMPromptRunner._render_messages),
        ),
    ):
        run_row(world, row_id, prepared)
    step_call.assert_not_called()
    item, statuses = sealed_stopped(world)
    assert statuses == ["SKIPPED", "SKIPPED"]
    assert item.error["type"] == "ExecutionStopped"
    assert billing.call_count == 0


def test_a_row_that_began_but_sent_nothing_is_sealed_stopped(tenants):
    from agent_playground.services import engine

    world = graph_world(tenants["a"])
    row_id = world["rows"][0]
    prepared = prepare(world, row_id)
    stack, _, billing = transports()
    with (
        stack,
        patch(STEP_CALL, autospec=True) as step_call,
        patch.object(
            engine, "inject_graph_inputs", once_then(world, engine.inject_graph_inputs)
        ),
    ):
        result = run_row(world, row_id, prepared)
    step_call.assert_not_called()
    assert result["status"] == "FAILED"
    item, statuses = sealed_stopped(world)
    assert statuses == ["SKIPPED", "SKIPPED"]
    assert item.error["type"] == "ExecutionStopped"
    assert all(
        ref["reason"] == "execution_stopped"
        for ref in item.score_refs[0]["terminal_results"]
    )
    assert billing.call_count == 0


def test_a_stop_just_before_an_evaluators_charge_is_never_charged(tenants):
    from model_hub.views.eval_runner import NativeEvaluationRunner

    world = graph_world(tenants["a"])
    row_id = world["rows"][0]
    prepared = prepare(world, row_id)
    stack, engine_stub, billing = transports()
    with (
        stack,
        patch(
            STEP_CALL,
            autospec=True,
            side_effect=lambda provider, *a, **k: ("answer", {}),
        ),
        patch.object(
            NativeEvaluationRunner,
            "_native_result_column",
            once_then(world, NativeEvaluationRunner._native_result_column),
        ),
    ):
        run_row(world, row_id, prepared)
    # The Stop landed after the evaluator's first read: no call, no charge.
    assert engine_stub.run.call_count == 0 and billing.call_count == 0
    item = stored_run(world).items.get(deleted=False)
    assert item.status == "completed"
    assert {ref["reason"] for ref in item.score_refs[0]["terminal_results"]} == {
        "execution_stopped"
    }


def test_after_a_stop_that_reached_no_workflow_a_row_not_started_is_refused(
    tenants,
):
    world = graph_world(tenants["a"])
    row_id = world["rows"][0]
    prepared = prepare(world, row_id)
    stack, _, billing = transports()
    with stack, patch(STEP_CALL, autospec=True) as step_call:
        # The Stop could not reach the workflow service: it ended nothing
        # itself, so this row was not sealed and its worker still comes.
        stop(world["actor"], world["experiment"], NOT_REACHED)
        assert stored_run(world).completed_at is None
        with pytest.raises(RunWriteError, match="not admitted or was stopped"):
            run_row(world, row_id, prepared)
    step_call.assert_not_called()
    assert billing.call_count == 0


def test_a_step_that_failed_before_the_stop_keeps_its_failure(tenants):
    world = graph_world(tenants["a"])
    row_id = world["rows"][0]
    prepared = prepare(world, row_id)

    def failing_step(provider, *args, **kwargs):
        # The step fails; Stop is pressed before the next one.
        stop(world["actor"], world["experiment"])
        raise RuntimeError("provider unavailable")

    stack, _, _ = transports()
    with stack, patch(STEP_CALL, autospec=True, side_effect=failing_step) as step_call:
        run_row(world, row_id, prepared)
    assert step_call.call_count == 1
    item, statuses = sealed_stopped(world)
    assert sorted(statuses) == ["FAILED", "SKIPPED"]
    assert item.status == "failed"
    assert item.error["type"] == "GraphExecutionFailure"
