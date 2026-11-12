"""Source-retention cleanup uses real PG rows; only external edges are faked."""

import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

import pytest
from django.utils import timezone

from accounts.models.organization import Organization
from tracer.models.eval_task import EvalTask, EvalTaskStatus, RunType
from tracer.models.insights import InsightConfig, InsightRun, InsightUnit
from tracer.models.instant_eval import InstantEvalAnswer, InstantEvalRow, InstantEvalRun
from tracer.models.observation_span import EvalLogger
from tracer.models.project import Project
from tracer.services import analysis_retention as retention
from tracer.tests.test_insights_api import insight_enabled as insight_enabled

pytestmark = [pytest.mark.unit, pytest.mark.django_db]


@pytest.fixture
def cutoff():
    return timezone.now() - timedelta(days=30)


@pytest.fixture(autouse=True)
def no_external_evidence(monkeypatch):
    forgotten = []
    monkeypatch.setattr("tfc.ee_gates.forget_analysis_evidence", forgotten.append)
    monkeypatch.setattr(
        "tracer.services.project_deletion._pause_eval_task_workflows", lambda ids: None
    )
    # Only the remote remap metadata is replaced, not PG deletion decisions.
    monkeypatch.setattr(
        retention,
        "_session_aliases",
        lambda ids: {value: value for value in retention._uuids(ids)},
    )
    return forgotten


def report(
    project,
    stamp,
    *,
    config=None,
    status="completed",
    unit="sessions",
    legacy=False,
    taxonomy=None,
):
    config = config or InsightConfig.objects.create(
        project=project,
        organization=project.organization,
        workspace=project.workspace,
        name="Failures",
        question="What failed?",
        lens="failures",
        unit=unit,
    )
    run = InsightRun.objects.create(
        config=config,
        project=project,
        organization=project.organization,
        workspace=project.workspace,
        unit=unit,
        status=status,
        source_created_at=None if legacy else stamp,
        settings={"taxonomy": taxonomy} if taxonomy else {},
        result={"summary": {"quote": "private source text"}},
        progress={"recent": ["private source text"]},
    )
    row = InsightUnit.objects.create(
        run=run,
        unit_id=str(uuid.uuid4()),
        time=stamp,
        status="done",
        content="private source text",
        excerpt="private source",
        summary="private summary",
        lens_answer="The requested cancellation was ignored.",
    )
    return config, run, row


def instant(project, stamp, *, unit="traces", row_id=None):
    task = EvalTask.objects.create(
        project=project,
        name="Instant eval · question",
        filters={},
        sampling_rate=100,
        run_type=RunType.HISTORICAL,
        row_type=unit,
        status=EvalTaskStatus.RUNNING,
    )
    run = InstantEvalRun.objects.create(
        eval_task=task,
        project=project,
        organization=project.organization,
        unit=unit,
        row_type=unit,
        rows_requested=1,
        population=1,
        questions=1,
    )
    row = InstantEvalRow.objects.create(
        task=task,
        row_id=row_id or str(uuid.uuid4()),
        unit=unit,
        time=stamp,
        source_created_at=stamp,
        excerpt="private excerpt",
    )
    InstantEvalAnswer.objects.create(
        task=task, row=row, config_id=uuid.uuid4(), label="private result", time=stamp
    )
    return task, run, row


def expired(run):
    run.refresh_from_db()
    assert run.deleted is True
    assert run.status == "failed"
    assert run.result == run.settings == {}
    assert "recent" not in run.progress
    assert not InsightUnit.objects.filter(run=run).exists()


def test_retention_uses_source_time_and_preserves_other_orgs_and_newer_runs(
    observe_project, cutoff, django_capture_on_commit_callbacks, no_external_evidence
):
    _, old, _ = report(observe_project, cutoff - timedelta(seconds=1))
    _, fresh, _ = report(observe_project, cutoff)
    org = Organization.objects.create(name="Other retention org")
    project = Project.objects.create(
        name="Other project",
        organization=org,
        trace_type="observe",
        model_type=observe_project.model_type,
    )
    _, other, _ = report(project, cutoff - timedelta(days=10))
    task, run, _ = instant(observe_project, cutoff - timedelta(seconds=1))
    fresh_task, _, _ = instant(observe_project, cutoff)
    with django_capture_on_commit_callbacks(execute=True):
        result = retention.expire_analysis_data(
            organization_id=observe_project.organization_id, cutoff=cutoff
        )
    assert result == {"instant_eval_runs": 1, "insight_runs": 1}
    expired(old)
    assert (
        InsightRun._base_manager.filter(
            id__in=[fresh.id, other.id], deleted=False
        ).count()
        == 2
    )
    run.refresh_from_db()
    assert run.cancelled_at is not None and run.answers_version == 1
    assert EvalTask._base_manager.get(id=task.id).deleted
    assert not InstantEvalRow.objects.filter(task=task).exists()
    assert not InstantEvalAnswer.objects.filter(task=task).exists()
    assert InstantEvalRow.objects.filter(task=fresh_task).count() == 1
    assert no_external_evidence == [str(old.id)]
    assert retention.expire_analysis_data(
        organization_id=observe_project.organization_id, cutoff=cutoff
    ) == {"instant_eval_runs": 0, "insight_runs": 0}


