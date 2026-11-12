"""PostgreSQL integration coverage for Python-task evaluator receipts.

These tests exercise the public run-creation and adapter services together.  In
particular, they keep evaluator identity (metric + pinned version) distinct
from the callback implementation and verify that score receipts cannot be
written for a different binding.
"""

from __future__ import annotations

import uuid
from types import MappingProxyType

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from model_hub.models.dataset_versions import DatasetVersion, DatasetVersionItem
from model_hub.models.develop_dataset import Dataset
from model_hub.models.evals_metric import (
    EvalTemplate,
    EvalTemplateVersion,
    UserEvalMetric,
)
from model_hub.models.experiments import ExperimentRun
from model_hub.services import python_task_adapter
from model_hub.services.experiment_run_creation import create_experiment_run
from model_hub.services.experiment_run_writes import (
    RunWriteError,
    claim_run_item,
    record_run_item_result,
    transition_run,
)
from model_hub.services.python_task_adapter import (
    EvaluatorResult,
    run_python_experiment,
)


def _tracer():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider.get_tracer("python-task-evaluator-integration"), exporter


@pytest.fixture
def bound_run(organization, workspace, user):
    """One item and two bindings that resolve to the same evaluator version."""
    dataset = Dataset.objects.create(
        name="Evaluator adapter dataset",
        organization=organization,
        workspace=workspace,
        user=user,
        column_order=[],
        column_config={},
    )
    version = DatasetVersion.objects.create(dataset=dataset)
    example = DatasetVersionItem.objects.create(
        dataset_version=version,
        input={"question": "capital of France", "nested": {"keep": True}},
        expected={"answer": "Paris"},
        metadata={"case": "evaluator"},
    )
    template = EvalTemplate.objects.create(
        name="adapter-score-template",
        organization=organization,
        workspace=workspace,
        owner="user",
        eval_type="code",
    )
    evaluator_version = EvalTemplateVersion.objects.create(
        eval_template=template,
        version_number=1,
        is_default=True,
        organization=organization,
        workspace=workspace,
        created_by=user,
        config_snapshot={
            "threshold": 0.7,
            "nested": {"immutable": True},
            "labels": ["correct", "incorrect"],
        },
        criteria="match the expected answer",
        model="deterministic",
    )
    first_metric = UserEvalMetric.objects.create(
        name="match-score",
        organization=organization,
        workspace=workspace,
        template=template,
        dataset=dataset,
        user=user,
        config={"binding": {"threshold": 0.7}},
    )
    second_metric = UserEvalMetric.objects.create(
        name="match-score-strict",
        organization=organization,
        workspace=workspace,
        template=template,
        dataset=dataset,
        user=user,
        config={"binding": {"threshold": 0.95}},
    )
    first_metric, second_metric = sorted(
        [first_metric, second_metric], key=lambda metric: str(metric.id)
    )
    run = create_experiment_run(
        run_id=uuid.uuid4(),
        dataset_version=version,
        created_by=user,
        task_kind="external",
        expected_items=[(example.example_id, 1)],
        evaluator_metrics=[first_metric, second_metric],
    )
    return {
        "run": run,
        "example": example,
        "version": version,
        "first_metric": first_metric,
        "second_metric": second_metric,
        "evaluator_version": evaluator_version,
        "scope": {"organization_id": organization.id, "workspace_id": workspace.id},
    }


def _run_parts(bound_run):
    return bound_run["run"], bound_run["example"], bound_run["scope"]


