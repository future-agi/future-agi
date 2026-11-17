"""Historical HTTP reason identities: preserve rows, resolve only an empty twin.

Tests-first specifications, not execution receipts. These exercise real ORM
lookups, the fenced worker method, and the actual Edit HTTP response. The native
case uses an admitted stored-output run but is not an orchestration/E2E claim.
No import of the new resolver is needed for collection on the frozen base.
"""

import pytest

from model_hub.models.choices import DataTypeChoices, SourceChoices
from model_hub.models.develop_dataset import Cell, Column
from model_hub.models.native_experiment_runs import NativeRunBinding
from model_hub.tests.network_isolation import no_external_lookups  # noqa: F401
from model_hub.tests.test_experiment_eval_update_http_source import _update
from model_hub.tests.test_experiment_eval_update_http_source import (
    eval_template as eval_template,
)
from model_hub.tests.test_experiment_eval_update_http_source import (
    prompt_experiment as prompt_experiment,
)
from model_hub.tests.test_http_canonical_evidence import _evaluator, _stage
from model_hub.tests.test_http_canonical_evidence import tenants as tenants
from model_hub.tests.test_http_stored_evaluations import (
    _rescore,
    _scoring_only,
    _start_stored,
    _terminal,
)
from model_hub.views.eval_runner import NativeEvaluationRunner

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("urlconf_loaded_without_workspace_context"),
]


@pytest.fixture
def reason_world(tenants):
    world = _stage(tenants, evaluated=True)
    world.evaluator, world.score, world.canonical = _evaluator(world)
    assert world.canonical.name == f"{world.metric.name}-{world.output.name}-reason"
    assert world.score.name == f"{world.metric.name}-{world.output.name}"
    return world


def _twin(world, **overrides):
    values = {
        "name": f"{world.metric.name}-{world.score.name}-reason",
        "data_type": DataTypeChoices.TEXT.value,
        "dataset": world.experiment.snapshot_dataset,
        "source": SourceChoices.EVALUATION_REASON.value,
        "source_id": f"{world.score.pk}-sourceid-{world.metric.pk}",
    }
    values.update(overrides)
    return Column.no_workspace_objects.create(**values)


def _cell(world, column, *, row=None, **payload):
    cell = Cell.no_workspace_objects.create(
        dataset=world.experiment.snapshot_dataset,
        row=row if row is not None else world.links[0].snapshot_row,
        column=column,
        value=None,
        value_infos=None,
        feedback_info=None,
        column_metadata=None,
    )
    # Historical JSON includes scalars/serialized strings, not just the shapes
    # accepted by today's optional metadata validator. Persist actual DB values.
    if payload:
        Cell.no_workspace_objects.filter(pk=cell.pk).update(**payload)
    return cell


def _records(world):
    datasets = [world.dataset.pk, world.experiment.snapshot_dataset_id]
    return {
        "columns": list(
            Column.all_objects.filter(dataset_id__in=datasets)
            .order_by("pk")
            .values()
        ),
        "cells": list(
            Cell.all_objects.filter(column__dataset_id__in=datasets)
            .order_by("pk")
            .values()
        ),
    }


def _identity(world):
    return list(
        Column.no_workspace_objects.filter(
            dataset=world.experiment.snapshot_dataset,
            source=SourceChoices.EVALUATION_REASON.value,
            source_id=world.canonical.source_id,
            deleted=False,
        ).order_by("pk")
    )


def _resolve(world, door):
    if door == "worker":
        return world.evaluator._create_reason_column(
            world.experiment.snapshot_dataset, "", parent_column=world.score
        )
    with _scoring_only() as starts:
        response = _rescore(world, "edit", run=False)
    for start in starts:
        start.assert_not_called()
    return response


def _assert_success(world, door):
    result = _resolve(world, door)
    if door == "worker":
        assert result.pk == world.canonical.pk
    else:
        assert result.status_code == 200, result.content
        assert result.json()["status"] is True