def test_unlimited_retention_preserves_all_copies(observe_project, cutoff):
    _, run, _ = report(observe_project, cutoff - timedelta(days=100))
    task, _, _ = instant(observe_project, None)
    assert retention.expire_analysis_data(
        organization_id=observe_project.organization_id, cutoff=None
    ) == {"instant_eval_runs": 0, "insight_runs": 0}
    assert InsightRun.objects.filter(pk=run.pk).exists()
    assert InstantEvalRow.objects.filter(task=task).exists()


def test_legacy_user_provenance_is_oldest_not_displayed_latest(observe_project, cutoff):
    _, old, old_unit = report(
        observe_project, cutoff + timedelta(days=10), legacy=True, unit="users"
    )
    old_unit.metrics = {"source_created_at": (cutoff - timedelta(days=1)).isoformat()}
    old_unit.save(update_fields=["metrics"])
    _, fresh, fresh_unit = report(
        observe_project, cutoff + timedelta(days=10), legacy=True, unit="users"
    )
    fresh_unit.metrics = {"source_created_at": (cutoff + timedelta(days=1)).isoformat()}
    fresh_unit.save(update_fields=["metrics"])
    retention.expire_analysis_data(
        organization_id=observe_project.organization_id, cutoff=cutoff
    )
    expired(old)
    fresh.refresh_from_db()
    assert fresh.source_created_at == cutoff + timedelta(days=1)
    fresh_unit.refresh_from_db()
    assert fresh_unit.time == cutoff + timedelta(days=10)


def test_finite_policy_retires_unknown_legacy_payload_but_not_empty_pending_run(
    observe_project, cutoff
):
    config, unknown, _ = report(observe_project, None, legacy=True)
    pending = InsightRun.objects.create(
        config=config,
        project=observe_project,
        organization=observe_project.organization,
        workspace=observe_project.workspace,
        unit="sessions",
    )
    InsightUnit.objects.create(run=pending, unit_id="pending-without-source-yet")
    retention.expire_analysis_data(
        organization_id=observe_project.organization_id, cutoff=cutoff
    )
    expired(unknown)
    pending.refresh_from_db()
    assert not pending.deleted and pending.status == "pending"


def test_deleted_report_payload_is_purged_too(observe_project, cutoff):
    _, run, _ = report(observe_project, cutoff - timedelta(days=1))
    InsightRun._base_manager.filter(pk=run.pk).update(deleted=True)
    retention.expire_analysis_data(
        organization_id=observe_project.organization_id, cutoff=cutoff
    )
    expired(run)


def test_taxonomy_and_comparison_descendants_expire_but_rediscovery_survives(
    observe_project, cutoff
):
    config, origin, _ = report(observe_project, cutoff - timedelta(days=1))
    taxonomy = {
        "source_run_id": str(origin.id),
        "subs": [{"name": "private source phrase"}],
    }
    config.taxonomy = taxonomy
    config.save(update_fields=["taxonomy"])
    _, child, _ = report(
        observe_project, cutoff + timedelta(days=1), config=config, taxonomy=taxonomy
    )
    _, grandchild, _ = report(
        observe_project, cutoff + timedelta(days=2), config=config
    )
    grandchild.result = {
        "changes": {
            "previous_run_id": str(child.id),
            "summary": "private copied comparison",
        }
    }
    grandchild.save(update_fields=["result"])
    _, independent, _ = report(
        observe_project, cutoff + timedelta(days=3), config=config
    )
    retention.expire_analysis_data(
        organization_id=observe_project.organization_id, cutoff=cutoff
    )
    for run in (origin, child, grandchild):
        expired(run)
    assert InsightRun.objects.filter(id=independent.id).exists()
    config.refresh_from_db()
    assert config.taxonomy == {}


def test_units_readback_refuses_after_source_retention(
    auth_client, observe_project, insight_enabled, cutoff
):
    _, run, _ = report(observe_project, cutoff - timedelta(days=1))
    url = f"/tracer/insights/runs/{run.id}/units/?include_content=true&limit=1"
    before = auth_client.get(url)
    assert before.status_code == 200
    assert before.json()["result"]["units"][0]["content"] == "private source text"
    retention.expire_analysis_data(
        organization_id=observe_project.organization_id, cutoff=cutoff
    )
    after = auth_client.get(url)
    assert after.status_code == 404
    assert "private source" not in after.content.decode()


