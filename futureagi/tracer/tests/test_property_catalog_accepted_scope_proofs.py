"""Offline accepted-scope safety; no source/database writes or merge claims."""

from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

import pytest

from tracer.services.clickhouse.v2.property_catalog import (
    accepted_scope_proofs as subject,
)
from tracer.services.clickhouse.v2.property_catalog.coordinator import (
    PropertyCatalogCoordinatorError,
)
from tracer.services.clickhouse.v2.property_catalog.source_parts import (
    notice_part_changes,
)
from tracer.services.clickhouse.v2.property_catalog.source_repair import (
    pending_source_repair,
    record_source_repair,
)
from tracer.services.clickhouse.v2.property_catalog.span_source import (
    CanonicalSpanSourceReader,
    FrozenSpanSource,
    SpanAggregateProof,
    SpanAuditComponent,
    combine_audit_components,
)
from tracer.tests.test_property_catalog_span_source import _FixedDeadline

pytestmark = pytest.mark.unit
ORG, WORKSPACE, PROJECT, OTHER = (str(UUID(int=i)) for i in range(1, 5))
SOURCE = subject.SourceIdentity(
    str(UUID(int=5)), "source-1", "ReplacingMergeTree", "", ""
)
T0 = datetime(2026, 9, 1, 12, 3, 4, tzinfo=UTC)
T1, T2, NOW = (
    T0 + timedelta(minutes=2),
    T0 + timedelta(minutes=4),
    T0 + timedelta(minutes=5),
)
OLD = (("20260901_1_1_0", "abc"),)
STARTED = (*OLD, ("20260901_2_2_0", "def"))
MERGED = (("20260901_1_2_1", "123"),)
POST = (*MERGED, ("20260901_3_3_0", "456"))
CONTRACT = "c" * 64


def scope(path):
    return {
        "state_directory": str(path),
        "organization_id": ORG,
        "workspace_id": WORKSPACE,
        "source_database": "default",
    }


def component(
    start=T0, end=T1, *, count=1, value=9, conflicts=0, project=PROJECT, generation=7
):
    return SpanAuditComponent(
        project,
        start,
        end,
        SpanAggregateProof(count, (value,) * 4, (value,) * 4, conflicts),
        generation,
    )


def build(revision=1, *, prior=None, start=T0, end=T1, projects=None):
    return {
        "organization_id": ORG,
        "workspace_id": WORKSPACE,
        "catalog_epoch": 1,
        "catalog_revision": revision,
        "build_token": str(UUID(int=100 + revision)),
        "projection_version": 1,
        "build_plan_sha256": f"{revision:064x}",
        "project_ids": projects or [PROJECT],
        "since_us": subject.utc_micros(start),
        "until_us": subject.utc_micros(end),
        "audit_generation": 8 if revision == 2 else 7,
        "replacement": prior is None,
        "prior_head": prior,
    }


def active(value):
    return SimpleNamespace(
        catalog_revision=value["catalog_revision"],
        build_token=value["build_token"],
        projection_version=value["projection_version"],
        activation_sequence=value["catalog_revision"],
        activation_sha256=f"{value['catalog_revision'] + 10:064x}",
        source_manifest_sha256="e" * 64,
        build_plan=SimpleNamespace(sha256=value["build_plan_sha256"]),
    )


def stage(journal, value, components, *, parts=OLD, identity=SOURCE):
    proof = combine_audit_components(components)
    return journal.stage(
        build=value,
        capture=subject.CapturedAudit(identity, CONTRACT, parts, components),
        expected_count=proof.count,
        expected_digest=proof.digest,
    )


class Reader:
    def __init__(self, journal, components, parts=MERGED):
        self.journal, self.components, self.parts = journal, components, parts
        self.calls = []

    def source_identity(self):
        return SOURCE

    def parts_snapshot(self):
        return self.parts

    def audit_contract_sha256(self):
        return CONTRACT

    def audit_components(self, frozen, *, expected_hostname):
        assert expected_hostname == SOURCE.hostname
        assert self.journal._load("dirty")["clean"] is False
        self.calls.append(frozen)
        return tuple(
            c
            for c in self.components
            if (c.project_id, c.since, c.until)
            == (frozen.project_ids[0], frozen.since, frozen.until)
        )


