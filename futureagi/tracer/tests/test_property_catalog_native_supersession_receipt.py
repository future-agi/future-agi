"""Exact reservation receipts and file recovery; only transport observations fake."""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
from unittest.mock import Mock
from uuid import UUID

import pytest

from tracer.services.clickhouse.v2.property_catalog import (
    durable_native_writer as native,
)
from tracer.services.clickhouse.v2.property_catalog.coordinator import (
    _SOURCE_STREAM_COLUMNS,
    _journal_source_row,
)
from tracer.services.clickhouse.v2.property_catalog.dev_runtime import (
    NativeCatalogClient,
)
from tracer.services.clickhouse.v2.property_catalog.native_write_journal import (
    NATIVE_WRITE_ATTEMPT_DIRECTORY,
    NativeQuarantineReason,
    NativeWriteAttempt,
    NativeWriteJournal,
    NativeWriteJournalError,
    NativeWriteScope,
)
from tracer.services.clickhouse.v2.property_catalog.native_write_proof import (
    _COLUMNS,
    NativeWriteProof,
    NativeWriteProofError,
)
from tracer.tests import test_property_catalog_native_write_proof as proof_fixture
from tracer.tests.test_property_catalog_durable_native_writer import setup
from tracer.tests.test_property_catalog_native_control_receipt import (
    assert_no_proofs,
    load,
    lost_ack,
    reset_proofs,
)
from tracer.tests.test_property_catalog_native_write_proof import row_for
from tracer.tests.test_property_catalog_supersession import _Harness

TABLE = "property_catalog_source_streams"
ACTIVE = "property_catalog_activations"


def options_for(writer, row):
    return {
        "table": f"`{writer.database}`.`{TABLE}`",
        "rows": (row,),
        "columns": _SOURCE_STREAM_COLUMNS,
        "timeout_ms": 5000,
        "deduplication_token": (
            f"property-catalog-supersession-v1:{row['build_token']}:{row['_version']}"
        ),
    }


def fixture_setup(tmp_path, monkeypatch):
    recovery = tmp_path / "coordinator"
    recovery.mkdir(mode=0o700)
    legacy = _Harness(recovery, with_active=False)
    installation = proof_fixture.installation
    monkeypatch.setattr(
        proof_fixture,
        "installation",
        lambda environment="development": replace(
            installation(environment), catalog_epoch=legacy.build.lease.catalog_epoch
        ),
    )
    writer, proof, transport, _, _ = setup(tmp_path, monkeypatch, table=TABLE)
    legacy.client.catalog_database = writer.database
    legacy.restart()
    return writer, proof, transport, legacy, options_for(writer, legacy.original)


@pytest.mark.parametrize("timestamp_text", [False, True])
def test_exact_reservation_native_uuid_and_timestamp_forms_reprove_full_metadata(
    tmp_path, monkeypatch, timestamp_text
):
    writer, proof, transport, _, options = fixture_setup(tmp_path, monkeypatch)
    writer.insert(**options)
    original = load(writer, options, TABLE)
    row = dict(options["rows"][0])
    for column in (
        "organization_id",
        "workspace_id",
        "build_token",
        "producer_stream_id",
    ):
        row[column] = UUID(row[column])
    if timestamp_text:
        for column, value in row.items():
            if isinstance(value, datetime):
                row[column] = value.strftime("%Y-%m-%d %H:%M:%S.%f")
    client = NativeCatalogClient(
        writer.driver, database=writer.database, durable_writer=writer
    )
    reset_proofs(proof)
    assert client.confirm_reservation_receipt(**{**options, "rows": (row,)}) is None
    assert load(writer, options, TABLE).encode() == original.encode()
    assert proof.cover.call_args.args == (TABLE, original.rows)
    assert proof.cover.call_args.kwargs["columns"] == original.columns
    assert proof.attest.call_count == 2
    proof.settled.assert_not_called()
    assert transport.call_count == 1


