"""Real repair pair, coordinator locks, journals and proof; external I/O is fake."""

from __future__ import annotations

import fcntl
import hashlib
import json
from dataclasses import replace
from functools import partial
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

import pytest

from tracer.services.clickhouse.v2.property_catalog import (
    durable_native_writer as native,
)
from tracer.services.clickhouse.v2.property_catalog import (
    native_write_proof as proof_module,
)
from tracer.services.clickhouse.v2.property_catalog import (
    publication_journal,
)
from tracer.services.clickhouse.v2.property_catalog.native_write_journal import (
    NativeQuarantineReason,
    NativeTerminalRepairReceipt,
    NativeWriteAttempt,
    NativeWriteJournalBusy,
    NativeWriteScope,
    native_parameters_sha256,
)
from tracer.services.clickhouse.v2.property_catalog.native_write_proof import (
    _COLUMNS,
    NativeWriteProof,
    NativeWriteProofError,
    coverage_query,
)
from tracer.services.clickhouse.v2.property_catalog.state_store import _activation_row
from tracer.services.clickhouse.v2.property_catalog.terminal_repair_execution import (
    NativeTerminalRepairExecutor,
)
from tracer.services.clickhouse.v2.property_catalog.terminal_repair_intent import (
    FrozenTerminalRepairIntent,
)
from tracer.tests import test_property_catalog_native_write_proof as proof_fixture
from tracer.tests.test_property_catalog_hot_drain import _CoordinatorClient
from tracer.tests.test_property_catalog_native_write_journal import path
from tracer.tests.test_property_catalog_terminal_repair_intent import (
    publication_evidence,
)
from tracer.tests.test_property_catalog_write_admission import Probe, admit

ACTIVE = "property_catalog_activations"
SOURCE = "property_catalog_source_streams"


def wire_result(rows):
    columns = tuple(rows[0]) if rows else ()
    return (
        [tuple(row[column] for column in columns) for row in rows],
        [(column, "") for column in columns],
        {},
    )


