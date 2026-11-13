"""Accepted span evaluations retain localization retries and task ownership.

Exercise real PostgreSQL/ClickHouse reads, run_entry's result fence and localization
writes. Only the external evaluator, billing and clustering dispatch are stubbed.
"""

import io
import uuid
from datetime import timedelta
from urllib.parse import urlparse

import pytest
import requests
from django.db import OperationalError, connection
from django.utils import timezone
from psycopg import pq
from structlog.testing import capture_logs

import model_hub.tasks  # noqa: F401 - initialize the tasks/eval import cycle
import model_hub.tasks.user_evaluation as user_evaluation
from accounts.models import Organization
from accounts.models.workspace import Workspace
from model_hub.models.error_localizer_model import (
    ErrorLocalizerSource,
    ErrorLocalizerStatus,
    ErrorLocalizerTask,
)
from model_hub.tasks.user_evaluation import trigger_error_localization_for_span
from tfc.middleware.workspace_context import clear_workspace_context
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.eval_task import EvalTask, EvalTaskStatus, RunType
from tracer.models.observation_span import (
    EvalEntryStatus,
    EvalLogger,
    EvalTargetType,
    ObservationSpan,
)
from tracer.models.project import Project
from tracer.models.trace import Trace
from tracer.services.eval_tasks.run_entry import run_entry
from tracer.services.project_deletion import soft_delete_projects
from tracer.tests._ch_seed import seed_ch_trace

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

NEW_INPUT = "When does the delivery arrive?"
NEW_OUTPUT = "It arrives Friday."
NEW_REASON = "The requested Monday delivery was answered as Friday."
CONTEXT_MISMATCH = "error_localizer_span_context_mismatch"
OUT_OF_SCOPE = "error_localizer_span_existing_task_out_of_scope"
WORKSPACE_MISMATCH = "error_localizer_workspace_mismatch"
TRIGGER_RETRYING = "error_localizer_span_trigger_retrying"
TRIGGER_FAILED = "error_localizer_span_trigger_failed"
DB_UNAVAILABLE = "error_localizer_span_trigger_db_unavailable"
MEDIA_HOST = "media.example.test"
RECORDING_URL = f"https://{MEDIA_HOST}/recordings/call-0001.wav"
# Enough of a RIFF/WAVE header for ``filetype`` to sniff audio.
WAV_HEADER = b"RIFF" + (36).to_bytes(4, "little") + b"WAVEfmt " + bytes(24)


def _events(records, event):
    return [record for record in records if record["event"] == event]


def _assert_no_trigger_errors(records):
    errors = [
        record
        for record in records
        if record["log_level"] == "error"
        and str(record["event"]).startswith("error_localizer")
    ]
    assert not errors, errors


@pytest.fixture
def scenario_factory(observe_project, eval_template, workspace, ch_seed, monkeypatch):
    monkeypatch.setattr(
        "tracer.tasks.eval_clustering.cluster_eval_results_task.apply_async",
        lambda *args, **kwargs: None,
    )
    eval_template.config = {
        "output": "boolean",
        "rule_prompt": "The delivery must arrive Monday.",
    }
    eval_template.save(update_fields=["config"])

    def create(
        *, borrowed=False, pg_span=True, fallback_workspace=False, span_input=NEW_INPUT
    ):
        target = observe_project
        if fallback_workspace:
            Project.no_workspace_objects.filter(pk=target.pk).update(workspace=None)
            target.refresh_from_db()
        author = target
        if borrowed:
            author = Project.objects.create(
                name="Sibling config author",
                organization=target.organization,
                workspace=workspace,
                model_type=target.model_type,
                trace_type="observe",
            )
        config = CustomEvalConfig.objects.create(
            name="Localization-enabled evaluator",
            project=author,
            eval_template=eval_template,
            config={"run_config": {"error_localizer_enabled": True}},
            mapping={"input": "input", "output": "output"},
            filters={},
        )
        owner_task = EvalTask.objects.create(
            project=target,
            name="Continuous span evaluation",
            row_type="spans",
            run_type=RunType.CONTINUOUS,
            status=EvalTaskStatus.PENDING,
            sampling_rate=1.0,
            filters={},
        )
        owner_task.evals.add(config)
        now = timezone.now() - timedelta(minutes=1)
        trace = Trace(
            id=uuid.uuid4(),
            project=target,
            name="Delivery",
            created_at=now,
            updated_at=now,
        )
        span = ObservationSpan(
            id=uuid.uuid4().hex[:16],
            trace=trace,
            project=target,
            name="Response",
            observation_type="llm",
            status="OK",
            input=span_input,
            output=NEW_OUTPUT,
            start_time=now,
            end_time=now + timedelta(seconds=1),
            created_at=now,
            updated_at=now,
        )
        if pg_span:
            trace.save()
            span.save()
        seed_ch_trace(trace)
        ch_seed(span)
        entry = EvalLogger(
            trace_id=trace.id,
            observation_span_id=span.id,
            custom_eval_config=config,
            eval_task_id=str(owner_task.id),
            target_type=EvalTargetType.SPAN,
            status=EvalEntryStatus.RUNNING,
        )
        EvalLogger.objects.bulk_create([entry])
        assert ObservationSpan.objects.filter(pk=span.pk).exists() is pg_span
        return entry, span, config, owner_task

    return create


