"""Ordinary composites keep their run policy beside dataset juries.

These composites carry no jury definition, so the composed jury code must not
change how a run primes its result cells, keeps a settled experiment
failed-row outcome or resolves a saved version.
"""

import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

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
    EvalTemplateVersion,
    UserEvalMetric,
)
from model_hub.models.experiments import ExperimentDatasetTable, ExperimentsTable
from model_hub.services.jury_binding import prepare_jury_binding
from model_hub.services.jury_versions import jury_result_column_type
from model_hub.tasks.composite_runner import CompositeEvaluationRunner
from tfc.temporal.experiments.run_policy import failed_row_values

JUDGE = "model_hub.views.utils.evals.run_eval_func"
RUNNING = ("", CellStatus.RUNNING.value)


def _judge(*_args, **_kwargs):
    return {"output": 0.8, "reason": "fixture judge", "output_type": "score"}


def _states(*columns):
    """(value, status) of every live cell in these columns, by (column, row)."""
    return {
        (cell.column_id, cell.row_id): (cell.value, cell.status)
        for cell in Cell.objects.filter(column__in=columns, deleted=False)
    }


def _rows(dataset, column, statuses):
    """One row per status; its cell in `column` holds "row-<order>"."""
    rows = []
    for order, status in enumerate(statuses):
        row = Row.objects.create(dataset=dataset, order=order)
        Cell.objects.create(
            dataset=dataset,
            column=column,
            row=row,
            value=f"row-{order}",
            status=status,
        )
        rows.append(row)
    return rows


def _bind(composite, dataset, source, user):
    return UserEvalMetric.objects.create(
        name="ordinary-binding",
        organization=dataset.organization,
        workspace=dataset.workspace,
        template=composite,
        dataset=dataset,
        user=user,
        config={"mapping": {"input": str(source.id)}},
    )


@pytest.fixture
def composite(db, organization, workspace):
    child = EvalTemplate.no_workspace_objects.create(
        name="ordinary-child",
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        config={"output": "score", "eval_type_id": "DeterministicEvaluator"},
        output_type_normalized="percentage",
        pass_threshold=0.5,
    )
    parent = EvalTemplate.no_workspace_objects.create(
        name="ordinary-composite",
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        template_type="composite",
        aggregation_enabled=True,
        aggregation_function="avg",
        pass_threshold=0.5,
        config={},
    )
    CompositeEvalChild.objects.create(parent=parent, child=child, order=0, weight=1.0)
    return parent


@pytest.fixture
def dataset_run(composite, organization, workspace, user):
    dataset = Dataset.objects.create(
        name="ordinary-rows",
        organization=organization,
        workspace=workspace,
        source=DatasetSourceChoices.BUILD.value,
    )
    source = Column.objects.create(
        name="input",
        dataset=dataset,
        data_type="text",
        source=SourceChoices.OTHERS.value,
    )
    rows = _rows(dataset, source, [CellStatus.PASS.value] * 2)
    metric = _bind(composite, dataset, source, user)
    result = Column.objects.create(
        **CompositeEvaluationRunner.build_column_config(metric, dataset)
    )
    reason = Column.objects.create(
        name=f"{metric.name}-reason",
        data_type="text",
        source=SourceChoices.EVALUATION_REASON.value,
        source_id=f"{result.id}-sourceid-{metric.id}",
        dataset=dataset,
    )
    return SimpleNamespace(
        dataset=dataset, rows=rows, metric=metric, result=result, reason=reason
    )


@pytest.fixture
def experiment_run(composite, organization, workspace, user):
    source = Dataset.objects.create(
        name="experiment-source", organization=organization, workspace=workspace
    )
    snapshot = Dataset.objects.create(
        name="experiment-snapshot", organization=organization, workspace=workspace
    )
    experiment = ExperimentsTable.no_workspace_objects.create(
        name="ordinary-experiment", dataset=source, snapshot_dataset=snapshot
    )
    table = ExperimentDatasetTable.no_workspace_objects.create(
        name="output-table", experiment=experiment
    )
    output = Column.objects.create(
        name="output",
        dataset=snapshot,
        data_type="text",
        source=SourceChoices.EXPERIMENT.value,
        source_id=str(table.id),
    )
    table.columns.add(output)
    # Rows whose output failed, passed, and passed on a re-run after failing.
    rows = _rows(
        snapshot,
        output,
        [CellStatus.ERROR.value, CellStatus.PASS.value, CellStatus.PASS.value],
    )
    metric = _bind(composite, snapshot, output, user)
    experiment.user_eval_template_ids.add(metric)
    return SimpleNamespace(
        snapshot=snapshot, table=table, output=output, rows=rows, metric=metric
    )