class PairHarness:
    def __init__(self, directory, monkeypatch):
        self.directory = directory
        recovery = directory / "coordinator"
        recovery.mkdir(mode=0o700)
        # Configure the database before the real publication document is frozen.
        monkeypatch.setattr(
            _CoordinatorClient, "catalog_database", proof_fixture.DATABASE
        )
        self.harness, values = publication_evidence(recovery)
        installation = proof_fixture.installation
        monkeypatch.setattr(
            proof_fixture,
            "installation",
            lambda environment="development": replace(
                installation(environment), catalog_epoch=values["lease"].catalog_epoch
            ),
        )
        self.identity = proof_fixture.installation()
        self.probe = Probe(2)
        self.admission = admit(directory, self.probe, identity=self.identity)
        # Only inject the HTTP observation transport, retaining actual attestation.
        monkeypatch.setattr(
            proof_module,
            "reattest_catalog_writes",
            partial(proof_module.reattest_catalog_writes, http_read=self.probe.http),
        )
        self.sent, self.coverage_reads, self.settlement_reads = [], [], []
        self.visible_logs, self.lose_ack, self.missing_coverage = set(), set(), set()
        self.log_changes = {}
        self.coverage_rounds = {ACTIVE: 0, SOURCE: 0}
        for connection in self.probe.connections:
            driver = connection.driver
            driver.host, driver.port = connection.native_member_host, 9000
            metadata_read = driver.execute_read
            driver.execute_read = partial(self.read, connection.name, metadata_read)
        self.transport = Mock(side_effect=self.send)
        monkeypatch.setattr(native, "insert_once", self.transport)
        self.restart()
        record = publication_journal._record(
            values["publication_document"]["publication"]["record"]
        )
        self.original_row = _activation_row(record)
        self.original_token = (
            f"property-catalog-activation-v1:{record.build_token}:"
            f"{record.activation_sha256}"
        )
        self.lose_ack.add(self.original_token)
        with pytest.raises(TimeoutError, match="lost native ACK"):
            self.writer.insert(
                f"`{self.writer.database}`.`{ACTIVE}`",
                (self.original_row,),
                columns=_COLUMNS[ACTIVE],
                timeout_ms=5000,
                deduplication_token=self.original_token,
            )
        self.scope = NativeWriteScope.from_rows(
            ACTIVE, (self.original_row,), self.identity
        )
        with self.writer._journal() as journal, journal.scope(self.scope) as scoped:
            quarantine = scoped.quarantine(
                scoped.capture_quarantine(NativeQuarantineReason.PUBLICATION_BLOCKED)
            )
            with scoped.session(ACTIVE, self.original_token) as session:
                self.original = session.load()
        assert self.original.state == "sent"
        assert [entry.query_id for entry in quarantine.pending] == [
            self.original.query_id
        ]
        self.intent = FrozenTerminalRepairIntent.from_evidence(
            **{**values, "quarantine": quarantine}
        )
        self.publication_before = self.harness.document()
        self.transport.reset_mock()
        self.probe.calls.clear()

    @property
    def coordinator(self):
        return self.harness.base.coordinator

    @property
    def workspace_key(self):
        return self.harness.workspace_key()

    @property
    def executor(self):
        return NativeTerminalRepairExecutor(self.writer, self.coordinator)

    @property
    def marker(self):
        return self.coordinator._recovery_journal.load_record(
            self.workspace_key + ":activation"
        )

    def restart(self):
        proof = NativeWriteProof(
            directory=self.directory,
            identity=self.identity,
            admission=self.admission,
            connections=self.probe.connections,
        )
        driver = SimpleNamespace(
            host=self.probe.connections[0].driver.host,
            port=9000,
            database=self.admission.database,
            user="catalog_writer",
            server_enforced_readonly=False,
        )
        self.writer = native.DurableNativeCatalogWriter(
            driver,
            directory=self.directory,
            proof=proof,
            member_name=self.probe.connections[0].name,
        )
        self.harness.base.restart()
        self.coordinator._client._durable_writer = self.writer

    def state(self):
        return self.coordinator._recovery_journal.load_record(
            f"{self.workspace_key}:terminal-repair:{self.intent.lease.build_lease_sha256}"
        )

    def write_for(self, table):
        return next(
            write for write in self.intent.binding.writes if write.table == table
        )

    def attempt(self, table):
        write = self.write_for(table)
        with self.writer._journal() as journal, journal.scope(self.scope) as scoped:
            with scoped.session(table, write.deduplication_token) as session:
                return session.load()

    def assert_workspace_locked(self):
        filename = hashlib.sha256(self.workspace_key.encode()).hexdigest() + ".lock"
        with (self.harness.base.directory / filename).open("rb") as lock:
            with pytest.raises(BlockingIOError):
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def send(self, driver, **kwargs):
        token = kwargs["settings"]["insert_deduplication_token"]
        table = next(
            table
            for table in (ACTIVE, SOURCE)
            if kwargs["sql"].startswith(f"INSERT INTO {self.writer.database}.{table} (")
        )
        kwargs["before_send"]()
        saved = NativeWriteAttempt(path(self.directory, table, token).read_bytes())
        assert saved.state == "sent" and saved.query_id == kwargs["query_id"]
        assert UUID(saved.query_id).version == 4
        assert list(saved.parameters) == kwargs["values"]
        assert dict(saved.settings) == kwargs["settings"]
        assert saved.sql == kwargs["sql"]
        if token.startswith("property-catalog-terminal-repair-v1:"):
            self.assert_workspace_locked()
            with self.writer._journal() as journal:
                with pytest.raises(NativeWriteJournalBusy):
                    with journal.scope(self.scope):
                        pytest.fail("terminal send did not hold the native BUILD lock")
            assert self.coordinator._recovery_journal.is_revoked(
                self.workspace_key, self.intent.lease.build_lease_sha256
            )
            assert self.state()["complete"] is False
            write = self.write_for(table)
            row = self.intent.rows[self.intent.binding.writes.index(write)]
            assert saved.rows == (row,)
            assert saved.parameters_sha256 == write.parameters_sha256
            if table == SOURCE:
                disabled = self.write_for(ACTIVE)
                completed = NativeWriteAttempt(
                    path(
                        self.directory, ACTIVE, disabled.deduplication_token
                    ).read_bytes()
                )
                assert completed.state == "complete"
                assert completed["completion"]["members"] == tuple(
                    member.name for member in self.admission.members
                )
        self.sent.append(saved)
        if token in self.lose_ack:
            self.lose_ack.remove(token)
            raise TimeoutError("lost native ACK")
        return len(kwargs["values"])

    def read(self, member, metadata_read, sql, params, *, timeout_ms, settings):
        assert sql.startswith("SELECT") and settings["readonly"] == 2
        assert 0 < timeout_ms <= 30_000
        if "FROM system.query_log" in sql:
            query_id = params["query_id"]
            self.settlement_reads.append((member, query_id))
            if query_id not in self.visible_logs:
                return wire_result([])
            attempt = next(
                attempt for attempt in self.sent if attempt.query_id == query_id
            )
            row = {
                "query_id": attempt.query_id,
                "type": "QueryFinish",
                "exception_code": 0,
                "current_database": self.writer.database,
                "user": attempt.user,
                "query": attempt.sql,
                "log_comment": attempt.parameters_sha256,
                "written_rows": attempt.row_count,
                "async_insert": "0",
                "insert_quorum": "2",
                "insert_quorum_parallel": "1",
            }
            row.update(self.log_changes)
            return wire_result([row])
        if sql.startswith("SELECT toUInt8("):
            table = next(
                table
                for table in (ACTIVE, SOURCE)
                if f"FROM `{self.writer.database}`.`{table}` WHERE" in sql
            )
            write = self.write_for(table)
            row = self.intent.rows[self.intent.binding.writes.index(write)]
            assert (sql, params) == coverage_query(
                self.writer.database, table, (row,), _COLUMNS[table]
            )
            if member == self.admission.members[0].name:
                self.coverage_rounds[table] += 1
            round_number = self.coverage_rounds[table]
            self.coverage_reads.append((member, table, round_number))
            missing = (
                member == self.admission.members[-1].name
                and (table, round_number) in self.missing_coverage
            )
            return wire_result([{"r0": 0 if missing else 1}])
        return metadata_read(sql, params, timeout_ms=timeout_ms, settings=settings)

    def confirm_marker(self, *, coordinator_hook=False):
        def confirm():
            self.assert_workspace_locked()
            if coordinator_hook:
                return self.coordinator._confirm_terminal_marker(
                    self.intent.lease, self.marker
                )
            return self.executor.confirm_marker_serialized(
                self.intent.lease, self.marker, timeout_ms=5000
            )

        return self.coordinator._serializer.serialize(self.workspace_key, confirm)

    def assert_original_unchanged(self):
        assert (
            path(self.directory, ACTIVE, self.original_token).read_bytes()
            == self.original.encode()
        )
        assert self.harness.document() == self.publication_before
        assert all(
            query_id != self.original.query_id for _, query_id in self.settlement_reads
        )
        assert all(attempt["table"] in (ACTIVE, SOURCE) for attempt in self.sent)


