"""Selected-row reruns own their runner state and always finish failed cells."""

import inspect
import json
import sys
from threading import Barrier, Event, Lock, Thread
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.db import OperationalError, connection

import model_hub.utils.eval_cell_status as eval_cell_status
from accounts.models import Organization, User
from accounts.models.workspace import Workspace
from model_hub.models.choices import CellStatus, SourceChoices, StatusType
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.develop_optimisation import OptimizationDataset
from model_hub.models.evals_metric import (
    EvalTemplate,
    EvalTemplateVersion,
    UserEvalMetric,
)
from model_hub.views import develop_dataset as dataset_views
from model_hub.views.develop_dataset import run_evaluation_task
from model_hub.views.eval_runner import EvaluationRunner

pytestmark = pytest.mark.django_db(transaction=True)

FAILED_REASON = "Evaluation failed. Please rerun the evaluation."
# Postgres rejects NUL bytes in text columns, so storing this result fails for
# real on every write path, without mocking the database layer.
UNSTORABLE_RESULT = ({"reason": "row-0"}, CellStatus.PASS.value, "row-0\x00")


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


def run_selected(rerun, metrics=None, rows=None):
    run_evaluation_task._original_func(
        {
            "metric_ids": [str(metric.id) for metric in metrics or [rerun.metric]],
            "row_ids": [str(row.id) for row in rows or rerun.rows[:2]],
        }
    )


def result_for_row(self, row, mappings):
    value = f"row-{row.order}"
    return {"reason": value}, CellStatus.PASS.value, value


def add_healthy_metric(rerun):
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
    return healthy, healthy_column


def fail_rows_of(metric):
    original = EvaluationRunner.run_evaluation_for_row

    def run(self, row_id):
        if str(self.user_eval_metric_id) == str(metric.id):
            raise RuntimeError("synthetic metric failure")
        return original(self, row_id)

    return run


def completion_counts(rerun):
    return [
        (str(call.kwargs["eval"].id), call.kwargs["count"], call.kwargs["failed"])
        for call in rerun.properties.call_args_list
        if "failed" in call.kwargs
    ]


def column_state(column):
    return list(
        Cell.objects.filter(column=column)
        .order_by("row__order")
        .values_list("status", "value")
    )


def status_of(metric):
    metric.refresh_from_db()
    return metric.status


def drop_task_connection_on_first_marker():
    """Kill the task thread's session server-side on the first terminal write.

    This is what a pooler restart or failover does to the task's connection.
    The real marker then runs on the dead session; later calls, including the
    retry, run the real marker. Returns a record of the kill (``rows`` holds
    the row ids whose write hit the dead session) and the patch. Tests must
    call ``assert_marker_write_failed`` on the record: a kill that misfires
    lets the write succeed, and the test would then pass without reaching the
    error handling it covers.
    """
    real_marker = eval_cell_status.mark_rerun_cells_failed
    kill = SimpleNamespace(
        rows=[], terminated=None, killer_error=None, write_error=None
    )

    def terminate(pid):
        from django.db import connection as killer

        # Record rather than raise: an exception in this thread would only
        # surface as a pytest warning.
        try:
            with killer.cursor() as cursor:
                cursor.execute("SELECT pg_terminate_backend(%s, 10000)", [pid])
                kill.terminated = cursor.fetchone()[0]
        except Exception as error:
            kill.killer_error = error
        finally:
            killer.close()

    def marker(metric_id, row_ids, **kwargs):
        if kill.rows:
            return real_marker(metric_id, row_ids, **kwargs)
        kill.rows.extend(str(row_id) for row_id in row_ids)
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_backend_pid()")
            pid = cursor.fetchone()[0]
        killer = Thread(target=terminate, args=(pid,))
        killer.start()
        killer.join(20)
        # Assertions here would be swallowed by the handler under test, so
        # keep the outcome for the test body to check.
        try:
            return real_marker(metric_id, row_ids, **kwargs)
        except Exception as error:
            kill.write_error = error
            raise

    return kill, patch(
        "model_hub.utils.eval_cell_status.mark_rerun_cells_failed", side_effect=marker
    )


