"""All-status allocation marks coupled to real ACTIVE publication/replay."""

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest

from tracer.services.clickhouse.v2.property_catalog import publication_journal
from tracer.services.clickhouse.v2.property_catalog.activation import (
    ActivationHistory,
    ActivationInventory,
    ActivationRejected,
    CatalogLifecycleMode,
    make_revision_fence,
)
from tracer.services.clickhouse.v2.property_catalog.mutation_lock import (
    InProcessCatalogMutationSerializer,
)
from tracer.services.clickhouse.v2.property_catalog.state_store import (
    ClickHouseCatalogStateStore,
    PropertyCatalogStateConflict,
    PropertyCatalogStateError,
    _activation_row,
)
from tracer.tests.test_property_catalog_activation_latest_status import (
    DATABASE,
    SCOPE,
    _record,
    _RelationalClient,
)
from tracer.tests.test_property_catalog_publication_journal import PublicationHarness


@pytest.fixture
def catalog():
    client = _RelationalClient()
    try:
        yield (
            client,
            ClickHouseCatalogStateStore(
                client,
                database=DATABASE,
                serializer=InProcessCatalogMutationSerializer(),
            ),
        )
    finally:
        client.connection.close()


def test_empty_history_is_one_query_and_immutable(catalog):
    client, store = catalog
    history = store.load_activation_history(**SCOPE)
    assert history == ActivationHistory((), 0, 0)
    assert len(client.queries) == 1
    with pytest.raises(FrozenInstanceError):
        history.maximum_sequence = 1


@pytest.mark.parametrize(
    "records,revision,sequence,error",
    [
        ([], 0, 0, TypeError),
        ((object(),), 0, 0, TypeError),
        ((), True, 0, ValueError),
        ((), 0, False, ValueError),
        ((), "1", 0, ValueError),
        ((), 0, 1.0, ValueError),
        ((), -1, 0, ValueError),
        ((), 0, -1, ValueError),
        ((), 1 << 64, 0, ValueError),
        ((), 0, 1 << 64, ValueError),
        ((), 0, 1, ValueError),
        ((_record(),), 0, 1, ValueError),
        ((_record(),), 1, 0, ValueError),
    ],
)
def test_history_requires_exact_immutable_types_and_covering_maxima(
    records, revision, sequence, error
):
    with pytest.raises(error):
        ActivationHistory(records, revision, sequence)


def test_history_resolves_all_statuses_once_before_selecting_active(catalog):
    client, store = catalog
    active = _record()
    invalidated = _activation_row(_record(revision=2, sequence=7))
    client.seed(
        _activation_row(active),
        invalidated,
        {**invalidated, "value_rows": 999},  # Obsolete equal-version conflict.
        {**invalidated, "status": "disabled", "_version": 8},
        {**_activation_row(_record(revision=3, sequence=11)), "status": "building"},
        {
            **_activation_row(_record(revision=4, sequence=12)),
            "status": "building",
        },
    )
    assert store.load_activation_history(**SCOPE) == ActivationHistory((active,), 4, 12)
    assert len(client.queries) == 1
    assert store.list_activations(**SCOPE) == (active,)
    assert len(client.queries) == 2


@pytest.mark.parametrize("left_status", ["active", "building", "disabled"])
@pytest.mark.parametrize("right_status", ["active", "building", "disabled"])
@pytest.mark.parametrize("owner_field", ["catalog_revision", "activation_sequence"])
def test_positive_owners_conflict_across_every_status(
    catalog, left_status, right_status, owner_field
):
    client, store = catalog
    left = {**_activation_row(_record()), "status": left_status}
    right = {
        **_activation_row(_record(revision=2, sequence=2)),
        "status": right_status,
        owner_field: 1,
    }
    client.seed(left, right)
    with pytest.raises(PropertyCatalogStateConflict, match=owner_field):
        store.load_activation_history(**SCOPE)
    assert len(client.queries) == 1


@pytest.mark.parametrize("status", ["building", "disabled"])
def test_same_version_conflicts_are_checked_before_nonactive_filtering(catalog, status):
    client, store = catalog
    row = _activation_row(_record())
    client.seed(row, {**row, "status": status})
    with pytest.raises(PropertyCatalogStateConflict, match="different rows"):
        store.load_activation_history(**SCOPE)