def test_prepare_cannot_restore_content_after_source_deletion(
    observe_project, monkeypatch, cutoff
):
    from ee.agenthub.insights import pipeline
    from ee.agenthub.insights.units import UnitText

    _, run, row = report(observe_project, cutoff + timedelta(days=1), status="pending")
    monkeypatch.setattr(pipeline, "check_setup", lambda: None)

    def load(*args, **kwargs):
        retention.retire_insight_runs([run.id], organization_id=run.organization_id)
        return {
            row.unit_id: UnitText(
                unit_id=row.unit_id, content="stale late content", time=timezone.now()
            )
        }

    monkeypatch.setattr(pipeline, "load_units", load)
    with pytest.raises(pipeline.InsightRunNotRunning):
        pipeline.prepare(str(run.id))
    expired(run)


def test_saved_read_and_final_taxonomy_cannot_resurrect_deleted_content(
    observe_project, cutoff
):
    from ee.agenthub.insights import pipeline
    from ee.agenthub.insights.llm import Spend

    config, run, row = report(
        observe_project, cutoff + timedelta(days=1), status="running"
    )
    retention.retire_insight_runs([run.id], organization_id=run.organization_id)
    row.summary = "late private summary"
    assert pipeline._save_read(run.id, [row], Spend()) is False
    run.status = "completed"
    pipeline._settle_written(
        run, {"tops": [{"name": "late private taxonomy"}], "subs": []}
    )
    config.refresh_from_db()
    assert config.taxonomy == {}
    expired(run)


def test_organise_cannot_save_after_cleanup_during_model_work(
    observe_project, monkeypatch, cutoff
):
    from ee.agenthub.insights import pipeline

    _, run, _ = report(observe_project, cutoff + timedelta(days=1), status="running")

    def organise(*args):
        retention.retire_insight_runs([run.id], organization_id=run.organization_id)
        return [{"id": "top", "name": "private"}], [], {}, {}, {}

    monkeypatch.setattr(pipeline, "_organise", organise)
    with pytest.raises(pipeline.InsightRunNotRunning):
        pipeline.organise(str(run.id))
    expired(run)


def test_instant_rows_and_answers_cannot_return_after_retirement(
    observe_project, cutoff
):
    from tracer.services.instant_eval_answers import record_answer, record_answers
    from tracer.services.instant_eval_rows import write_rows

    task, _, row = instant(observe_project, cutoff - timedelta(days=1), unit="sessions")
    entry = EvalLogger.objects.create(
        eval_task_id=str(task.id),
        target_type="session",
        trace_session_id=uuid.UUID(row.row_id),
        status="completed",
    )
    retention.expire_analysis_data(
        organization_id=observe_project.organization_id, cutoff=cutoff
    )
    facets = {
        row.row_id: {
            "time": timezone.now(),
            "end_user_id": None,
            "user_label": "",
            "model": "",
            "turns": 1,
            "duration_ms": 10,
            "error": False,
            "excerpt": "late private text",
            "trace_id": None,
        }
    }
    assert write_rows(task.id, "sessions", [(row.row_id, 0)], facets) == 0
    assert record_answer(entry.id) == 0
    assert record_answers(task.id, [entry.id]) == 0
    assert not InstantEvalRow.objects.filter(task=task).exists()
    assert not InstantEvalAnswer.objects.filter(task=task).exists()


def test_materialization_rejects_expired_source_not_fresh_run_timestamp(
    observe_project, monkeypatch, cutoff
):
    from tracer.services.instant_eval_rows import write_rows

    task, _, row = instant(observe_project, cutoff + timedelta(days=1), unit="sessions")
    monkeypatch.setattr(
        "tfc.ee_gates.trace_retention_cutoff", lambda *args, **kwargs: cutoff
    )
    facets = {
        "late": {
            "time": timezone.now(),
            "source_created_at": cutoff - timedelta(days=1),
        }
    }
    assert write_rows(task.id, "sessions", [("late", 0)], facets) == 0
    assert not InstantEvalRow.objects.filter(task=task).exists()
    assert EvalTask._base_manager.get(pk=task.id).deleted