def assert_marker_write_failed(kill):
    """The kill landed and the first terminal write really failed."""
    assert kill.killer_error is None, kill.killer_error
    assert kill.terminated is True
    assert isinstance(kill.write_error, OperationalError), kill.write_error


def assert_rows_finished(rerun, rows):
    for row in rows:
        for column in (rerun.column, rerun.reason_column):
            cell = Cell.objects.get(row=row, column=column)
            assert (cell.status, cell.value) == (CellStatus.ERROR.value, FAILED_REASON)


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


@pytest.mark.parametrize("binding", ["default", "pinned", "latest"])
def test_queued_rows_execute_the_version_stamped_before_fanout(rerun, binding):
    """Change the active version while all five production workers are busy.

    The sixth row cannot construct its runner until after the switch. Its
    execution config and usage metadata must both retain the original version.
    Keep the real executor, constructor, config overlay, and cell persistence.
    """
    template = rerun.metric.template
    original_version = EvalTemplateVersion.objects.create(
        eval_template=template,
        version_number=1,
        is_default=binding == "default",
        config_snapshot={"rule_prompt": "original instructions"},
    )
    if binding == "pinned":
        rerun.metric.pinned_version = original_version
        rerun.metric.save(update_fields=["pinned_version"])
    rows = rerun.rows + [
        Row.objects.create(dataset=rerun.dataset, order=index) for index in range(3, 6)
    ]
    for row in rows[3:]:
        for column in (rerun.column, rerun.reason_column):
            Cell.objects.create(
                dataset=rerun.dataset,
                row=row,
                column=column,
                status=CellStatus.PASS.value,
                value="previous result",
            )

    def activate_new_version():
        EvalTemplateVersion.objects.filter(eval_template=template).update(
            is_default=False
        )
        new_version = EvalTemplateVersion.objects.create(
            eval_template=template,
            version_number=2,
            is_default=True,
            config_snapshot={"rule_prompt": "new instructions"},
        )
        EvalTemplate.objects.filter(id=template.id).update(
            config=new_version.config_snapshot
        )
        if binding == "pinned":
            UserEvalMetric.objects.filter(id=rerun.metric.id).update(
                pinned_version=new_version
            )

    first_wave = Barrier(5, action=activate_new_version, timeout=20)
    executions = {}

    def evaluate(self, row, mappings):
        if row.order < 5:
            first_wave.wait()
        executions[row.order] = (
            self.version_number,
            str(self._resolved_version.id),
            self._get_effective_eval_config()["rule_prompt"],
            self.source_configs["version_number"],
            self.source_configs["version_id"],
        )
        return result_for_row(self, row, mappings)

    with (
        patch.object(EvaluationRunner, "_process_eval_result", evaluate),
        patch.object(
            EvalTemplateVersion.objects,
            "resolve_for_metric",
            wraps=EvalTemplateVersion.objects.resolve_for_metric,
        ) as resolve,
    ):
        run_evaluation_task._original_func(
            {
                "metric_ids": [str(rerun.metric.id)],
                "row_ids": [str(row.id) for row in rows],
            }
        )

    assert EvalTemplateVersion.objects.get_default(template).version_number == 2
    expected = (
        1,
        str(original_version.id),
        "original instructions",
        1,
        str(original_version.id),
    )
    assert executions == dict.fromkeys(range(6), expected)
    assert resolve.call_count == 1
    assert list(
        Cell.objects.filter(column=rerun.column)
        .order_by("row__order")
        .values_list("status", "value")
    ) == [(CellStatus.PASS.value, f"row-{index}") for index in range(6)]
    rerun.metric.refresh_from_db()
    assert rerun.metric.status == StatusType.COMPLETED.value


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
    method, behaviour = {
        "initialize": (
            "load_user_eval_metric",
            {"side_effect": RuntimeError("synthetic row failure")},
        ),
        "evaluate": (
            "_process_eval_result",
            {"side_effect": RuntimeError("synthetic row failure")},
        ),
        # The real cell-write failure: the in-place save and the
        # update_or_create fallback both fail to store the result.
        "persist": ("_process_eval_result", {"return_value": UNSTORABLE_RESULT}),
    }[failure_stage]

    def fail_one(self, row_id):
        if row_id == str(rerun.rows[0].id):
            with patch.object(self, method, **behaviour):
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
    assert completion_counts(rerun) == [(str(rerun.metric.id), 1, 1)]


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


