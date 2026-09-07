"""Real domain replay may reach a later unsent delivery, never waive publication."""

import json
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest

from tracer.services.clickhouse.v2.property_catalog import (
    durable_native_writer as native,
)
from tracer.services.clickhouse.v2.property_catalog.models import EnvelopeCounts
from tracer.services.clickhouse.v2.property_catalog.native_write_journal import (
    NativePublicationBinding,
    NativeQuarantineReason,
    NativeWriteJournalError,
    NativeWriteScope,
    _key,
)
from tracer.services.clickhouse.v2.property_catalog.native_write_proof import _COLUMNS
from tracer.services.clickhouse.v2.property_catalog.publisher import (
    CatalogWriteLease,
    ClickHouseEnvelopePublisher,
    PropertyCatalogPublishError,
)
from tracer.tests import test_property_catalog_control_plane as domain
from tracer.tests.test_property_catalog_native_replay import DELIVERY, harness
from tracer.tests.test_property_catalog_native_write_proof import row_for


@pytest.fixture
def case(tmp_path, monkeypatch):
    writer, proof, state, client, _, _ = harness(tmp_path, monkeypatch, DELIVERY)
    plan = domain._plan()
    stream = next(s for s in plan.streams if s.role.value == "source_audit")
    deliveries = []
    original_query = client.query
    transport = native.insert_once
    original_send = transport.side_effect

    def send(*args, **kwargs):
        result = original_send(*args, **kwargs)
        if f".{DELIVERY} " in kwargs["sql"]:
            deliveries.extend(
                dict(zip(_COLUMNS[DELIVERY], values, strict=True))
                for values in kwargs["values"]
            )
        return result

    def query(sql, params, **kwargs):
        if DELIVERY not in sql:
            return original_query(sql, params, **kwargs)
        matches = [
            row
            for row in deliveries
            if all(str(row[k]) == str(v) for k, v in params.items())
        ]
        return tuple(
            {
                "envelope_id": row["envelope_id"],
                "payload_sha256": row["payload_sha256"],
                "identity_variants": 1,
            }
            for row in matches
        )

    transport.side_effect = send
    client.query = query
    lease = CatalogWriteLease(
        organization_id=plan.organization_id,
        workspace_id=plan.workspace_id,
        catalog_epoch=plan.catalog_epoch,
        catalog_revision=plan.catalog_revision,
        build_token=plan.build_token,
        projection_version=plan.projection_version,
        source_adapter=stream.source_adapter,
        producer_stream_id=stream.producer_stream_id,
        build_plan_json=plan.canonical_json,
        build_lease_sha256=plan.sha256,
        expires_at=domain.NOW + timedelta(minutes=5),
    )
    c = SimpleNamespace(
        writer=writer,
        proof=proof,
        transport=transport,
        client=client,
        lease=lease,
        state=state,
        now=domain.NOW,
        deliveries=deliveries,
    )

    def publish(envelope):
        return ClickHouseEnvelopePublisher(
            client=client,
            database=writer.database,
            lease=c.lease,
            now=lambda: c.now,
        ).publish(envelope)

    c.publish = publish
    c.first = replace(
        domain._value_envelope(plan, stream), counts=EnvelopeCounts(0, 0, 0, 0, 0)
    )
    c.payload = publish(c.first)
    c.next = replace(
        c.first, sequence=2, terminal=True, previous_payload_sha256=c.payload
    )
    c.first_row = deliveries[0]
    c.first_token = f"property-catalog-v1:{c.first_row['envelope_id']}:delivery"
    c.scope = NativeWriteScope.from_rows(DELIVERY, (c.first_row,), proof.identity)
    with writer._journal() as journal, journal.scope(c.scope) as scoped:
        c.index_path = (
            writer.directory / "native-write-attempts" / (scoped._key + ".json")
        )
    return c