def test_source_deletion_matches_current_provenance_and_preserves_unrelated_runs(
    observe_project, cutoff
):
    trace_id = str(uuid.uuid4())
    _, related, row = report(observe_project, cutoff + timedelta(days=1), unit="users")
    row.metrics = {"source_trace_ids": [trace_id], "source_session_ids": []}
    row.save(update_fields=["metrics"])
    _, unrelated, unit = report(
        observe_project, cutoff + timedelta(days=1), unit="users"
    )
    unit.metrics = {"source_trace_ids": [str(uuid.uuid4())], "source_session_ids": []}
    unit.save(update_fields=["metrics"])
    retention.retire_analysis_sources(
        project_id=observe_project.id, trace_ids=[trace_id]
    )
    expired(related)
    assert InsightRun.objects.filter(id=unrelated.id).exists()


def test_pg_trace_delete_retires_materialized_trace_and_session_copies(
    observe_project, cutoff
):
    from tracer.models.trace import Trace
    from tracer.models.trace_session import TraceSession
    from tracer.views.trace import _soft_delete_trace_tree

    session = TraceSession.objects.create(project=observe_project)
    trace = Trace.objects.create(project=observe_project, session=session)
    _, run, row = report(observe_project, cutoff + timedelta(days=1))
    row.unit_id = str(session.id)
    row.save(update_fields=["unit_id"])
    task, _, _ = instant(
        observe_project, cutoff + timedelta(days=1), row_id=str(trace.id)
    )
    _soft_delete_trace_tree([trace])
    expired(run)
    assert not InstantEvalRow.objects.filter(task=task).exists()


def test_ch_only_session_delete_retires_copies_without_a_pg_source(
    observe_project, monkeypatch, cutoff
):
    from tracer.views import trace_session as view

    session_id = str(uuid.uuid4())
    _, run, row = report(observe_project, cutoff + timedelta(days=1))
    row.unit_id = session_id
    row.save(update_fields=["unit_id"])
    fake = mock.Mock()
    monkeypatch.setattr(
        view,
        "_resolve_ch_session_fields",
        lambda *args: {
            "project_id": str(observe_project.id),
            "external_session_id": "customer-session",
            "first_seen": timezone.now(),
        },
    )
    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.curated_writer._get_client", lambda: fake
    )
    target = view.TraceSessionView()
    target.kwargs = {"pk": session_id}
    response = target._destroy_ch_only_session(SimpleNamespace())
    assert response.status_code == 204
    fake.insert.assert_called_once()
    expired(run)


@pytest.mark.django_db(transaction=True)
def test_cloud_scheduler_uses_trace_entitlement_not_report_creation_time(
    observe_project, monkeypatch, cutoff
):
    # The Cloud scheduler lives in the private ee/cloud tree, which fork CI
    # does not check out; this test runs wherever that tree exists.
    cloud_retention = pytest.importorskip(
        "ee.cloud.tasks.retention",
        reason="the Cloud retention scheduler ships in the private ee/cloud tree",
    )

    _, run, _ = report(observe_project, cutoff - timedelta(days=1))
    monkeypatch.setattr(
        cloud_retention.Entitlements,
        "get_retention_days",
        lambda org, kind: 30 if kind == "traces" else -1,
    )
    monkeypatch.setattr(cloud_retention, "_get_model", lambda path: None)
    from tfc.temporal.schedules.retention import soft_delete_expired_data_activity

    # Exercise the live compatibility alias and activity's numeric aggregation.
    result = soft_delete_expired_data_activity()
    assert result[str(observe_project.organization_id)]["analysis_insight_runs"] == 1
    assert sum(sum(counts.values()) for counts in result.values()) == 1
    expired(run)


def test_policy_boundary_preserves_self_hosted_and_fails_closed_on_cloud_error(
    monkeypatch,
):
    from tfc.capabilities import service
    from tfc.ee_gates import trace_retention_cutoff
    from tfc.licensing.types import DeploymentLocation

    monkeypatch.setattr(
        service, "get_deployment_location", lambda: DeploymentLocation.SELF_HOSTED
    )
    with mock.patch(
        "ee.usage.services.entitlements.Entitlements.get_retention_days",
        side_effect=RuntimeError("policy unavailable"),
    ) as get:
        assert trace_retention_cutoff("org") is None
        get.assert_not_called()
        monkeypatch.setattr(
            service, "get_deployment_location", lambda: DeploymentLocation.CLOUD
        )
        with pytest.raises(RuntimeError, match="Data retention could not be checked"):
            trace_retention_cutoff("org")


