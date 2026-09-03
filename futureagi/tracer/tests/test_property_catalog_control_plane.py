from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from clickhouse_driver.columns.datetimecolumn import DateTime64Column
from clickhouse_driver.columns.uuidcolumn import UUIDColumn

from tracer.services.clickhouse.v2.property_catalog.activation import (
    ActivationRecord,
    ActivationStatus,
    BuildPlanSourceScope,
    BuildPlanStream,
    CatalogLifecycleMode,
    ManifestStreamRole,
    RevisionBuildPlan,
    RevisionLease,
)
from tracer.services.clickhouse.v2.property_catalog.codec import canonical_json
from tracer.services.clickhouse.v2.property_catalog.models import (
    EnvelopeCounts,
    EnvelopeOutcome,
    PropertyCatalogEnvelope,
    SourceAdapter,
)
from tracer.services.clickhouse.v2.property_catalog.mutation_lock import (
    InProcessCatalogMutationSerializer,
)
from tracer.services.clickhouse.v2.property_catalog.projection import (
    PostgresSnapshotContext,
)
from tracer.services.clickhouse.v2.property_catalog.publisher import (
    CatalogWriteLease,
    ClickHouseEnvelopePublisher,
    PropertyCatalogPublishError,
    SharedCatalogDeadline,
    _native_insert_rows,
)
from tracer.services.clickhouse.v2.property_catalog.qualification import (
    CatalogCheckpoint,
    CheckpointStatus,
)
from tracer.services.clickhouse.v2.property_catalog.reconciler import CheckpointWrite
from tracer.services.clickhouse.v2.property_catalog.span_source import (
    SPAN_AUDIT_CUTOFF_LABEL,
    CanonicalSpanSourceReader,
    FrozenSpanSource,
    SpanAuditAccumulator,
    _aggregate_audit_sql,
    _paged_payload_with_audit_sql,
)
from tracer.services.clickhouse.v2.property_catalog.state_store import (
    _ACTIVATION_COLUMNS,
    _CHECKPOINT_LOGICAL_COLUMNS,
    _SOURCE_STREAM_AUDIT_COLUMNS,
    ClickHouseCatalogStateStore,
    ClickHouseCurrentBindingReader,
    PropertyCatalogStateConflict,
    _activation,
    _activation_row,
    _active_lineage,
    _checkpoint_row,
    _latest_row,
    _row_identity,
)

ORG = "11111111-1111-4111-8111-111111111111"
WORKSPACE = "22222222-2222-4222-8222-222222222222"
PROJECT = "33333333-3333-4333-8333-333333333333"
OTHER_PROJECT = "77777777-7777-4777-8777-777777777777"
BUILD = "55555555-5555-4555-8555-555555555555"
DATABASE = "property_catalog_dev_unit"
NOW = datetime(2026, 8, 14, 12, tzinfo=UTC)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _stream_id(index: int) -> str:
    return str(uuid.UUID(int=100 + index))


def _clickhouse_uuid(value: str) -> uuid.UUID:
    """Return the exact stdlib UUID representation produced by our CH driver."""

    column = UUIDColumn(context=SimpleNamespace(client_settings={}))
    (decoded,) = column.after_read_items((uuid.UUID(value).int,))
    assert type(decoded) is uuid.UUID
    return decoded


def _plan() -> RevisionBuildPlan:
    streams: list[BuildPlanStream] = []
    index = 0
    for adapter in SourceAdapter:
        roles = (
            (
                ManifestStreamRole.DEFINITIONS,
                ManifestStreamRole.VALUES,
                ManifestStreamRole.HOT_VALUES,
                ManifestStreamRole.SOURCE_AUDIT,
            )
            if adapter is SourceAdapter.SPAN_ATTRIBUTE
            else (ManifestStreamRole.DEFINITIONS,)
        )
        for role in roles:
            streams.append(
                BuildPlanStream(
                    source_adapter=adapter,
                    role=role,
                    producer_stream_id=_stream_id(index),
                    source_cutoff_label=f"{adapter}_{role}",
                    source_version_fence=1000 + index,
                )
            )
            index += 1
    return RevisionBuildPlan(
        organization_id=ORG,
        workspace_id=WORKSPACE,
        catalog_epoch=1,
        catalog_revision=2,
        build_token=BUILD,
        projection_version=1,
        source_scope=BuildPlanSourceScope(
            project_ids=(PROJECT,),
            span_since_us=1_723_638_000_000_000,
            span_until_us=1_723_641_600_000_000,
        ),
        streams=tuple(streams),
    )


def _value_stream(plan: RevisionBuildPlan) -> BuildPlanStream:
    return next(
        stream for stream in plan.streams if stream.role is ManifestStreamRole.VALUES
    )


def test_build_plan_v2_canonically_binds_exact_project_and_time_scope() -> None:
    plan = _plan()
    decoded = json.loads(plan.canonical_json)
    assert decoded["version"] == 2
    assert decoded["source_scope"] == {
        "project_ids": [PROJECT],
        "span_since_us": 1_723_638_000_000_000,
        "span_until_us": 1_723_641_600_000_000,
    }
    assert RevisionBuildPlan.from_json(plan.canonical_json).canonical_json == (
        plan.canonical_json
    )

    decoded["source_scope"]["project_ids"] = [OTHER_PROJECT, PROJECT]
    with pytest.raises(ValueError, match="canonical v2 order"):
        RevisionBuildPlan.from_json(canonical_json(decoded))


