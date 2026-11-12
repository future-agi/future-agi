"""Dataset evaluation runs while custom code evaluations are paused.

While CUSTOM_CODE_EVALS_PAUSED is on, a customer's CustomCodeEval template is
refused on every dataset evaluation path before the run is charged: a
scheduled run and a batch (EvaluationRunner.update_cell), a single row
(run_evaluation_for_row) and a native experiment row (evaluate_native_row).
Nothing is logged or deducted, nothing reaches the code executor, and the row
records the fixed sentence as its reason. A built-in template, and a customer
template with the setting off, are charged and reach the executor as before.

The tests watch the executor client (urllib in fi_utils.sandbox) and the cost
function where eval_runner imports it. Rows run inline instead of on worker
threads so that they stay in the test transaction, and each path runs without
a request workspace context, as a background worker runs it.
"""

import json
import uuid
from concurrent.futures import Future
from contextlib import contextmanager
from copy import copy, deepcopy
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from django.test import override_settings

from agentic_eval.core_evals.fi_utils import custom_code_pause, sandbox
from model_hub.models.choices import (
    CellStatus,
    DatasetSourceChoices,
    SourceChoices,
    StatusType,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.evals_metric import (
    EvalTemplate,
    EvalTemplateVersion,
    UserEvalMetric,
)
from model_hub.models.experiments import ExperimentDatasetTable, ExperimentsTable
from model_hub.utils.eval_result_columns import infer_eval_result_column_data_type
from model_hub.views import eval_runner
from tfc.constants.api_calls import APICallStatusChoices
from tfc.middleware.workspace_context import workspace_context

pytestmark = pytest.mark.django_db

SENTENCE = "Custom code evaluations are temporarily unavailable on Future AGI Cloud."
BUILTIN_CODE = (
    "def evaluate(output, expected):\n    return 1.0 if output == expected else 0.0\n"
)
CUSTOMER_CODE = "def evaluate(output, expected):\n    return 0.5\n"


class _Response:
    def __init__(self, body):
        self.body = body

    def read(self, limit=-1):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def executor(monkeypatch):
    """Every request the executor client sends, answered with a score."""
    sent = []

    def urlopen(request, timeout=None):
        sent.append(json.loads(request.data))
        answer = {"status": "success", "data": {"result": 1.0, "reason": "scored"}}
        return _Response(json.dumps(answer).encode())

    monkeypatch.setattr(sandbox.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(sandbox, "CODE_EXECUTOR_URL", "http://code-executor.test:8060")
    return sent


@pytest.fixture(autouse=True)
def fresh_system_code(monkeypatch):
    monkeypatch.setattr(custom_code_pause, "_system_code_cache", None)


@contextmanager
def _inline_executor(**_kwargs):
    """update_cell's worker pool, running each row in the calling thread."""

    def submit(fn, *args, **kwargs):
        future = Future()
        try:
            future.set_result(fn(*args, **kwargs))
        except Exception as exc:
            future.set_exception(exc)
        return future

    yield SimpleNamespace(submit=submit)


@pytest.fixture(autouse=True)
def outside_services(monkeypatch):
    """Worker threads, the Redis run tracker, analytics, the usage pre-check,
    ground-truth retrieval and token counting (as in a build without ee)."""
    from ee.usage.services import metering
    from model_hub.services.ground_truth_service import GroundTruthService
    from tfc.utils import distributed_state

    tracker = MagicMock()
    tracker.should_cancel.return_value = False
    monkeypatch.setattr(distributed_state, "evaluation_tracker", tracker)
    monkeypatch.setattr(eval_runner, "ThreadPoolExecutor", _inline_executor)
    monkeypatch.setattr(eval_runner, "close_old_connections", lambda: None)
    monkeypatch.setattr(eval_runner, "track_mixpanel_event", MagicMock())
    monkeypatch.setattr(
        eval_runner, "get_mixpanel_properties", MagicMock(return_value={})
    )
    monkeypatch.setattr(eval_runner, "count_tiktoken_tokens", None)
    monkeypatch.setattr(
        metering,
        "check_usage",
        MagicMock(return_value=SimpleNamespace(allowed=True, reason="")),
    )
    monkeypatch.setattr(GroundTruthService, "inject_context", MagicMock())


@pytest.fixture
def billing(monkeypatch):
    """The cost function (it answers with a log row that admits the run), its
    refund and the usage emitted for an evaluated cell."""
    log_row = MagicMock(
        status=APICallStatusChoices.PROCESSING.value,
        config="{}",
        log_id=uuid.uuid4(),
    )
    charge = MagicMock(return_value=log_row)
    refund = MagicMock()
    emit = MagicMock()
    monkeypatch.setattr(eval_runner, "log_and_deduct_cost_for_api_request", charge)
    monkeypatch.setattr(eval_runner, "refund_cost_for_api_call", refund)
    monkeypatch.setattr(eval_runner.EvaluationRunner, "_emit_cell_usage", emit)
    return SimpleNamespace(charge=charge, refund=refund, emit=emit)


@pytest.fixture
def builtin_template(db):
    # Saved outside any workspace context: a context would give the row an
    # organization and a workspace, and it would no longer be a built-in.
    with workspace_context(None):
        template = EvalTemplate.no_workspace_objects.create(
            name="pause_dataset_builtin_code",
            owner="system",
            organization=None,
            workspace=None,
            eval_type="code",
            config={"eval_type_id": "CustomCodeEval", "code": BUILTIN_CODE},
        )
    template.refresh_from_db()
    assert template.organization_id is None
    assert template.workspace_id is None
    return template


@pytest.fixture
def customer_template(organization, workspace):
    return EvalTemplate.no_workspace_objects.create(
        name="pause_dataset_customer_code",
        owner="user",
        organization=organization,
        workspace=workspace,
        eval_type="code",
        config={"eval_type_id": "CustomCodeEval", "code": CUSTOMER_CODE},
    )


def dataset_world(organization, workspace, template):
    """A dataset row with two inputs, a code eval bound to it and the eval's
    result column, as adding the eval to the dataset leaves them."""
    dataset = Dataset.objects.create(
        name="Pause dataset runs",
        organization=organization,
        workspace=workspace,
        source=DatasetSourceChoices.BUILD.value,
        column_order=[],
        column_config={},
    )
    output = Column.objects.create(
        name="output",
        dataset=dataset,
        data_type="text",
        source=SourceChoices.OTHERS.value,
    )
    expected = Column.objects.create(
        name="expected",
        dataset=dataset,
        data_type="text",
        source=SourceChoices.OTHERS.value,
    )
    row = Row.objects.create(dataset=dataset, order=0)
    for column in (output, expected):
        Cell.objects.create(
            dataset=dataset,
            column=column,
            row=row,
            value="4",
            status=CellStatus.PASS.value,
        )
    metric = UserEvalMetric.objects.create(
        name="pause_code_eval",
        template=template,
        dataset=dataset,
        organization=organization,
        workspace=workspace,
        status=StatusType.NOT_STARTED.value,
        config={
            "mapping": {"output": str(output.id), "expected": str(expected.id)},
            "config": {},
        },
    )
    result = Column.objects.create(
        name=metric.name,
        dataset=dataset,
        data_type=infer_eval_result_column_data_type(template),
        source=SourceChoices.EVALUATION.value,
        source_id=str(metric.id),
    )
    return SimpleNamespace(dataset=dataset, row=row, metric=metric, result=result)


def dataset_runner(metric):
    """The runner a scheduled dataset evaluation builds (process_single_evaluation)."""
    return eval_runner.EvaluationRunner(
        user_eval_metric_id=metric.id,
        is_only_eval=True,
        source="dataset_evaluation",
        source_id=metric.template_id,
        source_configs={"dataset_id": str(metric.dataset_id), "source": "dataset"},
    )


def native_world(organization, workspace, user, template):
    """An experiment variant's output column and an input column on one
    snapshot row, a code eval bound to it and the template version it pins."""
    dataset = Dataset.objects.create(
        name="Pause native run",
        organization=organization,
        workspace=workspace,
        user=user,
        column_order=[],
        column_config={},
    )
    expected = Column.objects.create(
        name="expected",
        dataset=dataset,
        data_type="text",
        source=SourceChoices.OTHERS.value,
    )
    experiment = ExperimentsTable.objects.create(
        name="Pause native run", dataset=dataset, snapshot_dataset=dataset, user=user
    )
    variant = ExperimentDatasetTable.objects.create(name="A", experiment=experiment)
    output = Column.objects.create(
        name="output-A",
        dataset=dataset,
        data_type="text",
        source=SourceChoices.EXPERIMENT.value,
        source_id=str(variant.id),
    )
    variant.columns.add(output)
    row = Row.objects.create(dataset=dataset, order=0)
    metric = UserEvalMetric.objects.create(
        name="pause_native_code_eval",
        template=template,
        dataset=dataset,
        organization=organization,
        workspace=workspace,
        status=StatusType.RUNNING.value,
        config={
            "mapping": {"output": str(output.id), "expected": str(expected.id)},
            "config": {},
        },
    )
    version = EvalTemplateVersion.objects.create_version(
        eval_template=template,
        config_snapshot=deepcopy(template.config),
        organization=organization,
        workspace=workspace,
    )
    return SimpleNamespace(
        dataset=dataset,
        experiment=experiment,
        variant=variant,
        output=output,
        expected=expected,
        row=row,
        metric=metric,
        version=version,
    )


def native_runner(world, outcomes):
    """A NativeEvaluationRunner in the state its constructor leaves once the
    run's evaluator pin has been checked (the pin check is not exercised)."""
    runner = object.__new__(eval_runner.NativeEvaluationRunner)
    runner._native_guard = None
    runner._native_outcome = outcomes.append
    runner._native_graph = False
    eval_runner.EvaluationRunner.__init__(
        runner,
        world.metric.id,
        experiment_dataset=world.variant,
        column=world.output,
        format_output=True,
        source="experiment",
        source_id=str(world.metric.template_id),
        source_configs={
            "experiment_id": str(world.experiment.id),
            "run_id": str(uuid.uuid4()),
            "version_id": str(world.version.id),
            "version_number": world.version.version_number,
        },
        organization_id=world.dataset.organization_id,
        workspace_id=world.dataset.workspace_id,
        version_number=world.version.version_number,
    )
    runner.column = copy(world.output)
    runner.user_eval_metric = copy(world.metric)
    runner.user_eval_metric.config = deepcopy(world.metric.config)
    runner.dataset = world.dataset
    runner._resolved_version = world.version
    runner.eval_template = copy(world.metric.template)
    runner.eval_template.config = {
        **deepcopy(world.metric.template.config or {}),
        **deepcopy(world.version.config_snapshot or {}),
    }
    runner.eval_template.criteria = world.version.criteria
    runner.user_eval_metric.template = runner.eval_template
    runner.replace_column_id = runner.column.id
    runner.futureagi_eval = False
    runner._native_pin = {}
    return runner


def evaluation_cell(world):
    """The row's cell in the dataset evaluation's result column."""
    cells = list(
        Cell.no_workspace_objects.filter(
            row=world.row,
            column__dataset=world.dataset,
            column__source=SourceChoices.EVALUATION.value,
            column__source_id=str(world.metric.id),
            deleted=False,
        )
    )
    assert len(cells) == 1, cells
    return cells[0]


def reason_of(cell):
    infos = cell.value_infos
    if isinstance(infos, str):
        infos = json.loads(infos)
    return (infos or {}).get("reason")


def assert_refused_before_the_charge(executor, billing, reason):
    assert executor == []
    billing.charge.assert_not_called()
    billing.refund.assert_not_called()
    billing.emit.assert_not_called()
    assert reason == SENTENCE


@override_settings(CUSTOM_CODE_EVALS_PAUSED=True)
def test_paused_scheduled_dataset_run_refuses_before_charging(
    executor, billing, customer_template, organization, workspace
):
    world = dataset_world(organization, workspace, customer_template)
    with workspace_context(None):
        runner = dataset_runner(world.metric)
        runner.load_user_eval_metric()
        runner.update_cell()

    cell = evaluation_cell(world)
    assert_refused_before_the_charge(executor, billing, reason_of(cell))
    assert cell.status == CellStatus.ERROR.value


@override_settings(CUSTOM_CODE_EVALS_PAUSED=True)
def test_paused_batched_dataset_run_refuses_before_charging(
    executor, billing, customer_template, organization, workspace
):
    world = dataset_world(organization, workspace, customer_template)
    with workspace_context(None):
        runner = dataset_runner(world.metric)
        runner.load_user_eval_metric()
        runner.update_cell(row_ids=[str(world.row.id)])

    cell = evaluation_cell(world)
    assert_refused_before_the_charge(executor, billing, reason_of(cell))
    assert cell.status == CellStatus.ERROR.value


@override_settings(CUSTOM_CODE_EVALS_PAUSED=True)
def test_paused_single_row_run_refuses_before_charging(
    executor, billing, customer_template, organization, workspace
):
    world = dataset_world(organization, workspace, customer_template)
    with workspace_context(None):
        dataset_runner(world.metric).run_evaluation_for_row(world.row.id)

    cell = evaluation_cell(world)
    assert_refused_before_the_charge(executor, billing, reason_of(cell))
    assert cell.status == CellStatus.ERROR.value


@override_settings(CUSTOM_CODE_EVALS_PAUSED=True)
def test_paused_native_experiment_row_refuses_before_charging(
    executor, billing, customer_template, organization, workspace, user
):
    world = native_world(organization, workspace, user, customer_template)
    outcomes = []
    with workspace_context(None):
        outcome = native_runner(world, outcomes).evaluate_native_row(
            world.row.id,
            values={str(world.expected.id): "4"},
            columns=[
                {
                    "source_id": str(world.expected.id),
                    "snapshot_id": str(world.expected.id),
                    "name": world.expected.name,
                    "data_type": "text",
                }
            ],
            output="4",
            messages=None,
        )

    assert_refused_before_the_charge(executor, billing, outcome.get("explanation"))
    assert outcome["status"] == "error"
    assert outcomes == [outcome]
    result = Cell.no_workspace_objects.get(
        row=world.row,
        column__source=SourceChoices.EXPERIMENT_EVALUATION.value,
        column__source_id=f"{world.variant.id}-{world.output.id}-sourceid-{world.metric.id}",
        deleted=False,
    )
    assert reason_of(result) == SENTENCE
    assert result.status == CellStatus.ERROR.value


@override_settings(CUSTOM_CODE_EVALS_PAUSED=True)
def test_paused_builtin_template_is_charged_and_reaches_the_executor(
    executor, billing, builtin_template, organization, workspace
):
    world = dataset_world(organization, workspace, builtin_template)
    with workspace_context(None):
        dataset_runner(world.metric).run_evaluation_for_row(world.row.id)

    assert [request["code"] for request in executor] == [BUILTIN_CODE]
    billing.charge.assert_called_once()
    assert reason_of(evaluation_cell(world)) != SENTENCE


@override_settings(CUSTOM_CODE_EVALS_PAUSED=False)
def test_without_the_setting_customer_template_is_charged_and_runs(
    executor, billing, customer_template, organization, workspace
):
    world = dataset_world(organization, workspace, customer_template)
    with workspace_context(None):
        dataset_runner(world.metric).run_evaluation_for_row(world.row.id)

    assert [request["code"] for request in executor] == [CUSTOMER_CODE]
    billing.charge.assert_called_once()
    assert reason_of(evaluation_cell(world)) != SENTENCE
