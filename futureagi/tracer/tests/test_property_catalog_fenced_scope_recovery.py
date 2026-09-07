"""Frozen-scope recovery with real runtime/activator/journal; no source services."""

from copy import deepcopy
from dataclasses import replace
from datetime import timedelta

import pytest

from tracer.services.clickhouse.v2.property_catalog import dev_runtime as runtime
from tracer.services.clickhouse.v2.property_catalog.activation import (
    ActivationInventory,
    ActivationRejected,
    PropertyCatalogActivator,
    make_revision_fence,
)
from tracer.services.clickhouse.v2.property_catalog.coordinator import _stream_row
from tracer.services.clickhouse.v2.property_catalog.durable_lifecycle import (
    DurableLifecycleError,
    LifecycleRunMode,
    ReservationStatus,
)
from tracer.services.clickhouse.v2.property_catalog.models import SourceAdapter
from tracer.services.clickhouse.v2.property_catalog.publication_journal import (
    PublicationJournalError,
    _record,
)
from tracer.services.clickhouse.v2.property_catalog.publisher import (
    SharedCatalogDeadline,
)
from tracer.tests import test_property_catalog_dev_rollout as dev
from tracer.tests import test_property_catalog_durable_lifecycle as durable
from tracer.tests.test_property_catalog_publication_journal import PublicationHarness


class Forbidden:
    def __getattr__(self, name):
        pytest.fail(f"historical recovery accessed a source/publisher/repair: {name}")


class TransportIndependentCatalog(Forbidden):
    # Mode inspection is allowed; source/query/insert access remains forbidden.
    # Native transport completion has a separate real-journal fixture.
    _durable_writer = None


class RecoveryHarness(PublicationHarness):
    def __init__(self, directory, *, projects=()):
        super().__init__(directory)
        self.base.lifecycle._new_build_token = lambda: durable.TOKEN_D
        self.prepared_by_revision = {self.lease.catalog_revision: self.prepared}
        self.base.reader.checkpoints[self.lease.build_token] = (
            durable._complete_checkpoints(self.prepared)
        )
        self.base.reader.load_latest_active = self.active_evidence
        self.scope = replace(self.prepared.scope, project_ids=projects)
        self.inventory = dev._project_bindings(projects)
        self.probes, self.selections = [], []
        self.runtime = self.make_runtime()

    def active_evidence(self, scope):
        assert scope.organization_id == self.lease.organization_id
        assert scope.workspace_id == self.lease.workspace_id
        if not self.rows:
            return None
        record = self.store_record(self.rows[-1])
        state = durable._State()
        state.activate(
            self.prepared_by_revision[record.catalog_revision], at=record.qualified_at
        )
        # Model the control reader's full exact active evidence. The production
        # reader separately audits its reservation/checkpoint/lineage joins.
        anchor = state.active.lineage_anchor
        if anchor.catalog_revision == record.catalog_revision:
            anchor = replace(
                anchor,
                activation_sha256=record.activation_sha256,
                activation_sequence=record.activation_sequence,
            )
        active = replace(
            state.active,
            activation_sha256=record.activation_sha256,
            source_manifest_sha256=record.source_manifest_sha256,
            activation_sequence=record.activation_sequence,
            lineage_anchor=anchor,
        )
        self.base.client.active_rows = [deepcopy(self.rows[-1])]
        return active

    def make_runtime(self):
        config = replace(
            dev._unit_runtime_config(str(self.base.directory)),
            catalog_epoch=self.lease.catalog_epoch,
            projection_version=self.lease.projection_version,
            project_ids=self.scope.project_ids,
            span_since=durable.INITIAL_SINCE,
            span_until=durable.INITIAL_UNTIL,
        )
        request = dev._request(execute=True)

        def probe(projects, identity):
            self.probes.append(projects)
            assert projects == self.scope.project_ids
            assert identity == dev._provenance_observation().postgres
            return self.inventory

        return runtime.CheckedInPropertyCatalogDevRuntime(
            config=config,
            bound_request=request,
            provenance=dev._provenance_evidence(config=config, request=request),
            schema_client=Forbidden(),
            catalog_client=TransportIndependentCatalog(),
            source_client=Forbidden(),
            serializer=Forbidden(),
            deadline=SharedCatalogDeadline(wall_ms=30_000),
            state_store=self.store,
            coordinator=self.base.coordinator,
            lifecycle=self.base.lifecycle,
            lifecycle_state=self.base.reader,
            span_reader=Forbidden(),
            hot_proof_source=Forbidden(),
            producer_retirement_sink=Forbidden(),
            now=self.base.clock,
            new_build_token=lambda: pytest.fail(
                "historical publication allocated a lease"
            ),
            project_tenant_binding_probe=probe,
            _factory_authority=runtime._RUNTIME_FACTORY_AUTHORITY,
            _reader_activation_callback=lambda _, **kw: self.selections.append(kw),
        )

    def recover(self):
        return self.runtime._recover_fenced_scope_drift(self.scope)

    def next_fenced_revision(self, mode):
        self.base.clock.current += timedelta(minutes=2)
        self.prepared = self.base.lifecycle.prepare(
            scope=self.prepared.scope,
            mode=mode,
            configured_bounds=durable._bounds(),
        )
        assert self.prepared.mode is mode
        self.lease = self.prepared.lease
        self.prepared_by_revision[self.lease.catalog_revision] = self.prepared
        resumes = durable._complete_checkpoints(self.prepared)
        self.base.reader.checkpoints[self.lease.build_token] = resumes
        self.checkpoints = tuple(value.checkpoint for value in resumes)
        self.manifest = runtime._frozen_activation_manifest(
            lease=self.lease,
            lifecycle_mode=self.prepared.lifecycle_mode,
            lineage_anchor_revision=self.prepared.lineage_anchor_revision,
            checkpoints=self.checkpoints,
        )
        self.fence = make_revision_fence(
            manifest=self.manifest,
            build_plan=self.lease.build_plan,
            checkpoints=self.checkpoints,
            drain_deadline=self.lease.expires_at,
            fenced_at=self.base.clock(),
        )
        row = _stream_row(
            lease=self.lease,
            source_adapter=SourceAdapter.SYSTEM_MANIFEST,
            producer_stream_id=self.lease.build_token,
            envelope_version=0,
            status="fenced",
            now=self.base.clock(),
            drain_deadline=self.lease.expires_at,
        )
        self.base.client.stream_rows.append(
            {**row, "_version": 3, "fenced_at": self.base.clock()}
        )
        self.base.lifecycle._new_build_token = lambda: (
            "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
        )
        self.runtime = self.make_runtime()