@pytest.fixture
def lane(tmp_path):
    journal = subject.ScopeProofJournal(**scope(tmp_path))
    first = build()
    initial = component()
    stage(journal, first, (initial,))
    accepted = active(first)
    assert journal.register_active(build=first, active=accepted)
    second = build(2, prior=subject.active_head(accepted, 1), start=T1, end=T2)
    current = component(T1, T2, value=11, generation=8)
    stage(journal, second, (current,), parts=STARTED)
    reader = Reader(journal, (initial, current))
    return SimpleNamespace(
        path=tmp_path,
        journal=journal,
        first=first,
        second=second,
        initial=initial,
        current=current,
        reader=reader,
    )


def verify(lane, **overrides):
    return lane.journal.verify_dirty(
        **{
            "reader": lane.reader,
            "build": lane.second,
            "current": MERGED,
            "previous": OLD,
            "started": STARTED,
            "since": T0,
            "until": T2,
            **overrides,
        }
    )


def notice(lane, **overrides):
    def check(**kwargs):
        return lane.journal.verify_dirty(
            reader=lane.reader, build=lane.second, **kwargs
        )

    reader = SimpleNamespace(
        parts_snapshot=lane.reader.parts_snapshot,
        history_in_parts=Mock(return_value=T0),
    )
    return notice_part_changes(
        **scope(lane.path),
        reader=reader,
        project_ids=(PROJECT,),
        since=T0,
        until=T2,
        scan_since=T1,
        observed_at=NOW,
        full_replacement=False,
        started_parts=STARTED,
        scope_audit_check=check,
        **overrides,
    )


def test_equal_canonical_proofs_suppress_merge_shaped_notice_without_rewrite(lane):
    assert notice(lane) is False
    assert pending_source_repair(**scope(lane.path)) is None
    assert [
        (f.project_ids, f.since, f.until, f.audit_generation) for f in lane.reader.calls
    ] == [
        ((PROJECT,), T0, T1, 7),
        ((PROJECT,), T1, T2, 8),
    ]
    assert lane.journal._load("dirty")["clean"] is True
    assert lane.journal._load("accepted")["head"]["catalog_revision"] == 1


def test_unchanged_inventory_does_not_reaudit(lane):
    assert verify(lane, current=OLD, previous=OLD, started=OLD) is None
    assert lane.reader.calls == []


@pytest.mark.parametrize(
    "kind", ["same_version_conflict", "last_row_delete", "value_update", "empty_import"]
)
def test_changed_canonical_state_cannot_hide_behind_merge_inventory(lane, kind):
    old = lane.initial
    if kind == "same_version_conflict":
        changed = replace(old, proof=replace(old.proof, state_conflict_count=1))
        assert changed.proof.digest == old.proof.digest
    elif kind == "last_row_delete":
        changed = component(count=0, value=0)
    elif kind == "value_update":
        changed = component(value=99)
    else:
        # Establish a genuinely empty accepted scope, then introduce its first
        # logical row. The query must run even though the old count was zero.
        empty = component(count=0, value=0)
        doc = lane.journal._load("accepted")
        lane.journal._save(
            "accepted", {**doc, "components": [subject._component_document(empty)]}
        )
        changed = old
    lane.reader.components = (changed, lane.current)
    assert notice(lane)
    assert pending_source_repair(**scope(lane.path)) is not None
    assert lane.journal._load("dirty")["clean"] is False


def test_existing_unrelated_positive_notice_is_never_acknowledged(lane):
    original = record_source_repair(**scope(lane.path), seen_at=T0, observed_at=NOW)
    assert notice(lane) is False
    assert pending_source_repair(**scope(lane.path)).raw == original.raw


def test_dirty_restart_same_inventory_still_requires_reaudit(lane):
    lane.reader.components = ()
    assert verify(lane) is False
    generation = lane.journal._load("dirty")["generation"]
    lane.journal = subject.ScopeProofJournal(**scope(lane.path))
    lane.reader = Reader(lane.journal, (lane.initial, lane.current))
    assert verify(lane, previous=MERGED, started=MERGED)
    assert lane.journal._load("dirty")["generation"] == generation + 1
    assert len(lane.reader.calls) == 2


