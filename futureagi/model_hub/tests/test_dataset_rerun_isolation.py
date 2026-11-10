"""Selected-row reruns own their runner state and always finish failed cells."""

import inspect
import json
import sys
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from accounts.models import Organization, User
from accounts.models.workspace import Workspace
from django.db import OperationalError
from django.db.models.query import QuerySet
from model_hub.models.choices import CellStatus, SourceChoices, StatusType
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.develop_optimisation import OptimizationDataset
from model_hub.models.evals_metric import EvalTemplate, UserEvalMetric

from model_hub.views.develop_dataset import run_evaluation_task
from model_hub.views.eval_runner import EvaluationRunner

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def rerun():
    organization = Organization.objects.create(name="Rerun isolation test")
    user = User.objects.create_user(
        email="rerun@example.invalid", name="Rerun tester", organization=organization
    )
    workspace = Workspace.objects.create(
        name="Rerun workspace", organization=organization, created_by=user
    )
    dataset = Dataset.objects.create(
        name="Rerun dataset", organization=organization, workspace=workspace
    )
    template = EvalTemplate.objects.create(
        name="Rerun template", organization=organization, workspace=workspace, config={}
    )
    metric = UserEvalMetric.objects.create(
        name="Rerun metric",
        dataset=dataset,
        organization=organization,
        workspace=workspace,
        template=template,
        config={"mapping": {}},
    )
    column = Column.objects.create(
        name="Rerun score",
        dataset=dataset,
        source=SourceChoices.EVALUATION.value,
        source_id=str(metric.id),
        data_type="text",
    )
    reason_column = Column.objects.create(
        name="Rerun reason",
        dataset=dataset,
        source=SourceChoices.EVALUATION_REASON.value,
        source_id=f"{column.id}-sourceid-{metric.id}",
        data_type="text",
    )
    rows = [Row.objects.create(dataset=dataset, order=index) for index in range(3)]
    for row in rows:
        for col in (column, reason_column):
            Cell.objects.create(
                dataset=dataset,
                row=row,
                column=col,
                value="previous result",
                value_infos=json.dumps({"reason": "previous reasoning"}),
                status=CellStatus.PASS.value,
            )
    with (
        patch(
            "ee.usage.services.metering.check_usage",
            return_value=SimpleNamespace(allowed=True),
        ),
        patch(
            "model_hub.views.develop_dataset.get_mixpanel_properties", return_value={}
        ) as properties,
        patch("model_hub.views.develop_dataset.track_mixpanel_event"),
        patch("evaluations.engine.registry.get_eval_class", return_value=object()),
    ):
        yield SimpleNamespace(
            dataset=dataset,
            metric=metric,
            column=column,
            reason_column=reason_column,
            rows=rows,
            properties=properties,
        )


def run_selected(rerun):
    run_evaluation_task._original_func(
        {
            "metric_ids": [str(rerun.metric.id)],
            "row_ids": [str(row.id) for row in rerun.rows[:2]],
        }
    )


def result_for_row(self, row, mappings):
    value = f"row-{row.order}"
    return {"reason": value}, CellStatus.PASS.value, value


def test_selected_rows_do_not_share_runner_initialization(rerun):
    """Pause one real initializer just before indexing its column queryset.

    The other row completes initialization before it resumes. A shared runner
    then indexes a UUID, whereas independent runners both persist their result.
    No sleeps, mocked executor, or reliance on the scheduler winning a race.
    """
    paused, resume = Event(), Event()
    original = EvaluationRunner.run_evaluation_for_row
    loader = EvaluationRunner.load_user_eval_metric
    lines, start = inspect.getsourcelines(loader)
    subscript_line = next(
        start + index
        for index, line in enumerate(lines)
        if "self.replace_column_id = self.replace_column_id[0]" in line
    )
    runners = []

    def trace(frame, event, arg):
        if (
            frame.f_code is loader.__code__
            and event == "line"
            and frame.f_lineno == subscript_line
        ):
            paused.set()
            assert resume.wait(10), "second row never finished initialization"
        return trace

    def synchronized_run(self, row_id):
        runners.append(self)
        if row_id == str(rerun.rows[0].id):
            sys.settrace(trace)
            try:
                return original(self, row_id)
            finally:
                sys.settrace(None)
        try:
            assert paused.wait(10), "first row never reached column initialization"
            return original(self, row_id)
        finally:
            resume.set()

    with (
        patch.object(EvaluationRunner, "run_evaluation_for_row", synchronized_run),
        patch.object(EvaluationRunner, "_process_eval_result", result_for_row),
    ):
        run_selected(rerun)

    cells = list(Cell.objects.filter(column=rerun.column).order_by("row__order"))
    assert [(cell.status, cell.value) for cell in cells] == [
        (CellStatus.PASS.value, "row-0"),
        (CellStatus.PASS.value, "row-1"),
        (CellStatus.PASS.value, "previous result"),
    ]
    assert len(runners) == 2
    assert runners[0] is not runners[1]
    assert runners[0].source_configs is not runners[1].source_configs