def test_full_run_counts_an_unstored_result_as_a_failed_row(rerun):
    """The threaded full-dataset path shares the cell writer with reruns. A
    result that cannot be stored must fail only its row, once, and the run
    must still return normally so its task is never retried."""
    evaluated, lock = [], Lock()

    def evaluate(self, row, mappings):
        with lock:
            evaluated.append(row.id)
        if row.id == rerun.rows[0].id:
            return UNSTORABLE_RESULT
        return result_for_row(self, row, mappings)

    runner = EvaluationRunner(
        user_eval_metric_id=rerun.metric.id,
        is_only_eval=True,
        source="dataset_evaluation",
        source_id=rerun.metric.template.id,
    )
    with (
        patch.object(EvaluationRunner, "_process_eval_result", evaluate),
        patch(
            "model_hub.views.eval_runner.get_mixpanel_properties", return_value={}
        ) as properties,
        patch("model_hub.views.eval_runner.track_mixpanel_event"),
    ):
        runner.run_prompt()

    assert sorted(evaluated) == sorted(row.id for row in rerun.rows)
    (failed_status, _), *stored = column_state(rerun.column)
    assert failed_status == CellStatus.ERROR.value
    assert stored == [
        (CellStatus.PASS.value, "row-1"),
        (CellStatus.PASS.value, "row-2"),
    ]
    completion = properties.call_args.kwargs
    assert (completion["count"], completion["failed"]) == (2, 1)
    # The failure stays with its row: the run itself did not fail.
    assert status_of(rerun.metric) == StatusType.COMPLETED.value


def test_reporting_failure_does_not_request_replay_of_finished_evaluations(rerun):
    evaluated = []

    def evaluate(self, row, mappings):
        evaluated.append(row.id)
        return result_for_row(self, row, mappings)

    def properties(**kwargs):
        if "failed" in kwargs:
            raise RuntimeError("synthetic completion reporting failure")
        return {}

    rerun.properties.side_effect = properties
    with patch.object(EvaluationRunner, "_process_eval_result", evaluate):
        # Preserve the activity's existing non-rethrowing outer failure handler.
        # Requesting a retry here would replay already-completed billable calls.
        run_selected(rerun)
    assert set(evaluated) == {row.id for row in rerun.rows[:2]}
    assert list(
        Cell.objects.filter(column=rerun.column)
        .order_by("row__order")
        .values_list("value", flat=True)
    ) == ["row-0", "row-1", "previous result"]
    # Every row finished, so a reporting failure must not relabel the run.
    assert status_of(rerun.metric) == StatusType.COMPLETED.value


def test_completion_reporting_reads_nothing_after_fanout(rerun):
    """A usage-limited metric skips the start event, which used to load its
    dataset, so the completion loop read it after fan-out, and a database
    error there dropped its completion event. Every organization or dataset
    read after the rows are stored now fails, and none may happen: both
    metrics keep their status and send their completion event."""
    healthy, healthy_column = add_healthy_metric(rerun)
    limited = SimpleNamespace(
        allowed=False,
        reason="synthetic usage limit",
        error_code="USAGE_LIMIT_EXCEEDED",
        dimension="evals",
        current_usage=10,
        limit=10,
        upgrade_cta=None,
    )
    usage = iter([limited, SimpleNamespace(allowed=True)])
    stored, lock, rows_stored = [], Lock(), Event()
    original_create_cell = EvaluationRunner._create_cell

    def create_cell(self, *args, **kwargs):
        original_create_cell(self, *args, **kwargs)
        with lock:
            stored.append(self.user_eval_metric_id)
            if len(stored) == 2:
                rows_stored.set()

    tables = [f'FROM "{model._meta.db_table}"' for model in (Organization, Dataset)]
    late_reads = []

    def late_read_fails(execute, sql, params, many, context):
        if rows_stored.is_set() and any(table in sql for table in tables):
            late_reads.append(sql)
            raise OperationalError("synthetic connection failure")
        return execute(sql, params, many, context)

    with (
        patch(
            "ee.usage.services.metering.check_usage",
            side_effect=lambda *args, **kwargs: next(usage),
        ),
        patch.object(EvaluationRunner, "_process_eval_result", result_for_row),
        patch.object(EvaluationRunner, "_create_cell", create_cell),
        connection.execute_wrapper(late_read_fails),
    ):
        run_selected(rerun, [rerun.metric, healthy])

    assert rows_stored.is_set()
    assert late_reads == []
    assert column_state(healthy_column) == [
        (CellStatus.PASS.value, "row-0"),
        (CellStatus.PASS.value, "row-1"),
        (CellStatus.PASS.value, "previous result"),
    ]
    assert status_of(healthy) == StatusType.COMPLETED.value
    assert status_of(rerun.metric) == StatusType.FAILED.value
    assert completion_counts(rerun) == [
        (str(rerun.metric.id), 0, 2),
        (str(healthy.id), 2, 0),
    ]