@pytest.mark.parametrize("failure", ["before_commit", "lost_ack"])
@pytest.mark.parametrize("stale", [False, True])
@pytest.mark.parametrize("projects", [(), (durable.PROJECT_A,)])
def test_uncertain_scope_drift_resolves_exact_without_old_sources(
    tmp_path, failure, stale, projects
):
    h = RecoveryHarness(tmp_path, projects=projects)
    h.fail = failure
    with pytest.raises(TimeoutError):
        h.activate()
    intent = h.document()
    h.base.clock.current += timedelta(hours=1)
    h.restart()
    # The reusable test harness resets its deterministic token iterator; real
    # runtimes generate a fresh UUID for the post-recovery replacement.
    h.base.lifecycle._new_build_token = lambda: durable.TOKEN_D
    h.runtime = h.make_runtime()
    h.stale_reads = 2 if stale else 0
    assert h.recover()
    assert h.document() == {**intent, "phase": "resolved"}
    assert all(row == h.sent[0] for row in h.sent)
    assert h.runtime._execution is None
    assert h.runtime._authorized_revision_proof is None
    assert h.runtime._candidate_repair is None and h.runtime._source_repair is None
    assert h.probes == [projects, projects]
    assert len(h.selections) == 1 and "initial_target" in h.selections[0]
    # Only after completion can current-scope lifecycle preparation progress.
    fresh = h.base.lifecycle.prepare(
        scope=h.scope, mode=LifecycleRunMode.AUTO, configured_bounds=durable._bounds()
    )
    assert fresh.mode is LifecycleRunMode.FULL_REPAIR
    assert fresh.lease.build_plan.source_scope.project_ids == projects
    assert fresh.lease.catalog_revision > h.lease.catalog_revision
    assert len(fresh.streams) == 10
    assert not h.base.coordinator._recovery_journal.is_revoked(
        h.workspace_key(), h.lease.build_lease_sha256
    )


