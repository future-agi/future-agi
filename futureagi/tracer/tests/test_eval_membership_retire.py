"""Deleting eval results retires their Feed memberships and recounts the issue.

A membership whose eval was deleted kept the trace in its issue: the issue
listed the trace with no eval and still counted it. Every eval soft-delete now
goes through ``soft_delete_eval_results``, which retires those memberships in
the same transaction. Nothing is hard-deleted: an issue left empty is
soft-deleted unless a person owns it, and the same failure recurring revives it.
"""

import ast
import importlib
import pathlib
import uuid
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.apps import apps as django_apps
from django.utils import timezone

from tracer.models.observation_span import EvalLogger, EvalTargetType, ObservationSpan
from tracer.models.trace import Trace
from tracer.models.trace_error_analysis import (
    ClusterSource,
    ErrorClusterTraces,
    FeedIssueStatus,
    TraceErrorGroup,
)
from tracer.queries.eval_clustering import assign_to_cluster
from tracer.services.eval_tasks.entries import (
    soft_delete_eval_results,
    soft_delete_live,
)
from tracer.types.eval_cluster_types import ClusterableEvalResult

pytestmark = pytest.mark.django_db


def _span(project, project_version):
    trace = Trace.objects.create(
        project=project, project_version=project_version, name="t"
    )
    return ObservationSpan.objects.create(
        id=f"span_{uuid.uuid4().hex[:16]}",
        project=project,
        trace=trace,
        name="root",
        observation_type="llm",
        start_time=timezone.now() - timedelta(seconds=1),
        end_time=timezone.now(),
    )


def _failing_eval(cfg, span, task_id):
    # Trace-target rows carry the root span too; EvalLogger.save() enforces it.
    return EvalLogger.objects.create(
        trace=span.trace,
        observation_span=span,
        custom_eval_config=cfg,
        target_type=EvalTargetType.TRACE,
        output_float=0.2,
        eval_explanation="the agent skipped the order lookup",
        eval_task_id=task_id,
    )


def _issue(
    project, cfg, cluster_id="E-RETIRE-1", target=EvalTargetType.TRACE, **fields
):
    return TraceErrorGroup.objects.create(
        project=project,
        cluster_id=cluster_id,
        error_type="eval",
        issue_group=cfg.name,
        source=ClusterSource.EVAL,
        eval_target_type=target,
        eval_config=cfg,
        **fields,
    )


@pytest.fixture
def issue_with_three(project, project_version, custom_eval_config, eval_task):
    issue = _issue(
        project, custom_eval_config, error_count=3, total_events=3, unique_traces=3
    )
    evals = []
    for _ in range(3):
        ev = _failing_eval(
            custom_eval_config, _span(project, project_version), str(eval_task.id)
        )
        ErrorClusterTraces.objects.create(cluster=issue, trace=ev.trace, eval_logger=ev)
        evals.append(ev)
    return issue, evals


@pytest.fixture
def no_clickhouse():
    """Stub the centroid store and the LLM metadata call; PG is under test."""
    ch = MagicMock()
    ch.return_value.execute_read.return_value = []
    with (
        patch("tracer.queries.eval_clustering.ClickHouseVectorDB", ch),
        patch("tracer.queries.eval_clustering.ensure_centroid_table", MagicMock()),
        patch("tracer.ee_boundary.generate_eval_cluster_meta", return_value=None),
    ):
        yield


def test_deleting_one_eval_retires_its_membership_and_recounts(issue_with_three):
    issue, evals = issue_with_three

    deleted = soft_delete_eval_results(EvalLogger.objects.filter(id=evals[0].id))

    assert deleted == 1
    live = ErrorClusterTraces.objects.filter(cluster=issue)
    assert {m.eval_logger_id for m in live} == {evals[1].id, evals[2].id}
    issue.refresh_from_db()
    assert (issue.error_count, issue.total_events, issue.unique_traces) == (2, 2, 2)
    assert issue.deleted is False


