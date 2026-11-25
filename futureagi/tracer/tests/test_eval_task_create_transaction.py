"""Create acknowledges a saved resource, independently of start acknowledgement.

Real PostgreSQL commits/rollbacks are exercised; Temporal transport and worker
mutations are test doubles, not evidence of real Temporal acceptance semantics.
"""

from traceback import format_exception
from unittest.mock import Mock

import pytest
import structlog
from django.db import connections, transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from tracer.models.eval_task import EvalTask, EvalTaskLogger, EvalTaskStatus, RunType
from tracer.models.observation_span import EvalEntryStatus, EvalLogger
from tracer.services.filter_principal_context import FilterPrincipalContextError
from tracer.views.eval_task import (
    _EvalTaskCreateFailure,
    _normalize_eval_task_create_failure,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.api,
    pytest.mark.django_db(transaction=True),
]
_CREATE = "/tracer/eval-task/"


def payload(project, config, **overrides):
    return {
        "project": str(project.pk),
        "evals": [str(config.pk)],
        "name": "saved start outcome",
        "run_type": RunType.HISTORICAL,
        "spans_limit": 100,
        "sampling_rate": 100,
        "filters": {},
        **overrides,
    }


@pytest.fixture
def starter(monkeypatch):
    start = Mock(return_value="synthetic-workflow")
    monkeypatch.setattr("tracer.views.eval_task.start_eval_task_workflow_sync", start)
    return start


def task_snapshot(task_id):
    return EvalTask.all_objects.using("default").filter(pk=task_id).values().get()


def assert_safe_failure_log(caplog, event, failure_type, private_payload):
    # conftest configures the real application processors. Inspect the emitted
    # stdlib record too: a formatter can hide exc_info from rendered output while
    # another handler (including Sentry) still receives the original exception.
    records = [
        record for record in caplog.records if record.name == "tracer.views.eval_task"
    ]
    assert len(records) == 1
    record = records[0]
    assert record.levelname == "WARNING"
    assert record.msg["event"] == event
    assert record.msg["error_type"] == failure_type.__name__[:80]
    assert len(record.msg["error_type"]) <= 80
    assert not record.args
    assert not record.exc_info
    assert record.exc_text is None
    assert record.stack_info is None
    assert not {"exception", "exc_info", "stack_info"}.intersection(record.msg)
    for renderer in (
        structlog.processors.JSONRenderer(),
        structlog.dev.ConsoleRenderer(colors=False),
    ):
        formatter = structlog.stdlib.ProcessorFormatter(processor=renderer)
        rendered = formatter.format(record)
        assert private_payload not in rendered
        assert "Traceback (most recent call last)" not in rendered


def assert_saved(response, starter):
    assert response.status_code == 200, response.content
    assert response.json()["status"] is True
    task_id = response.json()["result"]["id"]
    starter.assert_called_once()
    assert str(starter.call_args.args[0].pk) == task_id
    # No replacement, additional start, or inferred "confirmed stopped" input.
    assert starter.call_args.kwargs == {}
    return task_id


@pytest.mark.parametrize(
    "failure_type",
    [
        RuntimeError,
        ValidationError,
        FilterPrincipalContextError,
        type("ProviderFailure" * 8, (RuntimeError,), {}),
    ],
)
def test_create_failure_normalization_keeps_only_bounded_type_metadata(failure_type):
    private_payload = "synthetic private failure payload must not be retained"
    with pytest.raises(_EvalTaskCreateFailure) as raised:
        with _normalize_eval_task_create_failure(preserve_validation_errors=False):
            raise failure_type(private_payload)

    failure = raised.value
    expected_type = failure_type.__name__[:80]
    assert type(failure) is _EvalTaskCreateFailure
    assert not isinstance(failure, (ValidationError, FilterPrincipalContextError))
    assert vars(failure) == {"error_type": expected_type}
    assert failure.args == (expected_type,)
    assert failure.__cause__ is None
    assert failure.__suppress_context__ is True
    assert private_payload not in "".join(format_exception(failure))