@pytest.mark.django_db(transaction=True)
def test_creation_pins_two_metric_bindings_to_same_version_with_distinct_config(
    bound_run,
):
    run = ExperimentRun.objects.get(id=bound_run["run"].id)
    snapshots = {entry["metric_id"]: entry for entry in run.evaluator_versions}

    assert len(snapshots) == 2
    assert {entry["evaluator_version_id"] for entry in snapshots.values()} == {
        str(bound_run["evaluator_version"].id)
    }
    for key in ("first_metric", "second_metric"):
        metric = bound_run[key]
        assert snapshots[str(metric.id)]["binding_config"] == metric.config
    assert (
        snapshots[str(bound_run["first_metric"].id)]["binding_config"]
        != (snapshots[str(bound_run["second_metric"].id)]["binding_config"])
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "status,value",
    [
        ("assessed", 0.82),
        ("unknown", None),
        ("not_assessed", None),
        ("unsupported", None),
        ("error", None),
    ],
)
def test_explicit_evaluator_statuses_are_persisted_without_fake_scores(
    bound_run, status, value
):
    run, example, scope = _run_parts(bound_run)
    metric_id = str(bound_run["first_metric"].id)
    tracer, _ = _tracer()

    def evaluator(_, context):
        assert context.metric_id == bound_run["first_metric"].id
        return EvaluatorResult(status=status, value=value, explanation="fixture status")

    result = run_python_experiment(
        run_id=run.id,
        task=lambda value, _: {"answer": value["question"]},
        evaluators={metric_id: evaluator},
        tracer=tracer,
        **scope,
    )
    item = result.items.get(example_id=example.example_id, trial_number=1)
    assert result.status == "completed"
    assert item.status == "completed"
    assert item.error is None
    refs = {ref["metric_id"]: ref for ref in item.score_refs}
    ref = refs[metric_id]
    assert ref["status"] == status
    assert ref["explanation"] == "fixture status"
    if status == "assessed":
        assert ref["value"] == value
    else:
        assert "value" not in ref
    omitted = refs[str(bound_run["second_metric"].id)]
    assert omitted["status"] == "not_assessed"
    assert "value" not in omitted


@pytest.mark.django_db(transaction=True)
def test_omitted_callback_is_explicitly_not_assessed(bound_run):
    run, example, scope = _run_parts(bound_run)
    metric_id = str(bound_run["first_metric"].id)
    result = run_python_experiment(
        run_id=run.id,
        task=lambda value, _: {"answer": value["question"]},
        evaluators={},
        **scope,
    )
    item = result.items.get(example_id=example.example_id, trial_number=1)
    assert len(item.score_refs) == 2
    assert all(ref["status"] == "not_assessed" for ref in item.score_refs)
    assert all("value" not in ref for ref in item.score_refs)
    ref = next(ref for ref in item.score_refs if ref["metric_id"] == metric_id)
    assert ref["status"] == "not_assessed"
    assert ref["explanation"] == "No callback supplied"


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "returned,needle",
    [
        pytest.param(object(), "invalid result", id="arbitrary-object"),
        pytest.param({"status": "assessed", "value": 1}, "invalid result", id="dict"),
        pytest.param(EvaluatorResult(status="invalid"), "invalid result", id="status"),
        pytest.param(
            EvaluatorResult(status="assessed", value=None), "finite numeric", id="none"
        ),
        pytest.param(
            EvaluatorResult(status="assessed", value=True), "finite numeric", id="bool"
        ),
        pytest.param(
            EvaluatorResult(status="assessed", value="0.5"),
            "finite numeric",
            id="numeric-string",
        ),
        pytest.param(
            EvaluatorResult(status="assessed", value=float("nan")),
            "finite numeric",
            id="nan",
        ),
        pytest.param(
            EvaluatorResult(status="assessed", value=float("inf")),
            "finite numeric",
            id="infinity",
        ),
        pytest.param(
            EvaluatorResult(status="assessed", value=float("-inf")),
            "finite numeric",
            id="negative-infinity",
        ),
        pytest.param(
            EvaluatorResult(status="assessed", value=0.5, explanation=object()),
            "explanation",
            id="explanation-type",
        ),
        pytest.param(
            EvaluatorResult(
                status="assessed", value=0.5, explanation="invalid\x00string"
            ),
            "invalid JSON string",
            id="nul-explanation",
        ),
        pytest.param(
            EvaluatorResult(
                status="assessed", value=0.5, explanation="invalid\ud800string"
            ),
            "invalid JSON string",
            id="surrogate-explanation",
        ),
    ],
)
def test_invalid_evaluator_result_becomes_error_receipt(bound_run, returned, needle):
    run, example, scope = _run_parts(bound_run)
    metric_id = str(bound_run["first_metric"].id)
    result = run_python_experiment(
        run_id=run.id,
        task=lambda value, _: {"answer": value["question"]},
        evaluators={metric_id: lambda *_: returned},
        **scope,
    )
    item = result.items.get(example_id=example.example_id, trial_number=1)
    assert item.status == "completed"
    assert item.error is None
    ref = next(ref for ref in item.score_refs if ref["metric_id"] == metric_id)
    assert ref["status"] == "error"
    assert needle in ref["explanation"]
    assert "value" not in ref


