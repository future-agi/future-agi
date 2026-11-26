"""Database-backed regressions for the pre-side-effect evaluation read boundary."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.db import OperationalError, connection, transaction
from psycopg.errors import ConnectionTimeout, QueryCanceled

from model_hub.models.choices import (
    CellStatus,
    DataTypeChoices,
    SourceChoices,
    StatusType,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.evals_metric import EvalTemplate, UserEvalMetric
from model_hub.models.experiments import ExperimentDatasetTable, ExperimentsTable
from model_hub.views.eval_runner import EvaluationRunner
from model_hub.views.experiment_runner import (
    ExperimentRunner,
    check_and_update_experiment_status,
)
from model_hub.views.utils.evals import process_eval_for_single_row

pytestmark = pytest.mark.django_db(transaction=True)


def connection_timeout():
    error = OperationalError("connection timeout expired")
    error.__cause__ = ConnectionTimeout("connection timeout expired")
    return error


class ReadFault:
    """Inject at SQL execution so lazy queryset and relation reads are covered."""

    def __init__(self, table, failures=1, error_factory=connection_timeout):
        self.table = table
        self.failures = failures
        self.calls = 0
        self.error_factory = error_factory

    def __call__(self, execute, sql, params, many, context):
        is_metadata = sql.startswith("SELECT") and f'FROM "{self.table}"' in sql
        if self.table == "model_hub_column":
            # Do not fault later full-model reads made by cell persistence.
            is_metadata = is_metadata and sql.startswith(
                'SELECT "model_hub_column"."id", "model_hub_column"."data_type"'
            )
        if is_metadata:
            self.calls += 1
            if self.calls <= self.failures:
                raise self.error_factory()
        return execute(sql, params, many, context)


@pytest.fixture
def evaluation_case(organization, workspace, user, monkeypatch):
    dataset = Dataset.objects.create(
        name="metadata-recovery",
        organization=organization,
        workspace=workspace,
        user=user,
        column_order=[],
        column_config={},
    )
    column = Column.objects.create(
        dataset=dataset,
        name="input",
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    row = Row.objects.create(dataset=dataset, order=0)
    Cell.objects.create(dataset=dataset, row=row, column=column, value="sample")
    template = EvalTemplate.objects.create(
        name="metadata-check",
        organization=organization,
        workspace=workspace,
        config={"required_keys": ["text"], "output": "float"},
    )
    mappings = {"text": str(column.id)}
    metric = UserEvalMetric.objects.create(
        name="metadata-check",
        dataset=dataset,
        template=template,
        user=user,
        organization=organization,
        workspace=workspace,
        config={"mapping": mappings, "config": {}},
    )
    output_column = Column.objects.create(
        dataset=dataset,
        name="score",
        data_type=DataTypeChoices.FLOAT.value,
        source=SourceChoices.EVALUATION.value,
        source_id=str(metric.id),
    )
    output_cell = Cell.objects.create(
        dataset=dataset,
        row=row,
        column=output_column,
        status=CellStatus.RUNNING.value,
        value_infos=json.dumps({}),
    )
    runner = EvaluationRunner(
        user_eval_metric_id=str(metric.id),
        is_only_eval=True,
        format_output=True,
        organization_id=organization.id,
        workspace_id=workspace.id,
    )
    runner.user_eval_metric = metric
    runner.dataset = dataset
    runner.column = output_column
    runner.replace_column_id = str(output_column.id)
    runner.eval_template = template
    runner.load_user_eval_metric = MagicMock()
    runner.update_config_list_values = MagicMock(return_value={})
    runner._get_input_token_count = MagicMock(return_value=1)
    runner._prepare_mapping_data = MagicMock(return_value=(["text"], ["sample"]))
    runner.map_fields = MagicMock(return_value={"text": "sample"})
    evaluator = MagicMock()
    evaluator.format_result = None
    evaluator.run.return_value = SimpleNamespace(
        eval_results=[
            {
                "data": 1.0,
                "reason": "matched",
                "metadata": {},
            }
        ]
    )
    runner._create_eval_instance = MagicMock(return_value=evaluator)
    runner._run_evaluation = MagicMock(return_value=(None, [], [], {}, evaluator, None))
    runner._format_response = MagicMock(return_value={"data": 1.0, "reason": "matched"})
    runner.format_output = MagicMock(return_value="1.0")
    runner._emit_cell_usage = MagicMock()
    runner._handle_api_call_status = MagicMock()
    runner._create_reason_column = MagicMock()
    runner._create_reason_cell = MagicMock()
    log = MagicMock(config="{}", status="processing")
    logger = MagicMock(return_value=log)
    monkeypatch.setattr(
        "model_hub.views.eval_runner.log_and_deduct_cost_for_api_request", logger
    )
    statuses = SimpleNamespace(
        PROCESSING=SimpleNamespace(value="processing"),
        SUCCESS=SimpleNamespace(value="success"),
        ERROR=SimpleNamespace(value="error"),
    )
    monkeypatch.setattr("model_hub.views.eval_runner.APICallStatusChoices", statuses)
    monkeypatch.setattr("model_hub.views.utils.evals.APICallStatusChoices", statuses)
    monkeypatch.setattr(
        "model_hub.services.experiment_utils.is_user_eval_stopped", lambda _: False
    )
    monkeypatch.setattr(
        "model_hub.views.utils.evals.GroundTruthService.resolve_preview_examples",
        lambda **_: [],
    )
    # Keep real connection disposal and database queries; skip only the backoff wait.
    monkeypatch.setattr("model_hub.utils.eval_metadata.time.sleep", MagicMock())
    return SimpleNamespace(
        runner=runner,
        row_id=row.id,
        dataset=dataset,
        mappings=mappings,
        output_cell=output_cell,
        user=user,
        template=template,
        workspace=workspace,
        logger=logger,
        log=log,
        evaluator=evaluator,
    )


def run_case(case, path):
    runner = case.runner
    # No relation cache: the workspace test exercises Django's lazy descriptor.
    row = Row.objects.get(id=case.row_id)
    if path == "threaded":
        runner._process_and_create_cell(row, case.mappings, case.dataset, runner.column)
    elif path == "direct":
        runner.run_evaluation_for_row(case.row_id)
    else:
        return process_eval_for_single_row(
            runner,
            case.user,
            row,
            case.mappings,
            {},
            False,
            None,
            case.template,
            False,
            "dataset",
            case.dataset.id,
        )
    case.output_cell.refresh_from_db()
    return json.loads(case.output_cell.value_infos)


def run_threaded_unstored(case, save_error):
    """Run the threaded row when no cell write can store its result.

    How the runner reports an unstored result to its caller is the cell
    persistence contract, not this read boundary's: it may log and return, or
    raise so update_cell counts the row as failed. update_cell consumes either
    outcome without running the row again, so accept exactly the injected write
    failure here and pin only that nothing after the metadata reads is replayed.
    """
    try:
        run_case(case, "threaded")
    except OperationalError as error:
        assert error is save_error


@pytest.mark.parametrize("path", ["threaded", "direct", "preview"])
@pytest.mark.parametrize("table", ["model_hub_column", "accounts_workspace"])
def test_transient_metadata_read_recovers_once(evaluation_case, path, table):
    case = evaluation_case
    fault = ReadFault(table)
    with (
        connection.execute_wrapper(fault),
        patch.object(connection, "close", wraps=connection.close) as close,
    ):
        result = run_case(case, path)
    assert result["reason"] == "matched"
    assert fault.calls == 2
    assert close.call_count >= 1
    case.logger.assert_called_once()
    assert case.logger.call_args.kwargs["workspace"].id == case.workspace.id
    if path == "preview":
        case.evaluator.run.assert_called_once_with(text="sample")
        assert result["output"] == "1.0"
    else:
        assert case.output_cell.status == CellStatus.PASS.value
        assert case.output_cell.value == "1.0"
        case.runner._run_evaluation.assert_called_once()
        case.runner._emit_cell_usage.assert_called_once()


@pytest.mark.parametrize("path", ["threaded", "direct", "preview"])
@pytest.mark.parametrize("table", ["model_hub_column", "accounts_workspace"])
def test_exhausted_metadata_is_infrastructure_not_evaluator_failure(
    evaluation_case, path, table
):
    case = evaluation_case
    case.runner.user_eval_metric.config["reason_column"] = True
    fault = ReadFault(table, failures=10)
    with connection.execute_wrapper(fault):
        result = run_case(case, path)
    assert fault.calls == 3
    assert result["error_code"] == "EVALUATION_METADATA_UNAVAILABLE"
    assert result["reason"].startswith(
        "Evaluation not started: database temporarily unavailable"
    )
    case.logger.assert_not_called()
    case.runner._run_evaluation.assert_not_called()
    case.evaluator.run.assert_not_called()
    case.runner._emit_cell_usage.assert_not_called()
    if path == "preview":
        case.runner._create_reason_cell.assert_not_called()
    else:
        case.runner._create_reason_cell.assert_called_once()
        assert case.runner._create_reason_cell.call_args.args[3:] == (
            result,
            result["reason"],
            CellStatus.ERROR.value,
        )
    case.runner._handle_api_call_status.assert_not_called()


def existing_reason_cell(case):
    # Use the real column/cell writers: a mock misses stale or RUNNING siblings.
    del case.runner._create_reason_column
    del case.runner._create_reason_cell
    column = case.runner._create_reason_column(
        case.dataset, f"{case.runner.user_eval_metric.name}-reason"
    )
    return Cell.objects.create(
        dataset=case.dataset,
        row_id=case.row_id,
        column=column,
        value="previous successful reasoning",
        value_infos=json.dumps({"reason": "previous successful reasoning"}),
        status=CellStatus.PASS.value,
    )


def assert_infrastructure_reason(case, reason_cell):
    case.output_cell.refresh_from_db()
    reason_cell.refresh_from_db()
    result = json.loads(case.output_cell.value_infos)
    assert case.output_cell.status == CellStatus.ERROR.value
    assert reason_cell.status == CellStatus.ERROR.value
    assert result["error_code"] == "EVALUATION_METADATA_UNAVAILABLE"
    assert json.loads(reason_cell.value_infos) == result
    assert reason_cell.value == result["reason"]
    assert (
        Cell.objects.filter(
            column=reason_cell.column, row_id=case.row_id, deleted=False
        ).count()
        == 1
    )
    case.logger.assert_not_called()
    case.runner._run_evaluation.assert_not_called()
    case.runner._emit_cell_usage.assert_not_called()
    case.runner._handle_api_call_status.assert_not_called()


@pytest.mark.parametrize("path", ["threaded", "direct"])
@pytest.mark.parametrize("table", ["model_hub_column", "accounts_workspace"])
def test_exhaustion_replaces_existing_dataset_reason(evaluation_case, path, table):
    case = evaluation_case
    case.runner.user_eval_metric.config["reason_column"] = True
    reason_cell = existing_reason_cell(case)
    fault = ReadFault(table, failures=10)
    with connection.execute_wrapper(fault):
        run_case(case, path)
    assert fault.calls == 3
    assert_infrastructure_reason(case, reason_cell)


@pytest.mark.parametrize("table", ["model_hub_column", "accounts_workspace"])
def test_exhaustion_terminalizes_experiment_rerun_reason(evaluation_case, table):
    case = evaluation_case
    runner = case.runner
    # Experiments require reasons even when the dataset's legacy flag is false.
    runner.user_eval_metric.config["reason_column"] = False
    runner.user_eval_metric.save(update_fields=["config"])
    case.template.config["eval_type_id"] = "CustomCodeEval"
    case.template.save(update_fields=["config"])
    experiment = ExperimentsTable.objects.create(
        name="metadata-rerun", dataset=case.dataset, status=StatusType.RUNNING.value
    )
    experiment.user_eval_template_ids.add(runner.user_eval_metric)
    edt = ExperimentDatasetTable.objects.create(
        name="metadata-rerun-output",
        experiment=experiment,
        status=StatusType.RUNNING.value,
    )
    # The runner's source column is a completed prompt output; the persisted
    # evaluation and reason columns are paired by the real experiment source IDs.
    runner.column.source = SourceChoices.EXPERIMENT.value
    runner.column.source_id = str(edt.id)
    runner.column.save(update_fields=["source", "source_id"])
    case.output_cell.status = CellStatus.PASS.value
    case.output_cell.save(update_fields=["status"])
    edt.columns.add(runner.column)
    runner.experiment_dataset = edt
    runner.is_only_eval = False
    eval_column = runner._create_or_update_column(
        case.dataset, runner._get_column_config(case.dataset)
    )
    case.output_cell = Cell.objects.create(
        dataset=case.dataset,
        row_id=case.row_id,
        column=eval_column,
        value="1.0",
        status=CellStatus.PASS.value,
    )
    reason_cell = existing_reason_cell(case)
    assert edt.columns.filter(id=reason_cell.column_id).exists()

    # Execute the real rerun initializer, not a hand-written status reset.
    experiment_runner = ExperimentRunner(experiment.id)
    experiment_runner.load_experiment()
    experiment_runner.empty_or_create_evals_column()
    reason_cell.refresh_from_db()
    case.output_cell.refresh_from_db()
    assert reason_cell.status == case.output_cell.status == CellStatus.RUNNING.value
    assert not check_and_update_experiment_status(experiment.id)

    fault = ReadFault(table, failures=10)
    with connection.execute_wrapper(fault):
        runner._process_and_create_cell(
            Row.objects.get(id=case.row_id), case.mappings, case.dataset, eval_column
        )
    assert fault.calls == 3
    assert check_and_update_experiment_status(experiment.id)
    assert_infrastructure_reason(case, reason_cell)
    experiment.refresh_from_db()
    reason_cell.column.refresh_from_db()
    assert experiment.status == StatusType.COMPLETED.value
    assert reason_cell.column.status == StatusType.COMPLETED.value
    assert not Cell.objects.filter(
        column__experiments_dataset_column=edt, status=CellStatus.RUNNING.value
    ).exists()


@pytest.mark.parametrize("reason_enabled,optimize", [(False, None), (True, object())])
def test_exhaustion_preserves_reason_opt_out(evaluation_case, reason_enabled, optimize):
    case = evaluation_case
    case.runner.user_eval_metric.config["reason_column"] = reason_enabled
    case.runner.optimize = optimize
    with connection.execute_wrapper(ReadFault("model_hub_column", failures=10)):
        run_case(case, "threaded")
    case.runner._create_reason_column.assert_not_called()
    case.runner._create_reason_cell.assert_not_called()


@pytest.mark.parametrize("path", ["threaded", "direct"])
def test_reason_persistence_failure_never_replays_evaluation(evaluation_case, path):
    case = evaluation_case
    case.runner.user_eval_metric.config["reason_column"] = True
    case.runner._create_reason_cell.side_effect = connection_timeout()
    fault = ReadFault("model_hub_column", failures=10)
    with connection.execute_wrapper(fault):
        with pytest.raises(OperationalError, match="connection timeout expired"):
            run_case(case, path)
    assert fault.calls == 3
    case.runner._create_reason_cell.assert_called_once()
    case.logger.assert_not_called()
    case.runner._run_evaluation.assert_not_called()
    case.runner._emit_cell_usage.assert_not_called()


def test_invalid_mapping_stays_terminal(evaluation_case):
    case = evaluation_case
    case.mappings["text"] = "not-a-column-id"
    result = run_case(case, "threaded")
    assert case.output_cell.status == CellStatus.ERROR.value
    assert "error_code" not in result
    case.logger.assert_not_called()
    case.runner._run_evaluation.assert_not_called()


def test_missing_workspace_stays_terminal(evaluation_case):
    case = evaluation_case
    with patch(
        "accounts.models.workspace.Workspace.from_db",
        side_effect=ValueError("Workspace missing"),
    ):
        result = run_case(case, "threaded")
    assert case.output_cell.status == CellStatus.ERROR.value
    assert "error_code" not in result
    case.logger.assert_not_called()


@pytest.mark.parametrize(
    "phase", ["logging", "provider", "result_save", "usage", "cell_save"]
)
def test_failures_after_metadata_never_replay_side_effects(evaluation_case, phase):
    case = evaluation_case
    if phase == "logging":
        case.logger.side_effect = connection_timeout()
    elif phase == "provider":
        case.runner._run_evaluation.side_effect = connection_timeout()
    elif phase == "result_save":
        case.log.save.side_effect = connection_timeout()
    elif phase == "usage":
        case.runner._emit_cell_usage.side_effect = connection_timeout()
    save_error = connection_timeout()
    if phase == "cell_save":
        with patch.object(Cell, "save", side_effect=save_error):
            run_threaded_unstored(case, save_error)
        case.output_cell.refresh_from_db()
        # The result was never stored, so it must not read as a pass.
        assert case.output_cell.status == CellStatus.RUNNING.value
    else:
        with patch("model_hub.utils.eval_metadata.time.sleep"):
            run_case(case, "threaded")
    case.logger.assert_called_once()
    assert case.runner._run_evaluation.call_count == (0 if phase == "logging" else 1)
    assert case.runner._emit_cell_usage.call_count <= 1


def test_outage_during_error_persistence_does_not_restart_evaluation(evaluation_case):
    case = evaluation_case
    save_error = connection_timeout()
    with (
        connection.execute_wrapper(ReadFault("model_hub_column", failures=10)),
        patch.object(Cell, "save", side_effect=save_error),
    ):
        run_threaded_unstored(case, save_error)
    case.output_cell.refresh_from_db()
    # The outage can prevent persistence; do not claim a final error was stored.
    assert case.output_cell.status == CellStatus.RUNNING.value
    case.logger.assert_not_called()
    case.runner._run_evaluation.assert_not_called()


def test_retry_does_not_close_or_replay_inside_atomic(evaluation_case):
    from model_hub.utils.eval_metadata import (
        EvaluationMetadataUnavailable,
        read_evaluation_column_types,
    )

    fault = ReadFault("model_hub_column")
    with (
        transaction.atomic(),
        connection.execute_wrapper(fault),
        patch.object(connection, "close") as close,
    ):
        with pytest.raises(EvaluationMetadataUnavailable):
            read_evaluation_column_types(evaluation_case.mappings.values())
    assert fault.calls == 1
    close.assert_not_called()


@pytest.mark.parametrize("has_columns", [False, True])
def test_column_types_accept_materialized_values(has_columns):
    from uuid import UUID

    from model_hub.utils.eval_metadata import read_evaluation_column_types

    column_id = UUID("00000000-0000-0000-0000-000000000001")
    rows = [{"id": column_id, "data_type": "text"}] if has_columns else []
    with patch.object(Column.objects, "filter") as columns:
        columns.return_value.db = "default"
        columns.return_value.values.return_value = rows
        result = read_evaluation_column_types(iter([column_id]))

    assert result == ({str(column_id): "text"} if has_columns else {})
    columns.assert_called_once_with(id__in=[column_id])
    columns.return_value.values.assert_called_once_with("id", "data_type")


def test_materializing_column_values_is_inside_retry_boundary():
    from model_hub.utils.eval_metadata import read_evaluation_column_types

    rows = [{"id": "column-id", "data_type": "text"}]
    with (
        patch.object(Column.objects, "filter") as columns,
        patch("model_hub.utils.eval_metadata.connections") as connections,
        patch("model_hub.utils.eval_metadata.time.sleep") as sleep,
    ):
        columns.return_value.db = "metadata"
        columns.return_value.values.side_effect = [connection_timeout(), rows]
        connections["metadata"].in_atomic_block = False

        assert read_evaluation_column_types(iter(["column-id"])) == {
            "column-id": "text"
        }

    columns.assert_called_once_with(id__in=["column-id"])
    assert columns.return_value.values.call_count == 2
    connections.__getitem__.assert_called_with("metadata")
    connections["metadata"].close.assert_called_once()
    sleep.assert_called_once_with(0.25)


def test_statement_timeout_is_not_retried(evaluation_case):
    from model_hub.utils.eval_metadata import read_evaluation_column_types

    error = OperationalError("statement timeout")
    error.__cause__ = QueryCanceled("statement timeout")
    fault = ReadFault("model_hub_column", error_factory=lambda: error)
    with connection.execute_wrapper(fault):
        with pytest.raises(OperationalError, match="statement timeout"):
            read_evaluation_column_types(evaluation_case.mappings.values())
    assert fault.calls == 1
