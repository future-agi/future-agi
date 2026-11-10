"""Accepted span evaluations retain localization retries and task ownership.

Exercise real PostgreSQL/ClickHouse reads, run_entry's result fence and localization
writes. Only the external evaluator, billing and clustering dispatch are stubbed.
"""

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

import model_hub.tasks  # noqa: F401 - initialize the tasks/eval import cycle
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
from tracer.tests._ch_seed import seed_ch_trace

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

NEW_INPUT = "When does the delivery arrive?"
NEW_OUTPUT = "It arrives Friday."
NEW_REASON = "The requested Monday delivery was answered as Friday."


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

    def create(*, borrowed=False, pg_span=True, fallback_workspace=False):
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
            input=NEW_INPUT,
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


def _trigger(entry, span, config):
    trigger_error_localization_for_span(
        eval_template=config.eval_template,
        eval_logger=entry,
        observation_span=span,
        value=False,
        mapping={"input": NEW_INPUT, "output": NEW_OUTPUT},
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
    _trigger(entry, span, config)
    old.refresh_from_db()
    assert old.status == ErrorLocalizerStatus.COMPLETED
    assert old.input_data == {"output": "obsolete answer"}
    assert old.eval_result is True
    assert old.workspace_id == original_workspace_id
    assert (
        ErrorLocalizerTask.no_workspace_objects.filter(source_id=entry.id).count() == 1
    )


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
    _trigger(entry, span, config)
    assert ErrorLocalizerTask.no_workspace_objects.filter(
        source_id=entry.id
    ).count() == int(existing)
    if existing:
        old.refresh_from_db()
        assert old.status == ErrorLocalizerStatus.COMPLETED
        assert old.input_data == {"output": "obsolete answer"}


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
    _trigger(entry, span, config)
    assert not ErrorLocalizerTask.no_workspace_objects.filter(
        source_id=entry.id
    ).exists()


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