@pytest.mark.parametrize(
    "change",
    [
        {"source_adapter": "span_attribute"},
        {"producer_stream_id": str(UUID(int=99))},
        {"producer_stream_id": "not-a-uuid"},
        {"envelope_version": 1},
        {"envelope_version": False},
    ],
)
def test_nonreservation_source_rows_rejected_before_journal_or_proof(
    tmp_path, monkeypatch, change
):
    writer, proof, transport, _, options = fixture_setup(tmp_path, monkeypatch)
    with pytest.raises((ValueError, NativeWriteJournalError)):
        writer.confirm_reservation_receipt(
            **{**options, "rows": ({**options["rows"][0], **change},)}
        )
    assert not (tmp_path / NATIVE_WRITE_ATTEMPT_DIRECTORY).exists()
    assert_no_proofs(proof)
    transport.assert_not_called()


@pytest.mark.parametrize("table", tuple(_COLUMNS))
def test_public_receipt_methods_do_not_share_table_permissions(
    tmp_path, monkeypatch, table
):
    writer, proof, transport, _, options = fixture_setup(tmp_path, monkeypatch)
    options.update(
        table=f"`{writer.database}`.`{table}`",
        rows=(row_for(table),),
        columns=_COLUMNS[table],
    )
    if table != "property_catalog_activation_control_events":
        with pytest.raises(
            ValueError,
            match="native receipt requires one exact control row/columns/token",
        ):
            writer.confirm_receipt(**options)
    if table != TABLE:
        with pytest.raises(
            ValueError,
            match="native receipt requires one exact reservation row/columns/token",
        ):
            writer.confirm_reservation_receipt(**options)
    assert_no_proofs(proof)
    transport.assert_not_called()
    assert not (tmp_path / NATIVE_WRITE_ATTEMPT_DIRECTORY).exists()


@pytest.mark.parametrize(
    "field",
    [
        "started_at",
        "updated_at",
        "drain_deadline",
        "fenced_at",
        "_version",
        "status",
        "gap_reasons",
        "build_plan_json",
        "last_issued_sequence",
    ],
)
def test_reservation_receipt_binds_full_metadata(tmp_path, monkeypatch, field):
    writer, proof, transport, _, options = fixture_setup(tmp_path, monkeypatch)
    writer.insert(**options)
    original = load(writer, options, TABLE)
    row = dict(options["rows"][0])
    value = row[field]
    row[field] = (
        value + timedelta(microseconds=1)
        if isinstance(value, datetime)
        else options["rows"][0]["updated_at"]
        if value is None
        else value + 1
        if type(value) is int
        else ["changed"]
        if isinstance(value, (tuple, list))
        else "changed"
    )
    reset_proofs(proof)
    with pytest.raises(
        native.NativeWriteUnresolved, match="source reservation differs"
    ):
        writer.confirm_reservation_receipt(**{**options, "rows": (row,)})
    assert load(writer, options, TABLE).encode() == original.encode()
    assert_no_proofs(proof)
    assert transport.call_count == 1


@pytest.mark.parametrize(
    "mutation", ["rows", "missing", "extra", "columns", "token", "foreign"]
)
def test_reservation_shape_table_and_token_are_exact(tmp_path, monkeypatch, mutation):
    writer, proof, transport, _, options = fixture_setup(tmp_path, monkeypatch)
    row = dict(options["rows"][0])
    options["rows"] = (row,)
    if mutation == "rows":
        options["rows"] *= 2
    elif mutation == "missing":
        del row["updated_at"]
    elif mutation == "extra":
        row["extra"] = "not a column"
    elif mutation == "columns":
        options["columns"] = tuple(reversed(options["columns"]))
    elif mutation == "token":
        options["deduplication_token"] = "invalid\nreceipt"
    else:
        options["table"] = f"`property_catalog_dev_foreign`.`{TABLE}`"
    with pytest.raises((ValueError, NativeWriteJournalError)):
        writer.confirm_reservation_receipt(**options)
    assert_no_proofs(proof)
    transport.assert_not_called()
    assert not (tmp_path / NATIVE_WRITE_ATTEMPT_DIRECTORY).exists()