@pytest.fixture
def pair(tmp_path, monkeypatch):
    return PairHarness(tmp_path, monkeypatch)


def assert_completed_pair(pair, receipt):
    assert type(receipt) is NativeTerminalRepairReceipt
    assert receipt.binding == pair.intent.binding
    completed = tuple(pair.attempt(table) for table in (ACTIVE, SOURCE))
    assert all(attempt.state == "complete" for attempt in completed)
    assert receipt.attempt_record_sha256s == tuple(
        attempt["record_sha256"] for attempt in completed
    )
    for attempt, write, row in zip(
        completed, pair.intent.binding.writes, pair.intent.rows, strict=True
    ):
        assert attempt.rows == (row,)
        assert attempt.parameters_sha256 == write.parameters_sha256
        assert attempt.parameters_sha256 == native_parameters_sha256(
            attempt.columns, attempt.rows
        )
        assert attempt["completion"]["members"] == tuple(
            member.name for member in pair.admission.members
        )
    assert [attempt.rows[0]["status"] for attempt in completed] == [
        "disabled",
        "failed",
    ]
    assert pair.state()["complete"] is True
    assert pair.state()["receipt_sha256s"] == list(receipt.attempt_record_sha256s)
    with pair.writer._journal() as journal, journal.scope(pair.scope) as scoped:
        assert scoped.quarantine_binding == pair.intent.binding.quarantine
        assert [entry.query_id for entry in scoped.pending()] == [
            pair.original.query_id
        ]
    pair.assert_original_unchanged()


def test_successful_pair_exact_rows_under_both_locks_and_positive_marker_confirmation(
    pair,
):
    assert pair.confirm_marker() is None  # An absent repair archive is not proof.
    receipt = pair.executor.apply(pair.intent, timeout_ms=5000)
    assert pair.transport.call_count == 2
    assert [attempt["table"] for attempt in pair.sent] == [ACTIVE, ACTIVE, SOURCE]
    assert len({attempt.query_id for attempt in pair.sent}) == len(pair.sent)
    assert pair.coverage_reads == [
        (member.name, table, round_number)
        for round_number in (1, 2)
        for table in (ACTIVE, SOURCE)
        for member in pair.admission.members
    ]
    assert_completed_pair(pair, receipt)
    pair.restart()
    calls_before = pair.transport.call_count
    for coordinator_hook in (False, True):
        before = len(pair.coverage_reads)
        assert pair.confirm_marker(coordinator_hook=coordinator_hook) == receipt
        assert len(pair.coverage_reads) - before == 2 * len(
            pair.intent.binding.writes
        ) * len(pair.admission.members)
        assert pair.transport.call_count == calls_before
    assert pair.executor.apply(pair.intent, timeout_ms=5000) == receipt
    assert pair.transport.call_count == calls_before
    assert_completed_pair(pair, receipt)


