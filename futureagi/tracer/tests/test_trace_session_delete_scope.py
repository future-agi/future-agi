"""Deletion revalidates its authorized snapshot and confines every descendant."""

import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.db import DatabaseError
from django.db.models.query import QuerySet
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.observation_span import EvalLogger, ObservationSpan
from tracer.models.trace import Trace
from tracer.models.trace_session import TraceSession
from tracer.views.trace_session import _soft_delete_trace_session_tree

pytestmark = pytest.mark.django_db


@pytest.fixture
def tree(observe_project, project, eval_template):
    session = TraceSession.no_workspace_objects.create(project=observe_project)
    trace = Trace.no_workspace_objects.create(
        project=observe_project, session=session, name="Owned trace"
    )
    other_project_trace = Trace.no_workspace_objects.create(
        project=project, session=session, name="Other project trace"
    )

    def span(p, t):
        return ObservationSpan.no_workspace_objects.create(
            id=uuid.uuid4().hex,
            project=p,
            trace=t,
            name="Span",
            observation_type="llm",
            start_time=timezone.now(),
        )

    owned_span = span(observe_project, trace)
    other_project_span = span(project, trace)
    config = CustomEvalConfig.no_workspace_objects.create(
        project=observe_project, eval_template=eval_template, name="Owned config"
    )
    other_project_config = CustomEvalConfig.no_workspace_objects.create(
        project=project, eval_template=eval_template, name="Other project config"
    )
    return SimpleNamespace(
        session=session,
        trace=trace,
        other_project_trace=other_project_trace,
        span=owned_span,
        other_project_span=other_project_span,
        config=config,
        other_project_config=other_project_config,
        project=observe_project,
        other_project=project,
    )


def evaluation(tree, **kwargs):
    values = {
        "trace_id": tree.trace.pk,
        "observation_span_id": tree.span.pk,
        "custom_eval_config": tree.config,
    }
    values.update(kwargs)
    obj = EvalLogger(**values)
    # bulk_create models deliberate inconsistent historical links without model validation.
    EvalLogger.no_workspace_objects.bulk_create([obj])
    return obj


def deleted(obj):
    obj.refresh_from_db()
    return obj.deleted


@pytest.mark.parametrize(
    "kind",
    [
        "trace",
        "span",
        "eval_config",
        "eval_span",
        "eval_trace",
        "session_eval_config",
        "span_wrong_trace",
    ],
)
def test_other_project_descendants_are_untouched(tree, kind):
    if kind == "trace":
        target = tree.other_project_trace
    elif kind == "span":
        target = tree.other_project_span
    elif kind == "eval_config":
        target = evaluation(tree, custom_eval_config=tree.other_project_config)
    elif kind == "eval_span":
        target = evaluation(tree, observation_span_id=tree.other_project_span.pk)
    elif kind == "eval_trace":
        target = evaluation(tree, trace_id=tree.other_project_trace.pk)
    elif kind == "session_eval_config":
        target = evaluation(
            tree,
            trace_id=None,
            observation_span_id=None,
            trace_session=tree.session,
            target_type="session",
            custom_eval_config=tree.other_project_config,
        )
    else:
        other_trace = Trace.no_workspace_objects.create(
            project=tree.project, name="Unrelated"
        )
        ObservationSpan.no_workspace_objects.filter(pk=tree.span.pk).update(
            trace=other_trace
        )
        target = evaluation(tree)
    _soft_delete_trace_session_tree([tree.session])
    assert not deleted(target)
    assert deleted(tree.session)


@pytest.mark.parametrize(
    "kind",
    ["normal", "null_config", "missing_pg_span", "session", "session_null_config"],
)
def test_owned_legacy_and_normal_cascades(tree, kind):
    kwargs = {}
    if kind == "null_config":
        kwargs["custom_eval_config"] = None
    elif kind == "missing_pg_span":
        kwargs["observation_span_id"] = "clickhouse-only-span"
    elif kind.startswith("session"):
        kwargs.update(
            trace_id=None,
            observation_span_id=None,
            trace_session=tree.session,
            target_type="session",
        )
        if kind == "session_null_config":
            kwargs["custom_eval_config"] = None
    target = evaluation(tree, **kwargs)
    _soft_delete_trace_session_tree([tree.session])
    assert all(deleted(obj) for obj in [target, tree.session, tree.trace, tree.span])


