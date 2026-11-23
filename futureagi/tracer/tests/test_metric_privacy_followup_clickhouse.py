# ruff: noqa: F811 -- shared native fixtures
"""Conservative legacy cleanup and isolated sessionless subject erasure."""

from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.utils import timezone

from tracer.models.derived_metric import (
    DerivedMetricRun,
    MetricErasureRequest,
    MetricProjectRefresh,
)
from tracer.models.project import Project
from tracer.models.sql_query import SQLExecution
from tracer.services.derived_metrics import erasure, retention
from tracer.services.derived_metrics.changes import (
    ChangeBatch,
    Dependency,
    ReconciliationCheckpoint,
    SourceKey,
    reconcile_page,
)
from tracer.services.derived_metrics.publication import committed_rows_select
from tracer.services.derived_metrics.queries import Scope
from tracer.services.derived_metrics.storage import SOURCE_DEPENDENCIES
from tracer.sql_workbench import metric_retention, tasks
from tracer.sql_workbench import service as sql_service
from tracer.sql_workbench.tests.test_permissions import (  # noqa: F401
    create_job,
    tenants,
)
from tracer.tests.test_derived_metric_changes_clickhouse import Analytics
from tracer.tests.test_derived_metric_clickhouse import seed_span
from tracer.tests.test_derived_metric_publication_clickhouse import (  # noqa: F401
    current_fields,
    live_writer,
    metric_ch,
    publication_store,
)
from tracer.tests.test_derived_metric_refresh_clickhouse import span_definition_for
from tracer.tests.test_metric_privacy_closeout_clickhouse import sql_fixture

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("status", ["queued", "done"])
@pytest.mark.parametrize("boundary", ["read", "sweep", "worker"])
def test_legacy_run_without_window_revokes_only_its_project_metric_results(
    publication_store, tenants, monkeypatch, settings, status, boundary
):
    actor, project, _, run, job, _ = sql_fixture(
        publication_store, tenants, monkeypatch, settings
    )
    tasks.execute_sql(job.pk)
    job.refresh_from_db()
    assert job.state == "succeeded" and job.result["rows"] == [["private-value", 42.0]]
    # Build the persisted pre-window-metadata state, not a new deletion through
    # today's signal. All saved payloads originate from the real native query.
    saved = job.result
    run_id = run.pk
    DerivedMetricRun.objects.filter(pk=run_id).delete()
    request = MetricErasureRequest.objects.get(deletion_key=f"run:{run_id}")
    if status == "done":
        assert erasure.process_erasure(request.pk, store=publication_store)
    completed = timezone.now()
    MetricErasureRequest.objects.filter(pk=request.pk).update(
        store_payload={},
        status=status,
        completed_at=completed if status == "done" else None,
    )
    request.refresh_from_db()
    assert not DerivedMetricRun.objects.filter(pk=run_id).exists()
    SQLExecution.objects.filter(pk=job.pk).update(
        state="succeeded",
        result=saved,
        output_bytes=100,
        cache_key="a" * 64,
        expires_at=completed + timedelta(hours=1),
        error="",
    )
    # No window survives, so ALL old metric results in this project are unsafe,
    # including another window. Neither ordinary SQL nor another project is.
    other_window = create_job(
        actor, project, state="succeeded", sql=job.sql, result=saved
    )
    SQLExecution.objects.filter(pk=other_window.pk).update(created_at=job.created_at)
    other_window.refresh_from_db()
    ordinary = create_job(actor, project, state="succeeded", result=saved)
    sibling = Project(
        id=uuid4(),
        name="unrelated-project",
        organization_id=actor.organization_id,
        workspace_id=actor.workspace_id,
        model_type=project.model_type,
        trace_type="observe",
    )
    Project.no_workspace_objects.bulk_create([sibling])
    other_actor, foreign, _ = tenants[1]
    unaffected = [
        ordinary,
        create_job(actor, sibling, state="succeeded", sql=job.sql, result=saved),
        create_job(other_actor, foreign, state="succeeded", sql=job.sql, result=saved),
    ]
    if boundary == "sweep":
        metric_retention.expire_results()
    elif boundary == "worker":
        metric_retention.invalidate_snapshot(request)
    if boundary != "read":
        for stale in (job, other_window):
            stale.refresh_from_db()
            assert stale.result == {} and stale.output_bytes == 0
    assert sql_service.snapshot(job)["result"] == {}
    assert sql_service.snapshot(other_window)["result"] == {}
    with pytest.raises(sql_service.ResultExpired):
        sql_service.export_result(actor, project.pk, job.pk, "csv")
    assert sql_service.reusable_result(actor, project, "a" * 64, timezone.now()) is None
    for kept in unaffected:
        assert sql_service.snapshot(kept)["result"] == saved
    # A completed legacy fence must not permanently disable fresh queries.
    if status == "queued":
        assert erasure.process_erasure(request.pk, store=publication_store)
    fresh = create_job(actor, project, sql=job.sql, start=job.start, end=job.end)
    tasks.execute_sql(fresh.pk)
    fresh.refresh_from_db()
    assert fresh.state == "succeeded" and fresh.result["rows"] == []