def _old_localization(entry, span, config, workspace, status):
    task = ErrorLocalizerTask.objects.create(
        source=ErrorLocalizerSource.OBSERVE,
        source_id=entry.id,
        eval_template=config.eval_template,
        organization=span.project.organization,
        workspace=workspace,
        status=status,
        input_data={"output": "obsolete answer"},
        input_keys=["output"],
        input_types={"output": "text"},
        eval_result=True,
        eval_explanation="obsolete explanation",
        rule_prompt="obsolete rule",
        error_message="obsolete failure",
        metadata={"previous_run": True},
    )
    # Guarantee a genuine pre-workspace row, independent of save hooks.
    if workspace is None:
        ErrorLocalizerTask.no_workspace_objects.filter(pk=task.pk).update(
            workspace=None
        )
        task.refresh_from_db()
        assert task.workspace_id is None
    return task


def _run_and_require_accepted(entry, stub_run_eval):
    stub_run_eval(value=False, reason=NEW_REASON)
    outcome = run_entry(entry)
    entry.refresh_from_db()
    assert outcome == EvalEntryStatus.COMPLETED, (outcome, entry.error_message)
    assert entry.status == EvalEntryStatus.COMPLETED
    assert entry.output_bool is False
    assert not entry.error
    assert stub_run_eval.requests, "the evaluator was never reached"


def _assert_refreshed(task, entry, workspace):
    task.refresh_from_db()
    assert (
        ErrorLocalizerTask.no_workspace_objects.filter(source_id=entry.id).count() == 1
    )
    assert task.status == ErrorLocalizerStatus.PENDING
    assert task.workspace_id == workspace.id
    assert task.input_data == {"input": NEW_INPUT, "output": NEW_OUTPUT}
    assert task.input_keys == ["input", "output"]
    assert task.input_types == {"input": "text", "output": "text"}
    assert task.eval_result is False
    assert task.eval_explanation == NEW_REASON
    assert task.error_message == ""
    assert task.metadata["previous_run"] is True
    assert task.rule_prompt == "The delivery must arrive Monday."


@pytest.mark.parametrize("pg_span", [True, False], ids=["pg-and-ch", "ch-only"])
@pytest.mark.parametrize("worker_context", [True, False], ids=["worker", "request"])
@pytest.mark.parametrize(
    "fallback_workspace", [False, True], ids=["explicit", "fallback"]
)
@pytest.mark.parametrize(
    "old_status", [ErrorLocalizerStatus.FAILED, ErrorLocalizerStatus.COMPLETED]
)
@pytest.mark.parametrize("legacy_null", [True, False], ids=["legacy-null", "assigned"])
def test_retry_adopts_legacy_null_workspace(
    scenario_factory,
    workspace,
    stub_run_eval,
    stub_cost_log,
    pg_span,
    worker_context,
    fallback_workspace,
    old_status,
    legacy_null,
):
    entry, span, config, _ = scenario_factory(
        pg_span=pg_span,
        fallback_workspace=fallback_workspace,
    )
    old = _old_localization(
        entry, span, config, None if legacy_null else workspace, old_status
    )
    if worker_context:
        clear_workspace_context()
    _run_and_require_accepted(entry, stub_run_eval)
    _assert_refreshed(old, entry, workspace)