def test_database_failure_rolls_back_every_early_write(tree):
    target = evaluation(tree)
    original = QuerySet.update

    def fail_evaluations(queryset, **kwargs):
        if queryset.model is EvalLogger:
            raise DatabaseError("cascade write failure")
        return original(queryset, **kwargs)

    with (
        patch.object(QuerySet, "update", fail_evaluations),
        pytest.raises(DatabaseError),
    ):
        _soft_delete_trace_session_tree([tree.session])
    assert not any(
        deleted(obj) for obj in [target, tree.session, tree.trace, tree.span]
    )


def test_reassigned_session_rejects_stale_authorized_snapshot(tree):
    TraceSession.no_workspace_objects.filter(pk=tree.session.pk).update(
        project=tree.other_project
    )
    with pytest.raises(ValidationError):
        _soft_delete_trace_session_tree([tree.session])
    assert not any(deleted(obj) for obj in [tree.session, tree.trace, tree.span])


@pytest.mark.django_db(transaction=True)
def test_concurrent_reassignment_waits_for_locked_revalidation(tree):
    import time
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from django.db import close_old_connections, connection, transaction

    ready = Event()
    worker_pid = []

    def delete_snapshot():
        close_old_connections()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                worker_pid.append(cursor.fetchone()[0])
            ready.set()
            try:
                _soft_delete_trace_session_tree([tree.session])
            except ValidationError:
                return "ownership_changed"
            return "deleted"
        finally:
            connection.close()

    blocked_query = None
    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            TraceSession.no_workspace_objects.select_for_update().get(
                pk=tree.session.pk
            )
            result = pool.submit(delete_snapshot)
            assert ready.wait(5), "worker database connection did not start"
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                with connection.cursor() as cursor:
                    # Refresh activity statistics within the open transaction.
                    cursor.execute("SELECT pg_stat_clear_snapshot()")
                    cursor.execute(
                        "SELECT query FROM pg_stat_activity WHERE pid = %s "
                        "AND cardinality(pg_blocking_pids(pid)) > 0",
                        [worker_pid[0]],
                    )
                    row = cursor.fetchone()
                if row:
                    blocked_query = row[0]
                    break
                time.sleep(0.02)
            TraceSession.no_workspace_objects.filter(pk=tree.session.pk).update(
                project=tree.other_project
            )
        outcome = result.result(timeout=10)
    assert blocked_query and "FOR UPDATE" in blocked_query
    assert outcome == "ownership_changed"
    assert not any(deleted(obj) for obj in [tree.session, tree.trace, tree.span])


def test_http_delete_preserves_other_project_children(auth_client, tree):
    target = evaluation(tree, custom_eval_config=tree.other_project_config)
    response = auth_client.delete(f"/tracer/trace-session/{tree.session.pk}/")
    assert response.status_code == 204
    assert all(deleted(obj) for obj in [tree.session, tree.trace, tree.span])
    assert not any(
        deleted(obj)
        for obj in [target, tree.other_project_trace, tree.other_project_span]
    )


def test_batch_delete_keeps_each_projects_descendants_separate(tree):
    second = TraceSession.no_workspace_objects.create(project=tree.other_project)
    second_trace = Trace.no_workspace_objects.create(
        project=tree.other_project, session=second
    )
    _soft_delete_trace_session_tree([second, tree.session])
    assert all(deleted(obj) for obj in [second, second_trace, tree.session, tree.trace])
    assert not deleted(tree.other_project_trace)
    assert not deleted(tree.other_project_span)


def test_batch_delete_revalidates_every_session_before_writes(tree):
    second = TraceSession.no_workspace_objects.create(project=tree.project)
    TraceSession.no_workspace_objects.filter(pk=second.pk).update(
        project=tree.other_project
    )
    with pytest.raises(ValidationError):
        _soft_delete_trace_session_tree([tree.session, second])
    assert not any(
        deleted(obj) for obj in [second, tree.session, tree.trace, tree.span]
    )