@pytest.mark.django_db(transaction=True)
def test_callback_exception_keeps_task_completed_and_only_metric_errors(bound_run):
    run, example, scope = _run_parts(bound_run)
    metric_id = str(bound_run["first_metric"].id)

    def evaluator(*_):
        raise RuntimeError("scorer unavailable")

    result = run_python_experiment(
        run_id=run.id,
        task=lambda value, _: {"answer": value["question"]},
        evaluators={
            metric_id: evaluator,
            bound_run["second_metric"].id: lambda *_: EvaluatorResult(
                status="assessed", value=0.9
            ),
        },
        **scope,
    )
    item = result.items.get(example_id=example.example_id, trial_number=1)
    assert item.status == "completed"
    assert item.output == {"answer": "capital of France"}
    ref = next(ref for ref in item.score_refs if ref["metric_id"] == metric_id)
    assert ref["status"] == "error"
    assert "scorer unavailable" in ref["explanation"]
    other = next(
        ref
        for ref in item.score_refs
        if ref["metric_id"] == str(bound_run["second_metric"].id)
    )
    assert other["status"] == "assessed"
    assert other["value"] == 0.9


@pytest.mark.django_db(transaction=True)
def test_task_failure_skips_all_scorers(bound_run):
    run, example, scope = _run_parts(bound_run)
    calls = []

    def evaluator(*_):
        calls.append(True)
        return EvaluatorResult(status="assessed", value=1)

    def failing_task(*_):
        raise RuntimeError("task failed")

    result = run_python_experiment(
        run_id=run.id,
        task=failing_task,
        evaluators={str(bound_run["first_metric"].id): evaluator},
        **scope,
    )
    item = result.items.get(example_id=example.example_id, trial_number=1)
    assert item.status == "failed"
    assert item.score_refs == []
    assert calls == []


@pytest.mark.django_db(transaction=True)
def test_callback_inputs_and_pinned_snapshot_are_isolated_and_immutable(bound_run):
    run, example, scope = _run_parts(bound_run)
    first_id = str(bound_run["first_metric"].id)
    second_id = str(bound_run["second_metric"].id)
    seen = []
    pinned_snapshots = {entry["metric_id"]: entry for entry in run.evaluator_versions}
    # The run already pinned these bindings; later edits must not change what
    # the callback receives.
    bound_run["first_metric"].config = {"binding": {"threshold": 0.01}}
    bound_run["first_metric"].save(update_fields=["config"])

    def first(output, context):
        assert isinstance(context.snapshot, MappingProxyType)
        with pytest.raises(TypeError):
            context.snapshot["new"] = "nope"
        with pytest.raises(TypeError):
            context.snapshot["config_snapshot"]["nested"] = "nope"
        with pytest.raises(TypeError):
            context.snapshot["config_snapshot"]["labels"][0] = "nope"
        assert (
            context.snapshot["binding_config"]
            == pinned_snapshots[first_id]["binding_config"]
        )
        output["mutated"] = True
        context.input["nested"]["keep"] = False
        context.expected["answer"] = "wrong"
        context.output["answer"] = "changed"
        seen.append(("first", output, context.input))
        return EvaluatorResult(status="assessed", value=0.1)

    def second(output, context):
        seen.append(("second", output, context.input))
        assert output == {"answer": "capital of France"}
        assert context.input["nested"]["keep"] is True
        assert context.expected == {"answer": "Paris"}
        assert context.output == {"answer": "capital of France"}
        return EvaluatorResult(status="assessed", value=0.9)

    result = run_python_experiment(
        run_id=run.id,
        task=lambda value, _: {"answer": value["question"]},
        evaluators={first_id: first, second_id: second},
        **scope,
    )
    item = result.items.get(example_id=example.example_id, trial_number=1)
    assert item.output == {"answer": "capital of France"}
    assert item.input == example.input
    assert item.expected == example.expected
    assert all(ref["status"] == "assessed" for ref in item.score_refs)
    assert [entry[0] for entry in seen] == ["first", "second"]
    assert seen[1][1] == {"answer": "capital of France"}
    assert seen[1][2]["nested"]["keep"] is True