@pytest.mark.parametrize("edge", ["before", "after", "final_watch"])
def test_inventory_races_never_acknowledge_missing_post_audit_content(lane, edge):
    snapshots = {
        "before": [MERGED, POST],
        "after": [MERGED, MERGED, POST],
        "final_watch": [MERGED, MERGED, MERGED, POST],
    }
    # notice() takes the initial observation, verify_dirty brackets its own
    # audits, and the original watcher performs the final independent recheck.
    lane.reader.parts_snapshot = Mock(side_effect=snapshots[edge])
    assert notice(lane)
    assert pending_source_repair(**scope(lane.path)) is not None


def test_newer_dirty_generation_cannot_be_cleared_by_old_verification(lane):
    original = lane.reader.audit_components

    def race(*args, **kwargs):
        result = original(*args, **kwargs)
        doc = lane.journal._load("dirty")
        lane.journal._save("dirty", {**doc, "generation": doc["generation"] + 1})
        return result

    lane.reader.audit_components = race
    assert verify(lane) is False
    assert lane.journal._load("dirty")["clean"] is False


@pytest.mark.parametrize(
    "operation",
    ["source_identity", "parts_snapshot", "audit_components", "audit_contract_sha256"],
)
def test_proof_query_failure_retains_normal_repair(lane, operation):
    setattr(lane.reader, operation, Mock(side_effect=TimeoutError(operation)))
    # Call the watcher's failure handler, not a direct proof method that is
    # allowed to raise while its dirty journal remains durable.
    source_reader = SimpleNamespace(
        parts_snapshot=lambda: MERGED, history_in_parts=Mock()
    )

    def check(**kwargs):
        return lane.journal.verify_dirty(
            reader=lane.reader, build=lane.second, **kwargs
        )

    assert notice_part_changes(
        **scope(lane.path),
        reader=source_reader,
        project_ids=(PROJECT,),
        since=T0,
        until=T2,
        observed_at=NOW,
        full_replacement=False,
        started_parts=STARTED,
        scope_audit_check=check,
    )
    assert pending_source_repair(**scope(lane.path)) is not None


@pytest.mark.parametrize("change", ["uuid", "replica", "contract"])
def test_source_incarnation_replica_and_semantic_changes_force_repair(lane, change):
    if change == "uuid":
        lane.reader.source_identity = lambda: replace(
            SOURCE, table_uuid=str(UUID(int=8))
        )
    elif change == "replica":
        lane.reader.source_identity = lambda: replace(SOURCE, hostname="source-2")
    else:
        lane.reader.audit_contract_sha256 = lambda: "f" * 64
    assert verify(lane) is False
    assert lane.reader.calls == []


def test_stage_is_not_accepted_until_exact_durable_active_and_retry_is_idempotent(lane):
    head = active(lane.second)
    with pytest.raises(ValueError, match="ACTIVE"):
        lane.journal.register_active(build=lane.second, active=active(lane.first))
    assert lane.journal.register_active(build=lane.second, active=head)
    record = lane.journal._load("accepted")
    assert len(record["components"]) == 2
    assert lane.journal.register_active(build=lane.second, active=head)
    assert lane.journal._load("accepted") == record


def test_accepted_full_repair_replaces_not_truncates_prior_scope_inventory(lane):
    replacement = build(3, start=T0 + timedelta(seconds=1), end=T2)
    c = component(T0 + timedelta(seconds=1), T2)
    stage(lane.journal, replacement, (c,), parts=MERGED)
    # Staging does not erase previously accepted coverage.
    assert lane.journal._load("accepted")["components"][0][
        "since_us"
    ] == subject.utc_micros(T0)
    assert lane.journal.register_active(build=replacement, active=active(replacement))
    assert lane.journal._load("accepted")["components"] == [
        subject._component_document(c)
    ]


@pytest.mark.parametrize(
    "kind", ["gap", "overlap", "tenant", "changed_head", "scope_subset"]
)
def test_unproven_scope_or_prior_active_binding_cannot_be_reused(lane, kind):
    value = dict(lane.second)
    if kind == "tenant":
        value["workspace_id"] = OTHER
        with pytest.raises(ValueError, match="tenant"):
            verify(lane, build=value)
        return
    if kind == "changed_head":
        value["prior_head"] = {**value["prior_head"], "activation_sha256": "f" * 64}
        assert verify(lane, build=value) is None
        return
    if kind == "scope_subset":
        assert verify(lane, since=T0 - timedelta(seconds=1)) is False
        return
    docs = [
        subject._component_document(lane.initial),
        subject._component_document(lane.current),
    ]
    docs[1]["since_us"] += 1 if kind == "gap" else -1
    with pytest.raises(ValueError, match="overlap|gap"):
        subject._components(docs)