def test_history_rejects_unknown_latest_status(catalog):
    client, store = catalog
    client.seed({**_activation_row(_record()), "status": "unknown"})
    with pytest.raises(PropertyCatalogStateConflict, match="unsupported latest status"):
        store.load_activation_history(**SCOPE)


@pytest.mark.parametrize(
    "field,value",
    [
        ("organization_id", str(UUID(int=999))),
        ("workspace_id", str(UUID(int=999))),
        ("catalog_epoch", 2),
        ("organization_id", 1),
        ("workspace_id", None),
        ("build_token", "invalid"),
        ("build_token", UUID(int=0)),
        ("catalog_epoch", True),
        ("catalog_epoch", 1 << 16),
        ("projection_version", False),
        ("projection_version", 1 << 16),
        ("catalog_revision", True),
        ("catalog_revision", 0),
        ("catalog_revision", "2"),
        ("catalog_revision", -1),
        ("activation_sequence", False),
        ("activation_sequence", 0),
        ("activation_sequence", "2"),
        ("activation_sequence", -1),
        ("activation_sequence", 1 << 64),
        ("_version", True),
    ],
)
def test_nonactive_rows_cannot_smuggle_wrong_scope_or_types(
    catalog, monkeypatch, field, value
):
    client, store = catalog
    row = {**_activation_row(_record()), "status": "disabled", field: value}
    # A misbehaving transport must not be trusted just because SQL is scoped.
    monkeypatch.setattr(client, "query", lambda *args, **kwargs: (row,))
    with pytest.raises(PropertyCatalogStateError):
        store.load_activation_history(**SCOPE)


def test_native_uuid_values_are_valid_history(catalog, monkeypatch):
    client, store = catalog
    record = _record()
    row = _activation_row(record)
    for field in ("organization_id", "workspace_id", "build_token"):
        row[field] = UUID(row[field])
    monkeypatch.setattr(client, "query", lambda *args, **kwargs: (row,))
    assert store.load_activation_history(**SCOPE) == ActivationHistory((record,), 1, 1)


@pytest.mark.parametrize(
    "changes,error",
    [
        ({"organization_id": 1}, TypeError),
        ({"workspace_id": None}, TypeError),
        ({"catalog_epoch": True}, ValueError),
        ({"catalog_epoch": 0}, ValueError),
        ({"catalog_epoch": 1 << 16}, ValueError),
    ],
)
def test_invalid_query_scope_is_rejected_before_read(catalog, changes, error):
    client, store = catalog
    with pytest.raises(error):
        store.load_activation_history(**{**SCOPE, **changes})
    assert not client.queries


@pytest.mark.parametrize("status", ["building", "disabled"])
@pytest.mark.parametrize("field", ["catalog_revision", "activation_sequence"])
def test_zero_raw_watermarks_are_not_valid_persisted_builds(catalog, status, field):
    client, store = catalog
    client.seed({**_activation_row(_record()), "status": status, field: 0})
    with pytest.raises(PropertyCatalogStateError, match="must be positive"):
        store.load_activation_history(**SCOPE)


def _nonactive_row(h, *, revision, sequence, status="disabled"):
    record = replace(
        _record(revision=revision, sequence=max(1, sequence)),
        organization_id=h.lease.organization_id,
        workspace_id=h.lease.workspace_id,
        catalog_epoch=h.lease.catalog_epoch,
        projection_version=h.lease.projection_version,
    )
    return {
        **_activation_row(record),
        "status": status,
        "activation_sequence": sequence,
        "_version": max(1, sequence) + 1,
    }


def _set_mode(h, mode, anchor):
    h.manifest = replace(
        h.manifest, lifecycle_mode=mode, lineage_anchor_revision=anchor
    )
    h.fence = make_revision_fence(
        manifest=h.manifest,
        build_plan=h.lease.build_plan,
        checkpoints=h.checkpoints,
        drain_deadline=h.lease.expires_at,
        fenced_at=h.base.clock(),
    )