@pytest.mark.django_db(transaction=True)
def test_child_otel_ids_are_exactly_recorded_on_item_and_score_refs(bound_run):
    run, example, scope = _run_parts(bound_run)
    metric_id = str(bound_run["first_metric"].id)
    tracer, exporter = _tracer()
    contexts = []

    def task(value, context):
        with context.tracer.start_as_current_span("agent.tool"):
            return {"answer": value["question"]}

    def evaluator(output, context):
        contexts.append(context)
        with context.tracer.start_as_current_span("judge.lookup"):
            return EvaluatorResult(status="assessed", value=1)

    result = run_python_experiment(
        run_id=run.id,
        task=task,
        evaluators={metric_id: evaluator},
        tracer=tracer,
        **scope,
    )
    item = result.items.get(example_id=example.example_id, trial_number=1)
    spans = exporter.get_finished_spans()
    task_span = next(
        span for span in spans if span.name == "futureagi.experiment.python_task"
    )
    evaluator_span = next(
        span for span in spans if span.name == "futureagi.experiment.evaluator"
    )
    tool_span = next(span for span in spans if span.name == "agent.tool")
    lookup_span = next(span for span in spans if span.name == "judge.lookup")
    assert task_span.parent is None
    assert tool_span.parent.span_id == task_span.context.span_id
    assert lookup_span.parent.span_id == evaluator_span.context.span_id
    assert lookup_span.context.trace_id == task_span.context.trace_id
    assert item.trace_id == f"{task_span.context.trace_id:032x}"
    assert item.root_span_id == f"{task_span.context.span_id:016x}"
    ref = next(ref for ref in item.score_refs if ref["metric_id"] == metric_id)
    assert ref["trace_id"] == f"{evaluator_span.context.trace_id:032x}"
    assert ref["span_id"] == f"{evaluator_span.context.span_id:016x}"
    assert evaluator_span.parent.span_id == task_span.context.span_id
    assert ref["trace_id"] == item.trace_id
    context = contexts[0]
    assert context.trace_id == item.trace_id
    assert context.root_span_id == item.root_span_id
    assert context.run_id == run.id
    assert context.example_id == example.example_id
    assert context.trial_number == 1
    assert context.evaluator_version_id == bound_run["evaluator_version"].id


@pytest.mark.django_db(transaction=True)
def test_terminal_resume_skips_task_and_evaluator_callbacks(bound_run):
    run, example, scope = _run_parts(bound_run)
    calls = []

    def task(value, _):
        calls.append("task")
        return {"answer": value["question"]}

    def evaluator(*_):
        calls.append("evaluator")
        return EvaluatorResult(status="assessed", value=1)

    callbacks = {str(bound_run["first_metric"].id): evaluator}
    first = run_python_experiment(
        run_id=run.id, task=task, evaluators=callbacks, **scope
    )
    item = first.items.get(example_id=example.example_id)
    before = (item.updated_at, item.metadata, item.score_refs)
    assert first.status == "completed"
    assert calls == ["task", "evaluator"]
    second = run_python_experiment(
        run_id=run.id, task=task, evaluators=callbacks, **scope
    )
    assert second.status == "completed"
    assert calls == ["task", "evaluator"]
    item.refresh_from_db()
    assert (item.updated_at, item.metadata, item.score_refs) == before
    assert second.completed_at == first.completed_at