@pytest.mark.parametrize("pg_span", [True, False], ids=["pg-and-ch", "ch-only"])
@pytest.mark.parametrize("borrowed", [True, False], ids=["borrowed", "same-project"])
@pytest.mark.parametrize("existing", [False, True], ids=["create", "refresh"])
def test_localization_uses_task_project(
    scenario_factory,
    workspace,
    stub_run_eval,
    stub_cost_log,
    pg_span,
    borrowed,
    existing,
):
    entry, span, config, owner = scenario_factory(borrowed=borrowed, pg_span=pg_span)
    assert (config.project_id != owner.project_id) is borrowed
    assert config.project.organization_id == owner.project.organization_id
    assert config.project.workspace_id == owner.project.workspace_id
    if existing:
        old = _old_localization(
            entry, span, config, workspace, ErrorLocalizerStatus.COMPLETED
        )
    clear_workspace_context()
    _run_and_require_accepted(entry, stub_run_eval)
    task = ErrorLocalizerTask.no_workspace_objects.get(source_id=entry.id)
    assert task.status == ErrorLocalizerStatus.PENDING
    assert task.organization_id == owner.project.organization_id
    assert task.workspace_id == workspace.id
    assert task.eval_result is False
    assert task.input_data == {"input": NEW_INPUT, "output": NEW_OUTPUT}
    if existing:
        assert task.id == old.id
        _assert_refreshed(old, entry, workspace)


@pytest.mark.parametrize("null_project", ["author", "target"])
def test_borrowed_config_resolves_legacy_default_workspace(
    scenario_factory,
    workspace,
    stub_run_eval,
    stub_cost_log,
    null_project,
):
    entry, _, config, owner = scenario_factory(borrowed=True, pg_span=False)
    project = config.project if null_project == "author" else owner.project
    Project.no_workspace_objects.filter(pk=project.pk).update(workspace=None)
    clear_workspace_context()
    _run_and_require_accepted(entry, stub_run_eval)
    task = ErrorLocalizerTask.no_workspace_objects.get(source_id=entry.id)
    assert task.workspace_id == workspace.id
    assert task.organization_id == owner.project.organization_id


def _trigger(entry, span, config, span_input=NEW_INPUT):
    trigger_error_localization_for_span(
        eval_template=config.eval_template,
        eval_logger=entry,
        observation_span=span,
        value=False,
        mapping={"input": span_input, "output": NEW_OUTPUT},
        eval_explanation=NEW_REASON,
    )


def _other_workspace(workspace):
    return Workspace.objects.create(
        name="Other workspace",
        organization=workspace.organization,
        is_active=True,
        is_default=False,
        created_by=workspace.created_by,
    )


@pytest.mark.parametrize(
    "mismatch", ["organization", "workspace", "source", "null-nondefault"]
)
def test_existing_localization_outside_scope_is_not_refreshed(
    scenario_factory,
    workspace,
    mismatch,
):
    entry, span, config, _ = scenario_factory()
    old = _old_localization(
        entry, span, config, workspace, ErrorLocalizerStatus.COMPLETED
    )
    if mismatch == "organization":
        old.organization = Organization.objects.create(name="Other tenant")
        old.workspace = None
    elif mismatch == "workspace":
        old.workspace = _other_workspace(workspace)
    elif mismatch == "source":
        old.source = ErrorLocalizerSource.DATASET
    else:
        span.project.workspace = _other_workspace(workspace)
        span.project.save(update_fields=["workspace"])
        old.workspace = None
    old.save()
    original_workspace_id = old.workspace_id
    clear_workspace_context()
    with capture_logs() as records:
        _trigger(entry, span, config)
    old.refresh_from_db()
    assert old.status == ErrorLocalizerStatus.COMPLETED
    assert old.input_data == {"output": "obsolete answer"}
    assert old.eval_result is True
    assert old.workspace_id == original_workspace_id
    assert (
        ErrorLocalizerTask.no_workspace_objects.filter(source_id=entry.id).count() == 1
    )
    # A deliberate refusal, not a trigger crash an operator must investigate.
    _assert_no_trigger_errors(records)
    (refusal,) = _events(records, OUT_OF_SCOPE)
    assert refusal["log_level"] == "warning"
    assert refusal["eval_logger_id"] == str(entry.id)