@pytest.mark.parametrize("unit", ["traces", "sessions", "users"])
@pytest.mark.parametrize("read_steps", [True, False])
def test_child_arrival_precedes_root_even_when_v2_reads_no_steps(
    observe_project, monkeypatch, cutoff, unit, read_steps
):
    from ee.agenthub.insights import units
    from ee.agenthub.insights.tests import seeded_support as seed

    reader = seed.FakeReader({"session": seed.SESSIONS["sess_refund_failed"][1]})
    for root in reader.roots:
        root.created_at = cutoff + timedelta(days=1)
    for step in reader.steps:
        step["created_at"] = cutoff - timedelta(days=1)
    monkeypatch.setattr("tracer.services.clickhouse.v2.get_reader", lambda: reader)
    monkeypatch.setattr(units, "_user_labels", lambda ids: {})
    root = reader.roots[0]
    ids = {
        "traces": [root.trace_id],
        "sessions": ["session"],
        "users": [root.end_user_id],
    }[unit]
    loaded = units.load_units(unit, str(observe_project.id), ids, read_steps=read_steps)
    assert loaded
    assert all(
        text.source_created_at == cutoff - timedelta(days=1) for text in loaded.values()
    )
    if not read_steps:
        assert reader.asked == []


def test_arrival_clock_does_not_expire_recently_imported_old_events(
    observe_project, cutoff
):
    _, run, row = report(observe_project, cutoff + timedelta(days=1))
    task, _, instant_row = instant(observe_project, cutoff + timedelta(days=1))
    InsightUnit.objects.filter(pk=row.pk).update(time=cutoff - timedelta(days=400))
    InstantEvalRow.objects.filter(pk=instant_row.pk).update(
        time=cutoff - timedelta(days=400)
    )
    assert retention.expire_analysis_data(
        organization_id=run.organization_id, cutoff=cutoff
    ) == {"instant_eval_runs": 0, "insight_runs": 0}
    assert InstantEvalRow.objects.filter(task=task).exists()
    assert InsightRun.objects.filter(pk=run.pk).exists()


def test_stale_progress_cannot_restore_recent_summaries(observe_project, cutoff):
    from ee.agenthub.insights import pipeline

    _, run, _ = report(observe_project, cutoff, status="running")
    retention.retire_insight_runs([run.id], organization_id=run.organization_id)
    with pytest.raises(pipeline.InsightRunNotRunning):
        pipeline.set_progress(run, "summarising", 1, 1)
    expired(run)


def test_generation_fences_first_insight_write_under_outer_transaction(
    observe_project, monkeypatch, cutoff
):
    from django.db import transaction

    from ee.agenthub.insights import pipeline
    from ee.agenthub.insights.units import UnitText

    _, run, row = report(observe_project, cutoff + timedelta(days=1), status="pending")
    InsightUnit.objects.filter(pk=row.pk).update(
        content="", excerpt="", summary="", lens_answer=""
    )
    monkeypatch.setattr(pipeline, "check_setup", lambda: None)

    def load(*args, **kwargs):
        retention.retire_analysis_sources(
            project_id=run.project_id, trace_ids=[uuid.uuid4()]
        )
        return {
            row.unit_id: UnitText(
                unit_id=row.unit_id,
                content="late text",
                source_created_at=timezone.now(),
            )
        }

    monkeypatch.setattr(pipeline, "load_units", load)
    with transaction.atomic():
        with pytest.raises(pipeline.InsightRunNotRunning):
            pipeline.prepare(str(run.id))
    expired(run)


def test_deleted_pg_markers_filter_new_read_of_stale_ch_roots(observe_project):
    from tracer.models.trace import Trace
    from tracer.models.trace_session import TraceSessionOverlay

    trace = Trace.objects.create(project=observe_project, deleted=True)
    session_id = uuid.uuid4()
    TraceSessionOverlay._base_manager.create(
        project_id=observe_project.id, trace_session_id=session_id, deleted=True
    )
    gone_trace = SimpleNamespace(trace_id=trace.id.hex, trace_session_id=None)
    gone_session = SimpleNamespace(
        trace_id=uuid.uuid4(), trace_session_id=session_id.hex
    )
    alive = SimpleNamespace(trace_id=uuid.uuid4(), trace_session_id=uuid.uuid4())
    assert retention.retained_spans(
        observe_project.id, [gone_trace, gone_session, alive]
    ) == [alive]


def test_marker_lookup_skips_only_absent_legacy_tables(observe_project, monkeypatch):
    from tracer.models.trace_session import TraceSessionOverlay

    session_id = uuid.uuid4()
    TraceSessionOverlay._base_manager.create(
        project_id=observe_project.id, trace_session_id=session_id, deleted=True
    )
    real_cursor = retention.connection.cursor

    def cursor():
        actual = real_cursor()
        original, execute = actual.fetchone, actual.execute
        guarding = [False]

        def run(sql, params=None):
            guarding[0] = "to_regclass" in sql
            return execute(sql, params)

        def fetch():
            value = original()
            return (None, None, value[2]) if guarding[0] else value

        actual.execute, actual.fetchone = run, fetch
        return actual

    monkeypatch.setattr(retention.connection, "cursor", cursor)
    traces, sessions = retention.deleted_source_ids(
        observe_project.id, [uuid.uuid4()], [session_id]
    )
    assert traces == set() and str(session_id) in sessions