def test_deleted_other_project_span_is_not_treated_as_absent(tree):
    ObservationSpan.no_workspace_objects.filter(pk=tree.other_project_span.pk).update(
        deleted=True
    )
    target = evaluation(tree, observation_span_id=tree.other_project_span.pk)
    _soft_delete_trace_session_tree([tree.session])
    assert not deleted(target)
    assert deleted(tree.session)


@pytest.fixture
def session_memberships(tree):
    from tracer.models.trace_error_analysis import ErrorClusterTraces, TraceErrorGroup

    memberships = []
    for config in [tree.config, tree.other_project_config]:
        result = evaluation(tree, custom_eval_config=config)
        issue = TraceErrorGroup.no_workspace_objects.create(
            project=config.project,
            cluster_id=f"eval-{len(memberships)}",
            error_type="eval",
            source="eval",
            eval_config=config,
            error_count=1,
            total_events=1,
            unique_traces=1,
        )
        memberships.append(
            ErrorClusterTraces.no_workspace_objects.create(
                cluster=issue, trace=tree.trace, eval_logger=result
            )
        )
    return memberships


def test_session_delete_retires_only_selected_eval_memberships(
    tree, session_memberships
):
    selected, retained = session_memberships
    _soft_delete_trace_session_tree([tree.session])
    assert deleted(selected)
    assert not deleted(retained)
    assert deleted(selected.cluster)
    assert not deleted(retained.cluster)
    retained.cluster.refresh_from_db()
    assert retained.cluster.error_count == 1


def test_analysis_retirement_failure_rolls_back_session_and_memberships(
    tree, session_memberships
):
    from tracer.services.analysis_retention import retire_analysis_sources

    generation = tree.project.analysis_source_generation

    def fail_after_retirement(**kwargs):
        retire_analysis_sources(**kwargs)
        raise DatabaseError("analysis cleanup failed")

    with (
        patch(
            "tracer.services.analysis_retention.retire_analysis_sources",
            side_effect=fail_after_retirement,
        ),
        pytest.raises(DatabaseError, match="analysis cleanup failed"),
    ):
        _soft_delete_trace_session_tree([tree.session])
    assert not any(
        deleted(obj)
        for obj in [tree.session, tree.trace, tree.span, *session_memberships]
    )
    tree.project.refresh_from_db()
    assert tree.project.analysis_source_generation == generation


@pytest.fixture
def project_analysis(tree, monkeypatch):
    from tracer.models.eval_task import EvalTask, EvalTaskStatus
    from tracer.models.instant_eval import InstantEvalRun

    monkeypatch.setattr(
        "tracer.services.analysis_retention._session_aliases",
        lambda ids: {str(value): str(value) for value in ids},
    )
    monkeypatch.setattr(
        "tracer.services.project_deletion._pause_eval_task_workflows", lambda ids: None
    )
    tasks = []
    for project in [tree.project, tree.other_project]:
        task = EvalTask.no_workspace_objects.create(
            project=project, name="Session analysis", status=EvalTaskStatus.RUNNING
        )
        InstantEvalRun.objects.create(
            eval_task=task,
            project=project,
            organization=project.organization,
            unit="sessions",
            row_type="sessions",
        )
        tasks.append(task)
    return tasks


@pytest.mark.parametrize("storage", ["postgres", "clickhouse"])
def test_session_delete_retires_only_own_project_analysis(
    tree, project_analysis, storage
):
    from tracer.views.trace_session import TraceSessionView

    if storage == "postgres":
        _soft_delete_trace_session_tree([tree.session])
    else:
        view = TraceSessionView()
        view.kwargs = {"pk": str(uuid.uuid4())}
        with (
            patch(
                "tracer.views.trace_session._resolve_ch_session_fields",
                return_value={"project_id": tree.project.pk},
            ),
            patch("tracer.services.clickhouse.v2.curated_writer._get_client") as client,
        ):
            assert view._destroy_ch_only_session(SimpleNamespace()).status_code == 204
        assert client.return_value.insert.call_args.args[1][0][0] == str(
            tree.project.pk
        )
    selected, retained = project_analysis
    assert deleted(selected)
    assert not deleted(retained)
    tree.project.refresh_from_db()
    tree.other_project.refresh_from_db()
    assert tree.project.analysis_source_generation == 1
    assert tree.other_project.analysis_source_generation == 0