def test_delete_and_rerun_soft_deletes_an_unowned_empty_issue(
    issue_with_three, eval_task
):
    issue, _evals = issue_with_three

    assert soft_delete_live(eval_task) == 3

    assert not TraceErrorGroup.objects.filter(pk=issue.pk).exists()
    assert TraceErrorGroup.all_objects.get(pk=issue.pk).deleted is True
    # Soft only: the retired rows stay in the table.
    assert (
        ErrorClusterTraces.all_objects.filter(cluster=issue, deleted=True).count() == 3
    )


@pytest.mark.parametrize(
    "owner",
    [
        {"external_issue_id": "LIN-1"},
        {"external_issue_url": "https://linear.app/x/issue/LIN-1"},
        {"status": FeedIssueStatus.ACKNOWLEDGED},
        "assignee",
    ],
)
def test_an_issue_a_person_owns_stays_live_at_zero(
    issue_with_three, eval_task, user, owner
):
    issue, _evals = issue_with_three
    if owner == "assignee":
        issue.assignee = user
        issue.save(update_fields=["assignee"])
    else:
        TraceErrorGroup.objects.filter(pk=issue.pk).update(**owner)

    soft_delete_live(eval_task)

    issue.refresh_from_db()
    assert issue.deleted is False
    assert (issue.error_count, issue.unique_traces) == (0, 0)


def test_the_same_failure_recurring_revives_the_issue(
    issue_with_three, eval_task, project, project_version, no_clickhouse
):
    issue, evals = issue_with_three
    soft_delete_live(eval_task)
    cfg = evals[0].custom_eval_config
    again = _failing_eval(cfg, _span(project, project_version), "et-rerun")
    result = ClusterableEvalResult(
        eval_logger_id=str(again.id),
        project_id=str(project.id),
        eval_name=cfg.name,
        eval_config_id=str(cfg.id),
        explanation=again.eval_explanation,
        target_type=EvalTargetType.TRACE,
        trace_id=str(again.trace_id),
        session_id=None,
        score=0.2,
    )

    assign_to_cluster(issue.cluster_id, str(project.id), result, [0.1, 0.2])

    revived = TraceErrorGroup.objects.get(pk=issue.pk)
    assert (revived.error_count, revived.unique_traces) == (1, 1)


def test_deleting_twice_changes_nothing_the_second_time(issue_with_three):
    issue, evals = issue_with_three
    soft_delete_eval_results(EvalLogger.objects.filter(id=evals[0].id))
    issue.refresh_from_db()
    first = (issue.error_count, issue.unique_traces, issue.updated_at)

    assert soft_delete_eval_results(EvalLogger.objects.filter(id=evals[0].id)) == 0

    issue.refresh_from_db()
    assert (issue.error_count, issue.unique_traces, issue.updated_at) == first


def test_session_memberships_are_left_alone(project, custom_eval_config, trace_session):
    issue = _issue(
        project,
        custom_eval_config,
        cluster_id="E-SESSION-1",
        target=EvalTargetType.SESSION,
        error_count=1,
    )
    ev = EvalLogger.objects.create(
        trace_session=trace_session,
        custom_eval_config=custom_eval_config,
        target_type=EvalTargetType.SESSION,
        output_float=0.2,
        eval_explanation="the caller was never helped",
        eval_task_id="et-session",
    )
    member = ErrorClusterTraces.objects.create(
        cluster=issue, trace_session=trace_session, eval_logger=ev
    )

    soft_delete_eval_results(EvalLogger.objects.filter(id=ev.id))

    assert ErrorClusterTraces.objects.filter(pk=member.pk).exists()
    issue.refresh_from_db()
    assert (issue.deleted, issue.error_count) == (False, 1)


def test_assign_derives_occurrences_from_live_rows(
    issue_with_three, project, project_version, no_clickhouse
):
    issue, evals = issue_with_three
    TraceErrorGroup.objects.filter(pk=issue.pk).update(error_count=50, total_events=50)
    cfg = evals[0].custom_eval_config
    fourth = _failing_eval(cfg, _span(project, project_version), "et-fourth")
    result = ClusterableEvalResult(
        eval_logger_id=str(fourth.id),
        project_id=str(project.id),
        eval_name=cfg.name,
        eval_config_id=str(cfg.id),
        explanation=fourth.eval_explanation,
        target_type=EvalTargetType.TRACE,
        trace_id=str(fourth.trace_id),
        session_id=None,
        score=0.2,
    )

    assign_to_cluster(issue.cluster_id, str(project.id), result, [0.1, 0.2])

    issue.refresh_from_db()
    assert (issue.error_count, issue.total_events) == (4, 4)