def test_dirty_fsync_failure_never_advances_part_inventory(lane, monkeypatch):
    original = lane.journal._save

    def fail(kind, doc):
        if kind == "dirty":
            raise OSError("dirty fsync")
        original(kind, doc)

    monkeypatch.setattr(lane.journal, "_save", fail)
    assert notice(lane)
    assert pending_source_repair(**scope(lane.path)) is not None


def test_component_budget_fails_without_truncating_accepted_history(lane, monkeypatch):
    before = lane.journal._load("accepted")
    monkeypatch.setattr(subject, "MAX_COMPONENTS", 1)
    with pytest.raises(ValueError, match="bound"):
        lane.journal.register_active(build=lane.second, active=active(lane.second))
    assert lane.journal._load("accepted") == before


@pytest.mark.parametrize("kind", ["fifo", "symlink", "checksum", "unknown_format"])
def test_proof_storage_is_bounded_nofollow_and_strict(lane, kind, monkeypatch):
    import os

    key = f"{lane.journal.key}:accepted"
    path = lane.journal.journal._path(key)
    if kind in {"fifo", "symlink"}:
        # Test files only, under pytest's private temporary directory.
        path.unlink()
        if kind == "fifo":
            os.mkfifo(path)
        else:
            path.symlink_to(lane.path / "absent")
        with pytest.raises(PropertyCatalogCoordinatorError):
            lane.journal._load("accepted")
    else:
        doc = lane.journal._load("accepted")
        if kind == "unknown_format":
            lane.journal._save("accepted", {**doc, "format": "future.v99"})
        else:
            monkeypatch.setattr(
                lane.journal.journal,
                "load_record",
                lambda _: {**doc, "contract": "bad"},
            )
        with pytest.raises(ValueError):
            lane.journal._load("accepted")


class AuditClient:
    source_database = "default"

    def __init__(self):
        self.calls = []
        self.hostname = SOURCE.hostname

    def query(self, sql, params, **options):
        self.calls.append((sql, params, options))
        return [
            {
                "source_count": 1,
                "state_conflict_count": 0,
                "source_hostname": self.hostname,
                **{
                    f"audit_h{i}_{kind}": i
                    for i in range(1, 5)
                    for kind in ("xor", "sum")
                },
            }
        ]


def test_fresh_components_keep_original_partial_boundaries_and_ignore_cached_empty_hours():
    client = AuditClient()
    reader = CanonicalSpanSourceReader(
        client,
        source_database="default",
        catalog_database="property_catalog_dev_unit",
        deadline=_FixedDeadline(8500),
    )
    end = T0 + timedelta(days=8, microseconds=1)
    frozen = FrozenSpanSource((PROJECT,), T0, end, 17)
    reader._occupied_frozen, reader._occupied_hours = frozen, frozenset()
    # Preserve legacy audit() behavior; the accepted-scope API must be fresh.
    assert reader.audit(frozen).count == 0
    components = reader.audit_components(frozen, expected_hostname=SOURCE.hostname)
    assert [(c.since, c.until) for c in components] == [
        (T0, T0 + timedelta(days=7)),
        (T0 + timedelta(days=7), end),
    ]
    assert combine_audit_components(components).count == 2
    for sql, params, options in client.calls:
        assert "_part" not in sql and "catalog_source_version_fence" not in sql
        assert "state_conflict_count" in sql and "sp.is_deleted" in sql
        assert params["catalog_project_ids"] == (PROJECT,)
        assert options["settings"]["readonly"] == 2
    client.hostname = "other-node"
    with pytest.raises(RuntimeError, match="source node"):
        reader.audit_components(frozen, expected_hostname=SOURCE.hostname)


def test_missing_typed_source_identity_keeps_original_audit_path():
    reader = SimpleNamespace(audit=Mock(return_value="original"))
    assert subject.capture_final_audit(
        reader, FrozenSpanSource((PROJECT,), T0, T1, 1)
    ) == ("original", None)
    reader.audit.assert_called_once()