def _assert_diagnostic(message, columns):
    assert "HTTP reason" in message
    for column in columns:
        assert str(column.pk) in message
        assert column.name in message
    assert "preserv" in message.lower()
    assert "reconcil" in message.lower()
    assert "export" in message.lower()
    assert "retry" in message.lower()


def _assert_refused(world, door):
    columns = _identity(world)
    before = _records(world)
    metric_before = type(world.metric).no_workspace_objects.values().get(
        pk=world.metric.pk
    )
    if door == "worker":
        with pytest.raises(ValueError) as error:
            _resolve(world, door)
        message = str(error.value)
    else:
        response = _resolve(world, door)
        assert response.status_code == 400, response.content
        message = response.content.decode()
    _assert_diagnostic(message, columns)
    assert _records(world) == before
    assert type(world.metric).no_workspace_objects.values().get(
        pk=world.metric.pk
    ) == metric_before


@pytest.mark.parametrize("door", ["worker", "edit"])
@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(None, id="no-cells"),
        pytest.param({}, id="null-cell"),
        pytest.param({"value": ""}, id="empty-value-cell"),
        pytest.param(
            {"value": None, "value_infos": [], "feedback_info": {}, "column_metadata": {}},
            id="null-empty-containers",
        ),
        pytest.param(
            {"value": "", "value_infos": "", "feedback_info": "", "column_metadata": ""},
            id="empty-metadata-strings",
        ),
        pytest.param(
            {"value": "preserved deleted value", "feedback_info": {"note": "keep"}, "deleted": True},
            id="deleted-populated-cell",
        ),
    ],
)
def test_empty_historical_twin_is_ignored_without_mutation(reason_world, door, payload):
    world = reason_world
    twin = _twin(world)
    _cell(world, world.canonical, value="preserved canonical reason")
    if payload is not None:
        _cell(world, twin, **payload)
    before = _records(world)
    _assert_success(world, door)
    assert _records(world) == before
    assert {column.pk for column in _identity(world)} == {world.canonical.pk, twin.pk}


POPULATED = [
    pytest.param({"value": "saved reason"}, id="one-nonempty-value"),
    pytest.param({"value": " "}, id="whitespace-value"),
    pytest.param({"value": "0"}, id="zero-text-value"),
    pytest.param({"value": "false"}, id="false-text-value"),
] + [
    pytest.param({field: value}, id=f"{field}-{label}")
    for field in ("feedback_info", "value_infos", "column_metadata")
    for label, value in (
        ("object", {"note": "keep"}),
        ("list", ["keep"]),
        ("false", False),
        ("zero", 0),
        ("serialized-object", "{}"),
        ("serialized-list", "[]"),
        ("serialized-null", "null"),
    )
]


@pytest.mark.parametrize("door", ["worker", "edit"])
@pytest.mark.parametrize("payload", POPULATED)
def test_populated_historical_twin_refuses_with_named_guidance(reason_world, door, payload):
    world = reason_world
    twin = _twin(world)
    _cell(world, twin)  # An earlier empty row must not hide the populated row.
    _cell(world, twin, row=world.links[1].snapshot_row, **payload)
    _assert_refused(world, door)