def test_row_initializers_leave_metric_lifecycle_to_the_batch(rerun):
    status_saves = []
    original = UserEvalMetric.save

    def save(self, *args, **kwargs):
        if kwargs.get("update_fields") == ["status"]:
            status_saves.append(self.status)
        return original(self, *args, **kwargs)

    with (
        patch.object(UserEvalMetric, "save", save),
        patch.object(EvaluationRunner, "_process_eval_result", result_for_row),
    ):
        run_selected(rerun)
    # The task already sets RUNNING once with a bulk update. Row constructors
    # must not write it again, particularly after another worker sees a stop.
    assert status_saves == []


@pytest.mark.parametrize("failure_stage", ["initialize", "evaluate", "persist"])
def test_failed_future_finishes_only_its_selected_row(rerun, failure_stage):
    original = EvaluationRunner.run_evaluation_for_row

    def fail_one(self, row_id):
        if row_id == str(rerun.rows[0].id):
            method = {
                "initialize": "load_user_eval_metric",
                "evaluate": "_process_eval_result",
                "persist": "_create_cell",
            }[failure_stage]
            with patch.object(
                self, method, side_effect=RuntimeError("synthetic row failure")
            ):
                return original(self, row_id)
        return original(self, row_id)

    with (
        patch.object(EvaluationRunner, "run_evaluation_for_row", fail_one),
        patch.object(EvaluationRunner, "_process_eval_result", result_for_row),
    ):
        run_selected(rerun)

    failed = Cell.objects.get(row=rerun.rows[0], column=rerun.column)
    reason = Cell.objects.get(row=rerun.rows[0], column=rerun.reason_column)
    assert failed.status == reason.status == CellStatus.ERROR.value
    assert "previous" not in str(failed.value_infos) + str(reason.value_infos)
    assert (
        failed.value
        == reason.value
        == "Evaluation failed. Please rerun the evaluation."
    )
    successful = Cell.objects.get(row=rerun.rows[1], column=rerun.column)
    assert (successful.status, successful.value) == (CellStatus.PASS.value, "row-1")
    untouched = Cell.objects.filter(row=rerun.rows[2])
    assert all(
        cell.status == CellStatus.PASS.value and cell.value == "previous result"
        for cell in untouched
    )
    rerun.metric.refresh_from_db()
    assert rerun.metric.status == StatusType.FAILED.value


def test_runner_constructor_failure_finishes_all_selected_rows(rerun):
    with patch(
        "model_hub.views.develop_dataset.EvaluationRunner",
        side_effect=RuntimeError("synthetic constructor failure"),
    ):
        run_selected(rerun)
    cells = Cell.objects.filter(row__in=rerun.rows[:2])
    assert cells.count() == 4
    assert all(
        cell.status == CellStatus.ERROR.value
        and "previous" not in str(cell.value_infos)
        for cell in cells
    )
    assert all(
        cell.status == CellStatus.PASS.value
        for cell in Cell.objects.filter(row=rerun.rows[2])
    )
    rerun.metric.refresh_from_db()
    assert rerun.metric.status == StatusType.FAILED.value