def test_empty_authorized_scope_never_reads_source_identity():
    reader = SimpleNamespace(
        audit=Mock(return_value="empty"),
        source_identity=Mock(side_effect=AssertionError("source read")),
    )
    assert subject.capture_final_audit(reader, FrozenSpanSource((), T0, T1, 1)) == (
        "empty",
        None,
    )
    reader.source_identity.assert_not_called()


def test_conflicts_are_not_part_of_digest_but_cannot_be_staged(lane):
    c = replace(lane.current, proof=replace(lane.current.proof, state_conflict_count=1))
    assert c.proof.digest == lane.current.proof.digest
    with pytest.raises(ValueError, match="conflicted"):
        stage(lane.journal, lane.second, (c,))


@pytest.mark.parametrize(
    "patch",
    [
        {"table_uuid": str(UUID(int=0))},
        {"hostname": ""},
        {"engine": "MergeTree"},
        {"replica_name": "unexpected"},
        {"engine": "ReplicatedReplacingMergeTree"},
    ],
)
def test_source_identity_is_not_guessed(patch):
    with pytest.raises(ValueError):
        subject.SourceIdentity.from_document({**asdict(SOURCE), **patch})


def test_real_sql_contract_digest_is_stable_and_projection_limits_are_bound():
    reader = CanonicalSpanSourceReader(
        AuditClient(),
        source_database="default",
        catalog_database="property_catalog_dev_unit",
        deadline=_FixedDeadline(8500),
    )
    digest = reader.audit_contract_sha256()
    assert len(digest) == 64 and reader.audit_contract_sha256() == digest
    parameters = reader._audit_parameters()
    reader._audit_parameters = lambda: {
        **parameters,
        "catalog_projected_array_members": parameters["catalog_projected_array_members"]
        + 1,
    }
    assert reader.audit_contract_sha256() != digest


def test_staged_snapshot_alone_cannot_stand_in_for_durable_active(tmp_path):
    journal = subject.ScopeProofJournal(**scope(tmp_path))
    value, c = build(), component()
    stage(journal, value, (c,))
    reader = Reader(journal, (c,))
    assert (
        journal.verify_dirty(
            reader=reader,
            build=value,
            current=MERGED,
            previous=OLD,
            started=OLD,
            since=T0,
            until=T1,
        )
        is None
    )
    assert reader.calls == [] and journal._load("accepted") is None


def test_replacement_does_not_clear_old_dirty_scope_before_active(lane):
    lane.reader.components = ()
    assert verify(lane) is False
    value = build(3, prior=subject.active_head(active(lane.first), 1), end=T2)
    value["replacement"] = True
    stage(lane.journal, value, (component(T0, T2),))
    before = lane.journal._load("dirty")
    assert verify(lane, build=value) is False
    assert lane.journal.register_active(build=value, active=active(value))
    assert lane.journal._load("dirty") == before


def test_stale_registration_and_stage_cannot_regress_newer_accepted_head(lane):
    assert lane.journal.register_active(build=lane.second, active=active(lane.second))
    before = lane.journal._load("accepted")
    assert not stage(lane.journal, lane.first, (lane.initial,))
    assert not lane.journal.register_active(build=lane.first, active=active(lane.first))
    assert lane.journal._load("accepted") == before


def test_same_qualified_build_cannot_overwrite_conflicting_component_evidence(lane):
    before = lane.journal._load("staged")
    changed = replace(lane.current, proof=component(value=99).proof)
    with pytest.raises(ValueError, match="conflicting staged"):
        stage(lane.journal, lane.second, (changed,))
    assert lane.journal._load("staged") == before
    # Ordinary inventory changes can retain the same independently qualified proof.
    assert stage(lane.journal, lane.second, (lane.current,), parts=MERGED)


def test_accepted_journal_cannot_pair_head_with_another_qualified_build(lane):
    doc = lane.journal._load("accepted")
    lane.journal._save(
        "accepted",
        {
            **doc,
            "head": subject.active_head(active(lane.second), 1),
        },
    )
    with pytest.raises(ValueError, match="qualified build"):
        lane.journal._load("accepted")


def test_component_generation_cannot_be_relabelled(lane):
    with pytest.raises(ValueError, match="generation"):
        stage(lane.journal, lane.second, (replace(lane.current, audit_generation=9),))