class _PublisherClient:
    catalog_database = DATABASE

    def __init__(self, *, plan: RevisionBuildPlan, stream: BuildPlanStream) -> None:
        self.plan = plan
        self.stream = stream
        self.inserts: list[tuple[str, tuple[Mapping[str, Any], ...]]] = []
        self.queries: list[str] = []
        self.delivery_override: Mapping[str, Any] | None = None
        self.source_stream_override: Sequence[Mapping[str, Any]] | None = None

    def query(
        self, sql: str, params: Mapping[str, Any], *, timeout_ms: int
    ) -> Sequence[Mapping[str, Any]]:
        assert timeout_ms > 0
        self.queries.append(sql)
        if "property_catalog_deliveries" in sql:
            if self.delivery_override is not None:
                return (self.delivery_override,)
            deliveries = [
                rows[0]
                for table, rows in self.inserts
                if table == f"`{DATABASE}`.`property_catalog_deliveries`"
            ]
            if not deliveries:
                return ()
            delivery = deliveries[-1]
            return (
                {
                    "envelope_id": delivery["envelope_id"],
                    "payload_sha256": delivery["payload_sha256"],
                    "identity_variants": 1,
                },
            )
        if self.source_stream_override is not None:
            return self.source_stream_override
        reservation = params["producer_stream_id"] == BUILD
        return (
            {
                "projection_version": 1,
                "envelope_version": 0 if reservation else 1,
                "build_plan_json": self.plan.canonical_json,
                "build_lease_sha256": self.plan.sha256,
                "status": "open",
                "drain_deadline": NOW + timedelta(minutes=5),
                "fenced_at": None,
                "_version": 1,
            },
        )

    def insert(
        self,
        table: str,
        rows: Sequence[Mapping[str, Any]],
        *,
        columns: Sequence[str],
        timeout_ms: int,
        deduplication_token: str,
    ) -> None:
        assert tuple(rows[0]) == tuple(columns)
        assert timeout_ms > 0
        assert deduplication_token.startswith("property-catalog-v1:")
        self.inserts.append((table, tuple(rows)))


def _value_envelope(
    plan: RevisionBuildPlan, stream: BuildPlanStream
) -> PropertyCatalogEnvelope:
    return PropertyCatalogEnvelope(
        organization_id=ORG,
        workspace_id=WORKSPACE,
        catalog_epoch=1,
        catalog_revision=2,
        build_token=BUILD,
        projection_version=1,
        source_adapter=SourceAdapter.SPAN_ATTRIBUTE,
        producer_stream_id=stream.producer_stream_id,
        sequence=1,
        previous_payload_sha256="0" * 64,
        source_version=stream.source_version_fence,
        source_fingerprint=_sha("source"),
        source_batch_digest=_sha("batch"),
        outcome=EnvelopeOutcome.COMMITTED,
        counts=EnvelopeCounts(1, 0, 1, 0, 0),
        definitions=(),
        gap_reasons=(),
        terminal=False,
    )


def _value_row() -> Mapping[str, Any]:
    return {
        "organization_id": ORG,
        "workspace_id": WORKSPACE,
        "project_id": PROJECT,
        "catalog_epoch": 1,
        "catalog_revision": 2,
        "build_token": BUILD,
        "source_kind": "custom_attribute",
        "attribute_key": "customer.plan",
        "attribute_type": "string",
        "value_fingerprint": _sha("pro"),
        "value_json": '"pro"',
        "value_search_text_folded": "pro",
        "first_seen": "2026-08-14 12:00:00.000000",
        "last_seen": "2026-08-14 12:00:00.000000",
    }


def test_real_publish_path_rechecks_reservation_and_exact_stream_before_writes() -> (
    None
):
    plan = _plan()
    stream = _value_stream(plan)
    client = _PublisherClient(plan=plan, stream=stream)
    publisher = ClickHouseEnvelopePublisher(
        client=client,
        database=DATABASE,
        lease=CatalogWriteLease(
            organization_id=ORG,
            workspace_id=WORKSPACE,
            catalog_epoch=1,
            catalog_revision=2,
            build_token=BUILD,
            projection_version=1,
            source_adapter=stream.source_adapter,
            producer_stream_id=stream.producer_stream_id,
            build_plan_json=plan.canonical_json,
            build_lease_sha256=plan.sha256,
            expires_at=NOW + timedelta(minutes=5),
        ),
        now=lambda: NOW,
    )

    publisher.publish(_value_envelope(plan, stream), value_rows=(_value_row(),))

    assert [table for table, _rows in client.inserts] == [
        f"`{DATABASE}`.`span_attribute_value_catalog`",
        f"`{DATABASE}`.`property_catalog_deliveries`",
    ]
    assert (
        sum("property_catalog_source_streams" in query for query in client.queries) == 6
    )
    value_insert = client.inserts[0][1][0]
    delivery_insert = client.inserts[1][1][0]
    assert isinstance(value_insert["first_seen"], datetime)
    assert isinstance(value_insert["last_seen"], datetime)
    assert isinstance(delivery_insert["delivered_at"], datetime)