def test_ch_failed_delete_keeps_empty_marker_and_blocks_edit(
    observe_project, monkeypatch, cutoff
):
    from django.http import Http404

    from tracer.models.trace_session import TraceSessionOverlay
    from tracer.views import trace_session as view

    session_id = uuid.uuid4()
    marker = TraceSessionOverlay._base_manager.create(
        project_id=observe_project.id,
        trace_session_id=session_id,
        display_name="private rename",
        bookmarked=True,
    )
    _, run, row = report(observe_project, cutoff + timedelta(days=1))
    InsightUnit.objects.filter(pk=row.pk).update(unit_id=str(session_id))
    real_resolver = view._resolve_ch_session_fields
    monkeypatch.setattr(
        view,
        "_resolve_ch_session_fields",
        lambda *args: {"project_id": str(observe_project.id)},
    )
    fake = mock.Mock()
    fake.insert.side_effect = RuntimeError("synthetic CH outage")
    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.curated_writer._get_client", lambda: fake
    )
    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.curated_writer._reset_client", lambda: None
    )
    target = view.TraceSessionView()
    target.kwargs = {"pk": str(session_id)}
    assert target._destroy_ch_only_session(SimpleNamespace()).status_code == 204
    marker.refresh_from_db()
    assert marker.deleted and marker.display_name is None and not marker.bookmarked
    expired(run)
    assert real_resolver(SimpleNamespace(), session_id) is None
    with pytest.raises(Http404):
        target._upsert_overlay(
            session_id,
            project_id=observe_project.id,
            bookmarked=True,
            display_name="late rename",
        )
    marker.refresh_from_db()
    assert marker.deleted and marker.display_name is None and not marker.bookmarked


def test_late_snapshot_put_is_deleted_after_retirement(
    observe_project, monkeypatch, settings, tmp_path, cutoff
):
    from ee.agenthub.insights import broker, evidence_store

    settings.INSIGHTS_EVIDENCE_STORE = "local"
    settings.INSIGHTS_EVIDENCE_CACHE_DIR = str(tmp_path / "snapshots")
    settings.INSIGHTS_EVIDENCE_CACHE_MAX_BYTES = 2 * 1024**3
    monkeypatch.setattr(evidence_store, "_arm_sweeper", lambda: None)
    _, run, row = report(observe_project, cutoff + timedelta(days=1), status="running")
    document = evidence_store.Cached(
        {"manifest": {}, "fields": {"private": "late raw source"}}, {}
    )
    monkeypatch.setattr(broker, "_build", lambda *args: document)
    store = evidence_store.current()
    put = store.put

    def late_put(*args, **kwargs):
        retention.retire_insight_runs([run.id], organization_id=run.organization_id)
        evidence_store.delete_run(run.id)
        return put(*args, **kwargs)

    monkeypatch.setattr(store, "put", late_put)
    with pytest.raises(broker.BrokerError) as refused:
        broker._build_and_store(run, row, store, wait_s=None, remember=True)
    assert refused.value.status == 404
    assert not store.path(run.id, str(row.pk)).exists()
    assert evidence_store.recall(run.id, str(row.pk)) is None
    with pytest.raises(broker.BrokerError):
        broker.snapshot(run, row)


def test_snapshot_build_does_not_publish_after_project_epoch_changes(
    observe_project, monkeypatch, cutoff
):
    from ee.agenthub.insights import broker, evidence_store

    _, run, row = report(observe_project, cutoff + timedelta(days=1), status="running")

    def build(*args):
        retention.retire_analysis_sources(
            project_id=run.project_id, trace_ids=[uuid.uuid4()]
        )
        return evidence_store.Cached({"manifest": {}, "fields": {}}, {})

    monkeypatch.setattr(broker, "_build", build)
    store = mock.Mock()
    with pytest.raises(broker.BrokerError):
        broker._build_and_store(run, row, store, wait_s=None, remember=False)
    store.put.assert_not_called()


def test_queued_worker_stops_from_database_when_redis_is_down(
    observe_project, monkeypatch, cutoff
):
    from ee.agenthub.insights import pipeline

    _, run, _ = report(observe_project, cutoff + timedelta(days=1), status="running")
    monkeypatch.setattr(
        "django.core.cache.cache.get",
        mock.Mock(side_effect=RuntimeError("redis unavailable")),
    )
    assert pipeline.stop_requested(str(run.id)) is False
    retention.retire_insight_runs([run.id], organization_id=run.organization_id)
    assert pipeline.stop_requested(str(run.id)) is True