def test_first_write_lost_ack_blocks_source_until_positive_original_query_proof(pair):
    pair.lose_ack.add(pair.write_for(ACTIVE).deduplication_token)
    with pytest.raises(TimeoutError, match="lost native ACK"):
        pair.executor.apply(pair.intent, timeout_ms=5000)
    disabled = pair.attempt(ACTIVE)
    assert disabled.state == "sent" and pair.attempt(SOURCE) is None
    assert pair.state()["complete"] is False
    pair.restart()
    for operation in (
        lambda: pair.executor.apply(pair.intent, timeout_ms=5000),
        pair.confirm_marker,
        lambda: pair.confirm_marker(coordinator_hook=True),
    ):
        with pytest.raises(native.NativeWriteUnresolved, match="still unresolved"):
            operation()
        assert pair.transport.call_count == 1 and pair.attempt(SOURCE) is None
    pair.visible_logs.add(disabled.query_id)
    # Proof-only confirmation can settle disabled, but must not create source.
    with pytest.raises(native.NativeWriteUnresolved, match="receipt is missing"):
        pair.confirm_marker(coordinator_hook=True)
    assert pair.attempt(ACTIVE).state == "complete"
    assert pair.attempt(ACTIVE).query_id == disabled.query_id
    assert pair.attempt(SOURCE) is None and pair.transport.call_count == 1
    assert pair.state()["complete"] is False
    receipt = pair.executor.apply(pair.intent, timeout_ms=5000)
    assert pair.transport.call_count == 2
    assert {query_id for _, query_id in pair.settlement_reads} == {disabled.query_id}
    assert_completed_pair(pair, receipt)


def test_native_uuid_reservation_preserves_typed_bytes_through_pair_and_confirmation(
    pair,
):
    original_intent = pair.intent
    reservation = original_intent.reservation
    uuid_fields = (
        "organization_id",
        "workspace_id",
        "build_token",
        "producer_stream_id",
    )
    for field in uuid_fields:
        reservation[field] = UUID(str(reservation[field]))
    pair.intent = FrozenTerminalRepairIntent.from_evidence(
        database=original_intent.database,
        quarantine=original_intent.binding.quarantine,
        lease=original_intent.lease,
        reservation=reservation,
        publication_document=original_intent.publication_document,
        repaired_at=original_intent.rows[-1]["updated_at"],
    )
    frozen = pair.intent.encode()
    assert FrozenTerminalRepairIntent.decode(frozen).encode() == frozen
    assert pair.write_for(ACTIVE) == original_intent.binding.writes[0]
    # UUID values compare as the same SQL identity, but must not be stringified
    # in the native journal's exact typed payload or its parameter digest.
    assert (
        pair.write_for(SOURCE).parameters_sha256
        != original_intent.binding.writes[-1].parameters_sha256
    )
    for field in uuid_fields:
        assert type(pair.intent.reservation[field]) is UUID
        assert type(pair.intent.rows[-1][field]) is UUID

    receipt = pair.executor.apply(pair.intent, timeout_ms=5000)
    assert pair.transport.call_count == 2
    assert [attempt["table"] for attempt in pair.sent] == [ACTIVE, ACTIVE, SOURCE]
    assert_completed_pair(pair, receipt)
    completed = pair.attempt(SOURCE)
    for attempt in (pair.sent[-1], completed):
        encoded_parameters = json.loads(attempt.encode())["parameters"][0]
        for field in uuid_fields:
            column = attempt.columns.index(field)
            assert type(attempt.rows[0][field]) is UUID
            assert type(attempt.parameters[0][column]) is UUID
            assert attempt.parameters[0][column] == reservation[field]
            assert encoded_parameters[column] == ["uuid", str(reservation[field])]
        assert attempt.parameters_sha256 == pair.write_for(SOURCE).parameters_sha256
    assert pair.sent[-1].query_id == completed.query_id

    pair.restart()
    pair.intent = FrozenTerminalRepairIntent.decode(frozen)
    assert pair.executor.confirm(pair.intent, timeout_ms=5000) == receipt
    assert pair.confirm_marker() == receipt
    assert pair.confirm_marker(coordinator_hook=True) == receipt
    assert pair.transport.call_count == 2
    assert pair.attempt(SOURCE).encode() == completed.encode()
    assert pair.intent.encode() == frozen
    assert_completed_pair(pair, receipt)


