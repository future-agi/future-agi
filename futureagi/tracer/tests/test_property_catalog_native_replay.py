"""Real publisher/state-store/adapter/journal, with fake SQL and native transport."""

from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest

from tracer.services.clickhouse.v2.property_catalog.dev_runtime import (
    NativeCatalogClient,
)
from tracer.services.clickhouse.v2.property_catalog.durable_native_writer import (
    NativeCheckpointReloadRequired,
    NativeWriteUnresolved,
)
from tracer.services.clickhouse.v2.property_catalog.mutation_lock import (
    InProcessCatalogMutationSerializer,
)
from tracer.services.clickhouse.v2.property_catalog.native_write_journal import (
    NativeWriteJournal,
    native_parameters_sha256,
)
from tracer.services.clickhouse.v2.property_catalog.native_write_proof import _COLUMNS
from tracer.services.clickhouse.v2.property_catalog.publisher import (
    CatalogWriteLease,
    ClickHouseEnvelopePublisher,
)
from tracer.services.clickhouse.v2.property_catalog.state_store import (
    ClickHouseCatalogStateStore,
    PropertyCatalogStateConflict,
    _checkpoint_row,
    _checkpoint_token,
)
from tracer.tests import test_property_catalog_control_plane as fixture
from tracer.tests.test_property_catalog_durable_native_writer import setup

CHECKPOINT = "property_catalog_checkpoints"
DELIVERY = "property_catalog_deliveries"


def harness(tmp_path, monkeypatch, target):
    writer, proof, transport, _, options = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(fixture, "DATABASE", writer.database)
    monkeypatch.setattr(fixture._PublisherClient, "catalog_database", writer.database)
    plan = fixture._plan()
    stream = fixture._value_stream(plan)
    observed = fixture._PublisherClient(plan=plan, stream=stream)
    state = SimpleNamespace(
        loss=False, target=target, sent=[], rows=[], hide=False, reads=0
    )

    def send(*args, **kwargs):
        kwargs["before_send"]()
        table = next(table for table in _COLUMNS if f".{table} " in kwargs["sql"])
        rows = tuple(
            dict(zip(_COLUMNS[table], row, strict=True)) for row in kwargs["values"]
        )
        state.sent.append((table, kwargs["query_id"]))
        observed.inserts.append((f"`{writer.database}`.`{table}`", rows))
        if table == CHECKPOINT:
            state.rows.extend(deepcopy(rows))
        if state.loss and table == target:
            raise TimeoutError("lost response after visibility")
        return len(rows)

    transport.side_effect = send
    client = NativeCatalogClient(
        writer.driver, database=writer.database, durable_writer=writer
    )

    def query(sql, params, *, timeout_ms):
        if CHECKPOINT in sql:
            state.reads += 1
            rows = state.rows if not state.hide else []
            # Exercise the actual native UUID representation on positive replay.
            return tuple(
                {
                    **row,
                    **{
                        k: UUID(row[k])
                        for k in (
                            "organization_id",
                            "workspace_id",
                            "build_token",
                            "producer_stream_id",
                            "run_id",
                        )
                    },
                }
                for row in rows
            )
        return observed.query(sql, params, timeout_ms=timeout_ms)

    client.query = query

    def store():
        return ClickHouseCatalogStateStore(
            client,
            database=writer.database,
            serializer=InProcessCatalogMutationSerializer(),
        )

    def publish():
        publisher = ClickHouseEnvelopePublisher(
            client=client,
            database=writer.database,
            lease=CatalogWriteLease(
                organization_id=fixture.ORG,
                workspace_id=fixture.WORKSPACE,
                catalog_epoch=1,
                catalog_revision=2,
                build_token=fixture.BUILD,
                projection_version=1,
                source_adapter=stream.source_adapter,
                producer_stream_id=stream.producer_stream_id,
                build_plan_json=plan.canonical_json,
                build_lease_sha256=plan.sha256,
                expires_at=fixture.NOW + timedelta(minutes=5),
            ),
            now=lambda: fixture.NOW,
        )
        return publisher.publish(
            fixture._value_envelope(plan, stream), value_rows=(fixture._value_row(),)
        )

    return writer, proof, state, client, store, publish


@pytest.mark.parametrize("target", [CHECKPOINT, DELIVERY])
def test_visible_replay_waits_for_original_settlement_without_resending(
    tmp_path, monkeypatch, target
):
    writer, proof, state, client, store, publish = harness(
        tmp_path, monkeypatch, target
    )
    operation = (
        (lambda: store().append(fixture._checkpoint_write()))
        if target == CHECKPOINT
        else publish
    )
    state.loss = True
    with pytest.raises(TimeoutError):
        operation()
    original = deepcopy(state.sent)
    reads = state.reads
    state.loss = False
    with pytest.raises(NativeWriteUnresolved, match="still unresolved"):
        operation()
    assert state.sent == original
    if target == CHECKPOINT:
        assert state.reads == reads  # Pending settlement precedes version selection.
    proof.settled.return_value = True
    operation()
    operation()
    assert state.sent == original
    if target == CHECKPOINT:
        assert [row["_version"] for row in state.rows] == [1]