def test_native_insert_rows_rehydrate_every_catalog_datetime64_column() -> None:
    timestamp = "2026-08-14 12:00:00.123456"
    definition_wire = {
        "first_seen": timestamp,
        "last_seen": timestamp,
        "deleted_at": None,
        "emitted_at": timestamp,
    }
    value_wire = {
        "first_seen": timestamp,
        "last_seen": timestamp,
    }
    delivery = {"delivered_at": NOW}

    definition = _native_insert_rows("property_definition_catalog", (definition_wire,))[
        0
    ]
    value = _native_insert_rows("span_attribute_value_catalog", (value_wire,))[0]
    typed_delivery = _native_insert_rows("property_catalog_deliveries", (delivery,))[0]

    # Preserve the immutable wire representation and change only insert rows.
    assert definition_wire["first_seen"] == timestamp
    assert value_wire["first_seen"] == timestamp
    assert definition["deleted_at"] is None
    datetime_values = (
        definition["first_seen"],
        definition["last_seen"],
        definition["emitted_at"],
        value["first_seen"],
        value["last_seen"],
        typed_delivery["delivered_at"],
    )
    assert all(
        isinstance(item, datetime) and item.tzinfo is UTC for item in datetime_values
    )

    # Exercise the pinned driver's actual DateTime64 conversion boundary. This
    # is the exact path that raised ``str has no attribute tzinfo`` on DEV.
    context = SimpleNamespace(client_settings={})
    native_column = DateTime64Column(scale=6, timezone=UTC, context=context)
    with pytest.raises(AttributeError, match="tzinfo"):
        native_column.before_write_items([timestamp])
    for item in datetime_values:
        driver_values = [item]
        native_column.before_write_items(driver_values)
        assert driver_values == [1_786_708_800_123_456] or driver_values == [
            1_786_708_800_000_000
        ]


def test_delivery_identity_queries_qualify_raw_aggregate_inputs() -> None:
    plan = _plan()
    stream = _value_stream(plan)
    client = _PublisherClient(plan=plan, stream=stream)
    publisher = ClickHouseEnvelopePublisher(
        client=client,
        database=DATABASE,
        lease=CatalogWriteLease(
            organization_id=ORG,
            workspace_id=WORKSPACE,
            catalog_epoch=1,
            catalog_revision=2,
            build_token=BUILD,
            projection_version=1,
            source_adapter=stream.source_adapter,
            producer_stream_id=stream.producer_stream_id,
            build_plan_json=plan.canonical_json,
            build_lease_sha256=plan.sha256,
            expires_at=NOW + timedelta(minutes=5),
        ),
        now=lambda: NOW,
    )

    publisher.publish(_value_envelope(plan, stream), value_rows=(_value_row(),))

    identity_queries = tuple(
        query for query in client.queries if "property_catalog_deliveries" in query
    )
    assert len(identity_queries) == 2
    for query in identity_queries:
        assert "property_catalog_deliveries` AS delivery" in query
        assert "any(delivery.envelope_id) AS envelope_id" in query
        assert "any(delivery.payload_sha256) AS payload_sha256" in query
        assert "delivery.envelope_id, delivery.payload_sha256" in query
        assert "any(envelope_id) AS envelope_id" not in query
        assert "any(payload_sha256) AS payload_sha256" not in query


def test_role_injection_is_rejected_before_any_query_or_write() -> None:
    plan = _plan()
    stream = next(
        item for item in plan.streams if item.role is ManifestStreamRole.SOURCE_AUDIT
    )
    client = _PublisherClient(plan=plan, stream=stream)
    publisher = ClickHouseEnvelopePublisher(
        client=client,
        database=DATABASE,
        lease=CatalogWriteLease(
            organization_id=ORG,
            workspace_id=WORKSPACE,
            catalog_epoch=1,
            catalog_revision=2,
            build_token=BUILD,
            projection_version=1,
            source_adapter=stream.source_adapter,
            producer_stream_id=stream.producer_stream_id,
            build_plan_json=plan.canonical_json,
            build_lease_sha256=plan.sha256,
            expires_at=NOW + timedelta(minutes=5),
        ),
        now=lambda: NOW,
    )
    forged = _value_envelope(plan, stream)

    with pytest.raises(PropertyCatalogPublishError, match="build-plan role"):
        publisher.publish(forged, value_rows=(_value_row(),))

    assert client.queries == []
    assert client.inserts == []


def test_publish_rejects_any_immutable_delivery_identity_variant_before_writes() -> (
    None
):
    plan = _plan()
    stream = _value_stream(plan)
    client = _PublisherClient(plan=plan, stream=stream)
    client.delivery_override = {
        "envelope_id": _sha("one"),
        "payload_sha256": _sha("one-payload"),
        "identity_variants": 2,
    }
    publisher = ClickHouseEnvelopePublisher(
        client=client,
        database=DATABASE,
        lease=CatalogWriteLease(
            organization_id=ORG,
            workspace_id=WORKSPACE,
            catalog_epoch=1,
            catalog_revision=2,
            build_token=BUILD,
            projection_version=1,
            source_adapter=stream.source_adapter,
            producer_stream_id=stream.producer_stream_id,
            build_plan_json=plan.canonical_json,
            build_lease_sha256=plan.sha256,
            expires_at=NOW + timedelta(minutes=5),
        ),
        now=lambda: NOW,
    )

    with pytest.raises(PropertyCatalogPublishError, match="other bytes"):
        publisher.publish(_value_envelope(plan, stream), value_rows=(_value_row(),))

    assert client.inserts == []