@pytest.mark.parametrize("phase", [None, "admitted", "prepared", "armed", "resolved"])
def test_fenced_restart_at_each_forward_phase(tmp_path, monkeypatch, phase):
    h = RecoveryHarness(tmp_path)
    journal = h.base.coordinator._recovery_journal
    save = journal.save_record
    if phase is not None:

        def crash(key, document):
            save(key, document)
            if key == h.key() and document["phase"] == phase:
                raise TimeoutError("crash after fsync")

        monkeypatch.setattr(journal, "save_record", crash)
        with pytest.raises(TimeoutError):
            h.activate()
        monkeypatch.setattr(journal, "save_record", save)
    original = h.document()
    inventories = []

    def catalog_only(_, manifest, context, prior):
        inventories.append(manifest)
        assert context.project_ids == h.lease.build_plan.source_scope.project_ids
        assert prior is None and context.catalog_revision == h.lease.catalog_revision
        return ActivationInventory(2, 1, 7)

    monkeypatch.setattr(
        runtime.CheckedInPropertyCatalogDevRuntime,
        "_catalog_activation_inventory",
        catalog_only,
    )
    assert h.recover()
    assert h.document()["phase"] == "resolved"
    assert len(inventories) == (1 if phase in {None, "admitted"} else 0)
    if original is not None and original["publication"] is not None:
        assert h.document()["publication"] == original["publication"]


@pytest.mark.parametrize("positive", [False, True])
def test_legacy_marker_is_positive_only_no_synthesized_resend(tmp_path, positive):
    h = RecoveryHarness(tmp_path)
    if positive:
        record = h.activate().record
        # Simulate an installation predating exact publication receipts.
        h.base.coordinator._recovery_journal._path(h.key()).unlink()
    h.legacy_marker()
    if positive:
        assert h.recover()
        assert h.store_record(h.rows[-1]) == record
    else:
        with pytest.raises(
            ActivationRejected, match="legacy_activation_requires_positive_evidence"
        ):
            h.recover()
    assert len(h.sent) == int(positive) and h.document() is None


@pytest.mark.parametrize("outcome", [False, None, "timeout"])
def test_positive_row_cannot_cross_unconfirmed_completion_barrier(
    tmp_path, monkeypatch, outcome
):
    h = RecoveryHarness(tmp_path)
    h.fail = "lost_ack"
    with pytest.raises(TimeoutError):
        h.activate()
    ready = [False]

    def completion(_):
        if ready[0]:
            return True
        if outcome == "timeout":
            raise TimeoutError("completion unknown")
        return outcome

    monkeypatch.setattr(
        runtime.CheckedInPropertyCatalogDevRuntime,
        "_activator",
        lambda self, guard: PropertyCatalogActivator(
            self.state_store, coordinator=guard, completion_probe=completion
        ),
    )
    for _ in range(2):
        with pytest.raises((ActivationRejected, TimeoutError)):
            h.runtime._prepare_revision(LifecycleRunMode.INITIAL_BACKFILL)
        assert h.runtime._execution is None and h.document()["phase"] == "armed"
    assert len(h.sent) == 1
    ready[0] = True
    assert h.recover() and h.document()["phase"] == "resolved"


@pytest.mark.parametrize(
    "invalid",
    [
        None,
        (),
        "foreign_workspace",
        "foreign_org",
        "bad_binding",
        "duplicate",
        "null_inventory",
    ],
)
def test_current_workspace_eligibility_required_before_recovery(tmp_path, invalid):
    h = RecoveryHarness(tmp_path)
    h.fail = "lost_ack"
    with pytest.raises(TimeoutError):
        h.activate()
    if invalid == "foreign_workspace":
        invalid = replace(h.inventory, workspace_id=dev.OTHER_WORKSPACE)
    elif invalid == "foreign_org":
        invalid = replace(h.inventory, organization_id=dev.OTHER_ORG)
    elif invalid == "bad_binding":
        invalid = dev._project_bindings((dev.PROJECT,), organization_id=dev.OTHER_ORG)
    elif invalid == "duplicate":
        invalid = dev._project_bindings((dev.PROJECT, dev.PROJECT))
    elif invalid == "null_inventory":
        invalid = replace(h.inventory, bindings=None)
    h.inventory = invalid
    with pytest.raises((runtime.PropertyCatalogDevRuntimeError, TypeError)):
        h.recover()
    assert h.selections == [] and h.document()["phase"] == "armed" and len(h.sent) == 1


def test_workspace_removed_at_publication_boundary_leaves_receipt_unresolved(
    tmp_path, monkeypatch
):
    h = RecoveryHarness(tmp_path)
    h.fail = "lost_ack"
    with pytest.raises(TimeoutError):
        h.activate()
    load = h.base.lifecycle.load_fenced_scope_drift

    def drift(scope):
        frozen = load(scope)
        h.inventory = None
        return frozen

    monkeypatch.setattr(h.base.lifecycle, "load_fenced_scope_drift", drift)
    with pytest.raises(
        runtime.PropertyCatalogDevRuntimeError,
        match="active workspace inventory required",
    ):
        h.recover()
    assert h.document()["phase"] == "armed" and h.selections == []