@pytest.mark.parametrize("existing_scope", [False, True])
def test_missing_receipt_never_initializes_or_confirms_an_attempt(
    tmp_path, monkeypatch, existing_scope
):
    writer, proof, transport, _, options = fixture_setup(tmp_path, monkeypatch)
    if existing_scope:
        scope = NativeWriteScope.from_rows(TABLE, options["rows"], proof.identity)
        with NativeWriteJournal(tmp_path) as journal, journal.scope(scope, create=True):
            pass
    before = {p: p.read_bytes() for p in tmp_path.rglob("*.json")}
    with pytest.raises(native.NativeWriteUnresolved, match="receipt"):
        writer.confirm_reservation_receipt(**options)
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*.json")}
    if not existing_scope:
        assert not (tmp_path / NATIVE_WRITE_ATTEMPT_DIRECTORY).exists()
    assert_no_proofs(proof)
    transport.assert_not_called()


def test_wrong_token_cannot_borrow_a_visible_reservation_receipt(tmp_path, monkeypatch):
    writer, proof, transport, _, options = fixture_setup(tmp_path, monkeypatch)
    writer.insert(**options)
    reset_proofs(proof)
    with pytest.raises(native.NativeWriteUnresolved, match="lacks its exact"):
        writer.confirm_reservation_receipt(
            **{**options, "deduplication_token": "another-reservation"}
        )
    assert_no_proofs(proof)
    assert transport.call_count == 1


@pytest.mark.parametrize("state", ["prepared", "sent"])
@pytest.mark.parametrize("table", [TABLE, ACTIVE])
def test_exact_receipt_ignores_other_pending_rows_even_in_quarantined_scope(
    tmp_path, monkeypatch, state, table
):
    writer, proof, transport, _, options = fixture_setup(tmp_path, monkeypatch)
    writer.insert(**options)
    row = dict(options["rows"][0]) if table == TABLE else row_for(table)
    for key in (
        "organization_id",
        "workspace_id",
        "catalog_epoch",
        "projection_version",
        "catalog_revision",
        "build_token",
    ):
        row[key] = options["rows"][0][key]
    if table == TABLE:
        row.update(
            source_adapter="span_attribute",
            producer_stream_id=str(UUID(int=99)),
            envelope_version=1,
        )
    other = {
        **options,
        "table": f"`{writer.database}`.`{table}`",
        "rows": (row,),
        "columns": _COLUMNS[table],
        "deduplication_token": "unrelated-pending",
    }
    if state == "prepared":
        proof.attest.side_effect = RuntimeError("pre-send failure")
    else:
        transport.side_effect = lost_ack
    with pytest.raises((RuntimeError, TimeoutError)):
        writer.insert(**other)
    pending = load(writer, other, table)
    assert pending.state == state
    proof.attest.side_effect = None
    scope = NativeWriteScope.from_rows(TABLE, options["rows"], proof.identity)
    with NativeWriteJournal(tmp_path) as journal, journal.scope(scope) as scoped:
        quarantine = scoped.capture_quarantine(
            NativeQuarantineReason.UNRESOLVED_NATIVE_WRITE
        )
        scoped.quarantine(quarantine)
    reset_proofs(proof)
    recover = Mock(side_effect=AssertionError("must not drain other attempts"))
    monkeypatch.setattr(writer, "_recover_scoped", recover)
    sends = transport.call_count
    writer.confirm_reservation_receipt(**options)
    assert load(writer, other, table).encode() == pending.encode()
    with NativeWriteJournal(tmp_path) as journal, journal.scope(scope) as scoped:
        assert [entry.deduplication_token for entry in scoped.pending()] == [
            "unrelated-pending"
        ]
        assert scoped.quarantine_binding == quarantine
    proof.settled.assert_not_called()
    assert proof.cover.call_args.args[0] == TABLE
    recover.assert_not_called()
    assert transport.call_count == sends