def test_publish_rejects_crowded_stream_lease_before_any_write() -> None:
    plan = _plan()
    stream = _value_stream(plan)
    client = _PublisherClient(plan=plan, stream=stream)
    client.source_stream_override = tuple(
        {
            "projection_version": 1,
            "envelope_version": 1,
            "build_plan_json": (
                plan.canonical_json if index < 16 else plan.canonical_json + " "
            ),
            "build_lease_sha256": plan.sha256 if index < 16 else _sha("conflict"),
            "status": "open",
            "drain_deadline": NOW + timedelta(minutes=5),
            "fenced_at": None,
            "_version": 1,
        }
        for index in range(17)
    )
    publisher = ClickHouseEnvelopePublisher(
        client=client,
        database=DATABASE,
        lease=CatalogWriteLease(
            organization_id=ORG,
            workspace_id=WORKSPACE,
            catalog_epoch=1,
            catalog_revision=2,
            build_token=BUILD,
            projection_version=1,
            source_adapter=stream.source_adapter,
            producer_stream_id=stream.producer_stream_id,
            build_plan_json=plan.canonical_json,
            build_lease_sha256=plan.sha256,
            expires_at=NOW + timedelta(minutes=5),
        ),
        now=lambda: NOW,
    )

    with pytest.raises(PropertyCatalogPublishError, match="conflict-proof row cap"):
        publisher.publish(_value_envelope(plan, stream), value_rows=(_value_row(),))

    assert client.inserts == []


def _checkpoint_write() -> CheckpointWrite:
    checkpoint = CatalogCheckpoint(
        organization_id=ORG,
        workspace_id=WORKSPACE,
        catalog_epoch=1,
        catalog_revision=2,
        build_token=BUILD,
        projection_version=1,
        source_adapter=SourceAdapter.DATASET_COLUMN,
        producer_stream_id=_stream_id(20),
        source_version_fence=55,
        status=CheckpointStatus.COMPLETE,
        terminal=True,
        source_count=4,
        definition_count=3,
        value_count=0,
        tombstone_count=1,
        gap_count=0,
        poison_count=0,
        conflict_count=0,
        first_sequence=1,
        last_sequence=2,
        last_issued_sequence=2,
        fenced_sequence=2,
        terminal_payload_sha256=_sha("terminal"),
        delivery_count=2,
        source_digest=_sha("source"),
        emitted_digest=_sha("emitted"),
    )
    return CheckpointWrite(
        checkpoint=checkpoint,
        source_cursor="",
        watermark="55",
        source_version_fence=55,
        source_fingerprint=_sha("source"),
        previous_payload_sha256=_sha("terminal"),
        processed_rows=4,
        gap_reasons=(),
    )


def test_clickhouse_uuid_rows_have_canonical_control_state_identities() -> None:
    checkpoint = _checkpoint_row(_checkpoint_write(), now=NOW, version=7)
    source_columns = tuple(
        column for column in _SOURCE_STREAM_AUDIT_COLUMNS if column != "_version"
    )
    source = {
        **dict.fromkeys(_SOURCE_STREAM_AUDIT_COLUMNS),
        "producer_stream_id": _stream_id(21),
        "_version": 7,
    }
    activation_columns = tuple(
        column for column in _ACTIVATION_COLUMNS if column != "_version"
    )
    activation = {
        **dict.fromkeys(_ACTIVATION_COLUMNS),
        "organization_id": ORG,
        "workspace_id": WORKSPACE,
        "build_token": BUILD,
        "_version": 7,
    }

    cases = (
        (
            "checkpoint",
            checkpoint,
            _CHECKPOINT_LOGICAL_COLUMNS,
            (
                "organization_id",
                "workspace_id",
                "build_token",
                "producer_stream_id",
            ),
        ),
        ("source-stream", source, source_columns, ("producer_stream_id",)),
        (
            "activation",
            activation,
            activation_columns,
            ("organization_id", "workspace_id", "build_token"),
        ),
    )
    for label, text_row, logical_columns, uuid_fields in cases:
        driver_row = {
            **text_row,
            **{field: _clickhouse_uuid(str(text_row[field])) for field in uuid_fields},
        }
        assert _row_identity(driver_row, logical_columns) == _row_identity(
            text_row, logical_columns
        )
        assert (
            _latest_row(
                (text_row, driver_row),
                logical_columns=logical_columns,
                label=label,
            )
            is text_row
        )


