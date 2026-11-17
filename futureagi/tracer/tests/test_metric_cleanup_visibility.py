# ruff: noqa: F811 -- shared isolated ClickHouse fixtures
"""Durable expiry admission with native PG and isolated physical CH cleanup."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from django.db import connection
from django.utils import timezone

from simulate.models.agent_definition import AgentDefinition
from tracer.models.derived_metric import MetricErasureRequest, MetricProjectRefresh
from tracer.models.project import Project
from tracer.services.derived_metrics import admission, retention
from tracer.services.derived_metrics.lease import project_lease
from tracer.services.derived_metrics.refresh import RefreshWriter
from tracer.services.derived_metrics.storage import FIELD_VALUES
from tracer.tasks import derived_metric as tasks
from tracer.tests.test_derived_metric_publication_clickhouse import (  # noqa: F401
    metric_ch,
    publication_store,
)
from tracer.tests.test_derived_metric_refresh import Recorder
from tracer.tests.test_derived_metric_retention_clickhouse import (
    numeric_definition,
    seed_fields_and_buckets,
)

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def expiry(project, settings):
    settings.METRICS_ENABLED = 1
    settings.METRICS_MAX_CONCURRENT_PROJECTS = 1
    store = Recorder()
    store.execute = Mock(return_value=[(0,)])
    with project_lease(project.pk, holder="cleanup-causal-fixture") as lease:
        writer = RefreshWriter(
            lease,
            store,
            org_id=project.organization_id,
            workspace_id=project.workspace_id,
        )
        checkpoint = writer.begin(
            purpose="compaction", ceilings={"logical_expiry": True}
        )
        writer.freeze(checkpoint, FIELD_VALUES, "SELECT retained_fixture")
        writer.seal(checkpoint)
        head = writer.stage_and_publish(checkpoint)
    intent = MetricErasureRequest.objects.get(
        project_key=project.pk, store_payload__protocol="metric_expiry"
    )
    assert intent.status == "queued" and intent.project_id is None
    assert intent.deletion_key == f"expiry:{checkpoint.id}"
    return SimpleNamespace(project=project, store=store, intent=intent, head=head)


def hide(project):
    agent = AgentDefinition.objects.create(
        agent_name="Cleanup visibility",
        description="Synthetic expiry transition",
        agent_type="text",
        inbound=True,
        organization=project.organization,
        workspace=project.workspace,
    )
    Project.no_workspace_objects.filter(pk=project.pk).update(
        simulation_agent_id=agent.pk
    )
    assert not Project.no_workspace_objects.filter(pk=project.pk).exists()


def cleanup_recorder(monkeypatch, expiry):
    calls = []

    def run(lease, expiry_id):
        assert not connection.in_atomic_block
        lease.check()
        assert expiry_id == expiry.intent.id
        calls.append((lease.grant.fencing_epoch, expiry_id))
        writer = SimpleNamespace(
            lease=lease, store=expiry.store, org_id=expiry.project.organization_id
        )
        return retention.finish_pending_expiry(writer, expiry_id=expiry_id)

    monkeypatch.setattr(admission, "run_project_cleanup", run, raising=False)
    normal = Mock(side_effect=AssertionError("hidden analytics executed"))
    monkeypatch.setattr(admission, "run_project_pass", normal)
    return calls, normal


def queued_analytics(project):
    # Model an already-queued analytics delivery, with its real persisted fence.
    # Fresh admission now prioritizes durable expiry debt even while visible;
    # asking reserve_projects for analytics here would instead reserve cleanup.
    grant = MetricProjectRefresh.take_lease(
        project.pk,
        holder="metrics:queued:incremental",
        ttl=timedelta(minutes=5),
    )
    assert grant is not None
    return admission.Reservation(grant, "incremental", None)


def test_durable_expiry_continues_after_visible_to_hidden(expiry, monkeypatch):
    # Revoke visibility before an already-queued ordinary delivery is claimed.
    normal = queued_analytics(expiry.project)
    assert normal.purpose != "cleanup"
    hide(expiry.project)
    calls, analytics = cleanup_recorder(monkeypatch, expiry)
    assert not tasks.refresh_derived_metric_project._original_func(normal.payload())
    assert not admission.request_refresh(expiry.project.pk)
    expiry.intent.refresh_from_db()
    assert expiry.intent.status == "queued"
    payloads = []
    assert admission.dispatch(payloads.append) == 1
    assert payloads[0]["purpose"] == "cleanup"
    assert payloads[0]["expiry_id"] == str(expiry.intent.id)
    assert tasks.refresh_derived_metric_project._original_func(payloads[0])
    expiry.intent.refresh_from_db()
    assert expiry.intent.status == "done"
    assert expiry.intent.receipts["final_scrub"]["remaining"] == 0
    assert len(calls) == 1 and calls[0][1] == expiry.intent.id
    assert not tasks.refresh_derived_metric_project._original_func(payloads[0])
    assert len(calls) == 1
    analytics.assert_not_called()
    state = MetricProjectRefresh.objects.get(project_id=expiry.project.pk)
    assert state.head_manifest_id == expiry.head.id and state.lease_token is None
    assert not state.pending_expiry
    assert (
        admission.reserve_projects(clock=lambda: timezone.now() + timedelta(days=1))
        == []
    )


@pytest.mark.parametrize("fault", ["lost_ack", "final_scrub", "undrained"])
def test_cleanup_failure_retries_same_intent_with_backoff(expiry, monkeypatch, fault):
    hide(expiry.project)
    calls, analytics = cleanup_recorder(monkeypatch, expiry)
    state = MetricProjectRefresh.objects.get(project_id=expiry.project.pk)
    requested = timezone.now()
    MetricProjectRefresh.objects.filter(pk=state.pk).update(
        refresh_requested_at=requested
    )
    now = timezone.now()
    (first,) = admission.reserve_projects(clock=lambda: now)
    submission = None
    if fault == "undrained":
        from tracer.services.derived_metrics.deletion_protocol import _intent

        submission = _intent(
            organization_id=expiry.project.organization_id,
            project_id=expiry.project.pk,
            scope="history",
            deletion_key=f"submission:{uuid4()}",
            store_payload={"protocol": "metric_submission", "generation": 1},
        )
        assert admission.execute_reservation(first.payload(), clock=lambda: now)
    else:
        execute = expiry.store.execute

        def fail(sql, *args, **kwargs):
            result = execute(sql, *args, **kwargs)
            if (fault == "lost_ack" and sql.startswith("ALTER TABLE")) or (
                fault == "final_scrub" and sql.startswith("SELECT count()")
            ):
                raise RuntimeError("synthetic cleanup interruption")
            return result

        with monkeypatch.context() as patch:
            patch.setattr(expiry.store, "execute", fail)
            with pytest.raises(RuntimeError, match="synthetic cleanup interruption"):
                admission.execute_reservation(first.payload(), clock=lambda: now)
        state.refresh_from_db()
        assert state.consecutive_failures == 1
        assert state.last_error_code == "metric_refresh_failed"
        assert (
            admission.reserve_projects(clock=lambda: now + timedelta(seconds=59)) == []
        )
    expiry.intent.refresh_from_db()
    assert expiry.intent.status == "queued" and expiry.intent.completed_at is None
    state.refresh_from_db()
    assert state.pending_expiry and state.lease_token is None
    assert state.refresh_requested_at == requested
    if submission:
        MetricErasureRequest.objects.filter(pk=submission.pk).update(
            status="done",
            completed_at=now,
            receipts={"synchronous_response": {"drained": 1}},
        )
    later = now + timedelta(seconds=61)
    (retry,) = admission.reserve_projects(clock=lambda: later)
    assert retry.expiry_id == first.expiry_id == expiry.intent.id
    assert retry.grant.fencing_epoch > first.grant.fencing_epoch
    assert admission.execute_reservation(retry.payload(), clock=lambda: later)
    expiry.intent.refresh_from_db()
    assert (
        expiry.intent.status == "done"
        and expiry.intent.receipts["final_scrub"]["remaining"] == 0
    )
    state.refresh_from_db()
    assert state.refresh_requested_at == requested and state.consecutive_failures == 0
    assert len(calls) == 2
    analytics.assert_not_called()


def test_cleanup_off_then_on_follows_source_retention_not_analytics_rollout(
    expiry, settings, monkeypatch
):
    hide(expiry.project)
    calls, analytics = cleanup_recorder(monkeypatch, expiry)
    (reservation,) = admission.reserve_projects()
    settings.METRICS_ENABLED = 0
    # OFF cannot revoke privacy obligations. The occupied slot still prevents
    # duplication, but a lost delivery must be reservable and executable OFF.
    assert admission.reserve_projects() == []
    later = reservation.grant.expires_at + timedelta(seconds=1)
    (retry,) = admission.reserve_projects(clock=lambda: later)
    assert retry.purpose == "cleanup"
    assert retry.expiry_id == reservation.expiry_id == expiry.intent.id
    assert retry.grant.fencing_epoch > reservation.grant.fencing_epoch
    assert not admission.execute_reservation(reservation.payload(), clock=lambda: later)
    expiry.intent.refresh_from_db()
    assert expiry.intent.status == "queued" and not calls
    assert admission.execute_reservation(retry.payload(), clock=lambda: later)
    expiry.intent.refresh_from_db()
    assert expiry.intent.status == "done"
    assert expiry.intent.receipts["final_scrub"]["remaining"] == 0
    assert len(calls) == 1
    settings.METRICS_ENABLED = 1
    assert admission.reserve_projects(clock=lambda: later) == []
    assert not admission.execute_reservation(retry.payload(), clock=lambda: later)
    assert len(calls) == 1
    analytics.assert_not_called()


@pytest.mark.parametrize(
    "invalid",
    ["absent", "foreign", "organization", "protocol", "done", "deleted", "identity"],
)
def test_cleanup_authority_rechecked_at_claim(
    expiry, observe_project, monkeypatch, invalid
):
    hide(expiry.project)
    calls, analytics = cleanup_recorder(monkeypatch, expiry)
    (reservation,) = admission.reserve_projects()
    payload = reservation.payload()
    if invalid == "absent":
        payload["expiry_id"] = str(uuid4())
    elif invalid == "foreign":
        MetricErasureRequest.objects.filter(pk=expiry.intent.pk).update(
            project_key=observe_project.pk
        )
    elif invalid == "organization":
        MetricErasureRequest.objects.filter(pk=expiry.intent.pk).update(
            organization_key=uuid4()
        )
    elif invalid == "protocol":
        MetricErasureRequest.objects.filter(pk=expiry.intent.pk).update(
            store_payload={"protocol": "metric_omission"}
        )
    elif invalid == "done":
        MetricErasureRequest.objects.filter(pk=expiry.intent.pk).update(
            status="done", completed_at=timezone.now()
        )
    elif invalid == "identity":
        MetricErasureRequest.objects.filter(pk=expiry.intent.pk).update(
            deletion_key=f"expiry:{uuid4()}"
        )
    else:
        Project.with_simulation.filter(pk=expiry.project.pk).update(deleted=True)
    assert not admission.execute_reservation(payload)
    assert not calls
    analytics.assert_not_called()
    state = MetricProjectRefresh.objects.get(project_id=expiry.project.pk)
    # Forged operation IDs must not release the genuinely queued operation.
    assert (state.lease_token == reservation.grant.lease_token) is (invalid == "absent")


def test_cleanup_replacement_and_running_duplicate_are_fenced(expiry, monkeypatch):
    hide(expiry.project)
    calls, analytics = cleanup_recorder(monkeypatch, expiry)
    (old,) = admission.reserve_projects()
    later = old.grant.expires_at + timedelta(seconds=1)
    (replacement,) = admission.reserve_projects(clock=lambda: later)
    assert not admission.execute_reservation(old.payload(), clock=lambda: later)
    state = MetricProjectRefresh.objects.get(project_id=expiry.project.pk)
    assert state.lease_token == replacement.grant.lease_token and not calls
    run = admission.run_project_cleanup

    def duplicate(lease, expiry_id):
        assert not admission.execute_reservation(
            replacement.payload(), clock=lambda: later
        )
        lease.check()
        run(lease, expiry_id)

    monkeypatch.setattr(admission, "run_project_cleanup", duplicate)
    assert admission.execute_reservation(replacement.payload(), clock=lambda: later)
    assert len(calls) == 1
    analytics.assert_not_called()


def test_cleanup_reservation_rollback(expiry, monkeypatch):
    hide(expiry.project)
    state = MetricProjectRefresh.objects.get(project_id=expiry.project.pk)
    epoch, started = state.fencing_epoch, state.last_started_at
    take = MetricProjectRefresh.take_lease

    def fail(*args, **kwargs):
        assert take(*args, **kwargs)
        raise RuntimeError("synthetic reservation rollback")

    with monkeypatch.context() as patch:
        patch.setattr(MetricProjectRefresh, "take_lease", fail)
        with pytest.raises(RuntimeError, match="synthetic reservation rollback"):
            admission.reserve_projects()
    state.refresh_from_db()
    expiry.intent.refresh_from_db()
    assert state.fencing_epoch == epoch and state.lease_token is None
    assert state.last_started_at == started and expiry.intent.status == "queued"
    assert len(admission.reserve_projects()) == 1


def test_flags_or_forged_payload_never_authorize_hidden_cleanup(
    project, settings, monkeypatch
):
    settings.METRICS_ENABLED = 1
    state = MetricProjectRefresh.objects.create(
        project=project, pending_expiry=True, pending_erasure=True
    )
    hide(project)
    normal = Mock()
    cleanup = Mock()
    monkeypatch.setattr(admission, "run_project_pass", normal)
    monkeypatch.setattr(admission, "run_project_cleanup", cleanup)
    assert admission.reserve_projects() == []
    assert not admission.request_refresh(project.pk)
    state.refresh_from_db()
    assert state.fencing_epoch == 0 and state.lease_token is None
    assert not MetricErasureRequest.objects.filter(project_key=project.pk).exists()
    grant = MetricProjectRefresh.take_lease(
        project.pk, holder="metrics:queued:incremental", ttl=timedelta(minutes=5)
    )
    forged = admission.Reservation(grant, "cleanup", None, uuid4())
    assert not admission.execute_reservation(forged.payload())
    state.refresh_from_db()
    assert state.lease_token == grant.lease_token
    normal.assert_not_called()
    cleanup.assert_not_called()


def test_operation_identity_cannot_be_substituted_or_drain_other_debt(
    expiry, monkeypatch
):
    with project_lease(expiry.project.pk, holder="second-cleanup-fixture") as lease:
        writer = RefreshWriter(
            lease, expiry.store, org_id=expiry.project.organization_id
        )
        checkpoint = writer.begin(
            purpose="compaction", ceilings={"logical_expiry": True}
        )
        writer.freeze(checkpoint, FIELD_VALUES, "SELECT second_retained_fixture")
        writer.seal(checkpoint)
        writer.stage_and_publish(checkpoint)
    second = MetricErasureRequest.objects.get(deletion_key=f"expiry:{checkpoint.id}")
    hide(expiry.project)
    calls, _ = cleanup_recorder(monkeypatch, expiry)
    (reservation,) = admission.reserve_projects()
    assert reservation.expiry_id == expiry.intent.id
    assert not admission.execute_reservation(
        {**reservation.payload(), "expiry_id": str(second.id)}
    )
    state = MetricProjectRefresh.objects.get(project_id=expiry.project.pk)
    assert state.lease_token == reservation.grant.lease_token and not calls
    assert admission.execute_reservation(reservation.payload())
    expiry.intent.refresh_from_db()
    second.refresh_from_db()
    state.refresh_from_db()
    assert (
        expiry.intent.status == "done"
        and second.status == "queued"
        and state.pending_expiry
    )
    (next_job,) = admission.reserve_projects()
    assert next_job.expiry_id == second.id
    # A duplicate of the completed first operation cannot release the second.
    assert not admission.execute_reservation(reservation.payload())
    state.refresh_from_db()
    assert state.lease_token == next_job.grant.lease_token
    expiry.intent = second
    assert admission.execute_reservation(next_job.payload())
    second.refresh_from_db()
    assert second.status == "done" and len(calls) == 2


@pytest.mark.parametrize("failure", ["none", "lost_ack", "final_scrub"])
def test_real_ch_cleanup_continues_without_hidden_analytics(
    publication_store, project, settings, monkeypatch, failure
):
    from tracer.services.clickhouse import v2
    from tracer.services.derived_metrics.publication import ClickHousePublicationStore

    settings.METRICS_ENABLED = 1
    settings.METRICS_MAX_CONCURRENT_PROJECTS = 1
    now = timezone.now()
    expired, retained, unrelated = str(uuid4()), str(uuid4()), uuid4()
    definition = numeric_definition(uuid4())
    with project_lease(project.pk, holder="physical-cleanup-fixture") as lease:
        writer = RefreshWriter(
            lease,
            publication_store,
            org_id=project.organization_id,
            workspace_id=project.workspace_id,
        )
        seed_fields_and_buckets(
            writer,
            definition,
            [
                (expired, now - timedelta(days=3), 4, "expired-user"),
                (retained, now - timedelta(hours=12), 6, "retained-user"),
            ],
        )
        # An unrelated project's identical subject in this test-owned CH database
        # must survive. No other tenant or sandbox is used by this adapter.
        publication_store.execute(
            "INSERT INTO metric_field_values SELECT * REPLACE(toUUID(%(other)s) AS project_id) "
            "FROM metric_field_values WHERE project_id = toUUID(%(project)s)",
            {"other": str(unrelated), "project": str(project.pk)},
        )

        def crash(*args, **kwargs):
            raise RuntimeError("synthetic crash after logical publication")

        with monkeypatch.context() as patch:
            patch.setattr(retention, "purge_checkpoint", crash)
            with pytest.raises(
                RuntimeError, match="synthetic crash after logical publication"
            ):
                retention.expire_project(
                    writer, [definition], retention_days=lambda: 1, now=now
                )
    intent = MetricErasureRequest.objects.get(
        project_key=project.pk, store_payload__protocol="metric_expiry"
    )
    assert intent.status == "queued" and intent.project_id is None
    state = MetricProjectRefresh.objects.get(project_id=project.pk)
    safe_head = state.head_manifest_id

    def count(project_id, unit):
        return sum(
            publication_store.execute(
                f"SELECT count() FROM {table} WHERE project_id = toUUID(%(project)s) AND unit_id = %(unit)s",
                {"project": str(project_id), "unit": unit},
            )[0][0]
            for table in ("metric_field_values", "metric_field_values_checkpoint")
        )

    assert count(project.pk, expired) > 0
    unrelated_count = count(unrelated, expired)
    assert unrelated_count > 0
    stale = queued_analytics(project)
    hide(project)
    analytics = Mock(side_effect=AssertionError("hidden analytics executed"))
    monkeypatch.setattr(admission, "run_project_pass", analytics)
    assert not tasks.refresh_derived_metric_project._original_func(stale.payload())
    config = v2.get_v2_config()
    config["database"] = publication_store.execute("SELECT currentDatabase()")[0][0]
    monkeypatch.setattr(v2, "get_v2_config", lambda: config)
    payloads = []
    assert admission.dispatch(payloads.append) == 1
    assert payloads[0]["expiry_id"] == str(intent.id)
    later = timezone.now()
    if failure != "none":
        execute = ClickHousePublicationStore.execute

        def interrupt(store, sql, *args, **kwargs):
            result = execute(store, sql, *args, **kwargs)
            if (
                failure == "lost_ack"
                and sql.startswith("ALTER TABLE metric_field_values DELETE")
            ) or (
                failure == "final_scrub"
                and sql.startswith(
                    "SELECT count() FROM metric_field_values WHERE org_id"
                )
            ):
                raise RuntimeError("synthetic physical cleanup interruption")
            return result

        with monkeypatch.context() as patch:
            patch.setattr(ClickHousePublicationStore, "execute", interrupt)
            with pytest.raises(
                RuntimeError, match="synthetic physical cleanup interruption"
            ):
                tasks.refresh_derived_metric_project._original_func(payloads[0])
        intent.refresh_from_db()
        state.refresh_from_db()
        assert intent.status == "queued" and intent.completed_at is None
        assert state.pending_expiry and state.last_error_code == "metric_refresh_failed"
        assert admission.reserve_projects() == []
        later = state.last_error_at + timedelta(seconds=61)
        (retry,) = admission.reserve_projects(clock=lambda: later)
        assert retry.expiry_id == intent.id
        payloads = [retry.payload()]
    assert admission.execute_reservation(payloads[0], clock=lambda: later)
    intent.refresh_from_db()
    state.refresh_from_db()
    assert intent.status == "done" and intent.receipts["final_scrub"] == {
        "verified_tables": 4,
        "remaining": 0,
        "private_payloads_remaining": 0,
    }
    assert count(project.pk, expired) == 0 and count(project.pk, retained) > 0
    assert count(unrelated, expired) == unrelated_count
    assert state.head_manifest_id == safe_head and not state.pending_expiry
    analytics.assert_not_called()