def test_ch_delete_preserves_other_project_overlay(tree):
    from tracer.models.trace_session import TraceSessionOverlay
    from tracer.views.trace_session import TraceSessionView

    session_id = uuid.uuid4()
    overlay = TraceSessionOverlay._base_manager.create(
        trace_session_id=session_id,
        project_id=tree.other_project.pk,
        display_name="Retained",
    )
    view = TraceSessionView()
    view.kwargs = {"pk": str(session_id)}
    with (
        patch(
            "tracer.views.trace_session._resolve_ch_session_fields",
            return_value={"project_id": tree.project.pk},
        ),
        patch("tracer.services.clickhouse.v2.curated_writer._get_client") as client,
        pytest.raises(ValidationError),
    ):
        view._destroy_ch_only_session(SimpleNamespace())
    assert not deleted(overlay)
    assert overlay.project_id == tree.other_project.pk
    assert overlay.display_name == "Retained"
    client.assert_not_called()


def test_ch_delete_rolls_back_marker_when_analysis_cleanup_fails(tree):
    from tracer.models.trace_session import TraceSessionOverlay
    from tracer.views.trace_session import TraceSessionView

    session_id = uuid.uuid4()
    overlay = TraceSessionOverlay._base_manager.create(
        trace_session_id=session_id, project_id=tree.project.pk, display_name="Retained"
    )
    view = TraceSessionView()
    view.kwargs = {"pk": str(session_id)}
    with (
        patch(
            "tracer.views.trace_session._resolve_ch_session_fields",
            return_value={"project_id": tree.project.pk},
        ),
        patch("tracer.services.clickhouse.v2.curated_writer._get_client") as client,
        patch(
            "tracer.services.analysis_retention.retire_analysis_sources",
            side_effect=DatabaseError("analysis cleanup failed"),
        ),
        pytest.raises(DatabaseError, match="analysis cleanup failed"),
    ):
        view._destroy_ch_only_session(SimpleNamespace())
    assert not deleted(overlay)
    assert overlay.display_name == "Retained"
    client.assert_not_called()


def test_project_delete_does_not_follow_other_project_session_links(
    tree, project_analysis
):
    from tracer.models.project import Project
    from tracer.services.project_deletion import soft_delete_projects

    soft_delete_projects(Project._base_manager.filter(pk=tree.project.pk), "observe")
    assert all(deleted(obj) for obj in [tree.session, tree.trace, tree.span])
    assert not deleted(tree.other_project_trace)
    assert not deleted(tree.other_project_span)
    assert deleted(project_analysis[0])
    assert not deleted(project_analysis[1])


@pytest.mark.django_db(transaction=True)
def test_session_delete_takes_project_lock_before_child_writes(tree):
    import time
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from django.db import close_old_connections, connection, transaction

    from tracer.models.project import Project

    ready = Event()
    worker_pid = []

    def delete_snapshot():
        close_old_connections()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                worker_pid.append(cursor.fetchone()[0])
            ready.set()
            _soft_delete_trace_session_tree([tree.session])
        finally:
            connection.close()

    blocked_query = None
    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            Project._base_manager.select_for_update(no_key=True).get(pk=tree.project.pk)
            result = pool.submit(delete_snapshot)
            assert ready.wait(5), "worker database connection did not start"
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                with connection.cursor() as cursor:
                    # Refresh activity statistics within the open transaction.
                    cursor.execute("SELECT pg_stat_clear_snapshot()")
                    cursor.execute(
                        "SELECT query FROM pg_stat_activity WHERE pid = %s "
                        "AND cardinality(pg_blocking_pids(pid)) > 0",
                        [worker_pid[0]],
                    )
                    row = cursor.fetchone()
                if row:
                    blocked_query = row[0]
                    break
                time.sleep(0.02)
            before = [deleted(obj) for obj in [tree.session, tree.trace, tree.span]]
        result.result(timeout=10)
    assert blocked_query and "FOR NO KEY UPDATE" in blocked_query
    assert not any(before)
    assert all(deleted(obj) for obj in [tree.session, tree.trace, tree.span])