@pytest.mark.parametrize("row_type", ["sessions", "traces", "spans"])
@pytest.mark.parametrize("delete_alias", [True, False])
def test_fresh_facets_refuse_deleted_owning_session_in_either_id_spelling(
    observe_project, monkeypatch, row_type, delete_alias
):
    from tracer.models.trace_session import TraceSessionOverlay
    from tracer.services import instant_eval_rows as rows

    alias, canonical, trace_id, span_id = (str(uuid.uuid4()) for _ in range(4))
    marker = alias if delete_alias else canonical
    TraceSessionOverlay._base_manager.create(
        project_id=observe_project.id, trace_session_id=marker, deleted=True
    )
    rid = {"sessions": alias, "traces": trace_id, "spans": span_id}[row_type]
    real_resolve = rows._resolve

    def resolve(kind, ids):
        if kind == "sessions":
            return {canonical: list(ids)}, {alias: canonical, canonical: canonical}
        return real_resolve(kind, ids)

    monkeypatch.setattr(rows, "_resolve", resolve)

    def ch(sql, params):
        assert "AS source_session_ids" in sql
        return [
            {
                "rid": rid,
                "tid": trace_id if row_type == "spans" else "",
                "source_session_ids": [alias],
                "traces": [trace_id],
            }
        ]

    monkeypatch.setattr(rows, "_ch", ch)
    assert rows.read_facets(str(observe_project.id), row_type, [rid]) == {}


@pytest.mark.parametrize("row_type", ["traces", "spans"])
def test_completed_copies_use_owning_session_lineage_and_preserve_proven_unrelated(
    observe_project, monkeypatch, cutoff, row_type
):
    alias, canonical, unrelated = (str(uuid.uuid4()) for _ in range(3))
    monkeypatch.setattr(
        retention,
        "_session_aliases",
        lambda ids: {alias: canonical, canonical: canonical},
    )
    affected, _, a = instant(observe_project, cutoff, unit=row_type)
    independent, _, b = instant(observe_project, cutoff, unit=row_type)
    legacy, _, _ = instant(observe_project, cutoff, unit=row_type)
    for task in (affected, independent, legacy):
        EvalTask._base_manager.filter(pk=task.id).update(
            status=EvalTaskStatus.COMPLETED
        )
    InstantEvalRow.objects.filter(pk=a.pk).update(source_session_ids=[alias, canonical])
    InstantEvalRow.objects.filter(pk=b.pk).update(source_session_ids=[unrelated])
    result = retention.retire_analysis_sources(
        project_id=observe_project.id, session_ids=[alias]
    )
    assert result["instant_eval_runs"] == 2
    assert not InstantEvalRow.objects.filter(task__in=[affected, legacy]).exists()
    assert InstantEvalRow.objects.filter(task=independent).exists()
    assert not EvalTask._base_manager.get(pk=independent.id).deleted


@pytest.mark.parametrize("delete_alias", [True, False])
def test_insights_source_guard_matches_both_sides_of_session_remap(
    observe_project, monkeypatch, delete_alias
):
    from tracer.models.trace_session import TraceSessionOverlay

    alias, canonical, independent = (str(uuid.uuid4()) for _ in range(3))
    marker, asked = (alias, canonical) if delete_alias else (canonical, alias)
    TraceSessionOverlay._base_manager.create(
        project_id=observe_project.id, trace_session_id=marker, deleted=True
    )
    monkeypatch.setattr(
        retention,
        "_session_aliases",
        lambda ids: {alias: canonical, canonical: canonical, independent: independent},
    )
    gone = SimpleNamespace(trace_id=uuid.uuid4(), trace_session_id=asked)
    safe = SimpleNamespace(trace_id=uuid.uuid4(), trace_session_id=independent)
    assert retention.retained_spans(observe_project.id, [gone, safe]) == [safe]


def test_remap_outage_fails_closed_and_cleanup_is_project_scoped(
    observe_project, monkeypatch, cutoff
):
    from tracer.models.trace_session import TraceSessionOverlay

    session_id = uuid.uuid4()
    TraceSessionOverlay._base_manager.create(
        project_id=observe_project.id, trace_session_id=session_id, deleted=True
    )
    monkeypatch.setattr(
        retention,
        "_session_aliases",
        mock.Mock(side_effect=RuntimeError("synthetic remap outage")),
    )
    with pytest.raises(retention.AnalysisSourceExpired):
        retention.deleted_source_ids(observe_project.id, session_ids=[uuid.uuid4()])
    _, local, _ = report(observe_project, cutoff)
    project = Project.objects.create(
        name="Unaffected project",
        trace_type="observe",
        organization=observe_project.organization,
        workspace=observe_project.workspace,
        model_type=observe_project.model_type,
    )
    _, other, _ = report(project, cutoff)
    retention.retire_analysis_sources(
        project_id=observe_project.id, session_ids=[session_id]
    )
    expired(local)
    assert InsightRun._base_manager.filter(pk=other.pk, deleted=False).exists()