class RecoveryHarness:
    """Real coordinator, adapters, writer, file journals and native read proofs."""

    def __init__(self, tmp_path, monkeypatch):
        self.writer, self.proof, self.transport, self.legacy, _ = fixture_setup(
            tmp_path, monkeypatch
        )
        self.observed = self.legacy.client
        self.observed.catalog_database = self.writer.database
        self.loss_status = None
        self.fail_before_send = False
        self.missing_member = None
        self.missing_build = None
        self.finishes = {}
        self.sent = []
        self.reads = []
        self.proof.cover = NativeWriteProof.cover.__get__(self.proof)
        self.proof.settled = NativeWriteProof.settled.__get__(self.proof)

        for connection in self.proof.connections:

            def execute_read(
                sql, params, *, timeout_ms, settings, name=connection.name
            ):
                assert settings["readonly"] == 2 and timeout_ms > 0
                self.reads.append((name, sql, params))
                if "FROM system.query_log" in sql:
                    finish = self.finishes.get(params["query_id"])
                    rows = [] if finish is None else [finish]
                elif " AS r0" in sql:
                    rows = [
                        {
                            "r0": int(
                                name != self.missing_member
                                or params.get("r0_build_token") != self.missing_build
                            )
                        }
                    ]
                else:
                    rows = self.observed.query(sql, params, timeout_ms=timeout_ms)
                names = tuple(rows[0]) if rows else _COLUMNS[TABLE]
                return [tuple(row[k] for k in names) for row in rows], names, {}

            monkeypatch.setattr(connection.driver, "execute_read", execute_read)

        def send(*args, **kwargs):
            if self.fail_before_send:
                raise RuntimeError("transport not dispatched")
            kwargs["before_send"]()
            rows = tuple(
                dict(zip(_SOURCE_STREAM_COLUMNS, values, strict=True))
                for values in kwargs["values"]
            )
            options = options_for(self.writer, rows[0])
            # Inspect the fsynced receipt without acquiring the attempt lock
            # already held by the real writer during its transport callback.
            attempts = [
                NativeWriteAttempt(path.read_bytes())
                for path in (
                    self.writer.directory / NATIVE_WRITE_ATTEMPT_DIRECTORY
                ).glob("*.json")
                if not path.name.endswith(".scope.json")
            ]
            original = next(
                attempt
                for attempt in attempts
                if attempt.query_id == kwargs["query_id"]
            )
            assert original.state == "sent" and original.query_id == kwargs["query_id"]
            self.sent.append(original)
            self.observed.insert(
                options["table"],
                rows,
                columns=options["columns"],
                timeout_ms=5000,
                deduplication_token=options["deduplication_token"],
            )
            if rows[0]["status"] == self.loss_status:
                raise TimeoutError("ACK lost after row became visible")
            return len(rows)

        self.transport.side_effect = send
        self.restart()

    def restart(self):
        self.writer = native.DurableNativeCatalogWriter(
            self.writer.driver,
            directory=self.writer.directory,
            proof=self.proof,
            member_name=self.writer.member.name,
        )
        self.client = NativeCatalogClient(
            self.writer.driver,
            database=self.writer.database,
            durable_writer=self.writer,
        )
        self.legacy.client = self.client
        self.legacy.restart()
        self.coordinator = self.legacy.coordinator
        self.key = self.coordinator._revision_key_for_lease(self.legacy.build.lease)

    def intent(self):
        return self.coordinator._recovery_journal.load(self.key)

    def recover(self):
        lease = self.legacy.build.lease
        self.coordinator.recover_pending(
            organization_id=lease.organization_id,
            workspace_id=lease.workspace_id,
            catalog_epoch=lease.catalog_epoch,
        )

    def finish(self, attempt):
        self.finishes[attempt.query_id] = {
            "query_id": attempt.query_id,
            "type": "QueryFinish",
            "exception_code": 0,
            "current_database": self.writer.database,
            "user": attempt["user"],
            "query": attempt.sql,
            "log_comment": attempt.parameters_sha256,
            "written_rows": attempt.row_count,
            "async_insert": "0",
            "insert_quorum": str(len(self.proof.connections)),
            "insert_quorum_parallel": "1",
        }

    def quarantine(self, options):
        scope = NativeWriteScope.from_rows(TABLE, options["rows"], self.proof.identity)
        with (
            NativeWriteJournal(self.writer.directory) as journal,
            journal.scope(scope) as scoped,
        ):
            scoped.quarantine(
                scoped.capture_quarantine(
                    NativeQuarantineReason.UNRESOLVED_NATIVE_WRITE
                )
            )


