"""A03 real PostgreSQL adapter tests; no CH/source qualification is claimed."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.db import close_old_connections, connection, connections, transaction
from django.db.models import Case, Count, QuerySet, Subquery, Value, When
from django.test import override_settings
from django.test.utils import CaptureQueriesContext

from accounts.models.user import User
from model_hub.models.develop_annotations import AnnotationsLabels
from model_hub.models.score import Score
from tracer.models.metric_pg_revision import (
    MetricPgIdentityRevision,
    MetricPgResponseRevision,
)
from tracer.models.project import Project
from tracer.models.project_version import ProjectVersion
from tracer.models.trace_session import TraceSessionOverlay
from tracer.services.derived_metrics import base_list_current_pg_state as pg
from tracer.services.derived_metrics.deletion_protocol import serialized_project
from tracer.services.derived_metrics.pg_response_hooks import PgResponseQuerySet

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def enrolled(settings, project):
    settings.FI_SESSIONS_PG_REVISIONS_ENABLED = True
    # Project predates enrollment; initialization is explicit mutation evidence,
    # never a completed bootstrap / deployed producer completeness certificate.
    project.save()
    return project


def scope(project):
    return pg.PgProjectScope(project.pk)


def vector(*projects):
    return pg.read_pg_response_vector([scope(project) for project in projects])


def revision(project):
    return vector(project).projects[0].token.revision


def other_project(project):
    return Project.objects.create(
        organization=project.organization,
        workspace=project.workspace,
        name=f"synthetic-{uuid4()}",
        model_type=project.model_type,
        trace_type=project.trace_type,
    )


def label(project):
    return AnnotationsLabels.objects.create(
        project=project,
        organization=project.organization,
        workspace=project.workspace,
        name=f"label-{uuid4()}",
        type="text",
    )


def score(project, annotation_label, **extra):
    values = {
        "organization": project.organization,
        "workspace": project.workspace,
        "label": annotation_label,
        "source_type": "trace_session",
        "trace_session_id": uuid4(),  # deliberately off-page / no PG session row
        "value": "synthetic",
    }
    values.update(extra)
    return Score.objects.create(**values)


def _in_connection(fn):
    close_old_connections()
    try:
        return fn()
    finally:
        connections.close_all()


def test_default_off_is_native_and_does_not_require_revision_tables(project, user):
    with override_settings(FI_SESSIONS_PG_REVISIONS_ENABLED=False):
        with CaptureQueriesContext(connection) as captured:
            project.session_config = [{"name": "synthetic"}]
            project.save(update_fields=["session_config"])
            assert Project.objects.filter(pk=project.pk).update(name="native-name") == 1
            user.name = "native-name"
            user.save(update_fields=["name"])
            overlay = TraceSessionOverlay.objects.create(
                project_id=project.pk, trace_session_id=uuid4(), display_name=""
            )
            result = TraceSessionOverlay.objects.filter(pk=overlay.pk).delete()
            assert result[0] == 1
        assert all("metric_pg_" not in query["sql"] for query in captured)
        assert all("pg_advisory" not in query["sql"] for query in captured)
        assert not MetricPgResponseRevision.objects.exists()
        assert not MetricPgIdentityRevision.objects.exists()
        project.refresh_from_db()
        assert project.session_config == [{"name": "synthetic"}]
        assert project.name == "native-name"


def test_source_and_revision_commit_visible_together(enrolled):
    project = enrolled
    before = vector(project)
    entered, release = Event(), Event()

    def write():
        with transaction.atomic():
            Project.objects.filter(pk=project.pk).update(
                session_config=[{"name": "new"}]
            )
            entered.set()
            assert release.wait(10)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_in_connection, write)
        try:
            assert entered.wait(10)
            assert vector(project) == before
            assert (
                Project.objects.get(pk=project.pk).session_config
                == project.session_config
            )
        finally:
            release.set()
        future.result(timeout=15)
    assert vector(project) != before
    assert Project.objects.get(pk=project.pk).session_config == [{"name": "new"}]


def test_project_soft_delete_boundary_is_atomic_and_keeps_callback(enrolled):
    from tracer.models.trace_session import TraceSession
    from tracer.services.project_deletion import soft_delete_projects

    session = TraceSession.objects.create(project=enrolled)
    before = vector(enrolled)
    with patch(
        "tracer.services.project_deletion.publish_project_invalidation"
    ) as notify:
        with patch.object(
            pg, "_increment", side_effect=RuntimeError("synthetic failure")
        ):
            with pytest.raises(RuntimeError):
                soft_delete_projects(Project.objects.filter(pk=enrolled.pk), "observe")
        notify.assert_not_called()
        assert not TraceSession.all_objects.get(pk=session.pk).deleted
        assert not Project.all_objects.get(pk=enrolled.pk).deleted
        assert vector(enrolled) == before
        ids = soft_delete_projects(Project.objects.filter(pk=enrolled.pk), "observe")
        assert ids == [str(enrolled.pk)]
        notify.assert_called_once_with(ids)
    assert TraceSession.all_objects.get(pk=session.pk).deleted
    assert Project.all_objects.get(pk=enrolled.pk).deleted
    assert vector(enrolled) != before


def test_identity_hook_failure_rolls_back_native_user(enrolled, user):
    before = vector(enrolled)
    with patch.object(
        pg, "_increment", side_effect=RuntimeError("identity fence failed")
    ):
        with pytest.raises(RuntimeError):
            user.name = "must-not-commit"
            user.save(update_fields=["name"])
    assert vector(enrolled) == before
    assert User.objects.get(pk=user.pk).name != "must-not-commit"


def test_concurrent_label_move_resolves_score_owner_under_gate(enrolled):
    first, second = enrolled, other_project(enrolled)
    annotation_label = label(first)
    item = score(first, annotation_label)
    entered, attempted, release = Event(), Event(), Event()
    before = vector(first, second)

    def move():
        with transaction.atomic():
            AnnotationsLabels.no_workspace_objects.filter(
                pk=annotation_label.pk
            ).update(project=second)
            entered.set()
            assert release.wait(10)

    def edit():
        assert entered.wait(10)
        attempted.set()
        Score.no_workspace_objects.filter(pk=item.pk).update(value="after-move")

    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(_in_connection, move)
        b = pool.submit(_in_connection, edit)
        try:
            assert attempted.wait(10)
            assert vector(first, second) == before
        finally:
            release.set()
        a.result(timeout=15)
        b.result(timeout=15)
    old = {entry.scope: entry.token.revision for entry in before.projects}
    new = {
        entry.scope: entry.token.revision for entry in vector(first, second).projects
    }
    assert new[scope(first)] == old[scope(first)] + 1
    assert new[scope(second)] == old[scope(second)] + 2


def test_source_and_revision_rollback_together(enrolled):
    before = vector(enrolled)
    with pytest.raises(ValueError):
        with transaction.atomic():
            Project.objects.filter(pk=enrolled.pk).update(
                session_config=["rolled-back"]
            )
            assert vector(enrolled) != before
            raise ValueError("synthetic rollback")
    assert vector(enrolled) == before
    assert Project.objects.get(pk=enrolled.pk).session_config == enrolled.session_config


def test_revision_failure_rolls_back_native_save(enrolled):
    before = vector(enrolled)
    with patch.object(pg, "_increment", side_effect=RuntimeError("synthetic failure")):
        with pytest.raises(RuntimeError):
            Project.objects.filter(pk=enrolled.pk).update(name="must-roll-back")
    assert vector(enrolled) == before
    assert Project.objects.get(pk=enrolled.pk).name == enrolled.name


def test_two_concurrent_writers_cannot_reuse_revision(enrolled):
    project = enrolled
    before = revision(project)
    entered, attempted, release, second_done = Event(), Event(), Event(), Event()

    def first():
        with pg.pg_response_mutation([scope(project)], False):
            entered.set()
            assert release.wait(10)

    def second():
        assert entered.wait(10)
        attempted.set()
        with pg.pg_response_mutation([scope(project)], False):
            pass
        second_done.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(_in_connection, first)
        b = pool.submit(_in_connection, second)
        try:
            assert attempted.wait(10)
            assert not second_done.wait(0.2)
            assert revision(project) == before
        finally:
            release.set()
        a.result(timeout=15)
        b.result(timeout=15)
    assert revision(project) == before + 2


def test_first_label_archive_restore_and_metadata_full_save(enrolled):
    before = vector(enrolled)
    annotation_label = label(enrolled)
    created = vector(enrolled)
    assert created != before
    annotation_label.delete()  # native BaseModel soft delete routes through save
    archived = vector(enrolled)
    assert archived != created
    annotation_label.deleted = False
    annotation_label.deleted_at = None
    annotation_label.metadata = {"synthetic": True}
    annotation_label.save()
    assert vector(enrolled) != archived
    assert AnnotationsLabels.objects.filter(pk=annotation_label.pk).exists()


def test_unproved_bulk_conflict_update_is_rejected_before_write(enrolled):
    before = vector(enrolled)
    with pytest.raises(pg.PgRevisionUnavailable):
        TraceSessionOverlay.objects.bulk_create(
            [TraceSessionOverlay(project_id=enrolled.pk, trace_session_id=uuid4())],
            update_conflicts=True,
            update_fields=["project_id"],
            unique_fields=["trace_session_id"],
        )
    assert vector(enrolled) == before
    assert not TraceSessionOverlay.objects.exists()


def test_score_move_uses_old_and_new_label_projects_not_score_project(enrolled):
    first, second = enrolled, other_project(enrolled)
    a, b = label(first), label(second)
    item = score(first, a, tracer_project_id=uuid4())
    before = vector(first, second)
    item.label = b
    item.save(update_fields=["label"])
    after = vector(first, second)
    assert all(
        new.token.revision > old.token.revision
        for old, new in zip(before.projects, after.projects, strict=True)
    )


def test_label_move_covers_attached_off_page_scores(enrolled):
    first, second = enrolled, other_project(enrolled)
    annotation_label = label(first)
    score(first, annotation_label)
    before = vector(first, second)
    annotation_label.project = second
    annotation_label.save(update_fields=["project"])
    after = vector(first, second)
    assert all(
        new.token.revision > old.token.revision
        for old, new in zip(before.projects, after.projects, strict=True)
    )


def test_stale_score_object_cannot_hide_current_old_owner(enrolled):
    projects = [enrolled, other_project(enrolled), other_project(enrolled)]
    labels = [label(project) for project in projects]
    item = score(enrolled, labels[0])
    stale = Score.objects.get(pk=item.pk)
    Score.objects.filter(pk=item.pk).update(label=labels[1])
    before = vector(*projects)
    stale.label = labels[2]
    stale.save(update_fields=["label"])
    after = vector(*projects)
    old = {entry.scope: entry.token.revision for entry in before.projects}
    new = {entry.scope: entry.token.revision for entry in after.projects}
    assert new[scope(projects[1])] > old[scope(projects[1])]
    assert new[scope(projects[2])] > old[scope(projects[2])]


def test_overlay_move_clear_bookmark_and_hard_delete(enrolled):
    other = other_project(enrolled)
    overlay = TraceSessionOverlay.objects.create(
        project_id=enrolled.pk, trace_session_id=uuid4(), display_name="name"
    )
    before = vector(enrolled, other)
    overlay.project_id = other.pk
    overlay.display_name = ""
    overlay.bookmarked = True
    overlay.save()
    after = vector(enrolled, other)
    assert all(
        new.token.revision > old.token.revision
        for old, new in zip(before.projects, after.projects, strict=True)
    )
    assert TraceSessionOverlay.objects.get(pk=overlay.pk).display_name == ""
    old = revision(other)
    assert TraceSessionOverlay.objects.filter(pk=overlay.pk).delete()[0] == 1
    assert revision(other) > old


def test_identity_only_aba_and_stale_full_save_affect_all_projects(enrolled, user):
    other = other_project(enrolled)
    stale = User.objects.get(pk=user.pk)
    before = vector(enrolled, other)
    user.name = "changed-name"
    user.save(update_fields=["name"])
    middle = vector(enrolled, other)
    stale.set_password("synthetic-password")
    stale.save()  # unrestricted save restores its earlier name
    after = vector(enrolled, other)
    assert before.projects == middle.projects == after.projects
    assert before.identity.revision < middle.identity.revision < after.identity.revision
    assert User.objects.get(pk=user.pk).name == stale.name


def test_identity_create_email_bulk_and_rollback(enrolled):
    before = vector(enrolled)
    user = User.objects.create_user(
        email=f"{uuid4()}@example.invalid", name="", password=None
    )
    middle = vector(enrolled)
    assert middle.identity.revision > before.identity.revision
    User.objects.filter(pk=user.pk).update(email=f"{uuid4()}@example.invalid")
    user.refresh_from_db()
    current = vector(enrolled)
    assert current.identity.revision > middle.identity.revision
    with pytest.raises(RuntimeError):
        with transaction.atomic():
            User.objects.filter(pk=user.pk).update(name="rollback")
            raise RuntimeError()
    assert vector(enrolled) == current
    assert User.objects.get(pk=user.pk).name == ""


@pytest.mark.parametrize("manager", ["objects", "all_objects", "no_workspace_objects"])
def test_all_named_score_managers_bulk_and_soft_delete(enrolled, manager):
    annotation_label = label(enrolled)
    objects = getattr(Score, manager)
    before = revision(enrolled)
    items = [
        Score(
            organization=enrolled.organization,
            workspace=enrolled.workspace,
            label=annotation_label,
            source_type="trace_session",
            trace_session_id=uuid4(),
            value="initial",
        )
        for _ in range(2)
    ]
    objects.bulk_create(items, ignore_conflicts=True)
    assert revision(enrolled) > before
    before = revision(enrolled)
    for item in items:
        item.value = "updated"
    assert objects.bulk_update(items, ["value"], batch_size=1) == 2
    assert revision(enrolled) > before
    before = revision(enrolled)
    assert objects.filter(pk__in=[item.pk for item in items]).update(deleted=True) == 2
    assert revision(enrolled) > before
    assert Score.all_objects.filter(pk=items[0].pk).get().deleted


def test_bulk_move_case_expression_and_failure_atomicity(enrolled):
    other = other_project(enrolled)
    a, b = label(enrolled), label(other)
    items = [score(enrolled, a), score(enrolled, a)]
    before = vector(enrolled, other)
    Score.objects.filter(pk__in=[item.pk for item in items]).update(
        label_id=Case(When(pk=items[0].pk, then=Value(b.pk)), default=Value(a.pk))
    )
    after = vector(enrolled, other)
    assert all(
        new.token.revision > old.token.revision
        for old, new in zip(before.projects, after.projects, strict=True)
    )
    for item in items:
        item.value = "must-not-commit"
    with patch.object(pg, "_increment", side_effect=RuntimeError("synthetic failure")):
        with pytest.raises(RuntimeError):
            Score.objects.bulk_update(items, ["value"], batch_size=1)
    assert vector(enrolled, other) == after
    assert not Score.objects.filter(value="must-not-commit").exists()


def test_update_or_create_takes_gate_before_native_source_lock(enrolled):
    overlay = TraceSessionOverlay.objects.create(
        project_id=enrolled.pk, trace_session_id=uuid4()
    )
    with CaptureQueriesContext(connection) as captured:
        row, created = TraceSessionOverlay.objects.update_or_create(
            trace_session_id=overlay.trace_session_id,
            defaults={"display_name": "changed"},
        )
    assert not created and row.display_name == "changed"
    sql = [item["sql"] for item in captured]
    gate = next(i for i, query in enumerate(sql) if "pg_advisory_xact_lock" in query)
    native_lock = next(
        i
        for i, query in enumerate(sql)
        if "trace_session_overlay" in query and "FOR UPDATE" in query
    )
    assert gate < native_lock


def test_raw_sql_and_base_manager_bypass_never_qualify(enrolled, user):
    before = vector(enrolled)
    with connection.cursor() as cursor:
        cursor.execute(
            'UPDATE "accounts_user" SET name=%s WHERE id=%s', ["raw bypass", user.pk]
        )
    assert (
        vector(enrolled) == before
    )  # negative completeness proof, NOT passing coverage
    assert not before.production_qualified
    Project._base_manager.filter(pk=enrolled.pk).update(
        session_config=["base-manager-bypass"]
    )
    assert vector(enrolled) == before
    assert not vector(enrolled).production_qualified


def test_cascade_and_historical_paths_are_not_claimed_enrolled(enrolled):
    # A06 is separately owned. No second Collector patch / false revision claim.
    from django.db.models.deletion import Collector

    assert getattr(Collector.delete, "_metric_lifecycle", False)
    assert not getattr(Collector.delete, "_pg_response", False)
    assert not isinstance(Project._base_manager.all(), PgResponseQuerySet)
    assert not vector(enrolled).production_qualified


def test_typed_scopes_primary_missing_and_snapshot_rejections(enrolled):
    with pytest.raises(TypeError):
        pg.PgProjectScope(str(enrolled.pk))
    with pytest.raises(TypeError):
        with pg.pg_response_mutation([enrolled.pk], False):
            pass
    with pytest.raises(pg.PgRevisionUnavailable):
        pg.read_pg_response_vector([scope(enrolled)], using="replica")
    with pytest.raises(pg.PgRevisionUnavailable):
        pg.read_pg_response_vector([pg.PgProjectScope(uuid4())])
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        with pytest.raises(pg.PgRevisionUnavailable):
            vector(enrolled)


def test_overflow_fails_without_wrapping_or_native_commit(enrolled):
    MetricPgResponseRevision.objects.filter(project_id=enrolled.pk).update(
        revision=pg.MAX_REVISION
    )
    before = vector(enrolled)
    with pytest.raises(pg.PgRevisionUnavailable):
        Project.objects.filter(pk=enrolled.pk).update(name="must-rollback")
    assert vector(enrolled) == before
    assert Project.objects.get(pk=enrolled.pk).name == enrolled.name


def test_lifecycle_first_then_gate_and_sorted_multiscope(enrolled):
    other = other_project(enrolled)
    before = vector(enrolled, other)
    with transaction.atomic():
        # Existing lock protocol is reused, not replaced. All lifecycle locks
        # precede writer admission. Reverse input order must not change fences.
        from contextlib import ExitStack

        with ExitStack() as stack:
            for project in sorted([enrolled, other], key=lambda row: str(row.pk)):
                stack.enter_context(serialized_project(project.pk))
            with pg.pg_response_mutation(
                [scope(other), scope(enrolled), scope(other)], False
            ):
                pass
    after = vector(enrolled, other)
    assert all(
        new.token.revision == old.token.revision + 1
        for old, new in zip(before.projects, after.projects, strict=True)
    )


def test_persistent_incarnation_survives_native_project_recreate(enrolled):
    before = vector(enrolled)
    # Simulate an explicitly fenced maintenance operation, not Collector
    # enrollment. Project has no owned metric data in this synthetic fixture.
    pk = enrolled.pk
    with serialized_project(pk):
        with pg.pg_response_mutation([scope(enrolled)], False):
            Project.with_simulation.filter(pk=pk).delete()
    enrolled._state.adding = True
    enrolled.pk = pk
    enrolled.save(force_insert=True)
    after = vector(enrolled)
    assert after.projects[0].token.incarnation == before.projects[0].token.incarnation
    assert after.projects[0].token.revision > before.projects[0].token.revision


@contextmanager
def _before_pg_revision_migration():
    from django.db.migrations.executor import MigrationExecutor

    executor = MigrationExecutor(connection)
    original_targets = executor.loader.graph.leaf_nodes()
    try:
        executor.migrate([("tracer", "0128_base_list_current_stores")])
        yield
    finally:
        # A historical target is not the integrated graph: restore every app's
        # original leaves, even if a rollback assertion fails. Reload applied state.
        MigrationExecutor(connection).migrate(original_targets)


def _assert_integrated_metric_schema():
    from django.db.migrations.executor import MigrationExecutor

    executor = MigrationExecutor(connection)
    assert executor.migration_plan(executor.loader.graph.leaf_nodes()) == []
    assert {
        "metric_pg_response_revision",
        "metric_pg_identity_revision",
        "tracer_metriccompactionrange",
        "tracer_metricattemptbatch",
        "tracer_metricsourcescope",
        "tracer_metricsourceintent",
        "tracer_metricsourcepage",
        "tracer_metricsourcedelivery",
    } <= set(connection.introspection.table_names())
    # User activity lives in CH, not a PG user-activity table. The PG receipt and
    # range ledgers must both admit its store through real, validated CHECKs.
    with connection.cursor() as cursor:
        for table, constraint in (
            ("tracer_metricattemptbatch", "metric_batch_table"),
            ("tracer_metriccompactionrange", "metric_range_table"),
        ):
            cursor.execute(
                "SELECT contype, convalidated, pg_get_constraintdef(oid) "
                "FROM pg_constraint WHERE conrelid = %s::regclass AND conname = %s",
                [table, constraint],
            )
            rows = cursor.fetchall()
            assert len(rows) == 1
            kind, validated, definition = rows[0]
            assert kind == "c" and validated
            assert "'list_current_user_activity'" in definition


def test_metrics_only_migration_reverses_and_reapplies():
    native_tables = [Project._meta.db_table, Score._meta.db_table, User._meta.db_table]
    with connection.cursor() as cursor:
        before = {
            table: connection.introspection.get_table_description(cursor, table)
            for table in native_tables
        }
    with _before_pg_revision_migration():
        assert (
            "metric_pg_response_revision" not in connection.introspection.table_names()
        )
        assert (
            "metric_pg_identity_revision" not in connection.introspection.table_names()
        )
    with connection.cursor() as cursor:
        after = {
            table: connection.introspection.get_table_description(cursor, table)
            for table in native_tables
        }
    assert after == before
    assert not MetricPgResponseRevision.objects.exists()
    assert not MetricPgIdentityRevision.objects.exists()
    _assert_integrated_metric_schema()


def test_metrics_migration_restores_integrated_schema_after_assertion_error():
    with pytest.raises(AssertionError, match="synthetic historical assertion"):
        with _before_pg_revision_migration():
            assert (
                "metric_pg_response_revision"
                not in connection.introspection.table_names()
            )
            raise AssertionError("synthetic historical assertion")
    _assert_integrated_metric_schema()


@pytest.mark.parametrize("operation", ["score_update", "overlay_delete"])
@pytest.mark.parametrize("transition", ["expand", "shrink"])
def test_mutable_predicate_cannot_escape_captured_ids(enrolled, operation, transition):
    first, second = enrolled, other_project(enrolled)
    versions = [
        ProjectVersion.objects.create(
            project=project,
            name="selected" if i == 0 else "excluded",
            version="v1",
        )
        for i, project in enumerate([first, second])
    ]
    if operation == "score_update":
        model = Score
        items = [score(project, label(project)) for project in [first, second]]
        scope_field = "label__project_id__in"
    else:
        model = TraceSessionOverlay
        items = [
            model.objects.create(project_id=project.pk, trace_session_id=uuid4())
            for project in [first, second]
        ]
        scope_field = "project_id__in"
    queryset = model.objects.filter(
        **{
            scope_field: Subquery(
                ProjectVersion.objects.filter(name="selected").values("project_id")
            )
        }
    )
    before = vector(first, second)
    native_method = "update" if operation == "score_update" else "delete"
    original = getattr(QuerySet, native_method)
    switched = False

    def switch_before_native_write(targets, *args, **kwargs):
        nonlocal switched
        if targets.model is model and not switched:
            switched = True
            # An ordinary non-response producer commits on a SECOND connection.
            # It neither bypasses a response hook nor needs the PG writer gate.
            changed = versions[1] if transition == "expand" else versions[0]
            with ThreadPoolExecutor(max_workers=1) as pool:
                affected = pool.submit(
                    _in_connection,
                    lambda: ProjectVersion.objects.filter(pk=changed.pk).update(
                        name="selected" if transition == "expand" else "excluded"
                    ),
                ).result(timeout=10)
                assert affected == 1
        return original(targets, *args, **kwargs)

    with patch.object(QuerySet, native_method, switch_before_native_write):
        if operation == "score_update":
            affected = queryset.update(value="after-capture")
        else:
            affected = queryset.delete()[0]
    assert switched
    assert affected == (1 if transition == "expand" else 0)
    after = vector(first, second)
    old = {entry.scope: entry.token.revision for entry in before.projects}
    new = {entry.scope: entry.token.revision for entry in after.projects}
    assert new[scope(second)] == old[scope(second)]
    assert new[scope(first)] == old[scope(first)] + affected
    if operation == "score_update":
        assert model.objects.get(pk=items[1].pk).value == "synthetic"
        assert model.objects.get(pk=items[0].pk).value == (
            "after-capture" if transition == "expand" else "synthetic"
        )
    else:
        assert model.objects.filter(pk=items[1].pk).exists()
        assert model.objects.filter(pk=items[0].pk).exists() == (transition == "shrink")


@pytest.mark.parametrize("pk_field", ["id", "pk"])
def test_primary_key_plus_label_move_refused_before_mutation(enrolled, pk_field):
    other = other_project(enrolled)
    a, b = label(enrolled), label(other)
    item = score(enrolled, a)
    new_pk = uuid4()
    before = vector(enrolled, other)
    with pytest.raises(pg.PgRevisionUnavailable, match="primary-key update"):
        Score.objects.filter(pk=item.pk).update(**{pk_field: new_pk, "label": b})
    assert vector(enrolled, other) == before
    assert Score.objects.get(pk=item.pk).label_id == a.pk
    assert not Score.objects.filter(pk=new_pk).exists()
    # Ordinary stable-PK ownership changes still invalidate BOTH scopes.
    assert Score.objects.filter(pk=item.pk).update(label=b) == 1
    after = vector(enrolled, other)
    assert all(
        new.token.revision == old.token.revision + 1
        for old, new in zip(before.projects, after.projects, strict=True)
    )


def test_primary_key_update_remains_native_when_hooks_off(project):
    with override_settings(FI_SESSIONS_PG_REVISIONS_ENABLED=False):
        overlay = TraceSessionOverlay.objects.create(
            project_id=project.pk, trace_session_id=uuid4()
        )
        new_pk = overlay.pk + 1000  # Overlay uses the native BigAutoField.
        assert TraceSessionOverlay.objects.filter(pk=overlay.pk).update(id=new_pk) == 1
        assert not TraceSessionOverlay.objects.filter(pk=overlay.pk).exists()
        assert TraceSessionOverlay.objects.filter(pk=new_pk).exists()
        assert not MetricPgResponseRevision.objects.exists()


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("shape", ["distinct", "aggregate", "having"])
def test_distinct_and_aggregate_update_native_parity(enrolled, enabled, shape):
    item = score(enrolled, label(enrolled))
    queryset = Score.objects.filter(pk=item.pk)
    if shape == "distinct":
        queryset = queryset.distinct()
    else:
        queryset = queryset.annotate(n=Count("id"))
        if shape == "having":
            queryset = queryset.filter(n__gte=1)
    before = vector(enrolled)
    with override_settings(FI_SESSIONS_PG_REVISIONS_ENABLED=enabled):
        assert queryset.update(value="annotated-update") == 1
    assert Score.objects.get(pk=item.pk).value == "annotated-update"
    assert (vector(enrolled) != before) is enabled


def test_distinct_on_update_is_explicitly_unenrolled(enrolled):
    items = [score(enrolled, label(enrolled)) for _ in range(2)]
    before = vector(enrolled)
    with pytest.raises(pg.PgRevisionUnavailable, match="DISTINCT ON"):
        Score.objects.order_by("value").distinct("value").update(value="not-written")
    assert vector(enrolled) == before
    assert (
        Score.objects.filter(
            pk__in=[item.pk for item in items], value="synthetic"
        ).count()
        == 2
    )


def test_native_queryset_rejections_survive_hooks(enrolled):
    overlay = TraceSessionOverlay.objects.create(
        project_id=enrolled.pk, trace_session_id=uuid4()
    )
    queryset = TraceSessionOverlay.objects.filter(pk=overlay.pk)
    before = vector(enrolled)
    with pytest.raises(TypeError):
        queryset[:1].update(display_name="not-written")
    with pytest.raises(TypeError):
        queryset[:1].delete()
    with pytest.raises(TypeError):
        queryset.values("id").delete()
    assert vector(enrolled) == before
    assert queryset.exists()


@pytest.mark.parametrize("operation", ["update", "delete"])
def test_queryset_mutation_clears_original_cache(enrolled, operation):
    overlay = TraceSessionOverlay.objects.create(
        project_id=enrolled.pk, trace_session_id=uuid4(), display_name="before"
    )
    queryset = TraceSessionOverlay.objects.filter(pk=overlay.pk)
    assert list(queryset)[0].display_name == "before"
    if operation == "update":
        assert queryset.update(display_name="after") == 1
        assert list(queryset)[0].display_name == "after"
    else:
        assert queryset.delete()[0] == 1
        assert list(queryset) == []


def test_same_transaction_explicit_api_rolls_back_hook_and_native(enrolled):
    before = vector(enrolled)
    with override_settings(FI_SESSIONS_PG_REVISIONS_ENABLED=False):
        with pytest.raises(ValueError):
            with pg.pg_response_mutation([scope(enrolled)], True):
                Project.objects.filter(pk=enrolled.pk).update(name="rollback")
                raise ValueError()
    assert vector(enrolled) == before
    assert Project.objects.get(pk=enrolled.pk).name == enrolled.name