def fail_second_metric_setup(step, rows_started, waited):
    """Fail the second metric's setup once, at ``step``, after the first
    metric's rows have started. ``waited`` records whether they had."""

    def fail():
        waited.append(rows_started.wait(20))
        raise OperationalError(f"synthetic connection failure at {step}")

    if step == "usage-check":
        calls = []

        def check_usage(*args, **kwargs):
            calls.append(args)
            if len(calls) == 2:
                fail()
            return SimpleNamespace(allowed=True)

        return patch("ee.usage.services.metering.check_usage", side_effect=check_usage)

    # The lazy load passed to the usage check, or to the start event.
    model = {"organization-load": Organization, "dataset-load": Dataset}[step]
    table = f'FROM "{model._meta.db_table}"'
    reads = []

    def second_read_fails(execute, sql, params, many, context):
        if sql.lstrip().upper().startswith("SELECT") and table in sql:
            reads.append(sql)
            # Read 1 is the first metric's setup, read 2 the second's.
            if len(reads) == 2:
                fail()
        return execute(sql, params, many, context)

    return connection.execute_wrapper(second_read_fails)


@pytest.mark.parametrize("step", ["organization-load", "usage-check", "dataset-load"])
def test_db_error_in_later_metric_setup_fails_only_that_metric(rerun, step):
    """The first metric's rows are running when the second metric's setup hits
    a database error. Only the second metric fails: none of its rows run, its
    selected cells are finished, and the first metric keeps its results."""
    healthy, healthy_column = add_healthy_metric(rerun)
    started, rows_started, waited = [], Event(), []
    original_row = dataset_views._run_dataset_evaluation_row

    def run_row(metric_id, row_id, runner_args):
        started.append(str(metric_id))
        rows_started.set()
        return original_row(metric_id, row_id, runner_args)

    with (
        patch.object(dataset_views, "_run_dataset_evaluation_row", run_row),
        patch.object(EvaluationRunner, "_process_eval_result", result_for_row),
        fail_second_metric_setup(step, rows_started, waited),
    ):
        run_selected(rerun, [healthy, rerun.metric])
    connection.close()

    assert waited == [True]
    assert started == [str(healthy.id)] * 2
    assert status_of(healthy) == StatusType.COMPLETED.value
    assert column_state(healthy_column) == [
        (CellStatus.PASS.value, "row-0"),
        (CellStatus.PASS.value, "row-1"),
        (CellStatus.PASS.value, "previous result"),
    ]
    assert status_of(rerun.metric) == StatusType.FAILED.value
    for column in (rerun.column, rerun.reason_column):
        assert column_state(column) == [
            (CellStatus.ERROR.value, FAILED_REASON),
            (CellStatus.ERROR.value, FAILED_REASON),
            (CellStatus.PASS.value, "previous result"),
        ]
    assert completion_counts(rerun) == [
        (str(healthy.id), 2, 0),
        (str(rerun.metric.id), 0, 2),
    ]


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
    healthy, healthy_column = add_healthy_metric(rerun)

    with (
        patch.object(
            EvaluationRunner, "run_evaluation_for_row", fail_rows_of(rerun.metric)
        ),
        patch.object(EvaluationRunner, "_process_eval_result", result_for_row),
    ):
        run_selected(rerun, [rerun.metric, healthy])

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