@pytest.mark.parametrize("blank", ["", " \t "])
def test_blank_recovery_owner_is_not_an_erasure_alias(publication_store, blank):
    erased = {blank, "erased-session"}
    handoff = {
        "membership_preimage": [
            {"canonical_user_id": "U", "canonical_session_id": blank},
            {"canonical_user_id": "V", "canonical_session_id": blank},
        ]
    }
    retained, units = retention._erasure_handoff(
        publication_store,
        handoff,
        SimpleNamespace(scope_kind="users", subject_ids=["U"]),
        erased,
    )
    assert units == {"erased-session"}
    assert retained == {"membership_preimage": [handoff["membership_preimage"][1]]}


@pytest.mark.parametrize("erase_present_user", [True, False])
def test_sessionless_user_erasure_preserves_other_dependency_and_repair(
    live_writer, workspace, observe_project, erase_present_user
):
    writer, now = live_writer, timezone.now()
    assert writer.retention_days() == 0
    definition = span_definition_for(writer, workspace, observe_project)
    scope = Scope(writer.org_id, writer.workspace_id, observe_project.pk)
    at = now - timedelta(hours=1)
    users = {"U": str(uuid4()), "V": str(uuid4())}
    deps = []
    for span, user in users.items():
        seed_span(
            writer.store.client, scope, span, "", user, at, resolved=4, trace_id="trace"
        )
        deps.append(Dependency("trace", span, "", "", user, user, at))
    kwargs = {"start": at - timedelta(hours=1), "end": now, "as_of": now}
    head = writer.refresh(
        ChangeBatch(
            tuple(SourceKey("trace", s) for s in users),
            (),
            (),
            tuple(deps),
            (),
            {"watermark_ns": 1},
        ),
        [definition],
        scope,
        **kwargs,
    )

    def dependencies(manifest):
        sql, params = committed_rows_select(
            SOURCE_DEPENDENCIES,
            manifest,
            org_id=scope.organization,
            project_id=scope.project,
        )
        return writer.store.execute(
            f"SELECT span_id, canonical_session_id, canonical_user_id FROM ({sql}) ORDER BY span_id",
            params,
        )

    assert dependencies(head) == [(s, "", users[s]) for s in users]
    assert len(current_fields(writer, head)) == 2
    user = users["U"] if erase_present_user else str(uuid4())
    writer.store.execute(
        "ALTER TABLE spans DELETE WHERE end_user_id = %(user)s SETTINGS mutations_sync=2",
        {"user": user},
    )
    request = erasure.erase_metric_data(
        organization_id=scope.organization,
        project_id=scope.project,
        scope_kind="users",
        subject_ids=[user],
    )
    assert erasure.process_erasure(request.pk, store=writer.store, writer=writer)
    head = MetricProjectRefresh.for_project(scope.project).head_manifest
    expected = ["V"] if erase_present_user else ["U", "V"]
    assert dependencies(head) == [(s, "", users[s]) for s in expected]
    assert len(current_fields(writer, head)) == len(expected)
    writer.store.execute(
        "ALTER TABLE spans DELETE WHERE id = 'V' SETTINGS mutations_sync=2"
    )
    page = reconcile_page(
        Analytics(writer.store.client),
        org_id=str(scope.organization),
        project_id=str(scope.project),
        committed_attempts=[str(a) for _, a in head.allowed_attempts()],
        now=now,
        checkpoint=ReconciliationCheckpoint(now, phase="dependencies"),
    )
    assert SourceKey("trace", "V") in page.changes.source_keys
    repaired = writer.refresh(page.changes, [definition], scope, **kwargs)
    assert len([row for row in current_fields(writer, repaired) if not row[2]]) == (
        0 if erase_present_user else 1
    )
