# ruff: noqa: F811 -- shared native fixtures
"""Snapshot SQL revocation and subject erasure across durable recovery heads."""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.utils import timezone

from tracer.models.derived_metric import (
    MetricAttemptLedgerChunk,
    MetricErasureRequest,
    MetricProjectRefresh,
)
from tracer.services.derived_metrics import erasure, retention, snapshots
from tracer.services.derived_metrics.changes import ChangeBatch
from tracer.services.derived_metrics.lease import ProjectLease
from tracer.services.derived_metrics.queries import Scope
from tracer.services.derived_metrics.refresh import RefreshWriter
from tracer.sql_workbench import service as sql_service
from tracer.sql_workbench import tasks
from tracer.sql_workbench.tests.test_permissions import (  # noqa: F401
    create_job,
    tenants,
)
from tracer.tests.test_derived_metric_clickhouse import seed_span
from tracer.tests.test_derived_metric_publication_clickhouse import (  # noqa: F401
    current_fields,
    live_writer,
    metric_ch,
    publication_store,
)
from tracer.tests.test_derived_metric_refresh_clickhouse import (
    change_for,
    definition_for,
)
from tracer.tests.test_metric_retention_review_clickhouse import frozen_run

pytestmark = pytest.mark.django_db(transaction=True)


def sql_fixture(store, tenants, monkeypatch, settings):
    (actor, project, user), _ = tenants
    run = frozen_run(project.workspace, project, timezone.now(), kind="snapshot")
    settings.CLICKHOUSE_V2 = {
        **settings.CLICKHOUSE_V2,
        "CH25_DATABASE": store.client.connection.database,
    }
    monkeypatch.setenv("SQL_WORKBENCH_DERIVED_METRICS_ENABLED", "true")
    params = {
        "org": str(actor.organization_id),
        "workspace": str(actor.workspace_id),
        "project": str(project.pk),
        "metric": str(run.definition.metric_id),
        "run": str(run.pk),
        "result": str(run.result_id),
        "start": run.window_start,
        "end": run.window_end,
    }
    store.execute(
        "INSERT INTO derived_metric_results (org_id, workspace_id, project_id, metric_id, "
        "run_id, result_id, window_start, window_end, rollup, group_key, status, value) "
        "VALUES (%(org)s, %(workspace)s, %(project)s, %(metric)s, %(run)s, %(result)s, "
        "%(start)s, %(end)s, 'overall', 'private-value', 'ok', 42.)",
        params,
    )
    store.execute(
        "INSERT INTO derived_metric_commits (org_id, workspace_id, project_id, metric_id, run_id, result_id, version) "
        "VALUES (%(org)s, %(workspace)s, %(project)s, %(metric)s, %(run)s, %(result)s, 1)",
        params,
    )
    job = create_job(
        actor,
        project,
        sql="SELECT group_key, value FROM derived_metrics",
        start=run.window_start - timedelta(hours=1),
        end=run.window_end + timedelta(hours=1),
    )

    def native(sql, *args, **kwargs):
        rows, columns = store.execute(sql, with_column_types=True)
        return {
            "columns": [{"name": name, "type": kind} for name, kind in columns],
            "rows": [list(row) for row in rows],
            "result_bytes": len(str(rows).encode()),
        }

    monkeypatch.setattr(tasks, "run", native)
    return actor, project, user, run, job, native