@pytest.mark.parametrize(
    "fault", ["missing", "poison", "foreign", "delivery", "manifest", "receipt"]
)
def test_frozen_evidence_corruption_refuses_publication(tmp_path, fault):
    h = RecoveryHarness(tmp_path)
    h.fail = "before_commit"
    with pytest.raises(TimeoutError):
        h.activate()
    resumes = list(h.base.reader.checkpoints[h.lease.build_token])
    if fault == "missing":
        resumes.pop()
    elif fault in {"poison", "foreign", "delivery"}:
        changes = {
            "poison": {"poison_count": 1},
            "foreign": {"workspace_id": dev.OTHER_WORKSPACE},
            "delivery": {"terminal_payload_sha256": "d" * 64},
        }[fault]
        resumes[0] = replace(
            resumes[0], checkpoint=replace(resumes[0].checkpoint, **changes)
        )
    elif fault == "manifest":
        resumes[0] = replace(
            resumes[0],
            checkpoint=replace(resumes[0].checkpoint, source_digest="d" * 64),
        )
    else:
        document = h.document()
        document["publication"]["record"]["value_rows"] += 1
        h.base.coordinator._recovery_journal.save_record(h.key(), document)
    h.base.reader.checkpoints[h.lease.build_token] = tuple(resumes)
    with pytest.raises(
        (
            DurableLifecycleError,
            ActivationRejected,
            PublicationJournalError,
            runtime.PropertyCatalogDevRuntimeError,
        )
    ):
        h.recover()
    assert len(h.sent) == 1


def test_refreshed_project_auth_still_required_before_replacement(tmp_path):
    h = RecoveryHarness(tmp_path)
    h.fail = "lost_ack"
    with pytest.raises(TimeoutError):
        h.activate()
    # Workspace is still eligible, but factory inventory became stale again.
    # Internal old publication can finish; no new source capability may escape.
    h.inventory = dev._project_bindings((dev.PROJECT,))
    with pytest.raises(runtime.PropertyCatalogDevRuntimeError, match="missing exact"):
        h.runtime._prepare_revision(LifecycleRunMode.AUTO)
    assert h.document()["phase"] == "resolved"
    assert h.runtime._execution is None and h.runtime._authorized_revision_proof is None
    assert _record(h.document()["publication"]["record"]) == h.store_record(h.rows[-1])


def test_recovery_refuses_foreign_requested_scope_and_status(tmp_path):
    h = RecoveryHarness(tmp_path)
    with pytest.raises(
        runtime.PropertyCatalogDevRuntimeError, match="changed runtime scope"
    ):
        h.runtime._recover_fenced_scope_drift(
            replace(h.scope, workspace_id=dev.OTHER_WORKSPACE)
        )
    assert h.probes == []
    object.__setattr__(h.runtime, "bound_request", dev._request(status=True))
    with pytest.raises(runtime.PropertyCatalogDevRuntimeError, match="execute mode"):
        h.recover()
    assert h.document() is None


def test_unchanged_and_unfenced_scopes_are_not_historically_published(tmp_path):
    h = RecoveryHarness(tmp_path)
    assert h.base.lifecycle.load_fenced_scope_drift(h.prepared.scope) is None
    h.base.client.stream_rows[-1]["status"] = ReservationStatus.DRAINING.value
    assert h.base.lifecycle.load_fenced_scope_drift(h.scope) is None
    assert h.sent == []


def test_empty_to_project_scope_recovers_all_ten_zero_streams(tmp_path, monkeypatch):
    from tracer.tests import test_property_catalog_supersession as supersession

    monkeypatch.setattr(
        supersession, "_scope", lambda: replace(durable._scope(), project_ids=())
    )
    h = RecoveryHarness(tmp_path, projects=(dev.PROJECT,))
    assert h.lease.build_plan.source_scope.project_ids == ()
    assert len(h.checkpoints) == 10
    assert all(
        cp.terminal and cp.source_count == 0 and cp.delivery_count == 1
        for cp in h.checkpoints
    )
    h.fail = "lost_ack"
    with pytest.raises(TimeoutError):
        h.activate()
    assert h.recover()
    assert h.probes == [(dev.PROJECT,), (dev.PROJECT,)]
    fresh = h.base.lifecycle.prepare(
        scope=h.scope, mode=LifecycleRunMode.AUTO, configured_bounds=durable._bounds()
    )
    assert fresh.mode is LifecycleRunMode.FULL_REPAIR
    assert fresh.scope.project_ids == (dev.PROJECT,) and len(fresh.streams) == 10