@pytest.mark.parametrize("passing_runs", [1, 2])
def test_overlapping_reruns_keep_the_failure_of_another_run(rerun, passing_runs):
    """Reruns of one metric overlap on different rows. While each passing
    run's row is in flight, the next run starts and finishes; the innermost
    run fails its row and finishes first. That row is still ERROR when the
    passing runs finish later, so none of them may relabel the metric
    COMPLETED."""
    original = EvaluationRunner.run_evaluation_for_row
    failing_row, passing_rows = rerun.rows[0], rerun.rows[1 : 1 + passing_runs]
    finished = []

    def run_rerun(row):
        try:
            run_selected(rerun, rows=[row])
            status = UserEvalMetric.objects.get(id=rerun.metric.id).status
            finished.append((row.order, status))
        except Exception as error:
            finished.append((row.order, error))
        finally:
            connection.close()

    def run(self, row_id):
        row = next(row for row in rerun.rows if str(row.id) == row_id)
        if row == failing_row:
            raise RuntimeError("synthetic row failure")
        overlapping = Thread(target=run_rerun, args=(rerun.rows[row.order - 1],))
        overlapping.start()
        overlapping.join(30)
        return original(self, row_id)

    with (
        patch.object(EvaluationRunner, "run_evaluation_for_row", run),
        patch.object(EvaluationRunner, "_process_eval_result", result_for_row),
    ):
        run_rerun(passing_rows[-1])

    # Runs finish innermost first; the status each one leaves behind.
    assert finished == [
        (row.order, StatusType.FAILED.value) for row in [failing_row, *passing_rows]
    ]
    assert column_state(rerun.column) == [
        (CellStatus.ERROR.value, FAILED_REASON),
        *[(CellStatus.PASS.value, f"row-{row.order}") for row in passing_rows],
        *[(CellStatus.PASS.value, "previous result")] * (2 - passing_runs),
    ]


def test_later_rerun_completes_a_metric_an_earlier_rerun_failed(rerun):
    """Reruns that do not overlap keep last-run-wins status: a run starts by
    setting RUNNING, so rerunning the failed row successfully completes the
    metric again."""
    with (
        patch.object(
            EvaluationRunner, "run_evaluation_for_row", fail_rows_of(rerun.metric)
        ),
        patch.object(EvaluationRunner, "_process_eval_result", result_for_row),
    ):
        run_selected(rerun, rows=rerun.rows[:1])
    assert status_of(rerun.metric) == StatusType.FAILED.value

    with patch.object(EvaluationRunner, "_process_eval_result", result_for_row):
        run_selected(rerun, rows=rerun.rows[:1])
    assert column_state(rerun.column)[0] == (CellStatus.PASS.value, "row-0")
    assert status_of(rerun.metric) == StatusType.COMPLETED.value


def test_lost_connection_on_one_failed_row_still_finishes_the_others(rerun):
    """Both selected rows of one metric fail; the task's session dies on the
    first terminal write. The write is retried on a fresh connection, the
    other failed row is still finished, and the healthy metric keeps its
    completed result and completion event."""
    healthy, healthy_column = add_healthy_metric(rerun)
    kill, marker = drop_task_connection_on_first_marker()
    try:
        with (
            marker,
            patch.object(
                EvaluationRunner, "run_evaluation_for_row", fail_rows_of(rerun.metric)
            ),
            patch.object(EvaluationRunner, "_process_eval_result", result_for_row),
        ):
            run_selected(rerun, [rerun.metric, healthy])
    finally:
        connection.close()

    assert len(kill.rows) == 1
    assert_marker_write_failed(kill)
    # The write that hit the dead session is retried, so no row stays RUNNING
    # with the previous run's reason.
    assert_rows_finished(rerun, rerun.rows[:2])
    assert status_of(rerun.metric) == StatusType.FAILED.value
    assert status_of(healthy) == StatusType.COMPLETED.value
    assert column_state(healthy_column) == [
        (CellStatus.PASS.value, "row-0"),
        (CellStatus.PASS.value, "row-1"),
        (CellStatus.PASS.value, "previous result"),
    ]
    assert completion_counts(rerun) == [
        (str(rerun.metric.id), 0, 2),
        (str(healthy.id), 2, 0),
    ]


