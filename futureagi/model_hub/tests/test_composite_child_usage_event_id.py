"""A composite's children are billed once when a worker runs the composite again.

Each child of a composite is its own usage event, keyed to the composite's step
(a dataset or experiment cell, or an Observe eval task entry), the run that
evaluates it and the child. When a worker runs the step again after a restart
or a retry, every child emits the event it emitted before, so the consumer
keeps one per child. Two children are two events, and a run the customer
starts again is billed again.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import pytest

from model_hub.models.choices import (
    CellStatus,
    DatasetSourceChoices,
    OwnerChoices,
    SourceChoices,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.evals_metric import (
    CompositeEvalChild,
    EvalTemplate,
    UserEvalMetric,
)
from model_hub.tasks.composite_runner import CompositeEvaluationRunner

pytest_plugins = ["evaluations.tests.usage_step_support"]
pytestmark = [pytest.mark.requires_ee, pytest.mark.django_db]


def _child(organization, workspace, name):
    return EvalTemplate.no_workspace_objects.create(
        name=name,
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        config={"output": "score", "eval_type_id": "DeterministicEvaluator"},
        output_type_normalized="percentage",
        pass_threshold=0.5,
    )


@pytest.fixture
def composite(db, organization, workspace):
    parent = EvalTemplate.no_workspace_objects.create(
        name="billed-composite",
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        template_type="composite",
        aggregation_enabled=True,
        aggregation_function="avg",
        config={},
    )
    for order, name in enumerate(("billed-child-a", "billed-child-b")):
        CompositeEvalChild.objects.create(
            parent=parent,
            child=_child(organization, workspace, name),
            order=order,
            weight=1.0,
        )
    return parent


@pytest.fixture
def row(db, organization, workspace):
    dataset = Dataset.objects.create(
        name="billed-composite-dataset",
        organization=organization,
        workspace=workspace,
        source=DatasetSourceChoices.BUILD.value,
    )
    column = Column.objects.create(
        name="input",
        dataset=dataset,
        data_type="text",
        source=SourceChoices.OTHERS.value,
    )
    dataset.column_order = [str(column.id)]
    dataset.save(update_fields=["column_order"])
    row = Row.objects.create(dataset=dataset, order=0)
    Cell.objects.create(
        dataset=dataset,
        column=column,
        row=row,
        value="hello world",
        status=CellStatus.PASS.value,
    )
    return row


@pytest.fixture
def metric(db, organization, workspace, user, composite, row):
    input_column = Column.objects.get(dataset=row.dataset, name="input")
    return UserEvalMetric.objects.create(
        name="billed-composite-metric",
        organization=organization,
        workspace=workspace,
        template=composite,
        dataset=row.dataset,
        user=user,
        config={"mapping": {"input": str(input_column.id)}},
    )


@pytest.fixture
def emitted(monkeypatch):
    """The usage events the children emit. Every child run logs its own call
    and runs a stand-in evaluator."""
    import model_hub.views.utils.evals as evals_module
    from tfc.constants.api_calls import APICallStatusChoices

    events = []

    class StandInEvaluator:
        cost = {"total_cost": 0.002}
        token_usage = {}

        def run(self, **_kwargs):
            return SimpleNamespace(
                eval_results=[
                    {
                        "data": {"input": "hello world"},
                        "failure": None,
                        "reason": "ok",
                        "runtime": 0.01,
                        "model": "turing_large",
                        "metrics": None,
                        "metadata": {},
                    }
                ]
            )

    def _call_log(**_kwargs):
        return SimpleNamespace(
            log_id=uuid4(),
            config=json.dumps({}),
            status=APICallStatusChoices.PROCESSING.value,
            input_token_count=0,
            save=MagicMock(),
        )

    monkeypatch.setattr(evals_module, "log_and_deduct_cost_for_api_request", _call_log)
    monkeypatch.setattr(
        "ee.usage.services.metering.check_usage",
        lambda *_args, **_kwargs: SimpleNamespace(allowed=True),
    )
    monkeypatch.setattr("ee.usage.services.emitter.emit", events.append)
    monkeypatch.setattr(
        "model_hub.views.utils.evals.EvaluationRunner._create_eval_instance",
        lambda *_args, **_kwargs: StandInEvaluator(),
    )
    monkeypatch.setattr(
        "model_hub.views.utils.evals.EvaluationRunner.map_fields",
        lambda *_args, **_kwargs: {"input": "hello world"},
    )
    monkeypatch.setattr(
        "model_hub.views.utils.evals.EvaluationRunner.format_output",
        lambda *_args, **_kwargs: 0.7,
    )
    return events


def _activity(workflow_run_id):
    return SimpleNamespace(
        activity_id="3",
        activity_type="process_eval_batch",
        attempt=1,
        task_queue="tasks_l",
        workflow_id="dataset-evaluation",
        workflow_namespace="default",
        workflow_run_id=workflow_run_id,
        workflow_type="DatasetEvaluationWorkflow",
    )


def _ids_of_one_attempt(emitted, attempt):
    """The event ids of one attempt, which bills each of the two children."""
    before = len(emitted)
    attempt()
    ids = [event.event_id for event in emitted[before:]]
    assert len(ids) == 2
    return ids


def _cell_attempt(metric, row, workflow_run_id="run-1"):
    """A worker evaluating a composite cell: it builds a runner inside an
    activity of the workflow run, as every attempt builds its own."""

    def attempt():
        with patch("temporalio.activity.info", return_value=_activity(workflow_run_id)):
            runner = CompositeEvaluationRunner(user_eval_metric_id=metric.id)
        runner.run_prompt(row_ids=[row.id])

    return attempt


def _entry_attempt(composite, organization, workspace, entry_id):
    """The eval task engine running a composite on a span entry."""
    from tracer.services.eval_tasks.entries import writing_onto_entry
    from tracer.utils import eval as tracer_eval

    span = MagicMock()
    span.id = "span-1"
    span.project.organization = organization
    span.project.workspace = workspace
    config = MagicMock()
    config.id = "config-1"
    config.eval_template = composite
    config.config = {}
    config.model = None

    def attempt():
        with (
            patch.object(
                tracer_eval.CustomEvalConfig.objects, "get", return_value=config
            ),
            writing_onto_entry(entry_id),
        ):
            logger_kwargs = tracer_eval._execute_composite_on_span(
                observation_span_id="span-1",
                custom_eval_config_id="config-1",
                eval_task_id="task-1",
                run_params={"input": "hello world"},
                observation_span=span,
            )
        assert not logger_kwargs.get("error"), logger_kwargs

    return attempt


def test_a_child_run_for_a_known_step_emits_the_event_it_emitted_before(
    composite, organization, workspace, emitted
):
    from model_hub.views.utils.evals import run_eval_func

    child = CompositeEvalChild.objects.filter(parent=composite).first().child
    step = {
        "evaluated": ("row-1", "column-1"),
        "run": ("dataset-evaluation", "run-1"),
        "evaluator": ("metric-1", str(child.id)),
    }
    for _attempt in range(2):
        run_eval_func(
            {"config": {}, "params": {}},
            {"input": "hello world"},
            child,
            organization,
            model=None,
            workspace=workspace,
            source="composite_eval_dataset",
            usage_step=step,
        )

    assert len(emitted) == 2
    assert emitted[1].event_id == emitted[0].event_id


def test_a_retried_composite_cell_bills_each_child_once(metric, row, emitted):
    first = _ids_of_one_attempt(emitted, _cell_attempt(metric, row))
    retried = _ids_of_one_attempt(emitted, _cell_attempt(metric, row))

    assert len(set(first)) == 2
    assert sorted(retried) == sorted(first)


def test_a_composite_cell_in_a_run_the_customer_starts_again_is_billed_again(
    metric, row, emitted
):
    ids = [
        event_id
        for run in ("run-1", "run-2") * 2
        for event_id in _ids_of_one_attempt(
            emitted, _cell_attempt(metric, row, workflow_run_id=run)
        )
    ]

    assert len(set(ids)) == 4


def test_a_retried_observe_composite_bills_each_child_once(
    composite, organization, workspace, emitted
):
    attempt = _entry_attempt(composite, organization, workspace, "entry-1")
    first = _ids_of_one_attempt(emitted, attempt)
    retried = _ids_of_one_attempt(emitted, attempt)

    assert len(set(first)) == 2
    assert sorted(retried) == sorted(first)


def test_two_observe_composite_entries_are_billed_apart(
    composite, organization, workspace, emitted
):
    ids = [
        event_id
        for entry in ("entry-1", "entry-2") * 2
        for event_id in _ids_of_one_attempt(
            emitted, _entry_attempt(composite, organization, workspace, entry)
        )
    ]

    assert len(set(ids)) == 4


def test_a_child_whose_step_cannot_be_read_is_still_billed(
    composite, organization, workspace, emitted, unreadable_step
):
    """Each run is billed once under a fresh id: a child is never left
    unbilled because its step cannot be read. A lookup that fails is logged
    with its stack."""
    from model_hub.views.utils.evals import run_eval_func

    child = CompositeEvalChild.objects.filter(parent=composite).first().child
    step = {
        "evaluated": ("row-1", "column-1"),
        "run": ("dataset-evaluation", "run-1"),
        "evaluator": ("metric-1", str(child.id)),
    }
    for _attempt in range(2):
        run_eval_func(
            {"config": {}, "params": {}},
            {"input": "hello world"},
            child,
            organization,
            model=None,
            workspace=workspace,
            source="composite_eval_dataset",
            usage_step=step,
        )

    assert len(emitted) == 2
    assert emitted[1].event_id != emitted[0].event_id
    assert all(UUID(event.event_id).version == 4 for event in emitted)
    assert unreadable_step.warnings() == unreadable_step.expected_warnings(lookups=2)


def test_a_composite_cell_whose_step_cannot_be_read_bills_each_child(
    metric, row, emitted, unreadable_step
):
    ids = _ids_of_one_attempt(emitted, _cell_attempt(metric, row))

    assert len(set(ids)) == 2
    assert all(UUID(event_id).version == 4 for event_id in ids)
    assert unreadable_step.warnings() == unreadable_step.expected_warnings(lookups=2)


def test_an_observe_composite_whose_step_cannot_be_read_bills_each_child(
    composite, organization, workspace, emitted, unreadable_step
):
    attempt = _entry_attempt(composite, organization, workspace, "entry-1")
    ids = _ids_of_one_attempt(emitted, attempt)

    assert len(set(ids)) == 2
    assert all(UUID(event_id).version == 4 for event_id in ids)
    assert unreadable_step.warnings() == unreadable_step.expected_warnings(lookups=2)


class _StepThatCannotBeRead(dict):
    """A composite's step whose parts cannot be read."""

    def get(self, *_args):
        raise RuntimeError("composite step unreadable")