@pytest.mark.parametrize("target", [CHECKPOINT, DELIVERY])
def test_prepared_replay_uses_original_generated_metadata_under_domain_authorization(
    tmp_path, monkeypatch, target
):
    writer, proof, state, client, store, publish = harness(
        tmp_path, monkeypatch, target
    )
    operation = (
        (lambda: store().append(fixture._checkpoint_write()))
        if target == CHECKPOINT
        else publish
    )
    insert = client.insert
    frozen = []

    def before_admission(table, rows, **kwargs):
        if table.endswith(f".`{target}`"):
            proof.attest.side_effect = RuntimeError("admission lost before dispatch")
            frozen.append(deepcopy(rows))
        return insert(table, rows, **kwargs)

    client.insert = before_admission
    with pytest.raises(RuntimeError, match="before dispatch"):
        operation()
    assert not any(table == target for table, _ in state.sent)
    client.insert = insert
    proof.attest.side_effect = None
    operation()
    assert len([table for table, _ in state.sent if table == target]) == 1
    if target == CHECKPOINT:
        assert native_parameters_sha256(
            _COLUMNS[target], (state.rows[0],)
        ) == native_parameters_sha256(_COLUMNS[target], frozen[0])
    else:
        # Exact stored journal fingerprint retained: delivery timestamp not now().
        token_row = frozen[0][0]
        token = f"property-catalog-v1:{token_row['envelope_id']}:delivery"
        with (
            NativeWriteJournal(writer.directory) as journal,
            journal.session(target, token) as session,
        ):
            assert session.load().rows[0]["delivered_at"] == token_row["delivered_at"]


@pytest.mark.parametrize(
    "field", ["_version", "source_cursor", "watermark", "source_rows", "build_token"]
)
def test_metadata_reuse_never_changes_checkpoint_version_or_logical_state(
    tmp_path, monkeypatch, field
):
    writer, proof, state, client, store, _ = harness(tmp_path, monkeypatch, CHECKPOINT)
    value = fixture._checkpoint_write()
    store().append(value)
    row = _checkpoint_row(value, now=fixture.NOW, version=1)
    row[field] = (
        row[field] + 1
        if type(row[field]) is int
        else (
            "ffffffff-ffff-4fff-8fff-ffffffffffff"
            if field == "build_token"
            else row[field] + "changed"
        )
    )
    before = tuple(state.sent)
    with pytest.raises((NativeWriteUnresolved, ValueError)):
        client.restore_insert_metadata(
            f"`{writer.database}`.`{CHECKPOINT}`",
            (row,),
            columns=_COLUMNS[CHECKPOINT],
            deduplication_token=_checkpoint_token(value.checkpoint, 1),
        )
    assert tuple(state.sent) == before


def test_visible_checkpoint_without_native_receipt_is_not_confirmed(
    tmp_path, monkeypatch
):
    writer, proof, state, client, store, _ = harness(tmp_path, monkeypatch, CHECKPOINT)
    value = fixture._checkpoint_write()
    row = _checkpoint_row(value, now=fixture.NOW, version=7)
    # Create only the write scope; there is still no receipt for this visible row.
    client.restore_insert_metadata(
        f"`{writer.database}`.`{CHECKPOINT}`",
        (row,),
        columns=_COLUMNS[CHECKPOINT],
        deduplication_token=_checkpoint_token(value.checkpoint, 7),
    )
    state.rows.append(row)
    with pytest.raises(NativeWriteUnresolved, match="lacks its exact native receipt"):
        store().append(value)
    assert not state.sent


@pytest.mark.parametrize("change", ["state", "version", "version-visible"])
def test_pending_checkpoint_cannot_be_evaded_by_allocating_or_selecting_another_version(
    tmp_path, monkeypatch, change
):
    writer, proof, state, client, store, _ = harness(tmp_path, monkeypatch, CHECKPOINT)
    value = fixture._checkpoint_write()
    version = 1 if change == "state" else 7
    row = _checkpoint_row(value, now=fixture.NOW, version=version)
    proof.attest.side_effect = RuntimeError("before dispatch")
    with pytest.raises(RuntimeError, match="before dispatch"):
        writer.insert(
            f"`{writer.database}`.`{CHECKPOINT}`",
            (row,),
            columns=_COLUMNS[CHECKPOINT],
            timeout_ms=5000,
            deduplication_token=_checkpoint_token(value.checkpoint, version),
        )
    proof.attest.side_effect = None
    if change == "state":
        value = replace(value, processed_rows=value.processed_rows + 1)
    elif change == "version-visible":
        state.rows.append(_checkpoint_row(value, now=fixture.NOW, version=2))
    with pytest.raises(
        (NativeWriteUnresolved, PropertyCatalogStateConflict),
        match="pending checkpoint|pending version/token",
    ):
        store().append(value)
    assert not state.sent
    if change == "state":
        assert state.reads == 0


def test_settling_newer_checkpoint_cannot_rewrite_callers_stale_state(
    tmp_path, monkeypatch
):
    writer, proof, state, client, store, _ = harness(tmp_path, monkeypatch, CHECKPOINT)
    old = fixture._checkpoint_write()
    current = replace(old, processed_rows=old.processed_rows + 1)
    row = _checkpoint_row(current, now=fixture.NOW, version=1)
    state.loss = True
    with pytest.raises(TimeoutError):
        writer.insert(
            f"`{writer.database}`.`{CHECKPOINT}`",
            (row,),
            columns=_COLUMNS[CHECKPOINT],
            timeout_ms=5000,
            deduplication_token=_checkpoint_token(current.checkpoint, 1),
        )
    state.loss = False
    proof.settled.return_value = True
    with pytest.raises(
        NativeCheckpointReloadRequired, match="reload its current state"
    ):
        store().append(old)
    assert len(state.sent) == 1 and state.reads == 0
    # A new controller cycle reloads the recovered checkpoint, not the old caller
    # object. Confirmation then succeeds without allocating another version.
    store().append(current)
    assert len(state.sent) == 1 and state.rows[0]["_version"] == 1