@pytest.mark.parametrize("status", ["disabled", "building"])
def test_real_initial_publication_allocates_after_nonactive_sequence(tmp_path, status):
    h = PublicationHarness(tmp_path)
    h.rows.append(
        _nonactive_row(
            h, revision=h.lease.catalog_revision - 1, sequence=4, status=status
        )
    )
    result = h.activate()
    assert result.record.lifecycle_mode is CatalogLifecycleMode.INITIAL_BACKFILL
    assert result.record.activation_sequence == result.record.version == 5
    assert h.document()["publication"]["previous_active"] is None
    assert h.document()["phase"] == "resolved"
    publication_journal._validate(h.document())
    assert h.sent[0][0]["activation_sequence"] == 5


def test_initial_publication_still_requires_exact_next_sequence_at_append(
    tmp_path, monkeypatch
):
    h = PublicationHarness(tmp_path)
    revision = h.lease.catalog_revision - 1
    h.rows.append(_nonactive_row(h, revision=revision, sequence=4))
    # Simulate a stale snapshot; structural INITIAL acceptance must not bypass
    # the concrete store's independent exact-next guard inside its write lock.
    monkeypatch.setattr(
        h.store, "load_activation_history", lambda **kwargs: ActivationHistory((), 0, 0)
    )
    with pytest.raises(PropertyCatalogStateConflict, match="workspace-monotonic"):
        h.activate()
    assert not h.sent
    assert h.document()["phase"] == "armed"
    assert h.document()["publication"]["record"]["activation_sequence"] == 1
    publication_journal._validate(h.document())


@pytest.mark.parametrize("status", ["disabled", "building"])
def test_real_full_repair_skips_consumed_sequence_without_inheriting_it(
    tmp_path, status
):
    h = PublicationHarness(tmp_path)
    previous = h.activate().record
    h.rows.append(
        _nonactive_row(
            h, revision=previous.catalog_revision + 1, sequence=2, status=status
        )
    )
    h.advance()
    h.advance()
    result = h.activate()
    assert result.record.activation_sequence == result.record.version == 3
    assert result.record.lineage_anchor_revision == result.record.catalog_revision
    assert h.document()["publication"][
        "previous_active"
    ] == publication_journal._document(previous)
    assert h.document()["phase"] == "resolved"
    publication_journal._validate(h.document())


def test_real_incremental_rejects_consumed_sequence_gap_before_publication(tmp_path):
    h = PublicationHarness(tmp_path)
    previous = h.activate().record
    h.rows.append(_nonactive_row(h, revision=previous.catalog_revision + 1, sequence=2))
    h.advance()
    h.advance()
    _set_mode(h, CatalogLifecycleMode.INCREMENTAL, previous.lineage_anchor_revision)
    with pytest.raises(publication_journal.PublicationJournalError, match="corrupt"):
        h.activate()
    assert len(h.sent) == 1
    assert h.document()["phase"] == "admitted"


def test_real_contiguous_incremental_still_inherits_active_anchor(tmp_path):
    h = PublicationHarness(tmp_path)
    previous = h.activate().record
    h.advance()
    _set_mode(h, CatalogLifecycleMode.INCREMENTAL, previous.lineage_anchor_revision)
    result = h.activate()
    assert result.record.activation_sequence == 2
    assert result.record.lineage_anchor_revision == previous.lineage_anchor_revision
    publication_journal._validate(h.document())


@pytest.mark.parametrize("gap", [False, True])
def test_frozen_intent_replays_original_sequence_and_bytes(tmp_path, gap):
    h = PublicationHarness(tmp_path)
    if gap:
        h.rows.append(
            _nonactive_row(h, revision=h.lease.catalog_revision - 1, sequence=4)
        )
    h.fail = "before_commit"
    with pytest.raises(TimeoutError):
        h.activate()
    frozen = deepcopy(h.document())
    h.restart()
    result = h.activate(
        inventory=ActivationInventory(900, 900, 900),
        now=h.base.clock() + timedelta(hours=1),
    )
    assert result.record == publication_journal._record(frozen["publication"]["record"])
    assert result.record.activation_sequence == (5 if gap else 1)
    assert h.sent[0] == h.sent[1]
    assert h.document() == {**frozen, "phase": "resolved"}