def test_lost_connection_after_initialization_failure_still_runs_later_metrics(
    rerun,
):
    """The first metric fails before fan-out and its terminal write hits a dead
    session. The write is retried on a fresh connection, and the next metric
    must still be submitted and complete."""
    healthy, healthy_column = add_healthy_metric(rerun)
    kill, marker = drop_task_connection_on_first_marker()
    original_wrap = dataset_views.wrap_for_thread
    wrapped = []

    def wrap(fn):
        wrapped.append(fn)
        if len(wrapped) == 1:
            raise RuntimeError("synthetic initialization failure")
        return original_wrap(fn)

    try:
        with (
            marker,
            patch.object(dataset_views, "wrap_for_thread", wrap),
            patch.object(EvaluationRunner, "_process_eval_result", result_for_row),
        ):
            run_selected(rerun, [rerun.metric, healthy])
    finally:
        connection.close()

    assert sorted(kill.rows) == sorted(str(row.id) for row in rerun.rows[:2])
    assert_marker_write_failed(kill)
    assert_rows_finished(rerun, rerun.rows[:2])
    assert status_of(rerun.metric) == StatusType.FAILED.value
    assert status_of(healthy) == StatusType.COMPLETED.value
    assert column_state(healthy_column) == [
        (CellStatus.PASS.value, "row-0"),
        (CellStatus.PASS.value, "row-1"),
        (CellStatus.PASS.value, "previous result"),
    ]
    assert completion_counts(rerun) == [
        (str(rerun.metric.id), 0, 2),
        (str(healthy.id), 2, 0),
    ]


def test_failed_row_whose_terminal_write_fails_twice_still_fails_the_metric(
    rerun,
):
    """The terminal write for one failed row fails on the first attempt and on
    the retry. That row cannot be finished, but it is still counted failed:
    the metric is FAILED, the other failed row is finished, and the healthy
    metric is untouched."""
    healthy, healthy_column = add_healthy_metric(rerun)
    real_marker = eval_cell_status.mark_rerun_cells_failed
    stuck_row = rerun.rows[0]
    attempts = []

    def marker(metric_id, row_ids, **kwargs):
        attempts.append([str(row_id) for row_id in row_ids])
        if row_ids == [str(stuck_row.id)]:
            raise OperationalError("synthetic lock timeout")
        return real_marker(metric_id, row_ids, **kwargs)

    with (
        patch(
            "model_hub.utils.eval_cell_status.mark_rerun_cells_failed",
            side_effect=marker,
        ),
        patch.object(
            EvaluationRunner, "run_evaluation_for_row", fail_rows_of(rerun.metric)
        ),
        patch.object(EvaluationRunner, "_process_eval_result", result_for_row),
    ):
        run_selected(rerun, [rerun.metric, healthy])

    assert attempts.count([str(stuck_row.id)]) == 2
    assert len(attempts) == 3
    assert_rows_finished(rerun, rerun.rows[1:2])
    # Neither write landed, so this row keeps the task's RUNNING reset.
    stuck = Cell.objects.get(row=stuck_row, column=rerun.column)
    assert (stuck.status, stuck.value) == (CellStatus.RUNNING.value, None)
    assert status_of(rerun.metric) == StatusType.FAILED.value
    assert status_of(healthy) == StatusType.COMPLETED.value
    assert column_state(healthy_column) == [
        (CellStatus.PASS.value, "row-0"),
        (CellStatus.PASS.value, "row-1"),
        (CellStatus.PASS.value, "previous result"),
    ]
    assert completion_counts(rerun) == [
        (str(rerun.metric.id), 0, 2),
        (str(healthy.id), 2, 0),
    ]