@pytest.mark.parametrize("door", ["worker", "edit"])
@pytest.mark.parametrize(
    "ambiguity",
    [
        "third-match", "third-wrong-type", "duplicate-canonical", "missing-canonical",
        "wrong-twin-name", "wrong-canonical-name", "wrong-twin-type", "wrong-canonical-type",
    ],
)
def test_ambiguous_identity_refuses_without_hiding_matches(reason_world, door, ambiguity):
    world = reason_world
    twin = _twin(world)
    if ambiguity == "third-match":
        _twin(world)
    elif ambiguity == "third-wrong-type":
        _twin(world, name="extra reason", data_type=DataTypeChoices.JSON.value)
    elif ambiguity == "duplicate-canonical":
        Column.no_workspace_objects.filter(pk=twin.pk).update(name=world.canonical.name)
    elif ambiguity == "missing-canonical":
        Column.no_workspace_objects.filter(pk=world.canonical.pk).update(name=twin.name)
    elif ambiguity == "wrong-twin-name":
        Column.no_workspace_objects.filter(pk=twin.pk).update(name=f"{twin.name}-other")
    elif ambiguity == "wrong-canonical-name":
        Column.no_workspace_objects.filter(pk=world.canonical.pk).update(name="old-reason")
    elif ambiguity == "wrong-twin-type":
        Column.no_workspace_objects.filter(pk=twin.pk).update(data_type=DataTypeChoices.JSON.value)
    else:
        Column.no_workspace_objects.filter(pk=world.canonical.pk).update(data_type=DataTypeChoices.JSON.value)
    _assert_refused(world, door)


@pytest.mark.parametrize("door", ["worker", "edit"])
@pytest.mark.parametrize("with_twin", [False, True], ids=["single-control", "pair"])
def test_resolution_keeps_dataset_source_source_id_and_deleted_scope(reason_world, door, with_twin):
    world = reason_world
    if with_twin:
        _twin(world)
    _twin(world, dataset=world.dataset)
    _twin(world, source=SourceChoices.EVALUATION.value)
    _twin(world, source_id=f"different-{world.canonical.source_id}")
    deleted = _twin(world)
    _cell(world, deleted, value="never clean up this excluded identity")
    Column.no_workspace_objects.filter(pk=deleted.pk).update(deleted=True)
    before = _records(world)
    _assert_success(world, door)
    assert _records(world) == before
    assert len(_identity(world)) == (2 if with_twin else 1)


@pytest.mark.parametrize("door", ["worker", "edit"])
def test_no_twin_preserves_existing_reason_and_cells(reason_world, door):
    world = reason_world
    _cell(world, world.canonical, value="existing canonical reason")
    before = _records(world)
    _assert_success(world, door)
    assert _records(world) == before


def test_single_http_reason_retains_source_rename_behavior(reason_world):
    world = reason_world
    world.evaluator.column.name = "Renamed endpoint"
    result = _resolve(world, "worker")
    assert result.pk == world.canonical.pk
    result.refresh_from_db()
    assert result.name == f"{world.metric.name}-Renamed endpoint-reason"
    assert len(_identity(world)) == 1


def test_foreign_edit_cannot_reconcile_owned_twins(reason_world, tenants):
    world = reason_world
    _twin(world)
    before = _records(world)
    with _scoring_only() as starts:
        response = _rescore(world, "edit", actor=tenants["b"], run=False)
    assert response.status_code in (403, 404), response.content
    for start in starts:
        start.assert_not_called()
    assert _records(world) == before


@pytest.mark.parametrize("populated", [False, True], ids=["empty", "populated"])
def test_native_stored_reason_resolution_preserves_twin_and_diagnostic(tenants, populated):
    world = _terminal(tenants)
    with _scoring_only() as starts:
        context = _start_stored(world, starts, door="rerun")
        binding = NativeRunBinding.objects.select_related(
            "run", "variant", "output_column", "snapshot__snapshot_dataset"
        ).get(run_id=context["run_id"])
        assert len(binding.run.evaluator_versions) == 1
        evaluator = NativeEvaluationRunner(
            dataset=world.dataset,
            run=binding.run,
            binding=binding,
            pin=binding.run.evaluator_versions[0],
        )
        evaluator._native_row_ids = [str(world.links[0].snapshot_row_id)]
        world.evaluator = evaluator
        world.score = world.source.columns.get(
            dataset=world.experiment.snapshot_dataset,
            source=SourceChoices.EXPERIMENT_EVALUATION.value,
            source_id=f"{world.source.pk}-{world.output.pk}-sourceid-{world.metric.pk}",
            deleted=False,
        )
        world.canonical = world.source.columns.get(
            dataset=world.experiment.snapshot_dataset,
            source=SourceChoices.EVALUATION_REASON.value,
            source_id=f"{world.score.pk}-sourceid-{world.metric.pk}",
            deleted=False,
        )
        twin = _twin(world)
        _cell(world, twin, value="saved native reason" if populated else None)
        if populated:
            _assert_refused(world, "worker")
        else:
            before = _records(world)
            _assert_success(world, "worker")
            assert _records(world) == before