@pytest.mark.parametrize("failure_stage", ["task_write", "relations_write"])
@pytest.mark.parametrize(
    "failure_type", [RuntimeError, ValidationError, FilterPrincipalContextError]
)
def test_partial_task_or_relation_write_rolls_back_without_dispatch(
    auth_client,
    project,
    custom_eval_config,
    monkeypatch,
    starter,
    failure_stage,
    failure_type,
    caplog,
):
    caplog.set_level("WARNING", logger="tracer.views.eval_task")
    private_payload = "synthetic private persistence payload must not be logged"
    before_tasks = set(
        EvalTask.all_objects.using("default").values_list("pk", flat=True)
    )
    through = EvalTask.evals.through
    before_relations = list(through.objects.using("default").order_by("pk").values())
    wrote = []
    if failure_stage == "task_write":
        original = EvalTask.save

        def fail_after_write(task, *args, **kwargs):
            original(task, *args, **kwargs)
            wrote.append(task.pk)
            raise failure_type(private_payload)

        monkeypatch.setattr(EvalTask, "save", fail_after_write)
    else:
        manager_class = EvalTask.evals.related_manager_cls
        original = manager_class.set

        def fail_after_write(manager, *args, **kwargs):
            original(manager, *args, **kwargs)
            wrote.append(manager.instance.pk)
            assert manager.using("default").count() == 1
            raise failure_type(private_payload)

        monkeypatch.setattr(manager_class, "set", fail_after_write)

    response = auth_client.post(
        _CREATE, payload(project, custom_eval_config), format="json"
    )

    assert response.status_code == 400, response.content
    assert len(wrote) == 1
    assert (
        set(EvalTask.all_objects.using("default").values_list("pk", flat=True))
        == before_tasks
    )
    assert (
        list(through.objects.using("default").order_by("pk").values())
        == before_relations
    )
    starter.assert_not_called()
    if failure_type is RuntimeError:
        assert_safe_failure_log(
            caplog, "eval_task.create_failed", failure_type, private_payload
        )
        assert private_payload not in response.content.decode()
    else:
        # The same classes raised by the starter below must not take this path.
        assert private_payload in response.content.decode()
        assert not [
            record
            for record in caplog.records
            if record.name == "tracer.views.eval_task"
        ]


def test_invalid_create_does_not_persist_or_dispatch(
    auth_client,
    project,
    custom_eval_config,
    starter,
):
    before = EvalTask.all_objects.using("default").count()
    relations_before = EvalTask.evals.through.objects.using("default").count()
    response = auth_client.post(
        _CREATE, payload(project, custom_eval_config, sampling_rate=0), format="json"
    )
    assert response.status_code == 400
    assert EvalTask.all_objects.using("default").count() == before
    assert EvalTask.evals.through.objects.using("default").count() == relations_before
    starter.assert_not_called()


@pytest.mark.parametrize("outer_transaction", [False, True])
def test_initial_dispatch_sees_committed_task_and_relations_from_another_connection(
    auth_client,
    project,
    custom_eval_config,
    starter,
    outer_transaction,
):
    seen = {}

    def observe(task):
        seen["in_atomic_block"] = connections["default"].in_atomic_block
        observer = connections["default"].copy()
        try:
            with observer.cursor() as cursor:
                cursor.execute(
                    "SELECT status FROM tracer_eval_task WHERE id = %s", [task.pk]
                )
                seen["task"] = cursor.fetchone()
                # Table identity comes from model metadata, never a request.
                table = observer.ops.quote_name(EvalTask.evals.through._meta.db_table)
                cursor.execute(
                    f"SELECT customevalconfig_id FROM {table} WHERE evaltask_id = %s",
                    [task.pk],
                )
                seen["evals"] = cursor.fetchall()
        finally:
            observer.close()

    starter.side_effect = observe
    if outer_transaction:
        with transaction.atomic(using="default"):
            response = auth_client.post(
                _CREATE, payload(project, custom_eval_config), format="json"
            )
            starter.assert_not_called()
            assert (
                EvalTask.objects.using("default")
                .filter(pk=response.json()["result"]["id"])
                .exists()
            )
    else:
        response = auth_client.post(
            _CREATE, payload(project, custom_eval_config), format="json"
        )

    task_id = assert_saved(response, starter)
    assert seen == {
        "in_atomic_block": False,
        "task": (EvalTaskStatus.PENDING,),
        "evals": [(custom_eval_config.pk,)],
    }
    saved = task_snapshot(task_id)
    assert saved["start_time"] is None
    assert saved["end_time"] is None
    assert saved["continuous_cursor"] is None


@pytest.mark.parametrize("outer_transaction", [False, True])
@pytest.mark.parametrize(
    "failure_type", [RuntimeError, ValidationError, FilterPrincipalContextError]
)
def test_start_failure_before_acknowledgement_keeps_saved_pending_and_safe_logs(
    auth_client,
    project,
    custom_eval_config,
    starter,
    failure_type,
    outer_transaction,
    caplog,
):
    caplog.set_level("WARNING", logger="tracer.views.eval_task")
    private_payload = "synthetic private provider payload must not be logged"
    before = {}

    def unavailable(task):
        before.update(task_snapshot(task.pk))
        raise failure_type(private_payload)

    starter.side_effect = unavailable
    if outer_transaction:
        with transaction.atomic(using="default"):
            response = auth_client.post(
                _CREATE, payload(project, custom_eval_config), format="json"
            )
            starter.assert_not_called()
    else:
        response = auth_client.post(
            _CREATE, payload(project, custom_eval_config), format="json"
        )

    task_id = assert_saved(response, starter)
    assert task_snapshot(task_id) == before
    assert before["status"] == EvalTaskStatus.PENDING
    assert list(
        EvalTask.objects.using("default")
        .get(pk=task_id)
        .evals.using("default")
        .values_list("pk", flat=True)
    ) == [custom_eval_config.pk]
    assert_safe_failure_log(
        caplog, "eval_task.create_start_unconfirmed", failure_type, private_payload
    )
    assert private_payload not in response.content.decode()