def test_second_write_lost_ack_restart_uses_query_proof_without_resending_either_row(
    pair,
):
    pair.lose_ack.add(pair.write_for(SOURCE).deduplication_token)
    with pytest.raises(TimeoutError, match="lost native ACK"):
        pair.executor.apply(pair.intent, timeout_ms=5000)
    disabled, failed = pair.attempt(ACTIVE), pair.attempt(SOURCE)
    assert disabled.state == "complete" and failed.state == "sent"
    pair.restart()
    for operation in (
        lambda: pair.executor.apply(pair.intent, timeout_ms=5000),
        pair.confirm_marker,
        lambda: pair.confirm_marker(coordinator_hook=True),
    ):
        with pytest.raises(native.NativeWriteUnresolved, match="still unresolved"):
            operation()
        assert pair.transport.call_count == 2
        assert pair.attempt(SOURCE).encode() == failed.encode()
        assert pair.state()["complete"] is False
    pair.visible_logs.add(failed.query_id)
    receipt = pair.confirm_marker(coordinator_hook=True)
    assert pair.attempt(SOURCE).query_id == failed.query_id
    assert pair.attempt(SOURCE).acknowledgement["kind"] == "query_log_finish"
    assert pair.attempt(ACTIVE).encode() == disabled.encode()
    assert {query_id for _, query_id in pair.settlement_reads} == {failed.query_id}
    assert pair.transport.call_count == 2
    assert_completed_pair(pair, receipt)
    pair.restart()
    assert pair.confirm_marker() == receipt
    assert pair.transport.call_count == 2


@pytest.mark.parametrize(
    "table,round_number", [(ACTIVE, 1), (SOURCE, 1), (ACTIVE, 2), (SOURCE, 2)]
)
def test_partial_member_coverage_blocks_initial_or_final_pair_completion(
    pair, table, round_number
):
    pair.missing_coverage.add((table, round_number))
    with pytest.raises(NativeWriteProofError, match="coverage absent"):
        pair.executor.apply(pair.intent, timeout_ms=5000)
    sent_count = 1 if (table, round_number) == (ACTIVE, 1) else 2
    assert pair.transport.call_count == sent_count
    assert pair.state()["complete"] is False
    assert pair.state()["receipt_sha256s"] is None
    if round_number == 1:
        assert pair.attempt(table).state == "acknowledged"
    else:
        assert all(
            pair.attempt(table).state == "complete" for table in (ACTIVE, SOURCE)
        )
    pair.assert_original_unchanged()
    pair.missing_coverage.clear()
    pair.restart()
    if sent_count == 1:
        with pytest.raises(native.NativeWriteUnresolved, match="receipt is missing"):
            pair.confirm_marker()
        assert pair.transport.call_count == sent_count
        receipt = pair.executor.apply(pair.intent, timeout_ms=5000)
    else:
        receipt = pair.confirm_marker()
    assert pair.transport.call_count == 2
    assert_completed_pair(pair, receipt)


@pytest.mark.parametrize("change", ["query_id", "log_comment", "query", "written_rows"])
def test_lost_ack_requires_query_log_for_exact_uuid_sql_digest_and_row_count(
    pair, change
):
    pair.lose_ack.add(pair.write_for(SOURCE).deduplication_token)
    with pytest.raises(TimeoutError):
        pair.executor.apply(pair.intent, timeout_ms=5000)
    failed = pair.attempt(SOURCE)
    pair.visible_logs.add(failed.query_id)
    pair.log_changes[change] = {
        "query_id": pair.original.query_id,
        "log_comment": pair.original.parameters_sha256,
        "query": pair.original.sql,
        "written_rows": failed.row_count + 1,
    }[change]
    with pytest.raises(native.NativeWriteUnresolved, match="still unresolved"):
        pair.confirm_marker()
    assert pair.attempt(SOURCE).encode() == failed.encode()
    assert pair.state()["complete"] is False
    pair.log_changes.clear()
    receipt = pair.confirm_marker()
    assert pair.transport.call_count == 2
    assert_completed_pair(pair, receipt)
