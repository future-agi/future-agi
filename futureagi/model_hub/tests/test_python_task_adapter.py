import uuid

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from model_hub.models.dataset_versions import DatasetVersion, DatasetVersionItem
from model_hub.models.develop_dataset import Dataset
from model_hub.models.experiments import ExperimentRun
from model_hub.services.experiment_run_creation import create_experiment_run
from model_hub.services.experiment_run_writes import (
    RunWriteError,
    claim_run_item,
    record_run_item_result,
    transition_run,
)
from model_hub.services.python_task_adapter import run_python_experiment


@pytest.fixture
def external_run(organization, workspace, user):
    dataset = Dataset.objects.create(
        name="Python adapter dataset",
        organization=organization,
        workspace=workspace,
        user=user,
        column_order=[],
        column_config={},
    )
    version = DatasetVersion.objects.create(dataset=dataset)
    examples = [
        DatasetVersionItem.objects.create(
            dataset_version=version, input={"question": "one"}, expected={"answer": "1"}
        ),
        DatasetVersionItem.objects.create(
            dataset_version=version, input={"question": "two"}, expected={"answer": "2"}
        ),
    ]
    scope = {"organization_id": organization.id, "workspace_id": workspace.id}
    run = create_experiment_run(
        run_id=uuid.uuid4(),
        dataset_version=version,
        created_by=user,
        task_kind="external",
        expected_items=[
            (examples[0].example_id, 1),
            (examples[0].example_id, 2),
            (examples[1].example_id, 1),
        ],
    )
    return run, examples, scope


def _tracer():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider.get_tracer("python-task-test"), exporter


@pytest.mark.django_db(transaction=True)
def test_adapter_runs_trials_traces_nested_work_and_resumes_terminal_rows(external_run):
    run, examples, scope = external_run
    tracer, exporter = _tracer()
    calls = []

    def task(value, context):
        calls.append((context.example_id, context.trial_number, value))
        with context.tracer.start_as_current_span("retrieval"):
            with context.tracer.start_as_current_span("tool"):
                pass
        if context.trial_number == 2:
            raise RuntimeError("fixture tool failed")
        return {"answer": value["question"]}

    with tracer.start_as_current_span("ambient-caller"):
        result = run_python_experiment(run_id=run.id, task=task, tracer=tracer, **scope)
    assert result.status == "partial"
    assert [(example_id, trial) for example_id, trial, _ in calls] == sorted(
        [
            (examples[0].example_id, 1),
            (examples[0].example_id, 2),
            (examples[1].example_id, 1),
        ]
    )
    assert (
        ExperimentRun.objects.get(id=run.id).items.filter(status="completed").count()
        == 2
    )
    assert (
        ExperimentRun.objects.get(id=run.id).items.filter(status="failed").count() == 1
    )
    roots = [
        span
        for span in exporter.get_finished_spans()
        if span.name == "futureagi.experiment.python_task"
    ]
    children = [
        span
        for span in exporter.get_finished_spans()
        if span.name in {"retrieval", "tool"}
    ]
    assert len(roots) == 3
    assert all(span.parent is None for span in roots)
    assert len(children) == 6
    assert all(span.parent is not None for span in children)
    roots_by_trace = {span.context.trace_id: span for span in roots}
    for retrieval in (span for span in children if span.name == "retrieval"):
        assert (
            retrieval.parent.span_id
            == roots_by_trace[retrieval.context.trace_id].context.span_id
        )
    retrievals = {
        span.context.trace_id: span for span in children if span.name == "retrieval"
    }
    for tool in (span for span in children if span.name == "tool"):
        assert tool.parent.span_id == retrievals[tool.context.trace_id].context.span_id
    roots_by_case = {
        (
            span.attributes["futureagi.example.id"],
            span.attributes["futureagi.trial.number"],
        ): span
        for span in roots
    }
    for item in ExperimentRun.objects.get(id=run.id).items.all():
        root = roots_by_case[(str(item.example_id), item.trial_number)]
        assert item.trace_id == f"{root.context.trace_id:032x}"
        assert item.root_span_id == f"{root.context.span_id:016x}"

    replay_calls = []
    replay = run_python_experiment(
        run_id=run.id, task=lambda *_: replay_calls.append(True), tracer=tracer, **scope
    )
    assert replay.status == "partial"
    assert replay_calls == []


@pytest.mark.django_db(transaction=True)
def test_adapter_rejects_competing_running_claim_without_callback(external_run):
    run, examples, scope = external_run
    transition_run(run_id=run.id, status="queued", **scope)
    transition_run(run_id=run.id, status="running", **scope)
    first_example = min(examples, key=lambda example: example.example_id)
    claim_run_item(
        run_id=run.id,
        example_id=first_example.example_id,
        trial_number=1,
        receipt_key="other-process",
        **scope,
    )
    calls = []
    with pytest.raises(RunWriteError, match="already claimed"):
        run_python_experiment(
            run_id=run.id, task=lambda *_: calls.append(True), **scope
        )
    assert calls == []


@pytest.mark.django_db(transaction=True)
def test_adapter_stops_after_cancellation_without_invoking_next_case(external_run):
    run, examples, scope = external_run
    calls = []

    def task(value, context):
        calls.append(context.trial_number)
        transition_run(run_id=run.id, status="cancelled", **scope)
        return {"ok": True}

    result = run_python_experiment(run_id=run.id, task=task, **scope)
    assert result.status == "cancelled"
    assert calls == [1]
    assert ExperimentRun.objects.get(id=run.id).items.count() == 1


@pytest.mark.django_db(transaction=True)
def test_deleted_item_requires_recovery_without_reinvoking_callback(external_run):
    run, examples, scope = external_run
    transition_run(run_id=run.id, status="queued", **scope)
    transition_run(run_id=run.id, status="running", **scope)
    first_example = min(examples, key=lambda example: example.example_id)
    item = claim_run_item(
        run_id=run.id, example_id=first_example.example_id, receipt_key="old", **scope
    )
    record_run_item_result(
        item_id=item.id,
        status="completed",
        output={"old": True},
        receipt_key="old",
        **scope,
    )
    item.delete()
    calls = []
    with pytest.raises(
        RunWriteError, match="deleted run item requires explicit recovery"
    ):
        run_python_experiment(
            run_id=run.id, task=lambda *_: calls.append(True), **scope
        )
    assert calls == []
    item.refresh_from_db()
    assert item.deleted is True


@pytest.mark.django_db(transaction=True)
def test_adapter_noop_tracer_does_not_fabricate_trace_ids(external_run):
    from opentelemetry.trace import NoOpTracer

    run, _, scope = external_run
    result = run_python_experiment(
        run_id=run.id, task=lambda *_: {"answer": "ok"}, tracer=NoOpTracer(), **scope
    )
    assert result.status == "completed"
    assert all(
        item.trace_id is None and item.root_span_id is None
        for item in result.items.all()
    )


@pytest.mark.django_db(transaction=True)
def test_adapter_rejects_internal_graph_header_without_running_callback(external_run):
    run, _, scope = external_run
    run.task_kind = "graph"
    run.save(update_fields=["task_kind"])
    calls = []
    with pytest.raises(RunWriteError, match="requires an external run"):
        run_python_experiment(
            run_id=run.id, task=lambda *_: calls.append(True), **scope
        )
    assert calls == []
    run.refresh_from_db()
    assert run.status == "draft"
