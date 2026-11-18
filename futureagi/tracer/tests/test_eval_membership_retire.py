"""Deleting eval results retires their Feed memberships and recounts the issue.

A membership whose eval was deleted kept the trace in its issue: the issue
listed the trace with no eval and still counted it. Every eval soft-delete now
goes through ``soft_delete_eval_results``, which retires those memberships in
the same transaction. Nothing is hard-deleted: an issue left empty is
soft-deleted unless a person owns it, and the same failure recurring revives it.
"""

import ast
import hashlib
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
from tracer.queries.eval_clustering import assign_to_cluster, create_cluster
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


def _chain_root(node) -> str | None:
    while isinstance(node, (ast.Call, ast.Attribute)):
        node = node.func if isinstance(node, ast.Call) else node.value
    return node.id if isinstance(node, ast.Name) else None


def _raw_eval_deletes(source: str) -> list[tuple[int, str | None]]:
    """(line, receiver) of deletes of eval results without the service.

    Three shapes: ``EvalLogger.<manager>....update(deleted=True)``,
    ``EvalLogger.<manager>....delete()`` (a soft delete on ``BaseModel``), and,
    in a module that imports ``EvalLogger``, any ``<x>.deleted = True``.
    """
    tree = ast.parse(source)
    imports_eval_logger = any(
        isinstance(n, ast.ImportFrom) and any(a.name == "EvalLogger" for a in n.names)
        for n in ast.walk(tree)
    )
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            sets_deleted = node.func.attr == "update" and any(
                kw.arg == "deleted"
                and isinstance(kw.value, ast.Constant)
                and kw.value.value is True
                for kw in node.keywords
            )
            if (sets_deleted or node.func.attr == "delete") and _chain_root(
                node.func.value
            ) == "EvalLogger":
                hits.append((node.lineno, "EvalLogger"))
        elif (
            imports_eval_logger
            and isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Constant)
            and node.value.value is True
            and any(
                isinstance(target, ast.Attribute) and target.attr == "deleted"
                for target in node.targets
            )
        ):
            hits.append((node.lineno, _chain_root(node.targets[0].value)))
    return hits


# ``<variable>.deleted = True`` sites in modules that import EvalLogger but
# delete another model. A new variable name here must be reviewed by hand.
_NOT_EVAL_RESULTS = {
    "model_hub/views/separate_evals.py": {"gt", "eval_template"},
    "model_hub/views/annotation_queues.py": {"comment"},
    "tracer/views/project_version.py": {"project_version"},
}


