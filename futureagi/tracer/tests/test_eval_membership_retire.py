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


@pytest.mark.parametrize(
    "delete_path", ["pending-proof", "stale-policy", "removed-evaluator"]
)
@pytest.mark.parametrize("retirement_fails", [False, True])
def test_sampling_retires_requeued_memberships_atomically(
    issue_with_three, eval_task, monkeypatch, settings, delete_path, retirement_fails
):
    """A completed linked eval can be requeued before sampling excludes it.

    The existing retirement contract above also applies to PENDING rows. CH
    streams are controlled here; all eval/membership/issue writes use real PG.
    """
    from django.db import connection, transaction

    from tracer.models.eval_sampling import EvalSamplingCohort
    from tracer.models.observation_span import EvalEntryStatus
    from tracer.selectors.eval_tasks.row_resolver import ResolvedRowSet
    from tracer.services.eval_tasks import entries
    from tracer.services.eval_tasks.reconciler import _requeue_and_drop
    from tracer.services.eval_tasks.sampling import pending, service

    assert connection.vendor == "postgresql"
    settings.EVAL_SAMPLING_CLOSED_SETTLE_SECONDS = 0
    now = timezone.now()
    eval_task.row_type = "traces"
    eval_task.sampling_policy = {
        "version": 1,
        "sampling_unit": "session",
        "mode": "closed",
        "window": {
            "start": (now - timedelta(days=1)).isoformat(),
            "end": (now - timedelta(seconds=1)).isoformat(),
        },
        "default": {"target_n": 1},
    }
    eval_task.save()
    policy, version = service.task_policy(eval_task, now=now)
    EvalSamplingCohort.objects.create(
        task=eval_task,
        policy_version=version,
        policy=policy,
        frozen_at=now,
        complete=True,
    )
    issue, evals = issue_with_three
    target, completed, unrelated = evals
    EvalLogger.objects.filter(pk=unrelated.pk).update(
        eval_task_id=str(uuid.uuid4()), status=EvalEntryStatus.PENDING
    )
    EvalLogger.objects.filter(pk=target.pk).update(
        config_hash="previous-evaluator-revision",
        output_metadata={
            "_task_selection": {
                "sampling": {
                    "policy_version": "previous-policy"
                    if delete_path == "stale-policy"
                    else version
                }
            }
        },
    )
    with transaction.atomic():
        assert _requeue_and_drop(
            eval_task,
            resolved=ResolvedRowSet(
                (str(target.trace_id),), (str(target.trace_id),), True
            ),
        ) == (1, 0)
    target.refresh_from_db()
    assert target.status == EvalEntryStatus.PENDING
    assert ErrorClusterTraces.objects.filter(eval_logger=target).exists()
    if delete_path == "removed-evaluator":
        eval_task.evals.clear()
    monkeypatch.setattr(service, "sampling_clock", lambda: now)
    # No discovery page: isolate the three sampling-owned removal paths, not
    # the reconciler's existing retirement path for discovered candidates.
    monkeypatch.setattr(service, "stream_query", lambda *_: iter(()))
    monkeypatch.setattr(pending, "read_current_rows", lambda *_: {})
    untouched = list(
        EvalLogger.objects.filter(pk__in=[completed.pk, unrelated.pk])
        .order_by("pk")
        .values("id", "status", "deleted", "output_metadata", "eval_task_id")
    )
    if retirement_fails:
        retire = entries.retire_eval_memberships

        def fail_after_retirement(results):
            retire(results)
            raise RuntimeError("retirement rollback control")

        monkeypatch.setattr(entries, "retire_eval_memberships", fail_after_retirement)
        with pytest.raises(RuntimeError, match="retirement rollback control"):
            service.reconcile_policy(eval_task)
        assert EvalLogger.objects.filter(pk=target.pk).exists()
        assert ErrorClusterTraces.objects.filter(eval_logger=target).exists()
        issue.refresh_from_db()
        assert (issue.error_count, issue.total_events, issue.unique_traces) == (3, 3, 3)
        monkeypatch.setattr(entries, "retire_eval_memberships", retire)

    result = service.reconcile_policy(eval_task)
    assert result.dropped == 1
    assert EvalLogger.all_objects.get(pk=target.pk).deleted
    assert not ErrorClusterTraces.objects.filter(eval_logger=target).exists()
    assert ErrorClusterTraces.all_objects.get(eval_logger=target).deleted
    assert set(
        ErrorClusterTraces.objects.filter(cluster=issue).values_list(
            "eval_logger_id", flat=True
        )
    ) == {completed.pk, unrelated.pk}
    issue.refresh_from_db()
    assert (
        issue.error_count,
        issue.total_events,
        issue.unique_traces,
        issue.deleted,
    ) == (2, 2, 2, False)
    assert (
        list(
            EvalLogger.objects.filter(pk__in=[completed.pk, unrelated.pk])
            .order_by("pk")
            .values("id", "status", "deleted", "output_metadata", "eval_task_id")
        )
        == untouched
    )
    first_updated = issue.updated_at
    assert service.reconcile_policy(eval_task).dropped == 0
    issue.refresh_from_db()
    assert issue.updated_at == first_updated