@pytest.mark.parametrize("door", ["worker", "edit"])
def test_non_http_populated_names_keep_existing_behavior(auth_client, prompt_experiment, door):
    from types import SimpleNamespace

    from model_hub.views.eval_runner import EvaluationRunner

    fixture = prompt_experiment
    experiment = fixture["experiment"]
    metric = fixture["metric"]
    source = experiment.experiments_datasets.get()
    output = source.columns.get(source=SourceChoices.EXPERIMENT.value, deleted=False)
    canonical = Column.objects.create(
        name=f"{metric.name}-{output.name}-reason",
        dataset=fixture["written"],
        source=SourceChoices.EVALUATION_REASON.value,
        source_id=fixture["reason"].source_id,
        data_type=DataTypeChoices.TEXT.value,
    )
    world = SimpleNamespace(dataset=fixture["base"], experiment=experiment)
    before = _records(world)
    if door == "worker":
        evaluator = EvaluationRunner(
            metric.pk, experiment_dataset=source, column=output, format_output=True
        )
        evaluator.user_eval_metric = metric
        result = evaluator._create_reason_column(
            fixture["written"], "", parent_column=fixture["score"]
        )
        assert result.pk == canonical.pk
    else:
        response = _update(
            auth_client, fixture["base"].pk, metric, experiment=experiment, run=False
        )
        assert response.status_code == 200, response.content
    assert _records(world) == before


@pytest.mark.parametrize("lag", ["column-identity", "cell-payload"])
def test_edit_twin_resolution_uses_primary_despite_replica_lag(reason_world, monkeypatch, lag):
    """Model routing is real; lagging replica results are simulated, not another DB."""
    from tfc import routers

    world = reason_world
    twin = _twin(world)
    _cell(world, twin, value="primary-only populated reason")
    routing = routers.ReadReplicaRouter(opt_in=["Column", "Cell"])
    monkeypatch.setattr(routers, "_replica_configured", lambda: True)
    column_manager, cell_manager = Column.objects, Cell.objects
    column_filter, cell_filter = column_manager.filter, cell_manager.filter
    reads = []

    def routed_columns(*args, **kwargs):
        result = column_filter(*args, **kwargs)
        if (
            kwargs.get("source") == SourceChoices.EVALUATION_REASON.value
            and kwargs.get("source_id") == world.canonical.source_id
            and kwargs.get("dataset") == world.experiment.snapshot_dataset
        ):
            selected = routing.db_for_read(Column)
            reads.append(("Column", selected))
            if lag == "column-identity" and selected == "replica":
                return result.filter(pk=world.canonical.pk)
        return result

    def routed_cells(*args, **kwargs):
        result = cell_filter(*args, **kwargs)
        if getattr(kwargs.get("column"), "pk", None) == twin.pk:
            selected = routing.db_for_read(Cell)
            reads.append(("Cell", selected))
            if lag == "cell-payload" and selected == "replica":
                return result.none()
        return result

    monkeypatch.setattr(column_manager, "filter", routed_columns)
    monkeypatch.setattr(cell_manager, "filter", routed_cells)
    _assert_refused(world, "edit")
    assert ("Column", "default") in reads
    assert ("Cell", "default") in reads
    assert all(selected == "default" for _, selected in reads)
