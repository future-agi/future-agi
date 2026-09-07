"""Latest-state semantics, including the SQL before Python conflict checks.

An in-memory SQLite relation executes this SELECT's shared relational subset.
This is not ClickHouse/replication qualification; unlike a canned query response,
it does catch status predicates that hide newer or conflicting physical rows.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from datetime import UTC, datetime
from uuid import UUID

import pytest

from tracer.services.clickhouse.v2.property_catalog.activation import (
    ActivationRecord,
    ActivationStatus,
    CatalogLifecycleMode,
)
from tracer.services.clickhouse.v2.property_catalog.codec import canonical_json
from tracer.services.clickhouse.v2.property_catalog.mutation_lock import (
    InProcessCatalogMutationSerializer,
)
from tracer.services.clickhouse.v2.property_catalog.state_store import (
    _ACTIVATION_COLUMNS,
    ClickHouseCatalogStateStore,
    PropertyCatalogStateConflict,
    _activation_row,
    _active_lineage,
    activation_latest_rows_sql,
)

DATABASE = "property_catalog_dev_latest_status"
TABLE = f"`{DATABASE}`.`property_catalog_activations`"
ORG = str(UUID(int=1))
WORKSPACE = str(UUID(int=2))
NOW = datetime(2026, 9, 6, tzinfo=UTC)
SCOPE = {"organization_id": ORG, "workspace_id": WORKSPACE, "catalog_epoch": 1}


def _record(*, revision=1, sequence=1, build_token=None):
    mode = (
        CatalogLifecycleMode.INITIAL_BACKFILL
        if revision == 1
        else CatalogLifecycleMode.FULL_REPAIR
    )
    manifest = canonical_json(
        {"lifecycle_mode": mode, "lineage_anchor_revision": revision}
    )
    return ActivationRecord(
        **SCOPE,
        catalog_revision=revision,
        build_token=build_token or str(UUID(int=100 + revision)),
        projection_version=1,
        lifecycle_mode=mode,
        lineage_anchor_revision=revision,
        activation_sequence=sequence,
        source_manifest_json=manifest,
        source_manifest_sha256=hashlib.sha256(manifest.encode()).hexdigest(),
        revision_fence_sha256="a" * 64,
        activation_sha256=hashlib.sha256(f"activation:{revision}".encode()).hexdigest(),
        status=ActivationStatus.ACTIVE,
        live_definition_rows=3,
        tombstone_rows=0,
        value_rows=5,
        qualified_at=NOW,
        updated_at=NOW,
        version=sequence,
    )


class _RelationalClient:
    catalog_database = DATABASE

    def __init__(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.execute(f"ATTACH DATABASE ':memory:' AS `{DATABASE}`")
        self.connection.execute(
            f"CREATE TABLE {TABLE} ({', '.join(_ACTIVATION_COLUMNS)})"
        )
        self.queries = []
        self.inserts = []

    def seed(self, *rows):
        self.connection.executemany(
            f"INSERT INTO {TABLE} VALUES ({', '.join('?' for _ in _ACTIVATION_COLUMNS)})",
            [
                tuple(
                    value.isoformat() if isinstance(value, datetime) else value
                    for value in (row[column] for column in _ACTIVATION_COLUMNS)
                )
                for row in rows
            ],
        )

    def query(self, sql, params, *, timeout_ms):
        assert timeout_ms > 0
        self.queries.append(sql)
        translated = re.sub(r"%\(([a-z_]+)\)s", r":\1", sql)
        return tuple(
            {
                key: datetime.fromisoformat(value)
                if key in {"qualified_at", "updated_at"} and value is not None
                else value
                for key, value in dict(row).items()
            }
            for row in self.connection.execute(translated, params)
        )

    def insert(self, table, rows, *, columns, timeout_ms, deduplication_token):
        assert table == TABLE and tuple(columns) == _ACTIVATION_COLUMNS
        assert timeout_ms > 0 and deduplication_token
        self.inserts.extend(rows)
        self.seed(*rows)


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


@pytest.mark.parametrize("physical_order", ("active_first", "disabled_first", "merged"))
def test_newer_disabled_state_cannot_resurrect_active(catalog, physical_order):
    client, store = catalog
    active = _activation_row(_record())
    disabled = {**active, "status": "disabled", "_version": 10}
    physical = {
        "active_first": (active, disabled),
        "disabled_first": (disabled, active),
        "merged": (disabled,),
    }[physical_order]
    client.seed(*physical)

    raw = store.list_activation_rows(**SCOPE)
    assert len(raw) == 1 and raw[0]["status"] == "disabled"
    assert raw[0]["_version"] == 10
    assert store.list_activations(**SCOPE) == ()
    assert _active_lineage(raw) == ()
    assert not client.inserts


@pytest.mark.parametrize("status", ("disabled", "building"))
def test_same_version_status_conflict_is_visible_before_filtering(catalog, status):
    client, store = catalog
    active = _activation_row(_record())
    client.seed(active, {**active, "status": status})
    raw = store.list_activation_rows(**SCOPE)
    assert len(raw) == 2
    with pytest.raises(PropertyCatalogStateConflict, match="different rows"):
        store.list_activations(**SCOPE)
    with pytest.raises(PropertyCatalogStateConflict, match="different rows"):
        _active_lineage(raw)


def test_same_version_duplicate_replays_are_not_conflicts(catalog):
    client, store = catalog
    record = _record()
    row = _activation_row(record)
    client.seed(row, row, row)
    assert store.list_activations(**SCOPE) == (record,)
    assert _active_lineage(store.list_activation_rows(**SCOPE)) == (
        (record.catalog_revision, record.build_token, record.projection_version),
    )


def test_unknown_latest_status_is_not_silently_treated_as_inactive(catalog):
    client, store = catalog
    client.seed({**_activation_row(_record()), "status": "unexpected"})
    with pytest.raises(PropertyCatalogStateConflict, match="unsupported latest status"):
        store.list_activations(**SCOPE)
    with pytest.raises(PropertyCatalogStateConflict, match="unsupported latest status"):
        _active_lineage(store.list_activation_rows(**SCOPE))


def test_old_version_conflict_does_not_override_unique_newer_disabled(catalog):
    client, store = catalog
    row = _activation_row(_record())
    client.seed(
        row,
        {**row, "value_rows": 100},
        {**row, "status": "disabled", "_version": 10},
    )
    assert store.list_activations(**SCOPE) == ()


@pytest.mark.parametrize("same_token", (True, False))
def test_invalidated_revision_cannot_be_appended_active_again(catalog, same_token):
    client, store = catalog
    original = _record()
    client.seed({**_activation_row(original), "status": "disabled", "_version": 10})
    proposed = original if same_token else _record(build_token=str(UUID(int=500)))
    with pytest.raises(PropertyCatalogStateConflict):
        store.append_active(
            proposed,
            fence_sha256=proposed.revision_fence_sha256,
            checkpoint_state_sha256s=("b" * 64,),
        )
    assert not client.inserts


def test_invalidated_build_still_consumes_its_activation_sequence(catalog):
    client, store = catalog
    client.seed({**_activation_row(_record()), "status": "disabled", "_version": 10})
    reused_sequence = _record(revision=2, sequence=1)
    with pytest.raises(PropertyCatalogStateConflict, match="workspace-monotonic"):
        store.append_active(
            reused_sequence,
            fence_sha256=reused_sequence.revision_fence_sha256,
            checkpoint_state_sha256s=("b" * 64,),
        )
    assert not client.inserts
    replacement = _record(revision=2, sequence=2)
    assert (
        store.append_active(
            replacement,
            fence_sha256=replacement.revision_fence_sha256,
            checkpoint_state_sha256s=("c" * 64,),
        )
        == replacement
    )
    assert store.list_activations(**SCOPE) == (replacement,)
    assert _active_lineage(store.list_activation_rows(**SCOPE)) == (
        (2, replacement.build_token, 1),
    )


def test_disabled_rows_from_other_scopes_do_not_hide_current_active(catalog):
    client, store = catalog
    record = _record()
    row = _activation_row(record)
    disabled = {**row, "status": "disabled", "_version": 10}
    client.seed(
        row,
        {**disabled, "organization_id": str(UUID(int=901))},
        {**disabled, "workspace_id": str(UUID(int=902))},
        {**disabled, "catalog_epoch": 2},
    )
    assert store.list_activations(**SCOPE) == (record,)


def test_query_keeps_all_latest_statuses_for_the_conflict_proof(catalog):
    client, store = catalog
    store.list_activation_rows(**SCOPE)
    sql = client.queries[-1]
    assert "FINAL" not in sql
    assert "status='active'" not in sql
    assert "max(_version) AS latest_version" in sql
    assert "activation._version=recent.latest_version" in sql
    assert "LIMIT 4096" in sql


def test_revision_bounded_lineage_uses_the_same_latest_status_rules(catalog):
    client, _ = catalog
    first = _activation_row(_record())
    second = _activation_row(_record(revision=2, sequence=2))
    client.seed(first, second, {**first, "status": "disabled", "_version": 10})
    sql = activation_latest_rows_sql(DATABASE, through_revision=True)
    raw = client.query(sql, {**SCOPE, "catalog_revision": 1}, timeout_ms=1_000)
    assert len(raw) == 1
    assert raw[0]["catalog_revision"] == 1 and raw[0]["status"] == "disabled"
    assert _active_lineage(raw) == ()
    newer = client.query(sql, {**SCOPE, "catalog_revision": 2}, timeout_ms=1_000)
    assert _active_lineage(newer) == ((2, second["build_token"], 1),)


@pytest.mark.parametrize("through_revision", (1, "true", None))
def test_reviewed_query_builder_rejects_non_boolean_variants(through_revision):
    with pytest.raises(TypeError, match="must be a boolean"):
        activation_latest_rows_sql(DATABASE, through_revision=through_revision)