@pytest.mark.parametrize(
    "worker_status",
    [
        EvalTaskStatus.RUNNING,
        EvalTaskStatus.COMPLETED,
        EvalTaskStatus.PAUSED,
        EvalTaskStatus.FAILED,
    ],
)
def test_uncertain_ack_after_simulated_worker_preserves_results_and_all_markers(
    auth_client,
    project,
    custom_eval_config,
    trace,
    starter,
    worker_status,
):
    worker = {}

    def accepted_but_reply_lost(task):
        now = timezone.now()
        EvalTask.objects.using("default").filter(pk=task.pk).update(
            status=worker_status,
            start_time=now,
            end_time=now if worker_status == EvalTaskStatus.COMPLETED else None,
            last_run=now,
            continuous_cursor=now,
            reclassified_evals_revision="synthetic-worker-revision",
            failed_spans=["synthetic-worker-marker"],
        )
        task_log = EvalTaskLogger.objects.using("default").create(
            eval_task_id=task.pk,
            status=worker_status,
            offset=7,
            continuous_cursor=now,
            spanids_processed=["synthetic-worker-span"],
        )
        result = EvalLogger.objects.using("default").create(
            trace=trace,
            observation_span_id="synthetic-worker-span",
            custom_eval_config=custom_eval_config,
            eval_task_id=str(task.pk),
            status=EvalEntryStatus.COMPLETED,
            output_bool=True,
            output_float=0.9,
            eval_explanation="synthetic completed result",
            attempts=1,
            config_hash="a" * 64,
        )
        worker["task"] = task_snapshot(task.pk)
        worker["task_log"] = (
            EvalTaskLogger.all_objects.using("default")
            .filter(pk=task_log.pk)
            .values()
            .get()
        )
        worker["result"] = (
            EvalLogger.all_objects.using("default").filter(pk=result.pk).values().get()
        )
        raise RuntimeError("synthetic acknowledgement lost after worker changes")

    starter.side_effect = accepted_but_reply_lost
    response = auth_client.post(
        _CREATE, payload(project, custom_eval_config), format="json"
    )

    task_id = assert_saved(response, starter)
    assert set(worker) == {"task", "task_log", "result"}
    assert task_snapshot(task_id) == worker["task"]
    assert (
        EvalTaskLogger.all_objects.using("default")
        .filter(eval_task_id=task_id)
        .values()
        .get()
    ) == worker["task_log"]
    assert (
        EvalLogger.all_objects.using("default")
        .filter(eval_task_id=task_id)
        .values()
        .get()
    ) == worker["result"]
    assert list(
        EvalTask.objects.using("default")
        .get(pk=task_id)
        .evals.using("default")
        .values_list("pk", flat=True)
    ) == [custom_eval_config.pk]


def test_create_stamps_the_first_run_so_a_replacement_fences_it(
    auth_client,
    project,
    custom_eval_config,
    starter,
):
    """TH-7963: without a create stamp the first run is unfenced for life, so
    its late failure marks the run that recovery or Resume started Failed."""
    from tfc.temporal.eval_tasks.activities import _set_task_status_sync
    from tfc.temporal.eval_tasks.client import _select

    response = auth_client.post(
        _CREATE, payload(project, custom_eval_config), format="json"
    )
    task_id = assert_saved(response, starter)
    first = task_snapshot(task_id)["workflow_generation"]
    assert first is not None
    started = starter.call_args.args[0]
    assert started.workflow_generation == first
    _, workflow_input = _select(started, "tasks_s")
    assert workflow_input.workflow_generation == str(first)

    EvalTask.objects.using("default").get(pk=task_id).prepare_workflow_replacement()
    late = _set_task_status_sync(
        task_id,
        EvalTaskStatus.FAILED,
        failure_reason="late failure from the first run",
        workflow_generation=str(first),
    )
    assert not late["changed"]
    saved = task_snapshot(task_id)
    assert (saved["status"], saved["failure_reason"]) == (EvalTaskStatus.PENDING, None)
    assert saved["workflow_generation"] not in (None, first)


def test_outer_rollback_discards_task_relations_and_deferred_dispatch(
    auth_client,
    project,
    custom_eval_config,
    starter,
):
    with transaction.atomic(using="default"):
        response = auth_client.post(
            _CREATE, payload(project, custom_eval_config), format="json"
        )
        assert response.status_code == 200, response.content
        task_id = response.json()["result"]["id"]
        assert EvalTask.objects.using("default").get(pk=task_id).evals.count() == 1
        starter.assert_not_called()
        transaction.set_rollback(True, using="default")

    assert not EvalTask.all_objects.using("default").filter(pk=task_id).exists()
    assert not (
        EvalTask.evals.through.objects.using("default")
        .filter(evaltask_id=task_id)
        .exists()
    )
    starter.assert_not_called()