def receipt(c, token, table=DELIVERY):
    with c.writer._journal() as journal, journal.scope(c.scope) as scoped:
        with scoped.session(table, token) as session:
            return session.load()


def prepare(c, *, sequence=2, changes=None, table=DELIVERY):
    row = dict(c.first_row) if table == DELIVERY else row_for(table)
    for field in (
        "organization_id",
        "workspace_id",
        "catalog_epoch",
        "catalog_revision",
        "build_token",
        "projection_version",
    ):
        if field in row:
            row[field] = c.first_row[field]
    if table == DELIVERY:
        row.update(
            sequence=sequence,
            terminal=1,
            _version=sequence,
            envelope_id="b" * 64,
            payload_sha256="c" * 64,
            previous_payload_sha256=c.payload,
        )
    row.update(changes or {})
    options = {
        "table": f"`{c.writer.database}`.`{table}`",
        "rows": (row,),
        "columns": _COLUMNS[table],
        "timeout_ms": 5000,
        "deduplication_token": f"future-{table}-{sequence}",
    }
    old = c.proof.attest.side_effect
    c.proof.attest.side_effect = TimeoutError("before send")
    try:
        with pytest.raises(TimeoutError, match="before send"):
            c.writer.insert(**options)
    finally:
        c.proof.attest.side_effect = old
    return options


def test_real_source_audit_restart_replays_first_then_authorized_terminal(case):
    c = case
    insert = c.client.insert
    frozen = []

    def fail_before_send(table, rows, **kwargs):
        if table.endswith(f".`{DELIVERY}`") and rows[0]["sequence"] == 2:
            frozen.append(dict(rows[0]))
            c.proof.attest.side_effect = TimeoutError("crash before terminal send")
        return insert(table, rows, **kwargs)

    c.client.insert = fail_before_send
    with pytest.raises(TimeoutError, match="before terminal send"):
        c.publish(c.next)
    c.client.insert = insert
    c.proof.attest.side_effect = None
    token = f"property-catalog-v1:{frozen[0]['envelope_id']}:delivery"
    before = receipt(c, token)
    first = receipt(c, c.first_token).encode()
    assert before.state == "prepared"
    index = json.loads(c.index_path.read_bytes())
    sent = c.transport.call_count
    # Reopen the real journal through a fresh writer/publisher; no cached session.
    c.writer = native.DurableNativeCatalogWriter(
        c.writer.driver,
        directory=c.writer.directory,
        proof=c.proof,
        member_name=c.writer.member.name,
    )
    c.client._durable_writer = c.writer
    c.proof.cover.reset_mock()
    assert c.publish(c.first) == c.payload
    assert c.proof.cover.call_count == 1
    assert c.transport.call_count == sent
    assert receipt(c, token).encode() == before.encode()
    assert receipt(c, c.first_token).encode() == first
    assert json.loads(c.index_path.read_bytes()) == index
    c.publish(c.next)
    finished = receipt(c, token)
    assert finished.state == "complete" and finished.query_id == before.query_id
    assert finished.parameters_sha256 == before.parameters_sha256
    assert finished.rows == before.rows and c.transport.call_count == sent + 1
    assert c.deliveries[-1] == before.rows[0]  # Native arrays normalize to tuples.
    with c.writer._journal() as journal, journal.scope(c.scope) as scoped:
        assert scoped.pending() == ()


@pytest.mark.parametrize("sequence", [2, 3, 17, (1 << 64) - 1])
def test_later_prepared_is_retained_without_global_recovery_permission(case, sequence):
    c = case
    future = prepare(c, sequence=sequence)
    raw = receipt(c, future["deduplication_token"]).encode()
    before = c.index_path.read_bytes()
    sent = c.transport.call_count
    assert c.publish(c.first) == c.payload
    assert c.index_path.read_bytes() == before
    assert receipt(c, future["deduplication_token"]).encode() == raw
    assert c.transport.call_count == sent
    with pytest.raises(native.NativeWriteUnresolved, match="Prepared"):
        c.writer.recover_scope(c.scope, timeout_ms=5000)