def test_runtime_rechecks_pg_and_dispatches_full_repair_after_initial_completion(
    tmp_path, monkeypatch
):
    h = RecoveryHarness(tmp_path)
    h.fail = "lost_ack"
    with pytest.raises(TimeoutError):
        h.activate()
    observations = []

    class ReplacementBoundary(Exception):
        pass

    def prepare(**kwargs):
        observations.append(kwargs)
        assert h.document()["phase"] == "resolved"
        assert h.probes == [(), (), ()]
        raise ReplacementBoundary

    monkeypatch.setattr(h.base.lifecycle, "prepare", prepare)
    with pytest.raises(ReplacementBoundary):
        h.runtime._prepare_revision(LifecycleRunMode.INITIAL_BACKFILL)
    assert len(observations) == 1
    assert observations[0]["mode"] is LifecycleRunMode.FULL_REPAIR
    assert observations[0]["scope"] == h.scope
    assert h.runtime._execution is None and h.runtime._authorized_revision_proof is None


@pytest.mark.parametrize("field", ["value_rows", "qualified_at", "updated_at"])
def test_positive_conflicting_active_does_not_resolve_or_allocate(tmp_path, field):
    h = RecoveryHarness(tmp_path)
    h.fail = "lost_ack"
    with pytest.raises(TimeoutError):
        h.activate()
    h.rows[0][field] += 1 if field == "value_rows" else timedelta(seconds=1)
    with pytest.raises(ActivationRejected, match="publication_intent_conflicts"):
        h.runtime._prepare_revision(LifecycleRunMode.AUTO)
    assert h.document()["phase"] == "armed" and h.runtime._execution is None
    assert len(h.sent) == 1


def test_positive_transport_completion_with_stale_control_reread_does_not_allocate(
    tmp_path, monkeypatch
):
    h = RecoveryHarness(tmp_path)
    h.fail = "lost_ack"
    with pytest.raises(TimeoutError):
        h.activate()
    read = h.base.reader.load_latest_active
    calls = []

    def stale(scope):
        calls.append(scope)
        return read(scope) if len(calls) == 1 else None

    monkeypatch.setattr(h.base.reader, "load_latest_active", stale)
    with pytest.raises(runtime.PropertyCatalogDevRuntimeError, match="durably reread"):
        h.runtime._prepare_revision(LifecycleRunMode.AUTO)
    assert h.document()["phase"] == "resolved" and h.runtime._execution is None
    monkeypatch.setattr(h.base.reader, "load_latest_active", read)
    assert h.recover() and len(h.sent) == 1


@pytest.mark.parametrize(
    "mode", [LifecycleRunMode.INCREMENTAL, LifecycleRunMode.FULL_REPAIR]
)
@pytest.mark.parametrize("failure", ["before_commit", "lost_ack"])
@pytest.mark.parametrize("stale", [False, True])
def test_existing_catalog_scope_drift_preserves_exact_incremental_or_snapshot_lineage(
    tmp_path, mode, failure, stale
):
    h = RecoveryHarness(tmp_path)
    first = h.activate().record
    initial_row = deepcopy(h.rows[0])
    h.next_fenced_revision(mode)
    h.fail = failure
    with pytest.raises(TimeoutError):
        h.activate()
    document = h.document()
    h.base.clock.current += timedelta(hours=1)
    h.stale_reads = 1 if stale else 0
    if stale:
        # A missing predecessor is not permission to change lineage or resend.
        with pytest.raises(
            PublicationJournalError,
            match="previous publication is not positively visible",
        ):
            h.recover()
        assert h.document() == document
    assert h.recover()
    assert h.rows[0] == initial_row
    assert h.document() == {**document, "phase": "resolved"}
    record = _record(document["publication"]["record"])
    assert record.activation_sequence == first.activation_sequence + 1
    assert record.lineage_anchor_revision == (
        first.lineage_anchor_revision
        if mode is LifecycleRunMode.INCREMENTAL
        else h.lease.catalog_revision
    )
    assert h.selections == []  # Existing catalogs never acquire a new INITIAL FOLLOW.
    fresh = h.base.lifecycle.prepare(
        scope=h.scope, mode=LifecycleRunMode.AUTO, configured_bounds=durable._bounds()
    )
    assert fresh.mode is LifecycleRunMode.FULL_REPAIR
    assert fresh.scope.project_ids == () and len(fresh.streams) == 10