@pytest.mark.django_db
def test_run_primes_result_and_reason_cells_before_evaluating(dataset_run):
    run = dataset_run
    for row in run.rows:
        for column, value in ((run.result, "0.1000"), (run.reason, "stale")):
            Cell.objects.create(
                dataset=run.dataset,
                column=column,
                row=row,
                value=value,
                status=CellStatus.PASS.value,
                value_infos=json.dumps({"stale": True}),
            )
    seen = []

    def judge(*args, **kwargs):
        seen.append(_states(run.result, run.reason))
        return _judge(*args, **kwargs)

    with patch(JUDGE, side_effect=judge):
        CompositeEvaluationRunner(run.metric.id).run_prompt()

    # Every aggregate and reason cell was reset before the first row ran.
    assert list(seen[0].values()) == [RUNNING] * 4
    statuses = {status for _, status in _states(run.result, run.reason).values()}
    assert statuses == {CellStatus.PASS.value}


@pytest.mark.django_db
def test_failed_row_settles_its_primed_placeholders_as_errors(dataset_run):
    run = dataset_run
    with patch(
        "model_hub.tasks.composite_runner.execute_composite_children_sync",
        side_effect=RuntimeError("judge unavailable"),
    ):
        CompositeEvaluationRunner(run.metric.id).run_prompt()

    # No cell existed: the error path can only settle the primed placeholders.
    for row in run.rows:
        result = Cell.objects.get(column=run.result, row=row, deleted=False)
        reason = Cell.objects.get(column=run.reason, row=row, deleted=False)
        assert result.status == CellStatus.ERROR.value
        assert "judge unavailable" in result.value
        assert reason.status == CellStatus.ERROR.value
        assert reason.value == "Evaluation failed"


@pytest.mark.django_db
@pytest.mark.parametrize("retype", [False, True], ids=["same-type", "type-switch"])
@pytest.mark.parametrize("policy", ["zero", "exclude"])
def test_experiment_run_keeps_settled_failed_row_outcomes(
    experiment_run, policy, retype
):
    run = experiment_run
    failed, fresh, recovered = run.rows
    config = CompositeEvaluationRunner.build_column_config(
        run.metric, run.snapshot, experiment_dataset=run.table, column=run.output
    )
    assert config["data_type"] == "float"
    if retype:
        # Written by a saved version that did not aggregate.
        config = {**config, "data_type": "text"}
    result = Column.objects.create(**config)
    run.table.columns.add(result)
    outcome = failed_row_values(policy, "upstream output failed")
    for row in (failed, recovered):
        Cell.objects.create(dataset=run.snapshot, column=result, row=row, **outcome)
    Cell.objects.create(
        dataset=run.snapshot,
        column=result,
        row=fresh,
        value="0.1000",
        status=CellStatus.PASS.value,
    )
    runner = CompositeEvaluationRunner(
        run.metric.id, experiment_dataset=run.table, column=run.output
    )
    seen = []

    def judge(*args, **kwargs):
        seen.append(_states(result))
        return _judge(*args, **kwargs)

    with patch(JUDGE, side_effect=judge) as judged:
        runner.run_prompt()

    result.refresh_from_db()
    assert result.data_type == "float"
    assert Column.objects.filter(source_id=result.source_id).count() == 1
    settled = (outcome["value"], outcome["status"])
    # The failed row is neither reset nor evaluated; the recovered row, whose
    # output passed on re-run, goes back to the evaluator with the fresh one.
    assert seen[0] == {
        (result.id, failed.id): settled,
        (result.id, fresh.id): RUNNING,
        (result.id, recovered.id): RUNNING,
    }
    judged_inputs = [call.args[1]["input"] for call in judged.call_args_list]
    assert judged_inputs == ["row-1", "row-2"]
    kept = Cell.objects.get(column=result, row=failed, deleted=False)
    assert (kept.value, kept.status) == settled
    assert kept.value_infos == outcome["value_infos"]
    for row in (fresh, recovered):
        cell = Cell.objects.get(column=result, row=row, deleted=False)
        assert cell.status == CellStatus.PASS.value
        assert json.loads(cell.value_infos)["aggregate_score"] == pytest.approx(0.8)