@pytest.mark.parametrize("status", ["failed", "open"])
@pytest.mark.parametrize("quarantined", [False, True])
def test_visible_lost_ack_does_not_complete_file_recovery_until_original_finish_and_all_members(
    tmp_path, monkeypatch, status, quarantined
):
    h = RecoveryHarness(tmp_path, monkeypatch)
    h.loss_status = status
    with pytest.raises(TimeoutError, match="ACK lost"):
        h.legacy.prepare()
    assert not h.intent()["complete"]
    original = h.sent[-1]
    assert original.rows[0]["status"] == status
    options = options_for(h.writer, original.rows[0])
    if quarantined:
        h.quarantine(options)
    assert any(row["status"] == status for row in h.observed.stream_rows)
    sends = len(h.sent)
    h.loss_status = None
    h.restart()
    with pytest.raises(native.NativeWriteUnresolved, match="still unresolved"):
        h.recover()
    assert not h.intent()["complete"] and len(h.sent) == sends
    assert load(h.writer, options, TABLE).encode() == original.encode()

    h.finish(original)
    h.finishes[original.query_id]["log_comment"] = "f" * 64
    with pytest.raises(native.NativeWriteUnresolved, match="still unresolved"):
        h.recover()
    assert not h.intent()["complete"] and len(h.sent) == sends
    h.finish(original)
    h.missing_member = h.proof.connections[1].name
    h.missing_build = original.rows[0]["build_token"]
    with pytest.raises(NativeWriteProofError, match="coverage absent"):
        h.recover()
    assert not h.intent()["complete"] and len(h.sent) == sends
    assert load(h.writer, options, TABLE).state == "acknowledged"
    h.missing_member = None
    h.recover()
    assert h.intent()["complete"]
    completed = load(h.writer, options, TABLE)
    assert completed.state == "complete" and completed.query_id == original.query_id
    assert completed.acknowledgement["kind"] == "query_log_finish"
    assert len(h.sent) == 2  # Each frozen reservation was dispatched exactly once.
    assert len({attempt.query_id for attempt in h.sent}) == 2
    finishes = [params for _, sql, params in h.reads if "FROM system.query_log" in sql]
    assert finishes and all(
        params == {"query_id": original.query_id} for params in finishes
    )


@pytest.mark.parametrize("quarantined", [False, True])
def test_visible_prepared_receipt_cannot_complete_file_recovery_or_dispatch(
    tmp_path, monkeypatch, quarantined
):
    h = RecoveryHarness(tmp_path, monkeypatch)
    h.fail_before_send = True
    with pytest.raises(RuntimeError, match="not dispatched"):
        h.legacy.prepare()
    row = _journal_source_row(h.intent()["revoked"])
    options = options_for(h.writer, row)
    original = load(h.writer, options, TABLE)
    assert original.state == "prepared"
    if quarantined:
        h.quarantine(options)
    h.observed.stream_rows.append(deepcopy(row))
    h.restart()
    h.reads.clear()
    sends = h.transport.call_count
    with pytest.raises(native.NativeWriteUnresolved, match="Prepared"):
        h.recover()
    assert not h.intent()["complete"]
    assert load(h.writer, options, TABLE).encode() == original.encode()
    assert not any(
        " AS r0" in sql or "FROM system.query_log" in sql for _, sql, _ in h.reads
    )
    assert h.transport.call_count == sends and h.sent == []


def test_visible_logical_match_cannot_confirm_mutated_frozen_metadata(
    tmp_path, monkeypatch
):
    h = RecoveryHarness(tmp_path, monkeypatch)
    h.loss_status = "failed"
    with pytest.raises(TimeoutError):
        h.legacy.prepare()
    original = h.sent[0]
    row = _journal_source_row(h.intent()["revoked"])
    row["updated_at"] += timedelta(microseconds=1)
    with pytest.raises(
        native.NativeWriteUnresolved, match="source reservation differs"
    ):
        h.coordinator._ensure_supersession_row(
            row, predecessor=h.intent()["predecessor"]
        )
    assert not h.intent()["complete"] and len(h.sent) == 1
    assert (
        load(h.writer, options_for(h.writer, row), TABLE).encode() == original.encode()
    )