@pytest.mark.parametrize("deletion", ["soft", "legacy", "hard"])
def test_deleted_snapshot_sql_poll_download_and_reuse_are_fenced(
    publication_store, tenants, monkeypatch, settings, deletion
):
    actor, project, user, run, job, _ = sql_fixture(
        publication_store, tenants, monkeypatch, settings
    )
    tasks.execute_sql(job.pk)
    job.refresh_from_db()
    assert job.state == "succeeded" and job.result["rows"] == [["private-value", 42.0]]
    job.cache_key = "a" * 64
    job.save(update_fields=["cache_key"])
    other_window = create_job(
        actor, project, state="succeeded", sql=job.sql, result=job.result
    )
    ordinary = create_job(
        actor,
        project,
        state="succeeded",
        sql="SELECT span_id FROM spans",
        result=job.result,
    )
    if deletion == "legacy":
        from tracer.models.derived_metric import DerivedMetricRun
        from tracer.services.derived_metrics.deletion_protocol import _intent

        DerivedMetricRun.mark_snapshot_deleted(run.pk, user_id=user.pk)
        _intent(
            organization_id=actor.organization_id,
            project_id=project.pk,
            scope="run",
            run_key=run.pk,
            snapshot_key=run.snapshot_id,
            deletion_key=f"run:{run.pk}",
        )
    elif deletion == "hard":
        type(run).objects.filter(pk=run.pk).delete()
    else:
        snapshots.delete_snapshot(run.definition.metric, run.pk, user)
    assert sql_service.snapshot(job)["result"] == {}
    with pytest.raises(sql_service.ResultExpired):
        sql_service.export_result(actor, project.pk, job.pk, "csv")
    assert sql_service.reusable_result(actor, project, "a" * 64, timezone.now()) is None
    # The pre-window intent cannot prove which catalog windows were consumed;
    # the conservative legacy fence covers every saved metric result here.
    if deletion == "legacy":
        assert sql_service.snapshot(other_window)["result"] == {}
    else:
        assert sql_service.snapshot(other_window)["result"]["rows"] == [
            ["private-value", 42.0]
        ]
    assert sql_service.snapshot(ordinary)["result"]["rows"] == [["private-value", 42.0]]
    # New scans cannot consume the namespace while physical cleanup is pending.
    blocked = create_job(actor, project, sql=job.sql, start=job.start, end=job.end)
    tasks.execute_sql(blocked.pk)
    blocked.refresh_from_db()
    assert blocked.state == "failed" and blocked.result == {}
    request = MetricErasureRequest.objects.get(deletion_key=f"run:{run.pk}")
    assert erasure.process_erasure(request.pk, store=publication_store)
    request.refresh_from_db()
    assert (
        request.store_payload["sql_source_window"]["start"]
        == run.window_start.isoformat()
    )
    assert sql_service.snapshot(job)["result"] == {}
    # After the real physical cleanup, a newly admitted query is usable again.
    fresh = create_job(actor, project, sql=job.sql, start=job.start, end=job.end)
    tasks.execute_sql(fresh.pk)
    fresh.refresh_from_db()
    assert fresh.state == "succeeded" and fresh.result["rows"] == []


def test_snapshot_deletion_rollback_keeps_saved_sql_result(
    publication_store, tenants, monkeypatch, settings
):
    from django.db import transaction

    _, _, user, run, job, _ = sql_fixture(
        publication_store, tenants, monkeypatch, settings
    )
    tasks.execute_sql(job.pk)
    with pytest.raises(RuntimeError, match="rollback"):
        with transaction.atomic():
            snapshots.delete_snapshot(run.definition.metric, run.pk, user)
            raise RuntimeError("rollback")
    run.refresh_from_db()
    assert run.deleted_at is None
    assert not MetricErasureRequest.objects.filter(
        deletion_key=f"run:{run.pk}"
    ).exists()
    assert sql_service.snapshot(job)["result"]["rows"] == [["private-value", 42.0]]


