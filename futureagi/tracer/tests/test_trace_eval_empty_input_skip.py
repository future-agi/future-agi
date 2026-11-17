"""A trace with no value for an input the evaluator needs is skipped before any
evaluator call or charge, with one plain sentence naming the trace field and
the input. A trace that has the input, and an evaluator's own failure, read as
they did."""

import uuid

import pytest

# Loads before the tracer imports to break the tracer.utils.eval import cycle.
import model_hub.tasks  # noqa: F401
from model_hub.models.evals_metric import EvalTemplate
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.observation_span import (
    EvalEntryStatus,
    EvalLogger,
    EvalTargetType,
)
from tracer.services.eval_tasks.run_entry import run_entry
from tracer.tests._ch_seed import seed_ch_trace

LOGS_URL = "/tracer/eval-task/get_eval_task_logs/"
NO_OUTPUT = "This trace has no output, which the evaluator needs as 'output'."


@pytest.fixture
def charges(monkeypatch, stub_cost_log):
    """Record each cost log and billing event an evaluation makes, in order."""
    calls = []

    def _cost_log(**kwargs):
        calls.append("cost_log")
        return stub_cost_log(**kwargs)

    monkeypatch.setattr(
        "tracer.utils.eval.log_and_deduct_cost_for_api_request", _cost_log
    )
    monkeypatch.setattr(
        "tracer.utils.eval._emit_eval_billing",
        lambda **kwargs: calls.append("billing"),
    )
    return calls


def _evaluator(project, organization, workspace, mapping, **template_config):
    template = EvalTemplate.objects.create(
        name=f"Answer check {uuid.uuid4().hex[:8]}",
        description="Checks the answer a trace gave",
        organization=organization,
        workspace=workspace,
        config={
            "type": "pass_fail",
            "criteria": "The answer is helpful",
            **template_config,
        },
    )
    return CustomEvalConfig.objects.create(
        name="Answer check",
        project=project,
        eval_template=template,
        config={},
        mapping=mapping,
        filters={},
    )


def _entry_on_trace(trace, anchor, config, task, **trace_fields):
    """Store the trace where the task runner reads it, with ``trace_fields``
    in place of its own values, and claim one entry for it."""
    for name, value in trace_fields.items():
        setattr(trace, name, value)
    seed_ch_trace(trace)
    entry = EvalLogger(
        target_type=EvalTargetType.TRACE,
        trace_id=str(trace.id),
        observation_span_id=anchor.id,
        custom_eval_config=config,
        eval_task_id=str(task.id),
        status=EvalEntryStatus.RUNNING,
    )
    EvalLogger.objects.bulk_create([entry])
    return entry