@pytest.mark.django_db(transaction=True)
def test_unknown_callback_fails_before_lifecycle_transition(bound_run):
    run, _, scope = _run_parts(bound_run)
    calls = []
    with pytest.raises(RunWriteError, match="not pinned"):
        run_python_experiment(
            run_id=run.id,
            task=lambda *_: calls.append("task"),
            evaluators={
                uuid.uuid4(): lambda *_: EvaluatorResult(status="assessed", value=1)
            },
            **scope,
        )
    run.refresh_from_db()
    assert run.status == "draft"
    assert run.started_at is None
    assert run.items.count() == 0
    assert calls == []


@pytest.mark.django_db(transaction=True)
def test_score_receipt_requires_exact_metric_version_pair_and_rejects_duplicates(
    bound_run,
):
    run, example, scope = _run_parts(bound_run)
    transition_run(run_id=run.id, status="queued", **scope)
    transition_run(run_id=run.id, status="running", **scope)
    item = claim_run_item(
        run_id=run.id, example_id=example.example_id, receipt_key="claim", **scope
    )
    first = run.evaluator_versions[0]
    duplicate = {
        "metric_id": first["metric_id"],
        "evaluator_version_id": first["evaluator_version_id"],
        "status": "assessed",
        "value": 0.2,
    }
    with pytest.raises(RunWriteError, match="duplicate"):
        record_run_item_result(
            item_id=item.id,
            status="completed",
            receipt_key="result",
            score_refs=[duplicate, duplicate],
            **scope,
        )
    wrong_pair = {
        "metric_id": str(uuid.uuid4()),
        "evaluator_version_id": first["evaluator_version_id"],
        "status": "assessed",
        "value": 0.2,
    }
    with pytest.raises(RunWriteError, match="not pinned"):
        record_run_item_result(
            item_id=item.id,
            status="completed",
            receipt_key="result-2",
            score_refs=[wrong_pair],
            **scope,
        )
    item.refresh_from_db()
    assert item.status == "running"
    assert item.score_refs == []
    # Two independent bindings of the same evaluator version are valid.
    second = run.evaluator_versions[1]
    written = record_run_item_result(
        item_id=item.id,
        status="completed",
        receipt_key="correct-result",
        score_refs=[
            duplicate,
            {**duplicate, "metric_id": second["metric_id"], "value": 0.9},
        ],
        **scope,
    )
    written.refresh_from_db()
    assert len(written.score_refs) == 2
    assert {ref["value"] for ref in written.score_refs} == {0.2, 0.9}


@pytest.mark.django_db(transaction=True)
def test_latency_is_task_only_and_does_not_include_evaluator_time(
    monkeypatch, bound_run
):
    run, example, scope = _run_parts(bound_run)
    clock = {"now": 100.0}
    reads = []

    def clock_read():
        reads.append(clock["now"])
        return clock["now"]

    monkeypatch.setattr(python_task_adapter, "perf_counter", clock_read)
    metric_id = str(bound_run["first_metric"].id)

    def task(value, _):
        clock["now"] = 100.125
        return {"answer": value["question"]}

    def evaluator(*_):
        clock["now"] = 130.125
        return EvaluatorResult(status="assessed", value=0.5)

    result = run_python_experiment(
        run_id=run.id,
        task=task,
        evaluators={metric_id: evaluator},
        **scope,
    )
    item = result.items.get(example_id=example.example_id, trial_number=1)
    assert item.latency_ms == pytest.approx(125.0)
    assert clock["now"] == 130.125
    assert reads == [100.0, 100.125]
