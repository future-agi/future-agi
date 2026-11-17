"""An evaluation edited in place marks its reference-column scores.

The edit keeps the metric id, so the reference column keeps the scores the
evaluation gave before the edit and is not scored again. Those scores are
marked (cell value_infos and Column.metadata) and the grid reports the mark
until each cell is scored again.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import MagicMock, patch

import pytest

from accounts.models import Organization, User
from accounts.models.workspace import Workspace
from model_hub.models.choices import (
    CellStatus,
    DatasetSourceChoices,
    DataTypeChoices,
    SourceChoices,
    StatusType,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.evals_metric import EvalTemplate, UserEvalMetric
from model_hub.models.experiments import ExperimentsTable

MARK = "scored_before_edit"


def _infos(cell):
    cell.refresh_from_db()
    raw = cell.value_infos
    if isinstance(raw, str):
        raw = json.loads(raw)
    return raw if isinstance(raw, dict) else {}


@pytest.fixture
def world(db):
    suffix = uuid.uuid4().hex[:8]
    organization = Organization.objects.create(name=f"Reference marks {suffix}")
    user = User.objects.create_user(
        email=f"reference-marks-{suffix}@example.com",
        password="testpassword123",
        name="Reference Marks",
        organization=organization,
    )
    workspace = Workspace.objects.create(
        name=f"Reference marks {suffix}",
        organization=organization,
        is_default=True,
        created_by=user,
    )
    dataset = Dataset.objects.create(
        name=f"reference-marks-{suffix}",
        organization=organization,
        workspace=workspace,
        source=DatasetSourceChoices.BUILD.value,
    )
    reference = Column.objects.create(
        name="answer",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.RUN_PROMPT.value,
    )
    experiment = ExperimentsTable.objects.create(
        name=f"reference-marks-{suffix}",
        dataset=dataset,
        snapshot_dataset=dataset,
        column=reference,
        status=StatusType.COMPLETED.value,
    )
    template = EvalTemplate.objects.create(
        name=f"reference-marks-template-{suffix}",
        organization=organization,
        workspace=workspace,
        criteria="Evaluate the following: {{output}}",
        model="gpt-4",
    )
    metric = UserEvalMetric.objects.create(
        name="Tone",
        organization=organization,
        workspace=workspace,
        dataset=dataset,
        template=template,
        config={"mapping": {"output": "output_column"}, "config": {}},
        model="turing_small",
        status=StatusType.EXPERIMENT_EVALUATION.value,
        source_id=str(experiment.id),
        user=user,
    )
    experiment.user_eval_template_ids.add(metric)

    # The reference column's scores for the evaluation.
    reference_eval = Column.objects.create(
        name="Tone",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.EVALUATION.value,
        source_id=str(metric.id),
    )
    # A run's output scores for the same evaluation.
    run_eval = Column.objects.create(
        name="Tone-output",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.EXPERIMENT_EVALUATION.value,
        source_id=f"{uuid.uuid4()}-{uuid.uuid4()}-sourceid-{metric.id}",
    )
    rows = [Row.objects.create(dataset=dataset, order=i) for i in range(2)]
    reference_cells = [
        Cell.objects.create(
            dataset=dataset,
            column=reference_eval,
            row=row,
            value="Passed",
            value_infos=json.dumps({"reason": f"fine {i}"}),
            status=CellStatus.PASS.value,
        )
        for i, row in enumerate(rows)
    ]
    run_cell = Cell.objects.create(
        dataset=dataset,
        column=run_eval,
        row=rows[0],
        value="Passed",
        value_infos=json.dumps({"reason": "fine"}),
        status=CellStatus.PASS.value,
    )
    return {
        "organization": organization,
        "user": user,
        "workspace": workspace,
        "dataset": dataset,
        "experiment": experiment,
        "template": template,
        "metric": metric,
        "reference_eval": reference_eval,
        "run_eval": run_eval,
        "rows": rows,
        "reference_cells": reference_cells,
        "run_cell": run_cell,
    }


def _entry(world, **changes):
    entry = {
        "id": world["metric"].id,
        "name": "Tone",
        "template_id": world["template"].id,
        "config": {"mapping": {"output": "output_column"}, "config": {}},
        "model": "turing_small",
    }
    entry.update(changes)
    return entry


def _update(world, entries, *, save_only):
    from model_hub.views.experiments import _diff_and_update_evals

    with patch(
        "model_hub.views.experiment_runner.ExperimentRunner", MagicMock()
    ) as runner:
        rerun_ids = _diff_and_update_evals(
            experiment=world["experiment"],
            new_eval_entries=entries,
            organization=world["organization"],
            user=world["user"],
            workspace=world["workspace"],
            save_only=save_only,
        )
    return rerun_ids, runner


@pytest.mark.django_db
@pytest.mark.parametrize("save_only", [False, True])
@pytest.mark.parametrize(
    "change",
    [
        {"model": "turing_large"},
        {"config": {"mapping": {"output": "output_column"}, "config": {"k": 1}}},
    ],
    ids=["model", "instructions"],
)
def test_edit_in_place_marks_reference_scores_not_run_scores(world, save_only, change):
    rerun_ids, _ = _update(world, [_entry(world, **change)], save_only=save_only)

    assert rerun_ids == [str(world["metric"].id)]
    world["reference_eval"].refresh_from_db()
    assert world["reference_eval"].metadata.get(MARK) is True
    for cell in world["reference_cells"]:
        assert _infos(cell)[MARK] is True
        # The old score stays (it is not scored again or reset).
        assert cell.value == "Passed"
        assert cell.status == CellStatus.PASS.value
        assert _infos(cell)["reason"].startswith("fine")
    world["run_eval"].refresh_from_db()
    assert MARK not in (world["run_eval"].metadata or {})
    assert MARK not in _infos(world["run_cell"])


@pytest.mark.django_db
@pytest.mark.parametrize("save_only", [False, True])
def test_edit_that_changes_nothing_scored_marks_nothing(world, save_only):
    _update(world, [_entry(world)], save_only=save_only)
    _update(world, [_entry(world, name="Tone of voice")], save_only=save_only)

    world["reference_eval"].refresh_from_db()
    assert MARK not in (world["reference_eval"].metadata or {})
    for cell in world["reference_cells"]:
        assert MARK not in _infos(cell)


@pytest.mark.django_db
def test_new_evaluation_marks_nothing(world):
    new = _entry(world, name="Helpfulness")
    new.pop("id")
    _update(world, [_entry(world), new], save_only=True)

    for column in Column.objects.filter(dataset=world["dataset"]):
        assert MARK not in (column.metadata or {})
    for cell in Cell.objects.filter(dataset=world["dataset"]):
        assert MARK not in _infos(cell)


@pytest.mark.django_db
def test_scoring_a_marked_cell_unmarks_only_that_cell(world):
    from model_hub.views.eval_runner import EvaluationRunner
    from model_hub.views.experiments import _reference_columns_scored_before_edit

    _update(world, [_entry(world, model="turing_large")], save_only=False)
    world["reference_eval"].refresh_from_db()

    # The write a reference-column scoring makes for one row.
    runner = EvaluationRunner.__new__(EvaluationRunner)
    runner.source_configs = {"experiment_id": str(world["experiment"].id)}
    runner.keep_results_after_stop = True
    runner.user_eval_metric_id = world["metric"].id
    runner.is_only_eval = True
    runner.replace_column_id = None
    runner.column = None
    first, second = world["reference_cells"]
    runner._create_cell(
        world["dataset"],
        world["reference_eval"],
        world["rows"][0],
        {"reason": "scored with the edited evaluation"},
        "Failed",
        CellStatus.PASS.value,
    )

    assert MARK not in _infos(first)
    assert first.value == "Failed"
    assert _infos(second)[MARK] is True
    assert _reference_columns_scored_before_edit([world["reference_eval"]]) == {
        str(world["reference_eval"].id)
    }

    runner._create_cell(
        world["dataset"],
        world["reference_eval"],
        world["rows"][1],
        {"reason": "scored with the edited evaluation"},
        "Passed",
        CellStatus.PASS.value,
    )

    assert MARK not in _infos(second)
    # No marked cell remains, so the grid no longer reports the column.
    assert _reference_columns_scored_before_edit([world["reference_eval"]]) == set()


@pytest.mark.django_db
def test_grid_payload_carries_the_marks(world):
    from model_hub.views.experiments import DatasetExperimentsView

    _update(world, [_entry(world, model="turing_large")], save_only=True)

    view = DatasetExperimentsView()
    _, marked = view._process_dataset_cell(
        Cell.objects.get(id=world["reference_cells"][0].id)
    )
    _, plain = view._process_dataset_cell(Cell.objects.get(id=world["run_cell"].id))

    assert marked["value_infos"] == {"reason": "fine 0", MARK: True}
    assert plain["value_infos"] == {"reason": "fine"}


def _add_scored_rows(world, count):
    start = Row.objects.filter(dataset=world["dataset"]).count()
    for order in range(start, start + count):
        row = Row.objects.create(dataset=world["dataset"], order=order)
        Cell.objects.create(
            dataset=world["dataset"],
            column=world["reference_eval"],
            row=row,
            value="Passed",
            value_infos=json.dumps({"reason": f"fine {order}"}),
            status=CellStatus.PASS.value,
        )


def _marking_queries(world):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    from model_hub.views.experiments import _mark_reference_scores_before_edit

    with CaptureQueriesContext(connection) as queries:
        _mark_reference_scores_before_edit(world["experiment"], world["metric"])
    return len(queries.captured_queries)


@pytest.mark.django_db
def test_marking_many_rows_takes_as_many_queries_as_marking_two(world):
    two_rows = _marking_queries(world)
    _add_scored_rows(world, 48)

    fifty_rows = _marking_queries(world)

    # The edit request does not grow with the reference column.
    assert fifty_rows == two_rows
    cells = Cell.objects.filter(column=world["reference_eval"], deleted=False)
    assert cells.count() == 50
    for cell in cells:
        assert _infos(cell)[MARK] is True


@pytest.mark.django_db
def test_a_marked_cell_running_again_shows_no_mark(world):
    from model_hub.views.experiments import (
        DatasetExperimentsView,
        _reference_columns_scored_before_edit,
    )

    _update(world, [_entry(world, model="turing_large")], save_only=True)
    world["reference_eval"].refresh_from_db()
    # The reset a rerun of these rows makes: running, no value, and the stored
    # details (with the mark) kept until the new score is written.
    Cell.objects.filter(column=world["reference_eval"]).update(
        status=CellStatus.RUNNING.value, value=""
    )
    running = Cell.objects.get(id=world["reference_cells"][0].id)
    assert _infos(running)[MARK] is True

    _, shown = DatasetExperimentsView()._process_dataset_cell(running)

    assert MARK not in shown["value_infos"]
    # Only running cells are left marked, so the column shows no chip.
    assert _reference_columns_scored_before_edit([world["reference_eval"]]) == set()