@pytest.mark.integration
@pytest.mark.django_db
class TestTraceWithoutAnInputIsSkipped:
    def test_trace_with_no_output_is_skipped_with_a_plain_reason(
        self,
        project,
        organization,
        workspace,
        trace,
        observation_span,
        eval_task,
        stub_run_eval,
        charges,
    ):
        config = _evaluator(
            project,
            organization,
            workspace,
            {"output": "output"},
            required_keys=["output"],
        )
        entry = _entry_on_trace(trace, observation_span, config, eval_task, output=None)

        assert run_entry(entry) == EvalEntryStatus.SKIPPED
        entry.refresh_from_db()
        assert entry.status == EvalEntryStatus.SKIPPED
        assert entry.error is False
        assert entry.error_message is None
        assert entry.skipped_reason == NO_OUTPUT
        assert stub_run_eval.requests == []
        assert charges == []

    def test_trace_without_the_mapped_field_is_skipped_with_a_plain_reason(
        self,
        project,
        organization,
        workspace,
        trace,
        observation_span,
        eval_task,
        stub_run_eval,
        charges,
    ):
        config = _evaluator(
            project,
            organization,
            workspace,
            {"output": "metadata.answer"},
            required_keys=["output"],
        )
        entry = _entry_on_trace(trace, observation_span, config, eval_task)

        assert run_entry(entry) == EvalEntryStatus.SKIPPED
        entry.refresh_from_db()
        assert entry.error is False
        assert entry.skipped_reason == (
            "This trace has no metadata.answer, which the evaluator needs as 'output'."
        )
        assert stub_run_eval.requests == []
        assert charges == []

    def test_trace_with_every_input_of_a_custom_evaluator_empty_is_skipped(
        self,
        project,
        organization,
        workspace,
        trace,
        observation_span,
        eval_task,
        stub_run_eval,
        charges,
    ):
        config = _evaluator(
            project,
            organization,
            workspace,
            {"input": "input", "output": "output"},
            custom_eval=True,
            required_keys=["input", "output"],
        )
        entry = _entry_on_trace(
            trace, observation_span, config, eval_task, input=None, output=None
        )

        assert run_entry(entry) == EvalEntryStatus.SKIPPED
        entry.refresh_from_db()
        assert entry.skipped_reason == (
            "This trace has no input and no output, which the evaluator needs "
            "as 'input' and 'output'."
        )
        assert stub_run_eval.requests == []
        assert charges == []

    def test_trace_with_its_output_is_evaluated_and_charged(
        self,
        project,
        organization,
        workspace,
        trace,
        observation_span,
        eval_task,
        stub_run_eval,
        charges,
    ):
        config = _evaluator(
            project,
            organization,
            workspace,
            {"output": "output"},
            required_keys=["output"],
        )
        entry = _entry_on_trace(trace, observation_span, config, eval_task)

        assert run_entry(entry) == EvalEntryStatus.COMPLETED
        entry.refresh_from_db()
        assert entry.error is False
        assert entry.skipped_reason is None
        assert len(stub_run_eval.requests) == 1
        assert charges == ["cost_log", "billing"]

    def test_evaluator_failure_keeps_its_own_text(
        self,
        monkeypatch,
        project,
        organization,
        workspace,
        trace,
        observation_span,
        eval_task,
        charges,
    ):
        def _engine_down(*args, **kwargs):
            raise RuntimeError("engine down")

        monkeypatch.setattr("evaluations.engine.run_eval", _engine_down, raising=False)
        monkeypatch.setattr(
            "evaluations.engine.runner.run_eval", _engine_down, raising=False
        )
        config = _evaluator(
            project,
            organization,
            workspace,
            {"output": "output"},
            required_keys=["output"],
        )
        entry = _entry_on_trace(trace, observation_span, config, eval_task)

        assert run_entry(entry) == EvalEntryStatus.ERRORED
        entry.refresh_from_db()
        assert entry.error is True
        assert entry.error_message == "Error during evaluation: engine down"
        assert entry.skipped_reason is None
        assert "billing" not in charges


def _entry_with_status(task, config, **fields):
    entry = EvalLogger(
        target_type=EvalTargetType.TRACE,
        trace_id=str(uuid.uuid4()),
        observation_span_id=f"root-{uuid.uuid4().hex[:12]}",
        custom_eval_config=config,
        eval_task_id=str(task.id),
        **fields,
    )
    EvalLogger.objects.bulk_create([entry])
    return entry


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
def test_task_logs_say_why_traces_were_skipped(
    auth_client, eval_task, custom_eval_config
):
    no_input = "This trace has no input, which the evaluator needs as 'input'."
    _entry_with_status(
        eval_task,
        custom_eval_config,
        status=EvalEntryStatus.COMPLETED,
        output_bool=True,
    )
    for reason in (NO_OUTPUT, NO_OUTPUT, no_input):
        _entry_with_status(
            eval_task,
            custom_eval_config,
            status=EvalEntryStatus.SKIPPED,
            skipped_reason=reason,
        )
    _entry_with_status(
        eval_task,
        custom_eval_config,
        status=EvalEntryStatus.ERRORED,
        error=True,
        error_message="Error during evaluation: engine down",
    )

    response = auth_client.get(LOGS_URL, {"eval_task_id": str(eval_task.id)})

    assert response.status_code == 200, response.content
    result = response.json()["result"]
    assert result["skipped_count"] == 3
    assert result["errors_count"] == 1
    assert [group["normalized"] for group in result["error_groups"]] == ["engine down"]
    assert result.get("skipped_groups") == [
        {"reason": NO_OUTPUT, "count": 2},
        {"reason": no_input, "count": 1},
    ]
