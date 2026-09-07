"""Crash/replay tests: real coordinator, file locks/journal and ACTIVE state store."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from functools import partial
from threading import Event, Thread
from types import MethodType, SimpleNamespace

import pytest

from tracer.services.clickhouse.v2.property_catalog import (
    publication_journal as subject,
)
from tracer.services.clickhouse.v2.property_catalog.activation import (
    ActivationInventory,
    ActivationManifest,
    ActivationRejected,
    CatalogLifecycleMode,
    ManifestStream,
    PropertyCatalogActivator,
    make_revision_fence,
)
from tracer.services.clickhouse.v2.property_catalog.coordinator import (
    PropertyCatalogCoordinatorError,
    _stream_row,
)
from tracer.services.clickhouse.v2.property_catalog.models import SourceAdapter
from tracer.services.clickhouse.v2.property_catalog.mutation_lock import (
    FileCatalogMutationSerializer,
)
from tracer.services.clickhouse.v2.property_catalog.span_source import (
    stream_requirement,
)
from tracer.services.clickhouse.v2.property_catalog.state_store import (
    ClickHouseCatalogStateStore,
    PropertyCatalogStateConflict,
    _activation_row,
)
from tracer.tests.test_property_catalog_supersession import (
    _complete_checkpoints,
    _Harness,
)


class PublicationHarness:
    def __init__(self, directory):
        self.base = _Harness(directory, with_active=False)
        self.prepared = self.base.prepare()
        self.lease = self.prepared.lease
        self.checkpoints = tuple(
            item.checkpoint for item in _complete_checkpoints(self.prepared)
        )
        planned = {stream.key: stream for stream in self.lease.build_plan.streams}
        self.manifest = ActivationManifest(
            organization_id=self.lease.organization_id,
            workspace_id=self.lease.workspace_id,
            catalog_epoch=self.lease.catalog_epoch,
            catalog_revision=self.lease.catalog_revision,
            build_token=self.lease.build_token,
            projection_version=self.lease.projection_version,
            lifecycle_mode=self.prepared.lifecycle_mode,
            lineage_anchor_revision=self.prepared.lineage_anchor_revision,
            streams=tuple(
                ManifestStream(
                    requirement=stream_requirement(checkpoint),
                    role=planned[checkpoint.key].role,
                )
                for checkpoint in self.checkpoints
            ),
        )
        self.fence = make_revision_fence(
            manifest=self.manifest,
            build_plan=self.lease.build_plan,
            checkpoints=self.checkpoints,
            drain_deadline=self.lease.expires_at,
            fenced_at=self.base.clock(),
        )
        self.base.client.stream_rows.append(
            {
                **self.base.client.inserts[-1][0],
                "status": "fenced",
                "_version": 3,
                "fenced_at": self.base.clock(),
            }
        )
        self.rows, self.sent, self.reads = [], [], []
        self.fail = None
        self.stale_reads = 0
        self.before_send = lambda: None
        owner = self

        class Client:
            catalog_database = owner.base.client.catalog_database

            def query(self, sql, params, *, timeout_ms):
                assert "property_catalog_activations" in sql and timeout_ms > 0
                assert params["organization_id"] == owner.lease.organization_id
                assert params["workspace_id"] == owner.lease.workspace_id
                owner.reads.append(sql)
                if owner.stale_reads:
                    owner.stale_reads -= 1
                    return ()
                return tuple(deepcopy(owner.rows))

            def insert(self, table, rows, *, columns, timeout_ms, deduplication_token):
                assert (
                    table == f"`{self.catalog_database}`.`property_catalog_activations`"
                )
                assert len(rows) == 1 and tuple(rows[0]) == columns
                document = owner.document()
                assert document["phase"] == "armed"
                assert subject._record(
                    document["publication"]["record"]
                ) == owner.store_record(rows[0])
                owner.before_send()
                owner.sent.append((deepcopy(rows[0]), deduplication_token))
                failure, owner.fail = owner.fail, None
                if failure != "before_commit":
                    owner.rows.extend(deepcopy(rows))
                if failure:
                    raise TimeoutError(failure)

        self.client = Client()
        self.restart()

    @staticmethod
    def store_record(row):
        from tracer.services.clickhouse.v2.property_catalog.state_store import (
            _activation,
        )

        return _activation(row)

    def restart(self):
        self.base.restart()
        self.store = ClickHouseCatalogStateStore(
            self.client,
            database=self.client.catalog_database,
            serializer=FileCatalogMutationSerializer(str(self.base.directory)),
        )
        # Qualification still runs against all ten valid synthetic proofs;
        # transport-independent delivery auditing has its existing focused suite.
        self.store.audit_build_plan = lambda **kw: self.audit(**kw)
        self.store.load_checkpoints = lambda _: self.checkpoints
        self.activator = PropertyCatalogActivator(
            self.store, coordinator=self.base.coordinator
        )

    def audit(self, *, build_plan, manifest):
        assert build_plan == self.lease.build_plan and build_plan.matches_manifest(
            manifest
        )

    def activate(self, **changes):
        return self.activator.activate(
            **{
                "manifest": self.manifest,
                "fence": self.fence,
                "inventory": ActivationInventory(2, 1, 7),
                "now": self.base.clock(),
                **changes,
            }
        )

    def workspace_key(self):
        return self.base.coordinator._revision_key_for_lease(self.lease)

    def key(self):
        return subject._key(self.workspace_key(), self.lease.build_lease_sha256)

    def document(self):
        return self.base.coordinator._recovery_journal.load_record(self.key())

    def legacy_marker(self):
        self.base.coordinator._recovery_journal.save_record(
            self.workspace_key() + ":activation",
            {
                "catalog_revision": self.lease.catalog_revision,
                "build_token": self.lease.build_token,
                "build_lease_sha256": self.lease.build_lease_sha256,
            },
        )

    def advance(self):
        """Synthetic qualified successor; source lifecycle itself is out of scope."""
        plan = replace(
            self.lease.build_plan,
            catalog_revision=self.lease.catalog_revision + 1,
            build_token="eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
        )
        self.lease = replace(
            self.lease,
            catalog_revision=plan.catalog_revision,
            build_token=plan.build_token,
            build_plan_json=plan.canonical_json,
            build_lease_sha256=plan.sha256,
        )
        self.checkpoints = tuple(
            replace(
                cp, catalog_revision=plan.catalog_revision, build_token=plan.build_token
            )
            for cp in self.checkpoints
        )
        planned = {stream.key: stream for stream in plan.streams}
        self.manifest = replace(
            self.manifest,
            catalog_revision=plan.catalog_revision,
            build_token=plan.build_token,
            lifecycle_mode=CatalogLifecycleMode.FULL_REPAIR,
            lineage_anchor_revision=plan.catalog_revision,
            streams=tuple(
                ManifestStream(
                    requirement=stream_requirement(cp), role=planned[cp.key].role
                )
                for cp in self.checkpoints
            ),
        )
        self.fence = make_revision_fence(
            manifest=self.manifest,
            build_plan=plan,
            checkpoints=self.checkpoints,
            drain_deadline=self.lease.expires_at,
            fenced_at=self.base.clock(),
        )
        self.base.client.stream_rows.append(
            {
                **_stream_row(
                    lease=self.lease,
                    source_adapter=SourceAdapter.SYSTEM_MANIFEST,
                    producer_stream_id=plan.build_token,
                    envelope_version=0,
                    status="fenced",
                    now=self.base.clock(),
                    drain_deadline=self.lease.expires_at,
                ),
                "fenced_at": self.base.clock(),
            }
        )


@pytest.mark.parametrize("failure", ["before_commit", "lost_ack"])
@pytest.mark.parametrize("stale", [False, True])
def test_uncertain_active_replays_exact_immutable_row(tmp_path, failure, stale):
    h = PublicationHarness(tmp_path)
    h.fail = failure
    with pytest.raises(TimeoutError):
        h.activate()
    original = h.document()
    assert original["phase"] == "armed"
    assert len(original["publication"]["checkpoint_states"]) == 10
    h.restart()
    # Both the activator's lookup and the store's pre-insert lookup can lag.
    h.stale_reads = 2 if stale else 0
    result = h.activate(
        now=h.base.clock() + timedelta(hours=1),
        inventory=ActivationInventory(900, 900, 900),
    )
    assert result.record == subject._record(original["publication"]["record"])
    assert h.document() == {**original, "phase": "resolved"}
    assert len(h.sent) == (1 if failure == "lost_ack" and not stale else 2)
    assert all(sent == h.sent[0] for sent in h.sent)
    # An identical late duplicate remains one logical ACTIVE, preserving cursors.
    h.rows.append(deepcopy(h.sent[0][0]))
    assert h.activate().idempotent


@pytest.mark.parametrize("phase", ["admitted", "prepared", "armed", "resolved"])
@pytest.mark.parametrize("after_persist", [False, True])
def test_crash_at_every_journal_boundary(tmp_path, monkeypatch, phase, after_persist):
    h = PublicationHarness(tmp_path)
    journal = h.base.coordinator._recovery_journal
    save = journal.save_record

    def crash(key, document):
        if key == h.key() and document["phase"] == phase:
            if after_persist:
                save(key, document)
            raise TimeoutError("journal crash")
        save(key, document)

    monkeypatch.setattr(journal, "save_record", crash)
    with pytest.raises(TimeoutError, match="journal crash"):
        h.activate()
    before = h.document()
    assert len(h.sent) == int(phase == "resolved")
    h.restart()
    result = h.activate(now=h.base.clock() + timedelta(seconds=30))
    assert h.document()["phase"] == "resolved" and len(h.sent) == 1
    if before and before["publication"]:
        assert result.record == subject._record(before["publication"]["record"])


def test_resolved_receipt_does_not_resend_on_lagging_read(tmp_path):
    h = PublicationHarness(tmp_path)
    h.activate()
    h.restart()
    h.stale_reads = 1
    with pytest.raises(subject.PublicationJournalError, match="not positively visible"):
        h.activate()
    assert len(h.sent) == 1


@pytest.mark.parametrize(
    "field", ["qualified_at", "live_definition_rows", "activation_sha256"]
)
def test_positive_but_conflicting_active_does_not_resolve_or_resend(tmp_path, field):
    h = PublicationHarness(tmp_path)
    h.fail = "lost_ack"
    with pytest.raises(TimeoutError):
        h.activate()
    h.rows[0][field] = (
        h.base.clock() + timedelta(seconds=1)
        if field == "qualified_at"
        else 999
        if field == "live_definition_rows"
        else "f" * 64
    )
    h.restart()
    with pytest.raises((subject.PublicationJournalError, ActivationRejected)):
        h.activate()
    assert len(h.sent) == 1 and h.document()["phase"] == "armed"


def test_store_detects_conflict_appearing_after_stale_activator_read(tmp_path):
    h = PublicationHarness(tmp_path)
    h.fail = "lost_ack"
    with pytest.raises(TimeoutError):
        h.activate()
    h.rows[0]["value_rows"] = 800
    h.stale_reads = 1
    with pytest.raises(PropertyCatalogStateConflict, match="another immutable state"):
        h.activate()
    assert len(h.sent) == 1 and h.document()["phase"] == "armed"


@pytest.mark.parametrize("visible", [False, True])
def test_legacy_marker_is_positive_evidence_only(tmp_path, visible):
    h = PublicationHarness(tmp_path)
    # Obtain a genuine qualified row independently; this journal is not the
    # legacy workspace's missing original payload.
    other = tmp_path / "reference"
    other.mkdir()
    record = PublicationHarness(other).activate().record
    if visible:
        h.rows = [_activation_row(record)]
    h.legacy_marker()
    if visible:
        assert h.activate(now=h.base.clock() + timedelta(hours=2)).record == record
    else:
        for _ in range(2):
            with pytest.raises(ActivationRejected, match="positive_evidence"):
                h.activate()
            h.restart()
    assert h.sent == [] and h.document() is None


@pytest.mark.parametrize(
    "bad", ["phase", "database", "lease", "record", "checkpoint", "extra"]
)
def test_checksum_correct_malformed_journal_is_not_replayed(tmp_path, bad):
    h = PublicationHarness(tmp_path)
    h.fail = "before_commit"
    with pytest.raises(TimeoutError):
        h.activate()
    doc = deepcopy(h.document())
    if bad == "phase":
        doc["phase"] = "invented"
    elif bad == "database":
        doc["database"] = "other_database"
    elif bad == "lease":
        doc["lease"]["issued_at"] = doc["lease"]["expires_at"]
    elif bad == "record":
        doc["publication"]["record"]["updated_at"] = "2026-01-01T00:00:00+00:00"
    elif bad == "checkpoint":
        doc["publication"]["checkpoint_states"] = []
    else:
        doc["extra"] = True
    h.base.coordinator._recovery_journal.save_record(h.key(), doc)
    h.restart()
    with pytest.raises(subject.PublicationJournalError):
        h.activate()
    assert len(h.sent) == 1


def test_journal_payload_is_bounded_before_write(tmp_path):
    h = PublicationHarness(tmp_path)
    h.activate()
    doc = deepcopy(h.document())
    doc["padding"] = "x" * subject.MAX_PUBLICATION_BYTES
    with pytest.raises(subject.PublicationJournalError):
        subject._validate(doc)


@pytest.mark.parametrize("uncertain", [False, True])
def test_successor_requires_exact_visible_predecessor_and_preserves_old_row(
    tmp_path, uncertain
):
    h = PublicationHarness(tmp_path)
    if uncertain:
        h.fail = "lost_ack"
        with pytest.raises(TimeoutError):
            h.activate()
    else:
        h.activate()
    old_key, old_document, old_row = h.key(), h.document(), deepcopy(h.rows[0])
    h.advance()
    h.stale_reads = 1
    with pytest.raises(subject.PublicationJournalError, match="not positively visible"):
        h.activate()
    assert len(h.sent) == 1
    result = h.activate()
    assert result.record.activation_sequence == 2
    assert (
        h.document()["publication"]["previous_active"]
        == old_document["publication"]["record"]
    )
    assert h.base.coordinator._recovery_journal.load_record(old_key) == {
        **old_document,
        "phase": "resolved",
    }
    assert h.rows[0] == old_row and len(h.sent) == 2


def test_successor_refuses_changed_predecessor_receipt(tmp_path):
    h = PublicationHarness(tmp_path)
    h.activate()
    h.advance()
    h.rows[0]["live_definition_rows"] += 1
    with pytest.raises(subject.PublicationJournalError, match="conflicts with ACTIVE"):
        h.activate()
    assert len(h.sent) == 1


def test_prepared_successor_refuses_same_sequence_but_different_head(tmp_path):
    h = PublicationHarness(tmp_path)
    h.activate()
    h.advance()
    h.fail = "before_commit"
    with pytest.raises(TimeoutError):
        h.activate()
    h.rows[0]["qualified_at"] += timedelta(seconds=1)
    with pytest.raises(
        subject.PublicationJournalError, match="previous ACTIVE head changed"
    ):
        h.activate()
    assert len(h.sent) == 2 and h.document()["phase"] == "armed"


def test_conflicting_revision_in_history_is_not_hidden_by_exact_target(tmp_path):
    h = PublicationHarness(tmp_path)
    h.activate()
    conflicting = {
        **h.rows[0],
        "build_token": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "activation_sequence": 2,
        "_version": 2,
    }
    h.rows.append(conflicting)
    with pytest.raises(ActivationRejected, match="activation_history_conflicts"):
        h.activate()
    assert len(h.sent) == 1


def test_reconstructed_fence_diagnostic_time_does_not_change_replay(tmp_path):
    h = PublicationHarness(tmp_path)
    h.fail = "before_commit"
    with pytest.raises(TimeoutError):
        h.activate()
    original = h.document()
    h.fence = replace(h.fence, fenced_at=h.lease.expires_at)
    h.restart()
    h.activate()
    assert h.document() == {**original, "phase": "resolved"}


def test_symlink_journal_is_not_followed(tmp_path):
    h = PublicationHarness(tmp_path)
    target = tmp_path / "not-a-journal"
    target.touch()
    h.base.coordinator._recovery_journal._path(h.key()).symlink_to(target)
    with pytest.raises(PropertyCatalogCoordinatorError, match="cannot read"):
        h.activate()
    assert h.sent == [] and target.stat().st_size == 0


@pytest.mark.parametrize("outcome", [False, None, "timeout"])
def test_completion_probe_unknown_leaves_armed_even_after_positive_active(
    tmp_path, outcome
):
    h = PublicationHarness(tmp_path)
    seen = []

    def incomplete(record):
        seen.append(record)
        if outcome == "timeout":
            raise TimeoutError("replica completion unknown")
        return outcome

    h.activator = PropertyCatalogActivator(
        h.store, coordinator=h.base.coordinator, completion_probe=incomplete
    )
    for _ in range(2):
        with pytest.raises((ActivationRejected, TimeoutError)):
            h.activate()
        assert h.document()["phase"] == "armed"
    assert len(h.sent) == 1 and len(seen) == 2 and seen[0] == seen[1]
    h.restart()
    h.activator = PropertyCatalogActivator(
        h.store,
        coordinator=h.base.coordinator,
        completion_probe=lambda record: record == seen[0],
    )
    assert h.activate().idempotent
    assert h.document()["phase"] == "resolved" and len(h.sent) == 1


def test_successor_does_not_resolve_predecessor_without_completion_barrier(tmp_path):
    h = PublicationHarness(tmp_path)
    h.fail = "lost_ack"
    with pytest.raises(TimeoutError):
        h.activate()
    old_key = h.key()
    h.advance()
    h.activator = PropertyCatalogActivator(
        h.store, coordinator=h.base.coordinator, completion_probe=lambda _: False
    )
    with pytest.raises(ActivationRejected, match="completion_unconfirmed"):
        h.activate()
    assert h.base.coordinator._recovery_journal.load_record(old_key)["phase"] == "armed"
    assert len(h.sent) == 1 and h.document()["phase"] == "admitted"


def test_runtime_does_not_retire_or_ack_repair_before_completion(tmp_path, monkeypatch):
    from tracer.services.clickhouse.v2.property_catalog import dev_runtime
    from tracer.services.clickhouse.v2.property_catalog.durable_lifecycle import (
        LifecycleRunMode,
    )

    h = PublicationHarness(tmp_path)
    h.activate()
    h.advance()
    steps = []
    ready = [False]
    monkeypatch.setattr(
        dev_runtime,
        "PropertyCatalogActivator",
        partial(
            PropertyCatalogActivator,
            completion_probe=lambda record: record.activation_sequence == 1 or ready[0],
        ),
    )
    execution = SimpleNamespace(
        manifest=h.manifest,
        fence=h.fence,
        activation=None,
        lease=h.lease,
        prepared=SimpleNamespace(
            mode=LifecycleRunMode.FULL_REPAIR,
            scope=h.prepared.scope,
            cutoffs=h.prepared.cutoffs,
        ),
    )

    def active(_):
        record = h.store_record(h.rows[-1])
        return SimpleNamespace(
            **{name: getattr(record, name) for name in record.__dataclass_fields__},
            build_plan=h.lease.build_plan,
        )

    runtime = SimpleNamespace(
        _source_capture=None,
        _validate_mutation_request=lambda *_: None,
        _refresh_project_tenant_authorization=lambda: SimpleNamespace(
            authorization_contract_sha256="a" * 64
        ),
        _require_execution=lambda: execution,
        _activation_inventory=lambda _: ActivationInventory(2, 1, 7),
        _notice_historical_source_change=lambda _: None,
        _notice_source_part_changes=lambda _: None,
        reconcile_reader_selection=lambda **kw: steps.append(
            "prepare" if kw else "select"
        ),
        _load_latest_active_retirement=active,
        _publish_producer_retirement=lambda *_: steps.append("retire"),
        _candidate_repair=SimpleNamespace(
            acknowledge_covered=lambda **_: steps.append("candidate_ack")
        ),
        _source_repair=SimpleNamespace(
            acknowledge_replacement=lambda **_: steps.append("source_ack")
        ),
        _authorized_build_binding_sha256=None,
        coordinator=h.base.coordinator,
        state_store=h.store,
        now=h.base.clock,
    )
    activate = MethodType(
        dev_runtime.CheckedInPropertyCatalogDevRuntime.activate, runtime
    )
    runtime._activator = MethodType(
        dev_runtime.CheckedInPropertyCatalogDevRuntime._activator, runtime
    )
    for _ in range(2):
        with pytest.raises(ActivationRejected, match="completion_unconfirmed"):
            activate(object())
        assert execution.activation is None and h.document()["phase"] == "armed"
        assert set(steps) == {"prepare"}
    assert len(h.sent) == 2  # Initial plus successor; no resend on positive retry.
    ready[0] = True
    assert activate(object())["activated"]
    assert steps[-4:] == ["retire", "select", "candidate_ack", "source_ack"]
    assert h.document()["phase"] == "resolved"


def test_publication_session_cannot_escape_workspace_lock(tmp_path):
    h = PublicationHarness(tmp_path)
    for activated in (False, True):
        if activated:
            h.activate()
        with pytest.raises(PropertyCatalogCoordinatorError, match="activation lock"):
            h.base.coordinator.publication_session(fence=h.fence)


def test_concurrent_activation_recovery_has_one_immutable_send(tmp_path):
    h = PublicationHarness(tmp_path)
    entered, release = Event(), Event()
    h.before_send = lambda: (entered.set(), release.wait(5))
    results, errors = [], []

    def run():
        try:
            results.append(h.activate())
        except BaseException as exc:
            errors.append(exc)

    first, second = Thread(target=run), Thread(target=run)
    first.start()
    try:
        assert entered.wait(5)
        second.start()
    finally:
        release.set()
        first.join(5)
        if second.ident:
            second.join(5)
    assert not first.is_alive() and not second.is_alive() and not errors
    assert len(h.sent) == 1 and len(results) == 2
    assert results[0].record == results[1].record