def _raw_eval_deletes(source: str) -> list[int]:
    """Lines of ``EvalLogger.<manager>....update(deleted=True)`` calls."""
    hits = []
    for node in ast.walk(ast.parse(source)):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "update"
            and any(
                kw.arg == "deleted"
                and isinstance(kw.value, ast.Constant)
                and kw.value.value is True
                for kw in node.keywords
            )
        ):
            continue
        receiver = node.func.value
        while isinstance(receiver, (ast.Call, ast.Attribute)):
            receiver = (
                receiver.func if isinstance(receiver, ast.Call) else receiver.value
            )
        if isinstance(receiver, ast.Name) and receiver.id == "EvalLogger":
            hits.append(node.lineno)
    return hits


def test_every_eval_delete_goes_through_the_service():
    """A raw ``update(deleted=True)`` on EvalLogger leaves Feed ghosts again.

    It reads the call chain, so it misses an aliased import, ``apps.get_model``
    and reverse-manager updates; those still need review.
    """
    root = pathlib.Path(__file__).resolve().parents[2]
    service = root / "tracer" / "services" / "eval_tasks" / "entries.py"
    offenders = [
        f"{path.relative_to(root).as_posix()}:{line}"
        for path in root.rglob("*.py")
        if "tests" not in path.parts
        and "migrations" not in path.parts
        and path != service
        for line in _raw_eval_deletes(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_backfill_retires_old_ghosts_and_repairs_counts(
    issue_with_three, project, project_version, custom_eval_config, user
):
    issue, evals = issue_with_three
    # Deleted the old way: the eval flag flipped, the membership left live.
    EvalLogger.objects.filter(id=evals[0].id).update(deleted=True)
    owned = _issue(
        project,
        custom_eval_config,
        cluster_id="E-OWNED-1",
        external_issue_id="LIN-2",
        error_count=1,
    )
    ghost = _issue(project, custom_eval_config, cluster_id="E-GHOST-1", error_count=1)
    for target in (owned, ghost):
        ev = _failing_eval(
            custom_eval_config, _span(project, project_version), f"et-{target.pk}"
        )
        ErrorClusterTraces.objects.create(
            cluster=target, trace=ev.trace, eval_logger=ev
        )
        EvalLogger.objects.filter(id=ev.id).update(deleted=True)
    drifted = _issue(
        project, custom_eval_config, cluster_id="E-DRIFT-1", error_count=40
    )
    ev = _failing_eval(custom_eval_config, _span(project, project_version), "et-drift")
    ErrorClusterTraces.objects.create(cluster=drifted, trace=ev.trace, eval_logger=ev)
    migration = importlib.import_module(
        "tracer.migrations.0118_retire_deleted_eval_memberships"
    )

    migration.retire_deleted_eval_memberships(django_apps, None)

    issue.refresh_from_db()
    assert (issue.error_count, issue.unique_traces, issue.deleted) == (2, 2, False)
    assert TraceErrorGroup.all_objects.get(pk=ghost.pk).deleted is True
    owned.refresh_from_db()
    assert (owned.deleted, owned.error_count) == (False, 0)
    drifted.refresh_from_db()
    assert (drifted.error_count, drifted.total_events) == (1, 1)

    repaired = (
        "pk",
        "error_count",
        "total_events",
        "unique_traces",
        "first_seen",
        "last_seen",
        "combined_impact",
        "deleted",
    )
    snapshot = list(TraceErrorGroup.all_objects.order_by("pk").values_list(*repaired))
    migration.retire_deleted_eval_memberships(django_apps, None)
    assert snapshot == list(
        TraceErrorGroup.all_objects.order_by("pk").values_list(*repaired)
    )
