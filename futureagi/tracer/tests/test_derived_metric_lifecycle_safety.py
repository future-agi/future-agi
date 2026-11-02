"""Regression tests for deletion-safe metric intent, GC and capture fencing."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from uuid import uuid4

import pytest
from django.db import close_old_connections, connection, models, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.models.deletion import Collector
from django.utils import timezone

from tracer.models.derived_metric import (
    DerivedMetric,
    DerivedMetricRun,
    DerivedMetricVersion,
    MetricErasureRequest,
    MetricGenerationPin,
    MetricProjectRefresh,
    MetricReadManifest,
    MetricRefreshAttempt,
    MetricStateConflict,
)
from tracer.services.derived_metrics import deletion_protocol


def _metric(workspace, project, name="Lifecycle metric"):
    return DerivedMetric.objects.create(
        organization=workspace.organization,
        workspace=workspace,
        project=project,
        name=name,
    )


def _version(metric, now, number=1):
    version = DerivedMetricVersion.objects.create(
        metric=metric,
        version=number,
        formula="x",
        inputs=[],
        selection_unit="session",
        weighting="none",
        definition_hash=(f"{metric.id}-{number}".encode().hex() * 2)[:64],
        backfill_status="complete",
    )
    if number == 1:
        DerivedMetric.activate_version(
            metric.id,
            version.id,
            expected_active_id=None,
            now=now,
        )
    return version


def _commit(
    project,
    now,
    *,
    purpose="incremental",
    cursors=None,
    retention=timedelta(days=7),
):
    grant = MetricProjectRefresh.take_lease(
        project.id,
        holder="lifecycle-test",
        ttl=timedelta(minutes=5),
        now=now,
    )
    if grant is None:
        grant_row = MetricProjectRefresh.objects.get(project_id=project.id)
        grant = type(
            "Grant",
            (),
            {
                "fencing_epoch": grant_row.fencing_epoch,
                "lease_token": grant_row.lease_token,
            },
        )()
    attempt = MetricRefreshAttempt.prepare(
        project.id,
        epoch=grant.fencing_epoch,
        token=grant.lease_token,
        purpose=purpose,
        source_ceilings={},
        metric_versions=[],
        retention=retention,
        now=now,
    )
    fence = {"epoch": grant.fencing_epoch, "token": grant.lease_token, "now": now}
    MetricRefreshAttempt.append_ledger_chunk(
        attempt.id,
        part="dirty_after",
        chunk_no=0,
        keys=[str(attempt.id)],
        **fence,
    )
    MetricRefreshAttempt.seal_ledger(
        attempt.id,
        expected_chunks=1,
        expected_keys=1,
        **fence,
    )
    MetricRefreshAttempt.transition(
        attempt.id,
        from_state="prepared",
        to_state="staging",
        **fence,
    )
    MetricRefreshAttempt.transition(
        attempt.id,
        from_state="staging",
        to_state="verified",
        **fence,
    )
    return MetricReadManifest.publish(
        attempt.id,
        epoch=grant.fencing_epoch,
        token=grant.lease_token,
        cursors=cursors,
        now=now,
    )


@pytest.mark.django_db(transaction=True)
def test_migration_0126_roundtrip_preserves_existing_intent_runs_manifests_and_pins():
    executor = MigrationExecutor(connection)
    # Historical assertions below deliberately stop at 0127. Cleanup must restore
    # every current leaf, including later CHECK constraints used by other tests.
    restore_targets = executor.loader.graph.leaf_nodes()
    previous = [("tracer", "0125_merge_metrics_and_dev_tracer_leaf")]
    base = [("tracer", "0126_derived_metric_refresh")]
    target = [("tracer", "0127_metric_lifecycle_safety")]
    try:
        executor.migrate(previous)
        old = executor.loader.project_state(previous).apps
        Organization = old.get_model("accounts", "Organization")
        User = old.get_model("accounts", "User")
        Workspace = old.get_model("accounts", "Workspace")
        Project = old.get_model("tracer", "Project")
        organization = Organization.objects.create(name="migration fixture")
        user = User.objects.create(
            email=f"{uuid4()}@example.invalid",
            name="Migration fixture",
            organization=organization,
        )
        workspace = Workspace.objects.create(
            name=f"workspace-{uuid4()}",
            organization=organization,
            created_by=user,
        )
        project = Project.objects.create(
            organization=organization,
            workspace=workspace,
            model_type="openai",
            name=f"project-{uuid4()}",
            trace_type="observe",
        )
        executor.loader.build_graph()
        executor.migrate(base)
        historical = executor.loader.project_state(base).apps
        Metric = historical.get_model("tracer", "DerivedMetric")
        Version = historical.get_model("tracer", "DerivedMetricVersion")
        Attempt = historical.get_model("tracer", "MetricRefreshAttempt")
        Manifest = historical.get_model("tracer", "MetricReadManifest")
        Refresh = historical.get_model("tracer", "MetricProjectRefresh")
        Pin = historical.get_model("tracer", "MetricGenerationPin")
        Erasure = historical.get_model("tracer", "MetricErasureRequest")
        Run = historical.get_model("tracer", "DerivedMetricRun")
        now = timezone.now()
        metric = Metric.objects.create(
            organization_id=organization.id,
            workspace_id=workspace.id,
            project_id=project.id,
            name="old",
        )
        version = Version.objects.create(
            metric=metric,
            version=1,
            formula="x",
            inputs=[],
            selection_unit="session",
            weighting="none",
            definition_hash="a" * 64,
            backfill_status="complete",
        )
        expires = now + timedelta(days=7)
        attempt1 = Attempt.objects.create(
            project_id=project.id,
            generation=1,
            fencing_epoch=1,
            lease_token=uuid4(),
            purpose="incremental",
            prepared_sha256="b" * 64,
            created_at=now,
            expires_at=expires,
        )
        manifest1 = Manifest.objects.create(
            project_id=project.id,
            generation=1,
            published_by=attempt1,
            manifest_sha256="c" * 64,
            created_at=now,
        )
        attempt2 = Attempt.objects.create(
            project_id=project.id,
            generation=2,
            fencing_epoch=1,
            lease_token=uuid4(),
            purpose="incremental",
            parent_manifest=manifest1,
            prepared_sha256="d" * 64,
            created_at=now,
            expires_at=expires,
        )
        manifest2 = Manifest.objects.create(
            project_id=project.id,
            generation=2,
            published_by=attempt2,
            parent=manifest1,
            manifest_sha256="e" * 64,
            created_at=now,
        )
        refresh = Refresh.objects.create(
            project_id=project.id,
            head_manifest=manifest2,
            remap_cursor_ns=77,
        )
        del refresh
        Pin.objects.create(
            project_id=project.id,
            manifest=manifest2,
            definition_version=version,
            consumer_kind="snapshot",
            captured_at=now,
            expires_at=now + timedelta(hours=1),
        )
        Run.objects.create(
            definition=version,
            window_start=now - timedelta(days=1),
            window_end=now,
            as_of=now,
            fingerprint=uuid4().hex,
        )
        Erasure.objects.create(
            organization_id=organization.id,
            project_id=project.id,
            scope_kind="project",
            subject_ids=[],
            subject_count=0,
            requested_at=now,
        )

        executor.loader.build_graph()
        executor.migrate(target)
        current = executor.loader.project_state(target).apps
        NewErasure = current.get_model("tracer", "MetricErasureRequest")
        NewAttempt = current.get_model("tracer", "MetricRefreshAttempt")
        NewManifest = current.get_model("tracer", "MetricReadManifest")
        NewPin = current.get_model("tracer", "MetricGenerationPin")
        NewRequest = NewErasure.objects.get(scope_kind="project")
        assert (NewRequest.organization_key, NewRequest.project_key) == (
            organization.id,
            project.id,
        )
        assert NewRequest.store_payload == {}
        assert (
            NewAttempt.objects.get(id=attempt2.id).parent_manifest_key == manifest1.id
        )
        assert NewAttempt.objects.get(id=attempt2.id).parent_manifest_sha256 == "c" * 64
        assert (
            NewManifest.objects.get(id=manifest2.id).parent_manifest_key == manifest1.id
        )
        assert (
            NewManifest.objects.get(id=manifest2.id).parent_manifest_sha256 == "c" * 64
        )
        assert NewPin.objects.get(manifest_id=manifest2.id).remap_revision == 77

        executor.loader.build_graph()
        executor.migrate(base)
        assert (
            historical.get_model("tracer", "MetricErasureRequest")
            .objects.filter(
                scope_kind="project",
                project_id=project.id,
            )
            .exists()
        )
        executor.loader.build_graph()
        executor.migrate(target)
        NewErasure = executor.loader.project_state(target).apps.get_model(
            "tracer",
            "MetricErasureRequest",
        )
        assert NewErasure.objects.get(scope_kind="project").project_key == project.id
    finally:
        executor.loader.build_graph()
        executor.migrate(restore_targets)


@pytest.mark.django_db(transaction=True)
def test_metric_hard_delete_records_runs_and_queued_snapshots_before_collector(
    workspace,
    observe_project,
):
    now = timezone.now()
    metric = _metric(workspace, observe_project)
    version = _version(metric, now)
    run = DerivedMetricRun.objects.create(
        definition=version,
        window_start=now - timedelta(days=1),
        window_end=now,
        as_of=now,
        fingerprint=uuid4().hex,
    )
    queued_snapshot = DerivedMetricRun.objects.create(
        definition=version,
        window_start=now - timedelta(days=1),
        window_end=now,
        as_of=now,
        fingerprint=uuid4().hex,
        kind="snapshot",
        supported_grain="conversation",
    )
    complete_snapshot = DerivedMetricRun.objects.create(
        definition=version,
        window_start=now - timedelta(days=1),
        window_end=now,
        as_of=now,
        fingerprint=uuid4().hex,
        kind="snapshot",
        supported_grain="conversation",
        status="complete",
        captured_at=now,
    )
    queued_snapshot.attempt_id = uuid4()
    queued_snapshot.snapshot_id = uuid4()
    queued_snapshot.result_id = uuid4()
    queued_snapshot.source_manifest = {"immutable": "receipt"}
    queued_snapshot.save(
        update_fields=["attempt_id", "snapshot_id", "result_id", "source_manifest"]
    )

    # QuerySet.delete uses Collector and must not rely on a model API wrapper.
    DerivedMetric.objects.filter(id=metric.id).delete()

    assert not DerivedMetricRun.objects.filter(
        id__in=[run.id, queued_snapshot.id, complete_snapshot.id]
    ).exists()
    jobs = list(
        MetricErasureRequest.objects.filter(metric_key=metric.id).order_by("scope_kind")
    )
    assert {job.scope_kind for job in jobs} >= {"metric", "version", "run"}
    run_job = next(
        job
        for job in jobs
        if job.scope_kind == "run" and job.run_key == queued_snapshot.id
    )
    assert run_job.snapshot_key == queued_snapshot.snapshot_id
    assert run_job.result_key == queued_snapshot.result_id
    assert run_job.store_payload["attempt_id"] == str(queued_snapshot.attempt_id)
    assert run_job.store_payload["source_manifest"] == {"immutable": "receipt"}
    assert all(job.organization_key == workspace.organization_id for job in jobs)
    assert all(job.status == "queued" and not job.receipts for job in jobs)


@pytest.mark.django_db(transaction=True)
def test_collector_rollback_rolls_back_the_outbox_intent(workspace, observe_project):
    metric = _metric(workspace, observe_project)
    with pytest.raises(RuntimeError):
        with transaction.atomic():
            DerivedMetric.objects.filter(id=metric.id).delete()
            raise RuntimeError("rollback probe")
    assert DerivedMetric.objects.filter(id=metric.id).exists()
    assert not MetricErasureRequest.objects.filter(metric_key=metric.id).exists()


@pytest.mark.django_db(transaction=True)
def test_hard_project_delete_keeps_identifier_after_project_and_org_navigation_are_gone(
    workspace,
    observe_project,
):
    organization_id, project_id = workspace.organization_id, observe_project.id
    observe_project.__class__.objects.filter(id=project_id).delete()
    request = MetricErasureRequest.objects.get(deletion_key=f"project:{project_id}")
    assert request.organization_id is None
    assert request.project_id is None
    assert (request.organization_key, request.project_key) == (
        organization_id,
        project_id,
    )
    assert request.status == "queued" and not request.receipts


@pytest.mark.django_db(transaction=True)
def test_workspace_hard_delete_keeps_durable_scope_for_cascaded_metric_runs(
    workspace,
    observe_project,
):
    now = timezone.now()
    metric = _metric(workspace, observe_project)
    version = _version(metric, now)
    snapshot = DerivedMetricRun.objects.create(
        definition=version,
        window_start=now - timedelta(days=1),
        window_end=now,
        as_of=now,
        fingerprint=uuid4().hex,
        kind="snapshot",
        supported_grain="conversation",
        status="complete",
        captured_at=now,
        snapshot_id=uuid4(),
        result_id=uuid4(),
    )
    workspace_id, project_id = workspace.id, observe_project.id
    type(workspace).objects.filter(id=workspace_id).delete()
    assert not DerivedMetricRun.objects.filter(id=snapshot.id).exists()
    assert MetricErasureRequest.objects.filter(
        scope_kind="project",
        project_key=project_id,
        status="queued",
    ).exists()
    assert MetricErasureRequest.objects.filter(
        scope_kind="run",
        run_key=snapshot.id,
        snapshot_key=snapshot.snapshot_id,
        result_key=snapshot.result_id,
        status="queued",
    ).exists()


@pytest.mark.django_db(transaction=True)
def test_organization_hard_delete_retains_parent_independent_intent(
    workspace, observe_project
):
    now = timezone.now()
    metric = _metric(workspace, observe_project)
    version = _version(metric, now)
    run = DerivedMetricRun.objects.create(
        definition=version,
        window_start=now - timedelta(days=1),
        window_end=now,
        as_of=now,
        fingerprint=uuid4().hex,
    )
    organization_id, project_id = workspace.organization_id, observe_project.id
    type(workspace.organization).objects.filter(id=organization_id).delete()
    assert not DerivedMetricRun.objects.filter(id=run.id).exists()
    assert MetricErasureRequest.objects.filter(
        scope_kind="organization",
        organization_key=organization_id,
        project_key__isnull=True,
        status="queued",
    ).exists()
    assert MetricErasureRequest.objects.filter(
        scope_kind="project",
        organization_key=organization_id,
        project_key=project_id,
        project__isnull=True,
        status="queued",
    ).exists()
    assert MetricErasureRequest.objects.filter(
        scope_kind="run",
        run_key=run.id,
        organization_key=organization_id,
        status="queued",
    ).exists()


@pytest.mark.django_db(transaction=True)
def test_repeated_checkpoint_gc_detaches_expired_ancestry_and_bounds_rows(
    observe_project,
):
    now = timezone.now()
    _commit(observe_project, now)
    # Publish repeated checkpoint generations with intervening deltas. These
    # manifests intentionally retain informational ancestry until collection.
    for _cycle in range(8):
        for _ in range(3):
            _commit(observe_project, now)
        _commit(observe_project, now, purpose="compaction")
    head_id = MetricProjectRefresh.objects.get(
        project_id=observe_project.id
    ).head_manifest_id
    head = MetricReadManifest.objects.get(id=head_id)
    later = now + timedelta(days=8)
    kept_pin = MetricGenerationPin.pin_head(
        observe_project.id,
        consumer_kind="request",
        ttl=timedelta(hours=1),
        now=later,
    )
    assert kept_pin.manifest_id == head.id
    collected = 0
    for _ in range(20):
        intent = deletion_protocol.collect_history(
            observe_project.id, now=later, limit=1000
        )
        if intent is None:
            break
        collected += 1
    assert collected > 0
    assert MetricGenerationPin.objects.filter(id=kept_pin.id).exists()
    assert MetricGenerationPin.objects.get(id=kept_pin.id).is_usable(later)
    assert MetricReadManifest.objects.filter(id=head.id).exists()
    assert MetricReadManifest.objects.filter(project_id=observe_project.id).count() <= 2
    assert (
        MetricRefreshAttempt.objects.filter(project_id=observe_project.id).count() <= 2
    )
    assert MetricErasureRequest.objects.filter(scope_kind="history").exists()
    assert not MetricGenerationPin.objects.get(id=kept_pin.id).is_usable(
        later + timedelta(hours=2)
    )


@pytest.mark.django_db(transaction=True)
def test_gc_keeps_recoverable_ancestry_then_collects_expired_and_aborted_attempts(
    observe_project,
):
    now = timezone.now()
    first = _commit(
        observe_project,
        now,
        purpose="compaction",
        retention=timedelta(hours=1),
    )
    second = _commit(observe_project, now, retention=timedelta(hours=1))
    refresh = MetricProjectRefresh.objects.get(project_id=observe_project.id)
    recoverable = MetricRefreshAttempt.prepare(
        observe_project.id,
        epoch=refresh.fencing_epoch,
        token=refresh.lease_token,
        purpose="incremental",
        source_ceilings={},
        metric_versions=[],
        retention=timedelta(days=2),
        now=now,
    )
    aborted = MetricRefreshAttempt.prepare(
        observe_project.id,
        epoch=refresh.fencing_epoch,
        token=refresh.lease_token,
        purpose="incremental",
        source_ceilings={},
        metric_versions=[],
        retention=timedelta(hours=1),
        now=now,
    )
    MetricRefreshAttempt.abort(
        aborted.id,
        epoch=refresh.fencing_epoch,
        token=refresh.lease_token,
        reason="test_aborted",
        now=now,
    )
    head = _commit(
        observe_project,
        now,
        purpose="compaction",
        retention=timedelta(hours=1),
    )
    assert head.parent_id == second.id

    one_day = now + timedelta(days=1)
    deletion_protocol.collect_history(observe_project.id, now=one_day)
    assert MetricReadManifest.objects.filter(id=second.id).exists()
    assert MetricRefreshAttempt.objects.filter(id=recoverable.id).exists()
    assert not MetricRefreshAttempt.objects.filter(id=aborted.id).exists()
    assert not MetricReadManifest.objects.filter(id=first.id).exists()

    three_days = now + timedelta(days=3)
    deletion_protocol.collect_history(observe_project.id, now=three_days)
    assert not MetricRefreshAttempt.objects.filter(id=recoverable.id).exists()
    assert not MetricReadManifest.objects.filter(id=second.id).exists()
    assert MetricReadManifest.objects.filter(id=head.id).exists()


@pytest.mark.django_db(transaction=True)
def test_capture_and_revocation_serialize_and_stale_pin_fails_closed(
    observe_project,
    monkeypatch,
):
    now = timezone.now()
    head = _commit(observe_project, now)
    reached_insert = Event()
    allow_insert = Event()
    original_create = MetricGenerationPin.objects.create

    def paused_create(*args, **kwargs):
        reached_insert.set()
        assert allow_insert.wait(timeout=10), "test did not release capture"
        return original_create(*args, **kwargs)

    monkeypatch.setattr(MetricGenerationPin.objects, "create", paused_create)

    def capture():
        close_old_connections()
        try:
            return MetricGenerationPin.pin_head(
                observe_project.id,
                consumer_kind="request",
                ttl=timedelta(minutes=10),
                now=now,
            ).id
        finally:
            close_old_connections()

    def revoke():
        close_old_connections()
        try:
            return MetricGenerationPin.invalidate(
                observe_project.id,
                reason="erased",
                now=now + timedelta(seconds=1),
            )
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        captured = pool.submit(capture)
        assert reached_insert.wait(timeout=10)
        revoked = pool.submit(revoke)
        # The revoker must block on the same project lock until pin insertion
        # commits; it may not publish a revoked generation and then permit late pins.
        allow_insert.set()
        pin_id = captured.result(timeout=10)
        assert revoked.result(timeout=10) == 1
    stale = MetricGenerationPin.objects.get(id=pin_id)
    assert stale.manifest_id == head.id
    assert not stale.is_usable(now)
    assert MetricReadManifest.objects.get(id=head.id).revoked_at is not None


@pytest.mark.django_db(transaction=True)
def test_pin_captures_active_definition_and_remap_and_definition_revoke_is_durable(
    workspace,
    observe_project,
):
    now = timezone.now()
    metric = _metric(workspace, observe_project)
    version = _version(metric, now)
    head = _commit(observe_project, now, cursors={"remap_cursor_ns": 42})
    pin = MetricGenerationPin.pin_head(
        observe_project.id,
        metric_id=metric.id,
        definition_version_id=version.id,
        consumer_kind="snapshot",
        ttl=timedelta(minutes=10),
        now=now,
    )
    assert pin.manifest_id == head.id
    assert pin.definition_version_id == version.id
    assert pin.remap_revision == 42
    assert (
        MetricGenerationPin.invalidate(
            observe_project.id,
            reason="version_retired",
            definition_version_ids=[version.id],
            now=now + timedelta(seconds=1),
        )
        == 1
    )
    assert not pin.is_usable(now)
    assert MetricReadManifest.objects.get(id=head.id).revoked_at is None
    with pytest.raises(MetricStateConflict, match="expired or inactive definition"):
        MetricGenerationPin.pin_head(
            observe_project.id,
            metric_id=metric.id,
            consumer_kind="request",
            ttl=timedelta(minutes=1),
            now=now,
        )


@pytest.mark.django_db(transaction=True)
def test_revocation_without_existing_pin_fences_late_capture(observe_project):
    now = timezone.now()
    head = _commit(observe_project, now)
    assert (
        MetricGenerationPin.invalidate(
            observe_project.id,
            reason="erased",
            now=now,
        )
        == 0
    )
    with pytest.raises(MetricStateConflict, match="expired generation"):
        MetricGenerationPin.pin_head(
            observe_project.id,
            consumer_kind="request",
            ttl=timedelta(minutes=1),
            now=now,
        )
    assert MetricReadManifest.objects.get(id=head.id).revoked_at == now


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("selector", ["project", "manifest", "definition"])
def test_revocation_fences_previously_verified_compaction(
    workspace, observe_project, monkeypatch, selector
):
    now = timezone.now()
    metric = _metric(workspace, observe_project)
    version = _version(metric, now)
    head = _commit(observe_project, now, purpose="compaction")
    publish = MetricReadManifest.publish
    pending = {}

    def hold_publication(*args, **kwargs):
        pending.update(args=args, kwargs=kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(MetricReadManifest, "publish", hold_publication)
        _commit(observe_project, now, purpose="compaction")
    attempt_id = pending["args"][0]
    assert MetricRefreshAttempt.objects.get(id=attempt_id).state == "verified"
    selectors = {}
    if selector == "manifest":
        selectors["manifest_ids"] = [head.id]
    elif selector == "definition":
        selectors["definition_version_ids"] = [version.id]
    deletion_protocol.revoke_generations(
        observe_project.id,
        reason="erased",
        now=now + timedelta(seconds=1),
        **selectors,
    )
    with pytest.raises(MetricStateConflict):
        publish(*pending["args"], **pending["kwargs"])
    attempt = MetricRefreshAttempt.objects.get(id=attempt_id)
    assert attempt.state == "aborted"
    assert attempt.abort_reason == "generation_revoked"
    assert (
        MetricProjectRefresh.objects.get(project_id=observe_project.id).head_manifest_id
        == head.id
    )

    # A complete checkpoint prepared afresh after revocation may publish. Its
    # worker must rebuild from the post-erasure source, not copy the revoked base.
    rebuilt = _commit(observe_project, now + timedelta(seconds=2), purpose="compaction")
    assert rebuilt.checkpoint_attempt_id != attempt_id
    assert rebuilt.revoked_at is None
    assert deletion_protocol.capture_pin(
        observe_project.id,
        consumer_kind="request",
        ttl=timedelta(minutes=1),
        now=now + timedelta(seconds=3),
    ).is_usable(now + timedelta(seconds=3))


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "parent", ["project", "workspace", "organization", "collector"]
)
@pytest.mark.parametrize("existing_head", [False, True])
def test_parent_delete_recollects_late_pins_and_refresh_dependencies(
    workspace, observe_project, monkeypatch, parent, existing_head
):
    now = timezone.now()
    if existing_head:
        _commit(observe_project, now, purpose="compaction")
    project_id = observe_project.id
    if parent == "workspace":
        root = workspace
    elif parent == "organization":
        root = workspace.organization
    else:
        root = observe_project
    root_model, root_id = type(root), root.id
    collected, dependencies_committed = Event(), Event()
    delete = Collector.delete

    def pause_after_collection(collector):
        collected.set()
        assert dependencies_committed.wait(timeout=15), "writer did not finish"
        return delete(collector)

    monkeypatch.setattr(Collector, "delete", pause_after_collection)

    def delete_parent():
        close_old_connections()
        try:
            if parent == "collector":
                collector = Collector(using="default")
                collector.collect([root_model._base_manager.get(id=root_id)])
                return collector.delete()
            if parent == "organization":
                from tfc.utils.audit import mute_audit_signals

                # Membership post_delete can create a fresh audit FK to the
                # disappearing organization even with Django's unmodified
                # Collector.delete. Isolate that unrelated accounts defect via
                # its existing bulk-operation switch, not a production change.
                # BaseModel instance soft-delete behavior remains unchanged.
                with mute_audit_signals():
                    instance = root_model._base_manager.get(id=root_id)
                    result = models.Model.delete(instance)
                    assert instance.pk is None
                    return result
            return root_model._base_manager.filter(id=root_id).delete()
        finally:
            close_old_connections()

    def create_dependencies():
        close_old_connections()
        try:
            assert collected.wait(timeout=15), "delete did not reach the barrier"
            # Also covers creation of the refresh row itself after collection.
            head = _commit(observe_project, now, purpose="compaction")
            pin = deletion_protocol.capture_pin(
                project_id,
                consumer_kind="snapshot",
                ttl=timedelta(minutes=5),
            )
            return head.id, head.published_by_id, pin.id
        finally:
            dependencies_committed.set()
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        deletion = pool.submit(delete_parent)
        writer = pool.submit(create_dependencies)
        head_id, attempt_id, pin_id = writer.result(timeout=25)
        count, _ = deletion.result(timeout=25)
    assert count > 0
    assert not root_model._base_manager.filter(id=root_id).exists()
    assert not MetricProjectRefresh.objects.filter(project_id=project_id).exists()
    assert not MetricReadManifest.objects.filter(id=head_id).exists()
    assert not MetricRefreshAttempt.objects.filter(id=attempt_id).exists()
    assert not MetricGenerationPin.objects.filter(id=pin_id).exists()
    assert MetricErasureRequest.objects.filter(
        deletion_key=f"project:{project_id}", project_key=project_id, status="queued"
    ).exists()


@pytest.mark.django_db(transaction=True)
def test_quiet_project_collects_expired_pins_in_bounded_batches(observe_project):
    now = timezone.now()
    head = _commit(observe_project, now, purpose="compaction")
    expired = [
        deletion_protocol.capture_pin(
            observe_project.id,
            consumer_kind="request",
            ttl=timedelta(minutes=1),
            now=now,
        )
        for _ in range(3)
    ]
    later = now + timedelta(days=8)
    live = deletion_protocol.capture_pin(
        observe_project.id,
        consumer_kind="export",
        ttl=timedelta(hours=1),
        now=later,
    )
    for remaining in (2, 1, 0):
        assert (
            deletion_protocol.collect_history(observe_project.id, now=later, limit=1)
            is None
        )
        assert (
            MetricGenerationPin.objects.filter(
                id__in=[pin.id for pin in expired]
            ).count()
            == remaining
        )
    assert live.is_usable(later)
    assert MetricReadManifest.objects.filter(id=head.id).exists()
    assert MetricRefreshAttempt.objects.filter(id=head.published_by_id).exists()
    assert not MetricErasureRequest.objects.filter(scope_kind="history").exists()


@pytest.mark.django_db(transaction=True)
def test_gc_drains_durably_revoked_pins_before_collecting_their_manifest(
    workspace, observe_project
):
    now = timezone.now()
    metric = _metric(workspace, observe_project)
    version = _version(metric, now)
    old = _commit(
        observe_project, now, purpose="compaction", retention=timedelta(minutes=1)
    )
    for _ in range(3):
        deletion_protocol.capture_pin(
            observe_project.id,
            metric_id=metric.id,
            consumer_kind="snapshot",
            ttl=timedelta(hours=1),
            now=now,
        )
    head = _commit(
        observe_project, now, purpose="compaction", retention=timedelta(minutes=1)
    )
    # The definition is permanently revoked, while the intersection selector
    # deliberately matches none of the pins. GC must consult durable fences too.
    deletion_protocol.revoke_generations(
        observe_project.id,
        reason="version_retired",
        definition_version_ids=[version.id],
        manifest_ids=[head.id],
        now=now,
    )
    for _ in range(3):
        deletion_protocol.collect_history(
            observe_project.id, now=now + timedelta(minutes=2), limit=1
        )
    assert not MetricGenerationPin.objects.filter(
        project_id=observe_project.id
    ).exists()
    assert not MetricReadManifest.objects.filter(id=old.id).exists()
    assert MetricReadManifest.objects.filter(id=head.id).exists()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("ambient_context", [False, True])
def test_outbox_creation_never_auto_assigns_navigation_fks(
    workspace, observe_project, ambient_context
):
    # Include the global signal even when another test loaded only model code.
    import tfc.utils.signals  # noqa: F401
    from tfc.middleware.workspace_context import (
        clear_workspace_context,
        set_workspace_context,
    )

    clear_workspace_context()
    if ambient_context:
        set_workspace_context(workspace=workspace, organization=workspace.organization)
    try:
        for scope in ("organization", "project"):
            request = deletion_protocol._intent(
                organization_id=workspace.organization_id,
                project_id=observe_project.id if scope == "project" else None,
                scope=scope,
                deletion_key=f"probe:{scope}:{uuid4()}",
            )
            request.refresh_from_db()
            assert request.organization_id is None
            assert request.project_id is None
            assert request.organization_key == workspace.organization_id
            assert request.project_key == (
                observe_project.id if scope == "project" else None
            )
            assert (
                deletion_protocol._intent(
                    organization_id=workspace.organization_id,
                    project_id=observe_project.id if scope == "project" else None,
                    scope=scope,
                    deletion_key=request.deletion_key,
                ).id
                == request.id
            )
    finally:
        clear_workspace_context()