@pytest.mark.parametrize("boundary", ["native_return", "publication_cas"])
def test_deleted_snapshot_late_sql_completion_cannot_publish(
    publication_store, tenants, monkeypatch, settings, boundary
):
    from tracer.sql_workbench import metric_retention

    actor, project, user, run, job, native = sql_fixture(
        publication_store, tenants, monkeypatch, settings
    )

    def delete():
        snapshots.delete_snapshot(run.definition.metric, run.pk, user)
        request = MetricErasureRequest.objects.get(deletion_key=f"run:{run.pk}")
        assert erasure.process_erasure(request.pk, store=publication_store)

    if boundary == "native_return":

        def late(sql, *args, **kwargs):
            result = native(sql, *args, **kwargs)
            assert result["rows"] == [["private-value", 42.0]]
            delete()
            return result

        monkeypatch.setattr(tasks, "run", late)
    else:
        allowed = metric_retention.result_allowed

        def late(job):
            result = allowed(job)
            assert result
            delete()
            return result

        monkeypatch.setattr(metric_retention, "result_allowed", late)
    tasks.execute_sql(job.pk)
    job.refresh_from_db()
    assert job.state != "succeeded" and job.result == {} and job.output_bytes == 0


def test_recovery_erasure_uses_native_span_identity_encoding(publication_store):
    from types import SimpleNamespace

    pair = ("trace/quoted", 'span/"unicode-雪')
    encoded = publication_store.execute(
        "SELECT toJSONString(tuple(%(trace)s, %(span)s))",
        {"trace": pair[0], "span": pair[1]},
    )[0][0]
    handoff = {
        "dirty_before": [{"unit_id": encoded}, {"unit_id": "unrelated"}],
        "membership_preimage": [
            {"trace_id": pair[0], "span_id": pair[1], "canonical_user_id": "erased"}
        ],
    }
    retained, units = retention._erasure_handoff(
        publication_store,
        handoff,
        SimpleNamespace(scope_kind="users", subject_ids=["erased"]),
        set(),
    )
    assert encoded in units
    assert retained == {
        "dirty_before": [{"unit_id": "unrelated"}],
        "membership_preimage": [],
    }