@pytest.mark.parametrize("mismatch", ["organization", "workspace"])
@pytest.mark.parametrize("existing", [False, True], ids=["create", "refresh"])
def test_borrowed_config_outside_tenant_scope_is_rejected(
    scenario_factory,
    workspace,
    mismatch,
    existing,
):
    entry, span, config, _ = scenario_factory(borrowed=True)
    if existing:
        old = _old_localization(
            entry, span, config, workspace, ErrorLocalizerStatus.COMPLETED
        )
    author = config.project
    if mismatch == "organization":
        author.organization = Organization.objects.create(name="Other tenant")
        author.workspace = None
    else:
        author.workspace = _other_workspace(workspace)
    author.save(update_fields=["organization", "workspace"])
    clear_workspace_context()
    with capture_logs() as records:
        _trigger(entry, span, config)
    assert ErrorLocalizerTask.no_workspace_objects.filter(
        source_id=entry.id
    ).count() == int(existing)
    _assert_no_trigger_errors(records)
    if mismatch == "workspace":
        (refusal,) = _events(records, WORKSPACE_MISMATCH)
        assert refusal["log_level"] == "warning"
        assert refusal["eval_logger_id"] == str(entry.id)
        assert refusal["reason"] == "cross_workspace_config"
    if existing:
        old.refresh_from_db()
        assert old.status == ErrorLocalizerStatus.COMPLETED
        assert old.input_data == {"output": "obsolete answer"}


def test_project_workspace_of_another_organization_is_refused(
    scenario_factory, workspace
):
    entry, span, config, _ = scenario_factory()
    other_tenant = Organization.objects.create(name="Other tenant")
    foreign_workspace = Workspace.objects.create(
        name="Other tenant workspace",
        organization=other_tenant,
        is_active=True,
        is_default=False,
        created_by=workspace.created_by,
    )
    # The span and config still agree on one organization, so only the
    # target workspace's organization differs.
    Project.no_workspace_objects.filter(pk=span.project.pk).update(
        workspace=foreign_workspace
    )
    span.project.refresh_from_db()
    assert span.project.organization_id == config.project.organization_id
    clear_workspace_context()
    with capture_logs() as records:
        _trigger(entry, span, config)
    assert not ErrorLocalizerTask.no_workspace_objects.filter(
        source_id=entry.id
    ).exists()
    _assert_no_trigger_errors(records)
    assert not _events(records, CONTEXT_MISMATCH)
    (refusal,) = _events(records, WORKSPACE_MISMATCH)
    assert refusal["log_level"] == "warning"
    assert refusal["eval_logger_id"] == str(entry.id)
    assert refusal["reason"] == "target_workspace_organization"


@pytest.mark.parametrize(
    "mismatch", ["span-project", "missing-task", "deleted-task", "no-task"]
)
def test_task_target_cannot_be_replaced_by_config_author_project(
    scenario_factory, mismatch
):
    entry, span, config, owner = scenario_factory(borrowed=True)
    if mismatch == "span-project":
        span.project = config.project
    elif mismatch == "missing-task":
        entry.eval_task_id = str(uuid.uuid4())
    elif mismatch == "deleted-task":
        owner.delete()
    else:
        # Legacy entries with no task still belong to the config's project.
        entry.eval_task_id = None
    clear_workspace_context()
    with capture_logs() as records:
        _trigger(entry, span, config)
    assert not ErrorLocalizerTask.no_workspace_objects.filter(
        source_id=entry.id
    ).exists()
    # A gone owning task is refused like any other context mismatch, at
    # WARNING, rather than surfacing as a trigger crash.
    _assert_no_trigger_errors(records)
    (refusal,) = _events(records, CONTEXT_MISMATCH)
    assert refusal["log_level"] == "warning"
    assert refusal["reason"] == (
        "owning_task_missing"
        if mismatch in ("missing-task", "deleted-task")
        else "span_context"
    )