@pytest.mark.parametrize("status", ["failed", "open"])
def test_visible_legacy_rows_without_native_receipt_keep_file_recovery_pending(
    tmp_path, monkeypatch, status
):
    writer, proof, transport, legacy, _ = fixture_setup(tmp_path, monkeypatch)
    # Persist the real coordinator's exact intent with the compatibility fake,
    # whose visible write has no native receipt, then attach the managed adapter.
    legacy.client.fail_at = 0 if status == "failed" else 1
    legacy.client.fail_after = True
    with pytest.raises(TimeoutError):
        legacy.prepare()
    key = legacy.coordinator._revision_key_for_lease(legacy.build.lease)
    intent = legacy.coordinator._recovery_journal.load(key)
    if status == "open":
        revoked = _journal_source_row(intent["revoked"])
        writer.insert(**options_for(writer, revoked))
    observed = legacy.client
    client = NativeCatalogClient(
        writer.driver, database=writer.database, durable_writer=writer
    )
    # SQL visibility is the only fake here; confirmation is the real adapter/writer.
    client.query = observed.query
    legacy.client = client
    legacy.restart()
    before = transport.call_count
    with pytest.raises(
        (native.NativeWriteUnresolved, NativeWriteJournalError), match="receipt|scope"
    ):
        lease = legacy.build.lease
        legacy.coordinator.recover_pending(
            organization_id=lease.organization_id,
            workspace_id=lease.workspace_id,
            catalog_epoch=lease.catalog_epoch,
        )
    assert not legacy.coordinator._recovery_journal.load(key)["complete"]
    assert transport.call_count == before
    if status == "failed":
        assert not (tmp_path / NATIVE_WRITE_ATTEMPT_DIRECTORY).exists()


def test_postinsert_confirmation_uses_frozen_row_and_token_before_journal_completion(
    tmp_path, monkeypatch
):
    h = RecoveryHarness(tmp_path, monkeypatch)
    original = h.writer.confirm_reservation_receipt
    calls = []

    def confirm(table, rows, **kwargs):
        calls.append((table, deepcopy(rows), kwargs))
        original(table, rows, **kwargs)
        if rows[0]["status"] == "open":
            raise RuntimeError("confirmation did not return")

    monkeypatch.setattr(h.writer, "confirm_reservation_receipt", confirm)
    with pytest.raises(RuntimeError, match="did not return"):
        h.legacy.prepare()
    assert not h.intent()["complete"]
    assert len(calls) == len(h.sent) == 2
    for (table, rows, kwargs), attempt in zip(calls, h.sent, strict=True):
        assert table == f"`{h.writer.database}`.`{TABLE}`"
        assert rows == tuple(attempt.as_mapping()["rows"])
        assert kwargs["columns"] == attempt.columns
        assert kwargs["deduplication_token"] == attempt["deduplication_token"]
    h.restart()
    h.recover()
    assert h.intent()["complete"] and len(h.sent) == 2


def test_native_adapter_legacy_noop_and_managed_failure_never_fall_back(
    tmp_path, monkeypatch
):
    writer, _, transport, _, options = fixture_setup(tmp_path, monkeypatch)
    writer.driver.execute = Mock(side_effect=AssertionError("no raw write"))
    writer.driver.execute_read = Mock(side_effect=AssertionError("no raw read"))
    legacy = NativeCatalogClient(writer.driver, database=writer.database)
    assert legacy.confirm_reservation_receipt(**options) is None
    client = NativeCatalogClient(
        writer.driver, database=writer.database, durable_writer=writer
    )
    failure = Mock(side_effect=native.NativeWriteUnresolved("receipt missing"))
    monkeypatch.setattr(writer, "confirm_reservation_receipt", failure)
    with pytest.raises(native.NativeWriteUnresolved, match="receipt missing"):
        client.confirm_reservation_receipt(**options)
    failure.assert_called_once_with(
        options["table"],
        options["rows"],
        columns=options["columns"],
        timeout_ms=options["timeout_ms"],
        deduplication_token=options["deduplication_token"],
    )
    writer.driver.execute.assert_not_called()
    writer.driver.execute_read.assert_not_called()
    transport.assert_not_called()