@pytest.mark.django_db(transaction=True)
def test_sampling_stale_retirement_uses_bounded_fenced_pages(
    issue_with_three, eval_task, monkeypatch, settings
):
    from django.db import connection

    from tracer.models.observation_span import EvalEntryStatus
    from tracer.services.eval_tasks import recalculate
    from tracer.services.eval_tasks.sampling import pending, service

    issue, evals = issue_with_three
    now = timezone.now()
    settings.EVAL_SAMPLING_CLOSED_SETTLE_SECONDS = 0
    eval_task.sampling_policy = {
        "version": 1,
        "sampling_unit": "session",
        "mode": "closed",
        "window": {
            "start": (now - timedelta(days=1)).isoformat(),
            "end": (now - timedelta(seconds=1)).isoformat(),
        },
        "default": {"target_n": 1},
    }
    eval_task.save()
    EvalLogger.objects.filter(pk__in=[ev.pk for ev in evals]).update(
        status=EvalEntryStatus.PENDING
    )
    monkeypatch.setattr(service, "sampling_clock", lambda: now)
    monkeypatch.setattr(service, "stream_query", lambda *_: iter(()))
    monkeypatch.setattr(pending, "read_current_rows", lambda *_: {})
    # A baseline without the page helper must fail on its bulk-delete behavior,
    # not on a missing test-control constant.
    monkeypatch.setattr(service, "BATCH_SIZE", 2, raising=False)
    original = recalculate.drop_pending_entries
    pages = []

    def retire(results):
        assert connection.in_atomic_block
        count = results.count()
        assert 0 < count <= 2
        pages.append(count)
        return original(results)

    monkeypatch.setattr(recalculate, "drop_pending_entries", retire)
    assert not connection.in_atomic_block
    assert service.reconcile_policy(eval_task).dropped == 3
    assert not connection.in_atomic_block
    assert pages == [2, 1]
    assert not ErrorClusterTraces.objects.filter(cluster=issue).exists()
    assert TraceErrorGroup.all_objects.get(pk=issue.pk).deleted


def test_sampling_retirement_advances_past_a_busy_recalculation(
    issue_with_three, monkeypatch
):
    from django.db import transaction

    from tracer.models.observation_span import EvalEntryStatus
    from tracer.services.eval_tasks import recalculate
    from tracer.services.eval_tasks.sampling import service

    _, evals = issue_with_three
    results = EvalLogger.objects.filter(pk__in=[ev.pk for ev in evals])
    results.update(status=EvalEntryStatus.PENDING)
    expected = sorted(ev.pk for ev in evals)
    seen = []
    drop = recalculate.drop_pending_entries

    def skip_first(page):
        (pk,) = page.values_list("pk", flat=True)
        assert pk not in seen  # fail immediately rather than spin on a skipped row
        seen.append(pk)
        return 0 if pk == expected[0] else drop(page)

    monkeypatch.setattr(service, "BATCH_SIZE", 1)
    monkeypatch.setattr(recalculate, "drop_pending_entries", skip_first)
    assert service._retire_pending_pages(results, write=transaction.atomic) == 2
    assert seen == expected
    assert list(results.values_list("pk", flat=True)) == expected[:1]


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

    Direct or straight-line aliased ``EvalLogger`` queryset ``update``/``delete``
    calls and, in a module importing ``EvalLogger``, ``<x>.deleted = True``.
    """
    tree = ast.parse(source)
    imports_eval_logger = any(
        isinstance(n, ast.ImportFrom) and any(a.name == "EvalLogger" for a in n.names)
        for n in ast.walk(tree)
    )
    hits = []
    # Track straight-line queryset aliases in their lexical scope too. In
    # particular, ``stale = EvalLogger.objects.filter(...); stale.update(...)``
    # must not evade the same gate as an inline queryset.
    aliases_at_call = {}

    class QuerysetAliases(ast.NodeVisitor):
        def __init__(self):
            self.aliases = {"EvalLogger"}

        def visit_FunctionDef(self, node):
            outer = self.aliases
            self.aliases = {"EvalLogger"}
            for statement in node.body:
                self.visit(statement)
            self.aliases = outer

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Assign(self, node):
            self.generic_visit(node)
            is_eval = _chain_root(node.value) in self.aliases
            for target in node.targets:
                if isinstance(target, ast.Name):
                    if is_eval:
                        self.aliases.add(target.id)
                    else:
                        self.aliases.discard(target.id)

        def visit_Call(self, node):
            if isinstance(node.func, ast.Attribute):
                aliases_at_call[id(node)] = _chain_root(node.func.value) in self.aliases
            self.generic_visit(node)

    QuerysetAliases().visit(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            sets_deleted = node.func.attr == "update" and any(
                kw.arg == "deleted"
                and isinstance(kw.value, ast.Constant)
                and kw.value.value is True
                for kw in node.keywords
            )
            if (sets_deleted or node.func.attr == "delete") and (
                _chain_root(node.func.value) == "EvalLogger"
                or aliases_at_call.get(id(node), False)
            ):
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

    It catches the shapes ``_raw_eval_deletes`` lists, including straight-line
    local queryset aliases. It is not a dataflow analyzer: aliased imports,
    ``apps.get_model``, reverse managers, control-flow-dependent aliases and
    instance assignments without an ``EvalLogger`` import still need review.
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
        ("stale = EvalLogger.objects.filter(id=1)\nstale.update(deleted=True)", True),
        ("qs = EvalLogger.objects.all()\npage = qs.filter(id=1)\npage.delete()", True),
        ("qs = EvalLogger.objects.all()\nqs = Trace.objects.all()\nqs.delete()", False),
        (
            "def first():\n qs = EvalLogger.objects.all()\ndef second():\n qs.delete()",
            False,
        ),
        ("qs = EvalLogger.objects.all()\nsoft_delete_eval_results(qs)", False),
        ("def action(rows=EvalLogger.objects.all().delete()):\n pass", True),
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