def test_owning_project_deleted_mid_run_is_refused_not_crashed(
    scenario_factory, stub_run_eval, stub_cost_log, monkeypatch
):
    # The sibling-authored config keeps the entry live when the target project
    # is deleted, so the accepted result lands and the trigger sees a gone task.
    entry, _, _, owner = scenario_factory(borrowed=True, pg_span=False)
    stub_run_eval(value=False, reason=NEW_REASON)
    import evaluations.engine.runner as runner_module

    evaluate = runner_module.run_eval

    def delete_target_project_then_evaluate(request):
        soft_delete_projects(Project.objects.filter(pk=owner.project_id), "observe")
        return evaluate(request)

    for target in ("evaluations.engine.run_eval", "evaluations.engine.runner.run_eval"):
        monkeypatch.setattr(target, delete_target_project_then_evaluate, raising=False)
    clear_workspace_context()
    with capture_logs() as records:
        outcome = run_entry(entry)
    entry.refresh_from_db()
    assert EvalTask.all_objects.get(pk=owner.pk).deleted
    assert outcome == EvalEntryStatus.COMPLETED, (outcome, entry.error_message)
    assert entry.output_bool is False
    assert not ErrorLocalizerTask.no_workspace_objects.filter(
        source_id=entry.id
    ).exists()
    _assert_no_trigger_errors(records)
    (refusal,) = _events(records, CONTEXT_MISMATCH)
    assert refusal["log_level"] == "warning"
    assert refusal["reason"] == "owning_task_missing"


def _fail_trigger_statements(monkeypatch, table, failures):
    """Fail the trigger's first ``failures`` statements on ``table``.

    Raised at the SQL layer like a statement timeout on a still-usable
    connection, independent of how the trigger builds the query. ``run_entry``'s
    own reads run outside the trigger and are unaffected.
    """
    real_trigger = user_evaluation.trigger_error_localization_for_span
    failed = []

    def flaky(execute, sql, params, many, context):
        if f'"{table}"' in sql and len(failed) < failures:
            failed.append(sql)
            raise OperationalError("canceling statement due to statement timeout")
        return execute(sql, params, many, context)

    def trigger_with_flaky_statements(*args, **kwargs):
        with connection.execute_wrapper(flaky):
            return real_trigger(*args, **kwargs)

    monkeypatch.setattr(
        user_evaluation,
        "trigger_error_localization_for_span",
        trigger_with_flaky_statements,
    )
    monkeypatch.setattr(
        user_evaluation,
        "SPAN_LOCALIZATION_RETRY_DELAY_SECONDS",
        0,
        raising=False,
    )
    return failed


@pytest.mark.parametrize(
    "table",
    ["tracer_eval_task", "error_localizer_task"],
    ids=["owning-task-read", "existing-localization-read"],
)
@pytest.mark.parametrize("borrowed", [True, False], ids=["borrowed", "same-project"])
def test_transient_read_failure_does_not_drop_localization(
    scenario_factory,
    workspace,
    stub_run_eval,
    stub_cost_log,
    monkeypatch,
    table,
    borrowed,
):
    entry, _, _, owner = scenario_factory(borrowed=borrowed)
    failed = _fail_trigger_statements(monkeypatch, table, failures=1)
    clear_workspace_context()
    with capture_logs() as records:
        _run_and_require_accepted(entry, stub_run_eval)
    assert len(failed) == 1, "the trigger never read the table under test"
    task = ErrorLocalizerTask.no_workspace_objects.get(source_id=entry.id)
    assert task.status == ErrorLocalizerStatus.PENDING
    assert task.organization_id == owner.project.organization_id
    assert task.workspace_id == workspace.id
    assert task.eval_result is False
    assert task.eval_explanation == NEW_REASON
    assert task.input_data == {"input": NEW_INPUT, "output": NEW_OUTPUT}
    _assert_no_trigger_errors(records)
    (retry,) = _events(records, TRIGGER_RETRYING)
    assert retry["log_level"] == "warning"
    assert retry["error_type"] == "OperationalError"