@pytest.mark.parametrize(
    "change",
    [
        {"sequence": 1},
        {"sequence": 0},
        {"producer_stream_id": str(UUID(int=999))},
        {"source_adapter": "dataset_column"},
        {"envelope_version": 2},
        {"envelope_format": "different"},
        {"transport": "kafka"},
    ],
)
def test_other_or_nonfuture_prepared_delivery_blocks_replay(case, change):
    c = case
    future = prepare(c, changes=change)
    raw = receipt(c, future["deduplication_token"]).encode()
    sent = c.transport.call_count
    with pytest.raises(native.NativeWriteUnresolved, match="Prepared"):
        c.publish(c.first)
    assert receipt(c, future["deduplication_token"]).encode() == raw
    assert c.transport.call_count == sent


def test_other_table_prepared_is_not_deferred(case):
    c = case
    prepare(c, table="property_definition_catalog")
    with pytest.raises(native.NativeWriteUnresolved, match="Prepared"):
        c.publish(c.first)


def test_positive_earlier_sequence_is_not_deferred(case):
    c = case
    third = replace(c.first, sequence=3)
    c.publish(third)
    prepare(c, sequence=2)
    with pytest.raises(native.NativeWriteUnresolved, match="Prepared"):
        c.publish(third)


def test_later_sent_requires_original_settlement_not_deferral(case):
    c = case
    future = prepare(c)
    with c.writer._journal() as journal, journal.scope(c.scope) as scoped:
        with scoped.session(DELIVERY, future["deduplication_token"]) as session:
            session.mark_sent(session.load())
    sent = c.transport.call_count
    with pytest.raises(native.NativeWriteUnresolved, match="still unresolved"):
        c.publish(c.first)
    assert c.transport.call_count == sent


@pytest.mark.parametrize("guard", ["quarantine", "closure"])
def test_no_deferral_after_quarantine_or_publication_closure(case, guard):
    c = case
    future = prepare(c)
    with c.writer._journal() as journal, journal.scope(c.scope) as scoped:
        if guard == "quarantine":
            scoped.quarantine(
                scoped.capture_quarantine(NativeQuarantineReason.BUILD_SUPERSEDED)
            )
        else:
            # Existing closure API refuses unresolved Prepared without creating a
            # closure. Its rejection itself must remain strict.
            binding = NativePublicationBinding(
                deduplication_token="active",
                parameters_sha256="a" * 64,
                publication_sha256="b" * 64,
                fence_sha256="c" * 64,
                build_lease_sha256="d" * 64,
                checkpoint_state_sha256s=("e" * 64,),
            )
            with pytest.raises(NativeWriteJournalError):
                scoped.begin_closure(binding)
            return
    with pytest.raises((native.NativeWriteUnresolved, NativeWriteJournalError)):
        c.publish(c.first)
    assert receipt(c, future["deduplication_token"]).state == "prepared"


def test_deferral_does_not_authorize_later_write_after_lease_expiry(case):
    c = case
    future = prepare(c)
    assert c.publish(c.first) == c.payload
    c.now = c.lease.expires_at
    sent = c.transport.call_count
    with pytest.raises(PropertyCatalogPublishError, match="expired"):
        c.publish(c.next)
    assert receipt(c, future["deduplication_token"]).state == "prepared"
    assert c.transport.call_count == sent


def test_corrupt_future_scope_cannot_be_deferred(case):
    c = case
    future = prepare(c)
    path = (
        c.writer.directory
        / "native-write-attempts"
        / (_key(DELIVERY, future["deduplication_token"]) + ".scope-binding")
    )
    document = json.loads(path.read_bytes())
    document["scope"]["build_token"] = str(UUID(int=999))
    path.write_text(json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n")
    with pytest.raises(NativeWriteJournalError):
        c.publish(c.first)