def test_every_eval_delete_goes_through_the_service():
    """A raw delete of an eval result leaves Feed ghosts again.

    It catches the three shapes ``_raw_eval_deletes`` lists. It cannot catch an
    aliased import, ``apps.get_model``, a reverse-manager update, a queryset held
    in a variable (``qs = EvalLogger...; qs.update(...)``) or a ``.deleted = True``
    on an instance in a module that does not import ``EvalLogger``; those need
    review.
    """
    root = pathlib.Path(__file__).resolve().parents[2]
    service = root / "tracer" / "services" / "eval_tasks" / "entries.py"
    offenders = [
        f"{path.relative_to(root).as_posix()}:{line}"
        for path in root.rglob("*.py")
        if "tests" not in path.parts
        and "migrations" not in path.parts
        and not any(part.startswith(".") for part in path.relative_to(root).parts)
        and path != service
        for line, receiver in _raw_eval_deletes(path.read_text(encoding="utf-8"))
        if receiver
        not in _NOT_EVAL_RESULTS.get(path.relative_to(root).as_posix(), set())
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
        "tracer.migrations.0119_retire_deleted_eval_memberships"
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


def _result_for(ev, project):
    return ClusterableEvalResult(
        eval_logger_id=str(ev.id),
        project_id=str(project.id),
        eval_name=ev.custom_eval_config.name,
        eval_config_id=str(ev.custom_eval_config_id),
        explanation=ev.eval_explanation,
        target_type=EvalTargetType.TRACE,
        trace_id=str(ev.trace_id),
        session_id=None,
        score=0.2,
    )


def test_assign_skips_an_eval_deleted_while_it_was_being_clustered(
    issue_with_three, project, project_version, no_clickhouse
):
    issue, evals = issue_with_three
    cfg = evals[0].custom_eval_config
    fourth = _failing_eval(cfg, _span(project, project_version), "et-late")
    result = _result_for(fourth, project)
    # Delete & rerun lands after the fetch and before the assign.
    soft_delete_eval_results(EvalLogger.objects.filter(id=fourth.id))

    assign_to_cluster(issue.cluster_id, str(project.id), result, [0.1, 0.2])

    assert not ErrorClusterTraces.all_objects.filter(eval_logger_id=fourth.id).exists()
    issue.refresh_from_db()
    assert issue.error_count == 3


def test_create_cluster_skips_an_eval_deleted_while_it_was_being_clustered(
    project, project_version, custom_eval_config, eval_task, no_clickhouse
):
    ev = _failing_eval(
        custom_eval_config, _span(project, project_version), str(eval_task.id)
    )
    result = _result_for(ev, project)
    soft_delete_eval_results(EvalLogger.objects.filter(id=ev.id))

    assert create_cluster(str(project.id), result, [0.1, 0.2]) is None

    assert not TraceErrorGroup.all_objects.filter(project=project).exists()


def _cluster_id_for(project, result):
    base = (
        f"{project.id}|eval|{result.target_type}|{result.eval_name}"
        f"|{result.explanation[:100]}"
    )
    digest = hashlib.md5(base.encode(), usedforsecurity=False).hexdigest()[:8]
    return f"E-{digest.upper()}"


def test_create_cluster_revives_the_retired_issue_instead_of_minting_a_twin(
    project, project_version, custom_eval_config, eval_task, no_clickhouse
):
    first = _failing_eval(
        custom_eval_config, _span(project, project_version), str(eval_task.id)
    )
    result = _result_for(first, project)
    retired = _issue(
        project,
        custom_eval_config,
        cluster_id=_cluster_id_for(project, result),
        error_count=1,
        total_events=1,
        unique_traces=1,
        combined_description=result.explanation,
    )
    ErrorClusterTraces.objects.create(
        cluster=retired, trace=first.trace, eval_logger=first
    )
    soft_delete_eval_results(EvalLogger.objects.filter(id=first.id))
    assert not TraceErrorGroup.objects.filter(pk=retired.pk).exists()
    again = _failing_eval(custom_eval_config, _span(project, project_version), "et-2")

    # The centroid lookup missed, so clustering creates instead of assigning.
    create_cluster(str(project.id), _result_for(again, project), [0.1, 0.2])

    issues = TraceErrorGroup.all_objects.filter(project=project)
    assert [i.pk for i in issues] == [retired.pk]
    retired.refresh_from_db()
    assert (retired.deleted, retired.error_count) == (False, 1)


def test_a_merged_issue_is_never_revived(
    issue_with_three, project, project_version, no_clickhouse
):
    issue, evals = issue_with_three
    winner = _issue(project, evals[0].custom_eval_config, cluster_id="E-WINNER-1")
    TraceErrorGroup.objects.filter(pk=issue.pk).update(
        deleted=True, deleted_at=timezone.now(), redirect_to=winner
    )
    fourth = _failing_eval(
        evals[0].custom_eval_config, _span(project, project_version), "et-merged"
    )

    with pytest.raises(TraceErrorGroup.DoesNotExist):
        assign_to_cluster(
            issue.cluster_id,
            str(project.id),
            _result_for(fourth, project),
            [0.1, 0.2],
        )

    assert TraceErrorGroup.all_objects.get(pk=issue.pk).deleted is True


def test_retire_and_revive_each_record_an_alert_event(
    issue_with_three, project, project_version, no_clickhouse
):
    issue, evals = issue_with_three
    cfg = evals[0].custom_eval_config
    with patch("tracer.services.feed_alerts.events.record_issue_event") as record:
        soft_delete_eval_results(
            EvalLogger.objects.filter(id__in=[e.id for e in evals])
        )
        retire = record.call_args.kwargs
        assert retire["source_key"].startswith(f"eval-retire:{issue.pk}:")
        assert retire["before"]["occurrences"] == 3
        assert retire["before"]["visible"] is True
        assert retire["cluster"].deleted is True

        again = _failing_eval(cfg, _span(project, project_version), "et-again")
        assign_to_cluster(
            issue.cluster_id, str(project.id), _result_for(again, project), [0.1, 0.2]
        )
        revive = record.call_args.kwargs
        assert revive["source_key"] == f"eval-assign:{again.id}:{issue.pk}"
        assert revive["before"]["visible"] is False
        assert revive["cluster"].deleted is False


@pytest.mark.parametrize(
    "source, expected",
    [
        ("EvalLogger.objects.filter(id=1).delete()", True),
        ("EvalLogger.objects.filter(id=1).update(deleted=True)", True),
        (
            "from tracer.models.observation_span import EvalLogger\n"
            "row.deleted = True\nrow.save()",
            True,
        ),
        ("row.deleted = True", False),
        ("EvalLogger.objects.filter(id=1).update(output_float=0)", False),
    ],
)
def test_the_gate_flags_the_delete_shapes_it_documents(source, expected):
    assert bool(_raw_eval_deletes(source)) is expected