def test_real_statement_timeout_on_owning_task_read_is_retried(
    scenario_factory, stub_run_eval, stub_cost_log, monkeypatch
):
    # PostgreSQL itself cancels the read, which aborts the enclosing
    # transaction. The retry must still find a usable connection afterwards.
    entry, _, _, _ = scenario_factory(borrowed=True)
    real_trigger = user_evaluation.trigger_error_localization_for_span
    cancelled = []

    def cancel_first_task_read(execute, sql, params, many, context):
        if '"tracer_eval_task"' in sql and not cancelled:
            cancelled.append(sql)
            execute("SET LOCAL statement_timeout = 1", None, False, context)
            execute("SELECT pg_sleep(1)", None, False, context)
        return execute(sql, params, many, context)

    def trigger_with_cancel(*args, **kwargs):
        with connection.execute_wrapper(cancel_first_task_read):
            return real_trigger(*args, **kwargs)

    monkeypatch.setattr(
        user_evaluation, "trigger_error_localization_for_span", trigger_with_cancel
    )
    monkeypatch.setattr(
        user_evaluation, "SPAN_LOCALIZATION_RETRY_DELAY_SECONDS", 0, raising=False
    )
    clear_workspace_context()
    with capture_logs() as records:
        _run_and_require_accepted(entry, stub_run_eval)
    assert len(cancelled) == 1
    assert ErrorLocalizerTask.no_workspace_objects.filter(source_id=entry.id).exists()
    _assert_no_trigger_errors(records)
    (retry,) = _events(records, TRIGGER_RETRYING)
    assert retry["error_type"] == "OperationalError"


def test_persistent_read_failure_is_reported_as_database_unavailable(
    scenario_factory, stub_run_eval, stub_cost_log, monkeypatch
):
    entry, _, _, _ = scenario_factory(borrowed=True)
    failed = _fail_trigger_statements(monkeypatch, "tracer_eval_task", 10**6)
    clear_workspace_context()
    with capture_logs() as records:
        # The accepted result stays accepted: localization never errors the entry.
        _run_and_require_accepted(entry, stub_run_eval)
    assert not ErrorLocalizerTask.no_workspace_objects.filter(
        source_id=entry.id
    ).exists()
    assert not _events(records, TRIGGER_FAILED)
    (failure,) = _events(records, DB_UNAVAILABLE)
    assert failure["log_level"] == "error"
    assert failure["error_type"] == "OperationalError"
    assert failure["attempts"] == len(failed) > 1
    assert len(_events(records, TRIGGER_RETRYING)) == len(failed) - 1


@pytest.mark.parametrize("pg_span", [True, False], ids=["pg-and-ch", "ch-only"])
@pytest.mark.parametrize("existing", [False, True], ids=["create", "refresh"])
def test_authenticated_sibling_config_patch_preserves_localization(
    scenario_factory,
    auth_client,
    workspace,
    stub_run_eval,
    stub_cost_log,
    pg_span,
    existing,
):
    entry, span, config, owner = scenario_factory(borrowed=True, pg_span=pg_span)
    if existing:
        old = _old_localization(
            entry, span, config, workspace, ErrorLocalizerStatus.COMPLETED
        )
    owner.evals.clear()
    response = auth_client.patch(
        f"/tracer/eval-task/{owner.id}/",
        {"evals": [str(config.id)]},
        format="json",
    )
    assert response.status_code == 200, response.content
    assert owner.evals.filter(pk=config.pk).exists()
    clear_workspace_context()
    _run_and_require_accepted(entry, stub_run_eval)
    localization = ErrorLocalizerTask.no_workspace_objects.get(source_id=entry.id)
    assert localization.workspace_id == workspace.id
    assert localization.organization_id == span.project.organization_id
    if existing:
        assert localization.id == old.id
        _assert_refreshed(old, entry, workspace)


class _MediaHost:
    """A synthetic media host behind ``requests.get``.

    Input-type detection sniffs every URL-shaped mapping value with a streamed
    GET. Each fetch records whether the trigger's database connection was
    inside a transaction at that moment, then runs ``during_fetch`` if set.
    """

    def __init__(self):
        self.fetches = []
        self.during_fetch = None

    def get(self, url, *args, **kwargs):
        pg = connection.connection
        status = None if pg is None else pg.info.transaction_status
        self.fetches.append(
            {
                "url": url,
                "in_atomic_block": connection.in_atomic_block,
                "pg_transaction_status": status,
            }
        )
        if self.during_fetch:
            self.during_fetch()
        response = requests.Response()
        response.status_code = 200
        response.headers["Content-Type"] = "audio/wav"
        response.raw = io.BytesIO(WAV_HEADER)
        response.url = url
        return response