def test_error_persistence_failure_is_not_acknowledged_as_success(rerun):
    original_update = QuerySet.update

    def update(self, **kwargs):
        if self.model is Cell and kwargs.get("status") == CellStatus.ERROR.value:
            raise OperationalError("synthetic terminal-state write failure")
        return original_update(self, **kwargs)

    with (
        patch(
            "model_hub.views.develop_dataset.EvaluationRunner",
            side_effect=RuntimeError("synthetic constructor failure"),
        ),
        patch.object(QuerySet, "update", update),
        pytest.raises(OperationalError, match="synthetic terminal-state write failure"),
    ):
        run_selected(rerun)
    rerun.metric.refresh_from_db()
    assert rerun.metric.status == StatusType.FAILED.value


def test_optimization_failure_only_updates_the_requested_column(rerun):
    optimize = OptimizationDataset.objects.create(
        name="Rerun optimization", dataset=rerun.dataset, optimize_type="RIGHT_ANSWER"
    )
    columns = [
        Column.objects.create(
            name=f"Optimization score {index}",
            dataset=rerun.dataset,
            source=SourceChoices.OPTIMISATION_EVALUATION.value,
            source_id=f"{optimize.id}-sourceid-{rerun.metric.id}",
            data_type="text",
        )
        for index in range(2)
    ]
    for column in columns:
        for row in rerun.rows:
            Cell.objects.create(
                dataset=rerun.dataset,
                row=row,
                column=column,
                status=CellStatus.PASS.value,
                value="previous result",
            )
    with patch(
        "model_hub.views.develop_dataset.EvaluationRunner",
        side_effect=RuntimeError("synthetic constructor failure"),
    ):
        run_evaluation_task._original_func(
            {
                "metric_ids": [str(rerun.metric.id)],
                "row_ids": [str(rerun.rows[0].id)],
                "column_source": SourceChoices.OPTIMISATION_EVALUATION.value,
                "column_source_id": columns[0].source_id,
                "column": str(columns[0].id),
            }
        )
    assert (
        Cell.objects.get(column=columns[0], row=rerun.rows[0]).status
        == CellStatus.ERROR.value
    )
    untouched = Cell.objects.exclude(column=columns[0], row=rerun.rows[0])
    assert all(
        cell.status == CellStatus.PASS.value and cell.value == "previous result"
        for cell in untouched
    )


def test_failed_metric_does_not_change_other_metric_or_completion_counts(rerun):
    healthy = UserEvalMetric.objects.create(
        name="Healthy metric",
        dataset=rerun.dataset,
        organization=rerun.metric.organization,
        workspace=rerun.metric.workspace,
        template=rerun.metric.template,
        config={"mapping": {}},
    )
    healthy_column = Column.objects.create(
        name="Healthy score",
        dataset=rerun.dataset,
        source=SourceChoices.EVALUATION.value,
        source_id=str(healthy.id),
        data_type="text",
    )
    for row in rerun.rows:
        Cell.objects.create(
            dataset=rerun.dataset,
            row=row,
            column=healthy_column,
            status=CellStatus.PASS.value,
            value="previous result",
        )
    original = EvaluationRunner.run_evaluation_for_row

    def fail_metric(self, row_id):
        if str(self.user_eval_metric_id) == str(rerun.metric.id):
            raise RuntimeError("synthetic metric failure")
        return original(self, row_id)

    with (
        patch.object(EvaluationRunner, "run_evaluation_for_row", fail_metric),
        patch.object(EvaluationRunner, "_process_eval_result", result_for_row),
    ):
        run_evaluation_task._original_func(
            {
                "metric_ids": [str(rerun.metric.id), str(healthy.id)],
                "row_ids": [str(row.id) for row in rerun.rows[:2]],
            }
        )

    rerun.metric.refresh_from_db()
    healthy.refresh_from_db()
    assert rerun.metric.status == StatusType.FAILED.value
    assert healthy.status == StatusType.COMPLETED.value
    assert list(
        Cell.objects.filter(column=healthy_column)
        .order_by("row__order")
        .values_list("value", flat=True)
    ) == ["row-0", "row-1", "previous result"]
    completed_calls = [
        call.kwargs
        for call in rerun.properties.call_args_list
        if "failed" in call.kwargs
    ]
    assert [
        (str(call["eval"].id), call["count"], call["failed"])
        for call in completed_calls
    ] == [(str(rerun.metric.id), 0, 2), (str(healthy.id), 2, 0)]