def test_capture_bound_keeps_mandatory_audit_without_partial_cache(monkeypatch):
    reader = SimpleNamespace(
        audit=Mock(return_value="mandatory"),
        source_identity=Mock(side_effect=AssertionError("metadata read")),
    )
    monkeypatch.setattr(subject, "MAX_COMPONENTS", 0)
    assert subject.capture_final_audit(
        reader, FrozenSpanSource((PROJECT,), T0, T1, 7)
    ) == (
        "mandatory",
        None,
    )
    reader.audit.assert_called_once()
    reader.source_identity.assert_not_called()


@pytest.mark.parametrize(
    "edge", ["stable", "parts", "source", "metadata_failure", "audit_failure"]
)
def test_final_capture_brackets_fresh_audit_and_discards_raced_cache(edge):
    c = component()
    reader = SimpleNamespace(
        source_identity=Mock(return_value=SOURCE),
        parts_snapshot=Mock(return_value=OLD),
        audit_contract_sha256=lambda: CONTRACT,
        audit_components=Mock(return_value=(c,)),
        audit=Mock(side_effect=AssertionError("legacy audit unexpected")),
    )
    if edge == "parts":
        reader.parts_snapshot.side_effect = [OLD, MERGED]
    elif edge == "source":
        reader.source_identity.side_effect = [
            SOURCE,
            replace(SOURCE, hostname="source-2"),
        ]
    elif edge == "metadata_failure":
        reader.parts_snapshot.side_effect = [OLD, TimeoutError("metadata")]
    elif edge == "audit_failure":
        reader.audit_components.side_effect = TimeoutError("canonical audit")
        with pytest.raises(TimeoutError, match="canonical audit"):
            subject.capture_final_audit(reader, FrozenSpanSource((PROJECT,), T0, T1, 7))
        return
    proof, capture = subject.capture_final_audit(
        reader, FrozenSpanSource((PROJECT,), T0, T1, 7)
    )
    assert proof == c.proof
    assert (capture is not None) == (edge == "stable")
    reader.audit_components.assert_called_once_with(
        FrozenSpanSource((PROJECT,), T0, T1, 7),
        expected_hostname=SOURCE.hostname,
    )


def test_dirty_reaudit_consumes_same_shrinking_deadline_and_retains_dirty(lane):
    class Deadline:
        def __init__(self):
            self.remaining = 50

        def remaining_ms(self, *, cap_ms):
            self.remaining -= 20
            if self.remaining <= 0:
                raise TimeoutError("shared deadline exhausted")
            return min(cap_ms, self.remaining)

    class Client(AuditClient):
        def source_identity(self, *, timeout_ms):
            return asdict(SOURCE)

        def source_parts(self, *, timeout_ms):
            return [{"name": n, "checksum": h} for n, h in MERGED]

    client = Client()
    reader = CanonicalSpanSourceReader(
        client,
        source_database="default",
        catalog_database="property_catalog_dev_unit",
        deadline=Deadline(),
    )
    reader.audit_contract_sha256 = lambda: CONTRACT
    with pytest.raises(TimeoutError, match="shared deadline"):
        verify(lane, reader=reader)
    assert client.calls == []
    assert lane.journal._load("dirty")["clean"] is False


def test_missing_staged_evidence_after_dirty_crash_keeps_repair(lane):
    lane.reader.components = ()
    assert verify(lane) is False
    next_build = build(3, start=T0, end=T2)
    stage(lane.journal, next_build, (component(T0, T2),))
    restarted = subject.ScopeProofJournal(**scope(lane.path))
    lane.journal = restarted
    assert verify(lane, previous=MERGED, started=MERGED) is False
    assert lane.journal._load("dirty")["clean"] is False


def test_failed_dirty_save_must_persist_repair_before_inventory_ack(lane, monkeypatch):
    import tracer.services.clickhouse.v2.property_catalog.source_parts as parts_module

    before = lane.journal.journal.load_record(f"source-parts:{ORG}:{WORKSPACE}:default")
    monkeypatch.setattr(lane.journal, "_save", Mock(side_effect=OSError("fsync")))
    monkeypatch.setattr(
        parts_module, "record_source_repair", Mock(side_effect=OSError("repair fsync"))
    )
    with pytest.raises(OSError, match="repair fsync"):
        notice(lane)
    assert (
        lane.journal.journal.load_record(f"source-parts:{ORG}:{WORKSPACE}:default")
        == before
    )