class _StateClient:
    catalog_database = DATABASE

    def __init__(self, row: Mapping[str, Any]) -> None:
        self.row = row
        self.queries: list[str] = []

    def query(
        self, sql: str, _params: Mapping[str, Any], *, timeout_ms: int
    ) -> Sequence[Mapping[str, Any]]:
        assert timeout_ms > 0
        self.queries.append(sql)
        return (self.row,)

    def insert(self, *_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("read mapping test must not insert")


def test_checkpoint_physical_row_names_map_to_logical_counts() -> None:
    value = _checkpoint_write()
    row = _checkpoint_row(value, now=NOW, version=7)
    store = ClickHouseCatalogStateStore(
        _StateClient(row),
        database=DATABASE,
        serializer=InProcessCatalogMutationSerializer(),
    )

    loaded = store.load_checkpoint_write(
        organization_id=ORG,
        workspace_id=WORKSPACE,
        catalog_epoch=1,
        catalog_revision=2,
        build_token=BUILD,
        source_adapter=SourceAdapter.DATASET_COLUMN,
        producer_stream_id=value.checkpoint.producer_stream_id,
    )

    assert loaded == value
    assert loaded is not None
    assert loaded.checkpoint.source_count == row["source_rows"] == 4
    assert loaded.checkpoint.definition_count == row["definition_rows"] == 3


class _CrowdedReadClient:
    catalog_database = DATABASE

    def __init__(self, rows: Sequence[Mapping[str, Any]]) -> None:
        self.rows = tuple(rows)
        self.queries: list[tuple[str, Mapping[str, Any]]] = []

    def query(
        self,
        sql: str,
        params: Mapping[str, Any],
        *,
        timeout_ms: int,
    ) -> Sequence[Mapping[str, Any]]:
        assert timeout_ms > 0
        self.queries.append((sql, params))
        return self.rows

    def insert(self, *_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("crowded read must fail before an insert")


def test_checkpoint_latest_state_crowding_fails_before_a_row_is_trusted() -> None:
    value = _checkpoint_write()
    row = _checkpoint_row(value, now=NOW, version=7)
    crowded = (*({**row} for _ in range(32)), {**row, "value_rows": 99})
    client = _CrowdedReadClient(crowded)
    store = ClickHouseCatalogStateStore(
        client,
        database=DATABASE,
        serializer=InProcessCatalogMutationSerializer(),
    )

    with pytest.raises(PropertyCatalogStateConflict, match="conflict-proof row cap"):
        store.load_checkpoint_write(
            organization_id=ORG,
            workspace_id=WORKSPACE,
            catalog_epoch=1,
            catalog_revision=2,
            build_token=BUILD,
            source_adapter=SourceAdapter.DATASET_COLUMN,
            producer_stream_id=value.checkpoint.producer_stream_id,
        )

    sql, params = client.queries[-1]
    assert "max(_version) OVER () AS latest_version" in sql
    assert params["row_limit"] == 33


def test_checkpoint_inventory_and_delivery_crowding_fail_before_audit() -> None:
    value = _checkpoint_write()
    inventory_client = _CrowdedReadClient(tuple({} for _ in range(257)))
    inventory_store = ClickHouseCatalogStateStore(
        inventory_client,
        database=DATABASE,
        serializer=InProcessCatalogMutationSerializer(),
    )
    requirement = SimpleNamespace(
        organization_id=ORG,
        workspace_id=WORKSPACE,
        catalog_epoch=1,
        catalog_revision=2,
        build_token=BUILD,
        projection_version=1,
        streams=tuple(object() for _ in range(2)),
    )

    with pytest.raises(PropertyCatalogStateConflict, match="checkpoint inventory"):
        inventory_store.load_checkpoints(requirement)  # type: ignore[arg-type]
    assert inventory_client.queries[-1][1]["row_limit"] == 257

    delivery_client = _CrowdedReadClient(tuple({} for _ in range(33)))
    delivery_store = ClickHouseCatalogStateStore(
        delivery_client,
        database=DATABASE,
        serializer=InProcessCatalogMutationSerializer(),
    )
    with pytest.raises(PropertyCatalogStateConflict, match="physical delivery audit"):
        delivery_store._audit_delivery_chain(value.checkpoint)
    assert delivery_client.queries[-1][1]["row_limit"] == 33


def test_source_stream_inventory_crowding_fails_before_latest_state() -> None:
    plan = _plan()
    manifest = SimpleNamespace(
        organization_id=plan.organization_id,
        workspace_id=plan.workspace_id,
        catalog_epoch=plan.catalog_epoch,
        catalog_revision=plan.catalog_revision,
        build_token=plan.build_token,
        projection_version=plan.projection_version,
        streams=tuple(
            SimpleNamespace(
                role=stream.role,
                requirement=SimpleNamespace(
                    source_adapter=stream.source_adapter,
                    producer_stream_id=stream.producer_stream_id,
                    source_version_fence=stream.source_version_fence,
                ),
            )
            for stream in plan.streams
        ),
    )
    row_limit = (len(plan.streams) + 1) * 32 + 1
    client = _CrowdedReadClient(tuple({} for _ in range(row_limit)))
    store = ClickHouseCatalogStateStore(
        client,
        database=DATABASE,
        serializer=InProcessCatalogMutationSerializer(),
    )

    with pytest.raises(PropertyCatalogStateConflict, match="source-stream inventory"):
        store.audit_build_plan(
            build_plan=plan,
            manifest=manifest,  # type: ignore[arg-type]
        )

    sql, params = client.queries[-1]
    assert "PARTITION BY source_adapter, producer_stream_id" in sql
    assert params["row_limit"] == row_limit


@pytest.mark.parametrize(
    ("crowd_source_stream", "message"),
    (
        (True, "hot source-stream state"),
        (False, "hot physical delivery audit"),
    ),
)
def test_hot_checkpoint_crowding_fails_before_proof_or_persistence(
    crowd_source_stream: bool,
    message: str,
) -> None:
    plan = _plan()
    hot = next(
        stream
        for stream in plan.streams
        if stream.role is ManifestStreamRole.HOT_VALUES
    )
    lease = RevisionLease(
        organization_id=plan.organization_id,
        workspace_id=plan.workspace_id,
        catalog_epoch=plan.catalog_epoch,
        catalog_revision=plan.catalog_revision,
        projection_version=plan.projection_version,
        build_token=plan.build_token,
        build_plan_json=plan.canonical_json,
        build_lease_sha256=plan.sha256,
        issued_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
    )
    stream_row = {
        "projection_version": 1,
        "source_adapter": str(SourceAdapter.SPAN_ATTRIBUTE),
        "producer_stream_id": hot.producer_stream_id,
        "envelope_version": 1,
        "first_sequence": 1,
        "last_sequence": 1,
        "max_contiguous_sequence": 1,
        "last_issued_sequence": 1,
        "fenced_sequence": 1,
        "terminal_payload_sha256": _sha("terminal"),
        "build_plan_json": plan.canonical_json,
        "build_lease_sha256": plan.sha256,
        "status": "draining",
        "gap_count": 0,
        "fenced_at": None,
        "_version": 1,
    }

    class Client(_CrowdedReadClient):
        def query(
            self,
            sql: str,
            params: Mapping[str, Any],
            *,
            timeout_ms: int,
        ) -> Sequence[Mapping[str, Any]]:
            assert timeout_ms > 0
            self.queries.append((sql, params))
            if "property_catalog_source_streams" in sql:
                return (
                    tuple({} for _ in range(33))
                    if crowd_source_stream
                    else (stream_row,)
                )
            return tuple({} for _ in range(33))

    class Proof:
        terminal_sequence = 1

        def to_checkpoint(self, **_kwargs: Any) -> object:
            raise AssertionError("crowded proof must fail before derivation")

    client = Client(())
    store = ClickHouseCatalogStateStore(
        client,
        database=DATABASE,
        serializer=InProcessCatalogMutationSerializer(),
    )

    with pytest.raises(PropertyCatalogStateConflict, match=message):
        store.append_hot_checkpoint_from_proof(
            lease=lease,
            assignment=object(),
            proof=Proof(),
        )

    assert client.queries[-1][1]["row_limit"] == 33


def test_activation_round_trip_and_same_version_conflict_fail_closed() -> None:
    source_manifest = canonical_json(
        {
            "a": 1,
            "lifecycle_mode": CatalogLifecycleMode.INITIAL_BACKFILL,
            "lineage_anchor_revision": 2,
        }
    )
    record = ActivationRecord(
        organization_id=ORG,
        workspace_id=WORKSPACE,
        catalog_epoch=1,
        catalog_revision=2,
        build_token=BUILD,
        projection_version=1,
        lifecycle_mode=CatalogLifecycleMode.INITIAL_BACKFILL,
        lineage_anchor_revision=2,
        activation_sequence=1,
        source_manifest_json=source_manifest,
        source_manifest_sha256=_sha(source_manifest),
        revision_fence_sha256=_sha("fence"),
        activation_sha256=_sha("activation"),
        status=ActivationStatus.ACTIVE,
        live_definition_rows=5,
        tombstone_rows=1,
        value_rows=6,
        qualified_at=NOW,
        updated_at=NOW,
        version=1,
    )
    row = _activation_row(record)
    assert _activation(row) == record

    client = _StateClient(row)
    store = ClickHouseCatalogStateStore(
        client,
        database=DATABASE,
        serializer=InProcessCatalogMutationSerializer(),
    )
    assert store.list_activations(
        organization_id=ORG,
        workspace_id=WORKSPACE,
        catalog_epoch=1,
    ) == (record,)
    assert "max(_version) AS latest_version" in client.queries[-1]
    assert "LIMIT 4096" in client.queries[-1]

    with pytest.raises(PropertyCatalogStateConflict, match="different rows"):
        _latest_row(
            (row, {**row, "value_rows": 7}),
            logical_columns=tuple(row),
            label="activation-test",
        )


def test_current_lineage_reads_newest_activation_window_first() -> None:
    class Client:
        catalog_database = DATABASE

        def __init__(self) -> None:
            self.sql: list[str] = []

        def query(
            self,
            sql: str,
            _params: Mapping[str, Any],
            *,
            timeout_ms: int,
        ) -> Sequence[Mapping[str, Any]]:
            assert timeout_ms > 0
            self.sql.append(sql)
            return ()

    client = Client()
    reader = ClickHouseCurrentBindingReader(client, database=DATABASE)

    rows = reader.read_current(
        context=PostgresSnapshotContext(
            organization_id=ORG,
            workspace_id=WORKSPACE,
            project_ids=(PROJECT,),
            catalog_epoch=1,
            catalog_revision=9_999,
            projection_version=1,
            snapshot_cutoff=NOW,
        ),
        source_adapter=SourceAdapter.DATASET_COLUMN,
        at_revision=9_999,
        build_token=BUILD,
    )

    assert rows == ()
    assert (
        "ORDER BY latest_activation_sequence DESC, catalog_revision DESC"
        in client.sql[0]
    )
    assert "LIMIT 4096" in client.sql[0]


def _active_row(
    *,
    revision: int,
    build_token: str,
    mode: CatalogLifecycleMode,
    anchor: int,
    sequence: int,
    projection_version: int = 1,
) -> dict[str, Any]:
    source_manifest = canonical_json(
        {
            "lifecycle_mode": mode,
            "lineage_anchor_revision": anchor,
        }
    )
    return _activation_row(
        ActivationRecord(
            organization_id=ORG,
            workspace_id=WORKSPACE,
            catalog_epoch=1,
            catalog_revision=revision,
            build_token=build_token,
            projection_version=projection_version,
            lifecycle_mode=mode,
            lineage_anchor_revision=anchor,
            activation_sequence=sequence,
            source_manifest_json=source_manifest,
            source_manifest_sha256=_sha(source_manifest),
            revision_fence_sha256=_sha(f"fence:{revision}:{build_token}"),
            activation_sha256=_sha(f"activation:{revision}:{build_token}"),
            status=ActivationStatus.ACTIVE,
            live_definition_rows=0,
            tombstone_rows=0,
            value_rows=0,
            qualified_at=NOW,
            updated_at=NOW,
            version=sequence,
        )
    )


def test_active_lineage_excludes_pre_repair_history_and_rejects_drift() -> None:
    old_anchor = _active_row(
        revision=1,
        build_token=_stream_id(201),
        mode=CatalogLifecycleMode.INITIAL_BACKFILL,
        anchor=1,
        sequence=1,
    )
    old_increment = _active_row(
        revision=2,
        build_token=_stream_id(202),
        mode=CatalogLifecycleMode.INCREMENTAL,
        anchor=1,
        sequence=2,
    )
    repair = _active_row(
        revision=3,
        build_token=_stream_id(203),
        mode=CatalogLifecycleMode.FULL_REPAIR,
        anchor=3,
        sequence=3,
    )
    current = _active_row(
        revision=4,
        build_token=_stream_id(204),
        mode=CatalogLifecycleMode.INCREMENTAL,
        anchor=3,
        sequence=4,
    )

    assert _active_lineage((old_anchor, old_increment, repair, current)) == (
        (3, _stream_id(203), 1),
        (4, _stream_id(204), 1),
    )

    conflicting = _active_row(
        revision=4,
        build_token=_stream_id(205),
        mode=CatalogLifecycleMode.INCREMENTAL,
        anchor=3,
        sequence=5,
    )
    with pytest.raises(PropertyCatalogStateConflict, match="multiple active"):
        _active_lineage((repair, current, conflicting))


def test_current_binding_rev_minus_one_never_admits_current_build_token() -> None:
    activation_rows = (
        _active_row(
            revision=3,
            build_token=_stream_id(203),
            mode=CatalogLifecycleMode.FULL_REPAIR,
            anchor=3,
            sequence=3,
        ),
        _active_row(
            revision=4,
            build_token=_stream_id(204),
            mode=CatalogLifecycleMode.INCREMENTAL,
            anchor=3,
            sequence=4,
        ),
    )

    class Client:
        catalog_database = DATABASE

        def __init__(self) -> None:
            self.definition_params: list[Mapping[str, Any]] = []

        def query(
            self,
            sql: str,
            params: Mapping[str, Any],
            *,
            timeout_ms: int,
        ) -> Sequence[Mapping[str, Any]]:
            assert timeout_ms > 0
            if "property_catalog_activations" in sql:
                return activation_rows
            self.definition_params.append(params)
            return ()

    client = Client()
    reader = ClickHouseCurrentBindingReader(client, database=DATABASE)
    context = PostgresSnapshotContext(
        organization_id=ORG,
        workspace_id=WORKSPACE,
        project_ids=(PROJECT,),
        catalog_epoch=1,
        catalog_revision=5,
        projection_version=1,
        snapshot_cutoff=NOW,
    )

    assert (
        reader.read_current(
            context=context,
            source_adapter=SourceAdapter.DATASET_COLUMN,
            at_revision=4,
            build_token=BUILD,
        )
        == ()
    )
    assert client.definition_params[-1]["allowed_lineage"] == (
        (3, _stream_id(203), 1),
        (4, _stream_id(204), 1),
    )

    assert (
        reader.read_current(
            context=context,
            source_adapter=SourceAdapter.DATASET_COLUMN,
            at_revision=5,
            build_token=BUILD,
        )
        == ()
    )
    assert client.definition_params[-1]["allowed_lineage"][-1] == (5, BUILD, 1)


def test_current_binding_empty_prior_lineage_returns_without_tuple_set_query() -> None:
    class Client:
        catalog_database = DATABASE

        def __init__(self) -> None:
            self.sql: list[str] = []

        def query(
            self,
            sql: str,
            _params: Mapping[str, Any],
            *,
            timeout_ms: int,
        ) -> Sequence[Mapping[str, Any]]:
            assert timeout_ms > 0
            self.sql.append(sql)
            return ()

    client = Client()
    reader = ClickHouseCurrentBindingReader(client, database=DATABASE)
    context = PostgresSnapshotContext(
        organization_id=ORG,
        workspace_id=WORKSPACE,
        project_ids=(PROJECT,),
        catalog_epoch=1,
        catalog_revision=2,
        projection_version=1,
        snapshot_cutoff=NOW,
    )

    assert (
        reader.read_current(
            context=context,
            source_adapter=SourceAdapter.DATASET_COLUMN,
            at_revision=1,
            build_token=BUILD,
        )
        == ()
    )
    assert len(client.sql) == 1
    assert "property_catalog_activations" in client.sql[0]


class _SpanAuditClient:
    source_database = "source_ch25"

    def __init__(self) -> None:
        self.sql: list[str] = []
        self.params: list[Mapping[str, Any]] = []

    def query(
        self,
        sql: str,
        _params: Mapping[str, Any],
        *,
        timeout_ms: int,
        settings: Mapping[str, Any],
    ) -> Sequence[Mapping[str, Any]]:
        assert timeout_ms > 0
        assert settings["readonly"] == 2
        self.sql.append(sql)
        self.params.append(_params)
        return (
            {
                "source_count": 1,
                "audit_h1_xor": 10,
                "audit_h1_sum": 10,
                "audit_h2_xor": 20,
                "audit_h2_sum": 20,
                "audit_h3_xor": 30,
                "audit_h3_sum": 30,
                "audit_h4_xor": 40,
                "audit_h4_sum": 40,
                "state_conflict_count": 0,
            },
        )


def test_source_audit_is_one_select_per_bounded_shard_and_matches_paged_components() -> (
    None
):
    client = _SpanAuditClient()
    reader = CanonicalSpanSourceReader(
        client,
        source_database="source_ch25",
        catalog_database=DATABASE,
        deadline=SharedCatalogDeadline(wall_ms=8_500),
    )
    frozen = FrozenSpanSource(
        (PROJECT,),
        datetime(2026, 8, 14, 12, tzinfo=UTC),
        datetime(2026, 8, 14, 13, tzinfo=UTC),
        77,
    )
    accumulator = SpanAuditAccumulator()
    accumulator.add("".join(f"{value:016x}" for value in (10, 20, 30, 40)))

    proof = reader.audit(frozen)

    assert proof.digest == accumulator.proof.digest
    assert len(client.sql) == 1
    assert client.params[0]["catalog_project_ids"] == (PROJECT,)
    assert "groupBitXor(audit_h1)" in client.sql[0]
    assert "state_conflict_count" in client.sql[0]
    assert "_version <= %(catalog_source_version_fence)s" not in client.sql[0]
    assert SPAN_AUDIT_CUTOFF_LABEL == "clickhouse_audit_generation"


def test_source_audit_splits_one_large_project_into_weekly_time_shards() -> None:
    client = _SpanAuditClient()
    reader = CanonicalSpanSourceReader(
        client,
        source_database="source_ch25",
        catalog_database=DATABASE,
        deadline=SharedCatalogDeadline(wall_ms=8_500),
    )
    since = datetime(2026, 8, 1, 12, tzinfo=UTC)
    split = since + timedelta(days=7)
    until = since + timedelta(days=8)
    frozen = FrozenSpanSource((PROJECT,), since, until, 77)

    proof = reader.audit(frozen)

    assert proof.count == 2
    assert [params["catalog_project_ids"] for params in client.params] == [
        (PROJECT,),
        (PROJECT,),
    ]
    assert [
        (params["catalog_since"], params["catalog_until"]) for params in client.params
    ] == [(since, split), (split, until)]


def test_source_audit_combines_disjoint_project_proofs_with_uint64_wrap() -> None:
    class Client(_SpanAuditClient):
        def query(
            self,
            sql: str,
            params: Mapping[str, Any],
            *,
            timeout_ms: int,
            settings: Mapping[str, Any],
        ) -> Sequence[Mapping[str, Any]]:
            super().query(
                sql,
                params,
                timeout_ms=timeout_ms,
                settings=settings,
            )
            project_ids = params["catalog_project_ids"]
            assert isinstance(project_ids, tuple) and len(project_ids) == 1
            if project_ids == (PROJECT,):
                components = (10, 20, 30, 40)
                count = 1
                conflicts = 0
            else:
                assert project_ids == (OTHER_PROJECT,)
                components = ((1 << 64) - 5, 7, 9, 11)
                count = 2
                conflicts = 1
            return (
                {
                    "source_count": count,
                    **{
                        f"audit_h{index}_xor": value
                        for index, value in enumerate(components, start=1)
                    },
                    **{
                        f"audit_h{index}_sum": value
                        for index, value in enumerate(components, start=1)
                    },
                    "state_conflict_count": conflicts,
                },
            )

    client = Client()
    reader = CanonicalSpanSourceReader(
        client,
        source_database="source_ch25",
        catalog_database=DATABASE,
        deadline=SharedCatalogDeadline(wall_ms=8_500),
    )
    frozen = FrozenSpanSource(
        (OTHER_PROJECT, PROJECT),
        datetime(2026, 8, 14, 12, tzinfo=UTC),
        datetime(2026, 8, 14, 13, tzinfo=UTC),
        77,
    )

    proof = reader.audit(frozen)

    assert [params["catalog_project_ids"] for params in client.params] == [
        (PROJECT,),
        (OTHER_PROJECT,),
    ]
    assert proof.count == 3
    assert proof.xor == (10 ^ ((1 << 64) - 5), 20 ^ 7, 30 ^ 9, 40 ^ 11)
    assert proof.total == (5, 27, 39, 51)
    assert proof.state_conflict_count == 1


def test_project_move_changes_every_paged_and_aggregate_audit_hash_input() -> None:
    paged = _paged_payload_with_audit_sql("`source_ch25`.`spans`")
    aggregate = _aggregate_audit_sql("`source_ch25`.`spans`")

    for sql in (paged, aggregate):
        assert sql.count("toJSONString(tuple(project_id, observation_type") == 4


def test_shared_catalog_deadline_fails_closed_when_cancelled() -> None:
    cancelled = False
    deadline = SharedCatalogDeadline(
        wall_ms=8_500,
        cancelled=lambda: cancelled,
    )

    assert deadline.remaining_ms(cap_ms=500) > 0
    cancelled = True

    with pytest.raises(PropertyCatalogPublishError, match="cancelled"):
        deadline.remaining_ms(cap_ms=500)


def test_shared_catalog_deadline_requires_callable_cancellation_probe() -> None:
    with pytest.raises(TypeError, match="cancellation probe"):
        SharedCatalogDeadline(wall_ms=8_500, cancelled=False)  # type: ignore[arg-type]