def test_a_composite_whose_step_cannot_be_read_by_its_children_bills_each_child(
    composite, organization, workspace, emitted, failed_step_read
):
    """Each child is billed under a fresh id: a child is never left unbilled
    because the composite's step cannot be read for it. The failed read is
    logged with its stack, once for each child."""
    from model_hub.utils.composite_execution import execute_composite_children_sync

    links = list(
        CompositeEvalChild.objects.filter(parent=composite)
        .select_related("child", "pinned_version")
        .order_by("order")
    )

    def attempt():
        execute_composite_children_sync(
            parent=composite,
            child_links=links,
            mapping={"input": "hello world"},
            config={},
            org=organization,
            workspace=workspace,
            source="composite_eval_dataset",
            usage_step=_StepThatCannotBeRead(
                evaluated=("row-1", "column-1"),
                run=("dataset-evaluation", "run-1"),
                evaluator=("metric-1",),
            ),
        )

    ids = _ids_of_one_attempt(emitted, attempt)

    assert len(set(ids)) == 2
    assert all(UUID(event_id).version == 4 for event_id in ids)
    assert failed_step_read.warnings() == failed_step_read.expected_warnings(lookups=2)


def test_a_composite_cell_whose_workflow_run_cannot_be_read_bills_each_child(
    metric, row, emitted, failed_step_read
):
    """Each child is billed under a fresh id: a cell is never left unbilled
    because its workflow run cannot be read when the runner is built. The
    failed read is logged with its stack."""
    with patch(
        "model_hub.views.eval_runner._workflow_run",
        side_effect=RuntimeError("workflow run unreadable"),
    ):
        runner = CompositeEvaluationRunner(user_eval_metric_id=metric.id)
    ids = _ids_of_one_attempt(emitted, lambda: runner.run_prompt(row_ids=[row.id]))

    assert len(set(ids)) == 2
    assert all(UUID(event_id).version == 4 for event_id in ids)
    assert failed_step_read.warnings() == failed_step_read.expected_warnings(lookups=1)