@pytest.fixture
def media_host(monkeypatch):
    host = _MediaHost()
    real_get = requests.get

    def get(url, *args, **kwargs):
        if urlparse(str(url)).hostname == MEDIA_HOST:
            return host.get(url, *args, **kwargs)
        # Plain text values also pass through requests.get and fail fast.
        return real_get(url, *args, **kwargs)

    monkeypatch.setattr(requests, "get", get)
    return host


FETCHED_OUTSIDE_TRANSACTION = {
    "url": RECORDING_URL,
    "in_atomic_block": False,
    "pg_transaction_status": pq.TransactionStatus.IDLE,
}


# Committed transactions, like the worker: the test's own transaction would
# otherwise make every statement look like it ran inside one.
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "transient",
    [None, "tracer_eval_task", "error_localizer_task"],
    ids=["first-attempt", "owning-task-read-retried", "localization-read-retried"],
)
def test_media_fetch_runs_outside_any_transaction(
    scenario_factory,
    workspace,
    stub_run_eval,
    stub_cost_log,
    media_host,
    monkeypatch,
    transient,
):
    # A transaction open across the fetch would pin a pooled server connection
    # and hold table locks for as long as the media host takes to answer.
    entry, _, _, owner = scenario_factory(borrowed=True, span_input=RECORDING_URL)
    failed = []
    if transient:
        failed = _fail_trigger_statements(monkeypatch, transient, failures=1)
    clear_workspace_context()
    with capture_logs() as records:
        _run_and_require_accepted(entry, stub_run_eval)
    assert len(failed) == int(bool(transient))
    # Fetched once, from autocommit; a retried read or write reuses the types.
    assert media_host.fetches == [FETCHED_OUTSIDE_TRANSACTION]
    task = ErrorLocalizerTask.no_workspace_objects.get(source_id=entry.id)
    assert task.status == ErrorLocalizerStatus.PENDING
    assert task.organization_id == owner.project.organization_id
    assert task.workspace_id == workspace.id
    assert task.input_data == {"input": RECORDING_URL, "output": NEW_OUTPUT}
    assert task.input_types == {"input": "audio", "output": "text"}
    _assert_no_trigger_errors(records)
    assert len(_events(records, TRIGGER_RETRYING)) == len(failed)


@pytest.mark.parametrize("refusal", ["owning-task-missing", "workspace-mismatch"])
def test_refused_span_never_fetches_media(
    scenario_factory, workspace, media_host, refusal
):
    entry, span, config, owner = scenario_factory(borrowed=True)
    if refusal == "owning-task-missing":
        owner.delete()
    else:
        author = config.project
        author.workspace = _other_workspace(workspace)
        author.save(update_fields=["workspace"])
    clear_workspace_context()
    with capture_logs() as records:
        _trigger(entry, span, config, span_input=RECORDING_URL)
    assert media_host.fetches == []
    assert not ErrorLocalizerTask.no_workspace_objects.filter(
        source_id=entry.id
    ).exists()
    _assert_no_trigger_errors(records)


def test_owning_project_deleted_during_media_fetch_is_refused(
    scenario_factory, media_host
):
    entry, span, config, owner = scenario_factory(borrowed=True)
    media_host.during_fetch = lambda: soft_delete_projects(
        Project.objects.filter(pk=owner.project_id), "observe"
    )
    clear_workspace_context()
    with capture_logs() as records:
        _trigger(entry, span, config, span_input=RECORDING_URL)
    assert len(media_host.fetches) == 1
    assert EvalTask.all_objects.get(pk=owner.pk).deleted
    # The write re-reads the owning task instead of trusting the pre-fetch read.
    assert not ErrorLocalizerTask.no_workspace_objects.filter(
        source_id=entry.id
    ).exists()
    _assert_no_trigger_errors(records)
    (refusal,) = _events(records, CONTEXT_MISMATCH)
    assert refusal["log_level"] == "warning"
    assert refusal["reason"] == "owning_task_missing"