def test_frozen_old_intent_is_not_renumbered_when_history_head_changes(tmp_path):
    h = PublicationHarness(tmp_path)
    h.fail = "before_commit"
    with pytest.raises(TimeoutError):
        h.activate()
    frozen = deepcopy(h.document())
    h.rows.append(_nonactive_row(h, revision=h.lease.catalog_revision - 1, sequence=2))
    h.restart()
    with pytest.raises(ActivationRejected, match="publication_intent_changed"):
        h.activate()
    assert h.document() == frozen
    assert len(h.sent) == 1 and h.sent[0][0]["activation_sequence"] == 1


@pytest.mark.parametrize("frozen", [False, True])
@pytest.mark.parametrize("same_token", [False, True])
def test_activator_rejects_consumed_revision_before_new_or_replayed_write(
    tmp_path, frozen, same_token
):
    h = PublicationHarness(tmp_path)
    if frozen:
        h.fail = "before_commit"
        with pytest.raises(TimeoutError):
            h.activate()
    row = _nonactive_row(h, revision=h.lease.catalog_revision, sequence=1)
    if same_token:
        row["build_token"] = h.lease.build_token
    h.rows.append(row)
    before = deepcopy(h.document())
    with pytest.raises(ActivationRejected, match="activation_revision_not_monotonic"):
        h.activate()
    assert len(h.sent) == int(frozen)
    if frozen:
        assert h.document() == before


@pytest.mark.parametrize(
    "mode", [CatalogLifecycleMode.INITIAL_BACKFILL, CatalogLifecycleMode.FULL_REPAIR]
)
def test_invalidated_marker_still_requires_terminal_predecessor_proof(tmp_path, mode):
    h = PublicationHarness(tmp_path)
    h.activate()
    h.rows.append({**h.rows[0], "status": "disabled", "_version": 10})
    h.advance()
    _set_mode(h, mode, h.lease.catalog_revision)
    with pytest.raises(
        publication_journal.PublicationJournalError, match="not positively visible"
    ):
        h.activate()
    assert len(h.sent) == 1


@pytest.mark.parametrize(
    "field", ["organization_id", "workspace_id", "catalog_epoch", "projection_version"]
)
def test_activator_rejects_wrong_scope_typed_history(tmp_path, monkeypatch, field):
    h = PublicationHarness(tmp_path)
    original = h.activate().record
    foreign = replace(
        original, **{field: str(UUID(int=999)) if field.endswith("_id") else 99}
    )
    monkeypatch.setattr(
        h.store,
        "load_activation_history",
        lambda **kwargs: ActivationHistory(
            (foreign,), original.catalog_revision, original.activation_sequence
        ),
    )
    with pytest.raises(ActivationRejected, match="activation_history_conflicts"):
        h.activate()
    assert len(h.sent) == 1


def test_activator_requires_typed_history_without_active_only_fallback(
    tmp_path, monkeypatch
):
    h = PublicationHarness(tmp_path)
    monkeypatch.setattr(
        h.store,
        "load_activation_history",
        lambda **kwargs: SimpleNamespace(
            active_records=(), maximum_revision=0, maximum_sequence=0
        ),
    )
    monkeypatch.setattr(
        h.store,
        "list_activations",
        lambda **kwargs: pytest.fail("active-only fallback"),
    )
    with pytest.raises(ActivationRejected, match="activation_history_conflicts"):
        h.activate()
    assert not h.sent


def test_sequence_exhaustion_does_not_prepare_an_intent(tmp_path):
    h = PublicationHarness(tmp_path)
    h.rows.append(
        _nonactive_row(h, revision=h.lease.catalog_revision - 1, sequence=(1 << 64) - 1)
    )
    h.rows[0]["_version"] = (1 << 64) - 1
    with pytest.raises(ActivationRejected, match="activation_sequence_exhausted"):
        h.activate()
    assert not h.sent and h.document()["phase"] == "admitted"