def test_cached_get_cannot_remember_raw_content_after_retirement(
    observe_project, monkeypatch, settings, tmp_path, cutoff
):
    from ee.agenthub.insights import broker, evidence_store

    settings.INSIGHTS_EVIDENCE_STORE = "local"
    settings.INSIGHTS_EVIDENCE_CACHE_DIR = str(tmp_path / "snapshots")
    settings.INSIGHTS_EVIDENCE_CACHE_MAX_BYTES = 2 * 1024**3
    monkeypatch.setattr(evidence_store, "_arm_sweeper", lambda: None)
    _, run, row = report(observe_project, cutoff, status="running")
    raw = evidence_store.Cached(
        {"manifest": {}, "fields": {"private": "cached source"}}, {}
    )
    store = evidence_store.current()
    put = store.put

    def get(*args):
        retention.retire_insight_runs([run.id], organization_id=run.organization_id)
        evidence_store.delete_run(run.id)
        put(run.id, str(row.pk), raw, remember=True)
        return raw

    monkeypatch.setattr(store, "get", get)
    monkeypatch.setattr(evidence_store, "current", lambda: store)
    with pytest.raises(broker.BrokerError):
        broker.snapshot(run, row)
    assert evidence_store.recall(run.id, str(row.pk)) is None
    assert not store.path(run.id, str(row.pk)).exists()


@pytest.mark.django_db(transaction=True)
def test_retirement_stops_queued_builtin_provider_calls_without_redis(
    observe_project, monkeypatch, cutoff
):
    from ee.agenthub.insights import pipeline
    from ee.agenthub.insights.llm import InsightModelError

    _, run, first = report(observe_project, cutoff, status="running")
    InsightUnit.objects.filter(pk=first.pk).update(status="pending")
    rest = [
        InsightUnit.objects.create(
            run=run, unit_id=str(uuid.uuid4()), content="queued private source"
        )
        for _ in range(4)
    ]
    monkeypatch.setattr(pipeline, "EXTRACT_WORKERS", 1)
    monkeypatch.setattr(
        "django.core.cache.cache.get",
        mock.Mock(side_effect=RuntimeError("redis unavailable")),
    )
    calls = []

    def provider(*args, **kwargs):
        calls.append(1)
        retention.retire_insight_runs([run.id], organization_id=run.organization_id)
        raise InsightModelError("synthetic in-flight provider failure")

    monkeypatch.setattr(pipeline, "extract", provider)
    with pytest.raises(pipeline.InsightRunNotRunning):
        pipeline.extract_batch(str(run.id), [first.pk, *(row.pk for row in rest)])
    assert len(calls) == 1
    expired(run)


@pytest.mark.django_db(transaction=True)
def test_retirement_stops_queued_v2_provider_calls_without_redis(
    observe_project, monkeypatch, cutoff
):
    from ee.agenthub.insights import broker, evidence_store, pipeline
    from ee.agenthub.insights.llm import InsightModelError
    from ee.agenthub.insights.tests.test_insights_v2 import V2

    _, run, first = report(observe_project, cutoff, status="running")
    rest = [
        InsightUnit.objects.create(
            run=run, unit_id=str(uuid.uuid4()), content="queued source"
        )
        for _ in range(4)
    ]
    monkeypatch.setattr(pipeline, "V2_PARALLEL_CALLS", 1)
    monkeypatch.setattr(pipeline, "V2_UNITS_PER_CALL", 1)
    monkeypatch.setattr(
        "django.core.cache.cache.get",
        mock.Mock(side_effect=RuntimeError("redis unavailable")),
    )
    monkeypatch.setattr(evidence_store, "sweep", lambda: None)
    monkeypatch.setattr(broker, "mint", lambda *args, **kwargs: "synthetic-capability")
    monkeypatch.setattr(
        broker,
        "prebuild",
        lambda run, rows, **kwargs: broker.Prebuilt([str(row.pk) for row in rows], {}),
    )
    calls = []

    def provider(*args, **kwargs):
        calls.append(1)
        retention.retire_insight_runs([run.id], organization_id=run.organization_id)
        raise InsightModelError("synthetic in-flight provider failure")

    monkeypatch.setattr(pipeline.insight_agent, "read_units_v2", provider)
    pipeline._read_with_agent_v2(
        run,
        [first, *rest],
        [],
        V2,
        lambda read, spend: pipeline._save_read(run.id, read, spend),
    )
    assert len(calls) == 1
    expired(run)