@pytest.mark.parametrize("interrupt_erasure", [False, True])
@pytest.mark.parametrize("kind", ["users", "sessions"])
def test_pending_recovery_survives_restart_erasure_and_fresh_work(
    live_writer, workspace, observe_project, monkeypatch, kind, interrupt_erasure
):
    writer, now = live_writer, timezone.now()
    definition = definition_for(writer, workspace, observe_project)
    scope = Scope(writer.org_id, writer.workspace_id, writer.lease.project_id)
    erased, kept, erased_user, kept_user = (uuid4() for _ in range(4))
    at = now - timedelta(hours=12)
    kwargs = {"start": at - timedelta(days=1), "end": now, "as_of": now}
    for session, user in ((erased, erased_user), (kept, kept_user)):
        seed_span(
            writer.store.client, scope, str(session), session, user, at, resolved=4
        )
        writer.refresh(change_for(session), [definition], scope, **kwargs)
    seed_span(
        writer.store.client,
        scope,
        "exclusive",
        kept,
        kept_user,
        at + timedelta(hours=1),
        resolved=8,
        trace_id="trace",
    )
    failed = writer.begin(
        ledger={
            "dirty_before": [{"unit_id": str(session)} for session in (erased, kept)],
            "membership_preimage": [
                {
                    "trace_id": "trace",
                    "span_id": "exclusive",
                    "canonical_session_id": str(kept),
                    "canonical_user_id": str(kept_user),
                },
                {
                    "trace_id": "trace",
                    "span_id": str(erased),
                    "canonical_session_id": str(erased),
                    "canonical_user_id": str(erased_user),
                },
            ],
        }
    )
    writer.seal(failed)
    future = now + timedelta(minutes=6)

    def restart(at):
        grant = MetricProjectRefresh.take_lease(
            scope.project, holder="privacy-restart", ttl=timedelta(minutes=5), now=at
        )
        return RefreshWriter(
            ProjectLease(grant, clock=lambda: at),
            writer.store,
            org_id=writer.org_id,
            workspace_id=writer.workspace_id,
            retention_days=lambda: 1,
        )

    replacement = restart(future)
    original = retention._purge_terminal_payloads

    def crash(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("crash after durable recovery publication")

    monkeypatch.setattr(retention, "_purge_terminal_payloads", crash)
    with pytest.raises(RuntimeError, match="durable recovery"):
        retention.expire_project(
            replacement, [definition], retention_days=lambda: 1, now=future
        )
    monkeypatch.setattr(retention, "_purge_terminal_payloads", original)
    pending = MetricProjectRefresh.for_project(scope.project).head_manifest
    assert pending.published_by.source_ceilings["recovery_pending"]
    assert pending.published_by.ledger_chunks.filter(
        part="membership_preimage"
    ).exists()
    replacement = restart(future + timedelta(minutes=6))
    # The derived-store erasure contract requires upstream removal first.
    writer.store.execute(
        "ALTER TABLE spans DELETE WHERE trace_session_id = %(session)s SETTINGS mutations_sync=2",
        {"session": str(erased)},
    )
    request = erasure.erase_metric_data(
        organization_id=scope.organization,
        project_id=scope.project,
        scope_kind=kind,
        subject_ids=[str(erased_user if kind == "users" else erased)],
    )
    if interrupt_erasure:

        def interrupted(*args, **kwargs):
            raise RuntimeError("crash before private ledger scrub")

        monkeypatch.setattr(retention, "_purge_terminal_payloads", interrupted)
        assert not erasure.process_erasure(
            request.pk, store=writer.store, writer=replacement
        )
        request.refresh_from_db()
        assert request.receipts == {} and request.completed_at is None
        monkeypatch.setattr(retention, "_purge_terminal_payloads", original)
        replacement = restart(future + timedelta(minutes=12))
    assert erasure.process_erasure(request.pk, store=writer.store, writer=replacement)
    request.refresh_from_db()
    assert request.status == "done" and request.completed_at is not None
    assert set(request.receipts) == set(MetricErasureRequest.Store.values)
    assert all(receipt == {"remaining": 0} for receipt in request.receipts.values())
    head = MetricProjectRefresh.for_project(scope.project).head_manifest
    assert head.published_by.source_ceilings["recovery_pending"]
    handoff = list(
        head.published_by.ledger_chunks.exclude(part="cleanup_intent").values_list(
            "payload", flat=True
        )
    )
    assert (
        str(kept) in str(handoff)
        and str(erased) not in str(handoff)
        and str(erased_user) not in str(handoff)
    )
    for table in ("metric_field_values", "metric_field_values_checkpoint"):
        assert writer.store.execute(
            f"SELECT count() FROM {table} WHERE user_id = %(user)s OR unit_id = %(unit)s",
            {"user": str(erased_user), "unit": str(erased)},
        ) == [(0,)]
    assert writer.store.execute(
        "SELECT count() FROM metric_source_dependencies WHERE raw_user_id = %(user)s "
        "OR canonical_user_id = %(user)s OR raw_session_id = %(unit)s "
        "OR canonical_session_id = %(unit)s",
        {"user": str(erased_user), "unit": str(erased)},
    ) == [(0,)]
    assert writer.store.execute(
        "SELECT count() FROM metric_unit_state WHERE unit_id = %(unit)s",
        {"unit": str(erased)},
    ) == [(0,)]
    assert {row[0] for row in current_fields(replacement, head)} == {str(kept)}
    watermark = MetricProjectRefresh.for_project(scope.project).watermark_ns
    empty = ChangeBatch((), (), (), (), (), {"watermark_ns": watermark + 1})
    final = replacement.refresh(empty, [definition], scope, **kwargs)
    assert [(row[0], row[1]) for row in current_fields(replacement, final)] == [
        (str(kept), 8.0)
    ]
    assert not final.published_by.source_ceilings.get("recovery_pending")
    assert MetricProjectRefresh.for_project(scope.project).watermark_ns == watermark + 1
    assert not MetricAttemptLedgerChunk.objects.filter(
        attempt__project_id=scope.project
    ).exists()