@pytest.mark.django_db
def test_soft_deleted_pin_falls_back_for_an_ordinary_composite(
    dataset_run, composite, organization, workspace, user
):
    run = dataset_run
    stale = EvalTemplateVersion.objects.create(
        eval_template=composite,
        version_number=1,
        is_default=False,
        config_snapshot={"aggregation_enabled": False},
        deleted=True,
    )
    EvalTemplateVersion.objects.create(
        eval_template=composite,
        version_number=2,
        is_default=True,
        config_snapshot={"aggregation_enabled": True},
    )
    run.metric.pinned_version = stale
    run.metric.save(update_fields=["pinned_version"])
    # The shared pin rule falls back to the default for a soft-deleted pin.
    fallback = EvalTemplateVersion.objects.resolve_for_metric(run.metric)
    assert fallback.version_number == 2

    runner = CompositeEvaluationRunner(run.metric.id)
    assert runner.template.id == composite.id
    assert runner.jury_version is None
    with patch(JUDGE, side_effect=_judge):
        runner.run_prompt()
    statuses = {status for _, status in _states(run.result).values()}
    assert statuses == {CellStatus.PASS.value}
    # Dataset precreation and the binding writers take the same ordinary path.
    assert jury_result_column_type(run.metric, "float") == "float"
    request = SimpleNamespace(
        organization=organization,
        workspace=workspace,
        user=user,
    )
    payload = {"config": {"mapping": run.metric.config["mapping"]}}
    unchanged = deepcopy(payload)
    prepare_jury_binding(request, run.dataset, payload, metric=run.metric)
    assert payload == unchanged


@pytest.mark.django_db
@pytest.mark.parametrize(
    "aggregate", [True, False], ids=["text-to-float", "float-to-text"]
)
def test_row_scoped_rerun_after_a_type_switch_keeps_the_other_rows(
    dataset_run, composite, aggregate
):
    run = dataset_run
    rerun, other = run.rows
    # Both rows completed under a saved version with the other result type.
    run.result.data_type = "text" if aggregate else "float"
    run.result.save(update_fields=["data_type"])
    composite.aggregation_enabled = aggregate
    composite.save(update_fields=["aggregation_enabled"])
    for row in run.rows:
        Cell.objects.create(
            dataset=run.dataset,
            column=run.result,
            row=row,
            value=f"previous-{row.order}",
            status=CellStatus.PASS.value,
            value_infos=json.dumps({"previous": row.order}),
        )

    with patch(JUDGE, side_effect=_judge) as judged:
        CompositeEvaluationRunner(run.metric.id).run_prompt(row_ids=[str(rerun.id)])

    run.result.refresh_from_db()
    assert run.result.data_type == ("float" if aggregate else "text")
    assert Column.objects.filter(source_id=run.result.source_id).count() == 1
    assert [call.args[1]["input"] for call in judged.call_args_list] == ["row-0"]
    # The row outside the rerun keeps its completed result, never left running.
    kept = Cell.objects.get(column=run.result, row=other, deleted=False)
    assert (kept.value, kept.status, kept.value_infos) == (
        "previous-1",
        CellStatus.PASS.value,
        json.dumps({"previous": 1}),
    )
    rewritten = Cell.objects.get(column=run.result, row=rerun, deleted=False)
    assert rewritten.status == CellStatus.PASS.value
    assert json.loads(rewritten.value_infos)["aggregation_enabled"] is aggregate
