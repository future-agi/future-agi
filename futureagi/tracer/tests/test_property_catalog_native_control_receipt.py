"""Exact control receipt proofs with real journals and fake native observations."""

from datetime import UTC, datetime, timedelta
from unittest.mock import Mock
from uuid import UUID

import pytest

from tracer.services.clickhouse.v2.property_catalog import (
    durable_native_writer as subject,
)
from tracer.services.clickhouse.v2.property_catalog.native_write_journal import (
    NativeWriteJournal,
    NativeWriteJournalBusy,
    NativeWriteJournalError,
    NativeWriteScope,
    native_insert_sql,
    native_parameters_sha256,
)
from tracer.services.clickhouse.v2.property_catalog.native_write_proof import (
    _COLUMNS,
    NativeWriteProof,
    NativeWriteProofError,
)
from tracer.tests.test_property_catalog_durable_native_writer import setup
from tracer.tests.test_property_catalog_native_write_proof import make_proof, row_for

TABLE = "property_catalog_activation_control_events"
ACTIVE = "property_catalog_activations"
AT = datetime(2026, 9, 6, microsecond=123456, tzinfo=UTC)


def control_setup(
    tmp_path, monkeypatch, *, replicas=2, action="follow", targetless=False
):
    writer, proof, transport, steps, options = setup(
        tmp_path, monkeypatch, replicas=replicas, table=TABLE
    )
    row = options["rows"][0]
    row.update(
        action=action,
        control_sequence=1,
        request_id=str(UUID(int=11)),
        target_activation_sha256="a" * 64,
        previous_control_sha256="0" * 64,
        control_sha256="b" * 64,
        controlled_at=AT,
    )
    if targetless:
        row.update(target_catalog_revision=0, target_build_token=str(UUID(int=0)))
    return writer, proof, transport, steps, options


def load(writer, options, table=TABLE):
    with NativeWriteJournal(writer.directory) as journal:
        with journal.session(table, options["deduplication_token"]) as session:
            return session.load()


def reset_proofs(proof):
    for method in (proof.attest, proof.cover, proof.settled):
        method.reset_mock()


def assert_no_proofs(proof):
    for method in (proof.attest, proof.cover, proof.settled):
        method.assert_not_called()


def lost_ack(*args, **kwargs):
    kwargs["before_send"]()
    raise TimeoutError("original native ACK lost")


@pytest.mark.parametrize("replicas", [1, 2])
@pytest.mark.parametrize("timestamp_text", [False, True])
def test_complete_native_read_forms_reprove_original_row_without_rewriting(
    tmp_path, monkeypatch, replicas, timestamp_text
):
    writer, proof, transport, _, options = control_setup(
        tmp_path, monkeypatch, replicas=replicas
    )
    writer.insert(**options)
    original = load(writer, options)
    row = dict(options["rows"][0])
    for column in (
        "organization_id",
        "workspace_id",
        "request_id",
        "target_build_token",
    ):
        row[column] = UUID(row[column])
    if timestamp_text:
        row["controlled_at"] = AT.strftime("%Y-%m-%d %H:%M:%S.%f")
    reset_proofs(proof)
    assert writer.confirm_receipt(**{**options, "rows": (row,)}) is None
    proof.cover.assert_called_once()
    assert proof.cover.call_args.args == (TABLE, original.rows)
    assert proof.cover.call_args.kwargs["columns"] == original.columns
    assert proof.attest.call_count == 2
    proof.settled.assert_not_called()
    assert load(writer, options).encode() == original.encode()
    assert transport.call_count == 1


def test_lost_ack_reopens_sent_and_requires_original_query_finish_without_resend(
    tmp_path, monkeypatch
):
    writer, proof, transport, _, options = control_setup(tmp_path, monkeypatch)
    transport.side_effect = lost_ack
    with pytest.raises(TimeoutError):
        writer.insert(**options)
    original = load(writer, options)
    reset_proofs(proof)
    writer = subject.DurableNativeCatalogWriter(
        writer.driver,
        directory=writer.directory,
        proof=proof,
        member_name=writer.member.name,
    )
    with pytest.raises(subject.NativeWriteUnresolved, match="still unresolved"):
        writer.confirm_receipt(**options)
    assert load(writer, options).encode() == original.encode()
    proof.cover.assert_not_called()
    settled = proof.settled.call_args.args[0]
    assert settled.query_id == original.query_id
    assert settled.parameters_sha256 == original.parameters_sha256
    assert settled.sql == original.sql
    proof.settled.return_value = True
    assert writer.confirm_receipt(**options) is None
    completed = load(writer, options)
    assert completed.state == "complete" and completed.query_id == original.query_id
    assert completed.acknowledgement["kind"] == "query_log_finish"
    assert transport.call_count == 1


def test_prepared_control_is_not_inferred_sent_or_dispatched(tmp_path, monkeypatch):
    writer, proof, transport, _, options = control_setup(tmp_path, monkeypatch)
    proof.attest.side_effect = RuntimeError("pre-send admission failure")
    with pytest.raises(RuntimeError):
        writer.insert(**options)
    original = load(writer, options)
    assert original.state == "prepared"
    proof.attest.side_effect = None
    with pytest.raises(subject.NativeWriteUnresolved, match="Prepared"):
        writer.confirm_receipt(**options)
    assert load(writer, options).encode() == original.encode()
    transport.assert_not_called()
    proof.settled.assert_not_called()
    proof.cover.assert_not_called()


def test_acknowledged_requires_coverage_and_attestation_before_complete(
    tmp_path, monkeypatch
):
    writer, proof, transport, _, options = control_setup(tmp_path, monkeypatch)
    proof.cover.side_effect = RuntimeError("missing serving member")
    with pytest.raises(RuntimeError):
        writer.insert(**options)
    assert load(writer, options).state == "acknowledged"
    with pytest.raises(RuntimeError, match="missing serving member"):
        writer.confirm_receipt(**options)
    assert load(writer, options).state == "acknowledged"
    proof.cover.side_effect = None
    proof.attest.side_effect = [None, RuntimeError("member changed")]
    with pytest.raises(RuntimeError, match="member changed"):
        writer.confirm_receipt(**options)
    assert load(writer, options).state == "acknowledged"
    proof.attest.side_effect = None
    writer.confirm_receipt(**options)
    assert load(writer, options).state == "complete"
    assert transport.call_count == 1


def test_complete_receipt_rechecks_actual_coverage_on_every_admitted_member(
    tmp_path, monkeypatch
):
    writer, proof, transport, _, options = control_setup(tmp_path, monkeypatch)
    writer.insert(**options)
    original = load(writer, options)
    proof.cover = NativeWriteProof.cover.__get__(proof)
    reads = []
    missing = [True]
    for index, connection in enumerate(proof.connections):
        original_read = connection.driver.execute_read

        def read(
            sql,
            params,
            *,
            timeout_ms,
            settings,
            index=index,
            original_read=original_read,
        ):
            reads.append(index)
            if index == 1 and missing[0]:
                return [(0,)], [("r0", "UInt8")], {}
            return original_read(sql, params, timeout_ms=timeout_ms, settings=settings)

        monkeypatch.setattr(connection.driver, "execute_read", read)
    with pytest.raises(NativeWriteProofError, match="coverage absent"):
        writer.confirm_receipt(**options)
    assert reads == [0, 1]
    assert load(writer, options).encode() == original.encode()
    missing[0] = False
    assert writer.confirm_receipt(**options) is None
    assert reads == [0, 1, 0, 1]
    assert transport.call_count == 1


@pytest.mark.parametrize("existing_scope", [False, True])
def test_missing_receipt_never_initializes_or_confirms_an_attempt(
    tmp_path, monkeypatch, existing_scope
):
    writer, proof, transport, _, options = control_setup(tmp_path, monkeypatch)
    if existing_scope:
        scope = NativeWriteScope.from_rows(TABLE, options["rows"], proof.identity)
        with NativeWriteJournal(tmp_path) as journal, journal.scope(scope, create=True):
            pass
    before = {p.name: p.read_bytes() for p in tmp_path.rglob("*.json")}
    with pytest.raises(subject.NativeWriteUnresolved, match="receipt"):
        writer.confirm_receipt(**options)
    assert before == {p.name: p.read_bytes() for p in tmp_path.rglob("*.json")}
    if not existing_scope:
        assert not (tmp_path / "native-write-attempts").exists()
    assert_no_proofs(proof)
    transport.assert_not_called()


@pytest.mark.parametrize(
    "field,value",
    [
        ("controlled_at", AT + timedelta(microseconds=1)),
        ("control_sequence", 2),
        ("request_id", str(UUID(int=12))),
        ("action", "rollback"),
        ("target_activation_sha256", "c" * 64),
        ("previous_control_sha256", "c" * 64),
        ("control_sha256", "c" * 64),
    ],
)
def test_every_metadata_and_payload_field_is_bound(tmp_path, monkeypatch, field, value):
    writer, proof, transport, _, options = control_setup(tmp_path, monkeypatch)
    writer.insert(**options)
    original = load(writer, options)
    reset_proofs(proof)
    rows = [{**options["rows"][0], field: value}]
    with pytest.raises(subject.NativeWriteUnresolved, match="differs"):
        writer.confirm_receipt(**{**options, "rows": rows})
    assert load(writer, options).encode() == original.encode()
    assert_no_proofs(proof)
    assert transport.call_count == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("organization_id", str(UUID(int=99))),
        ("workspace_id", str(UUID(int=99))),
        ("catalog_epoch", 2),
        ("projection_version", 2),
        ("target_catalog_revision", 3),
        ("target_build_token", str(UUID(int=99))),
    ],
)
def test_receipt_never_follows_another_tenant_installation_or_target(
    tmp_path, monkeypatch, field, value
):
    writer, proof, transport, _, options = control_setup(tmp_path, monkeypatch)
    writer.insert(**options)
    reset_proofs(proof)
    with pytest.raises(NativeWriteJournalError):
        writer.confirm_receipt(
            **{**options, "rows": [{**options["rows"][0], field: value}]}
        )
    assert_no_proofs(proof)
    assert transport.call_count == 1


@pytest.mark.parametrize(
    "mutation", ["empty", "multiple", "missing", "extra", "columns", "table", "token"]
)
def test_invalid_shape_or_target_is_rejected_before_journal_or_proof(
    tmp_path, monkeypatch, mutation
):
    writer, proof, transport, _, options = control_setup(tmp_path, monkeypatch)
    if mutation == "empty":
        options["rows"] = []
    elif mutation == "multiple":
        options["rows"] *= 2
    elif mutation == "missing":
        del options["rows"][0]["controlled_at"]
    elif mutation == "extra":
        options["rows"][0]["extra"] = "not a column"
    elif mutation == "columns":
        options["columns"] = tuple(reversed(options["columns"]))
    elif mutation == "table":
        options["table"] = f"`{writer.database}`.`{ACTIVE}`"
    else:
        options["deduplication_token"] = "invalid\ncontrol"
    with pytest.raises((ValueError, NativeWriteJournalError)):
        writer.confirm_receipt(**options)
    assert not (tmp_path / "native-write-attempts").exists()
    assert_no_proofs(proof)
    transport.assert_not_called()


def test_wrong_token_cannot_borrow_an_existing_receipt(tmp_path, monkeypatch):
    writer, proof, transport, _, options = control_setup(tmp_path, monkeypatch)
    writer.insert(**options)
    reset_proofs(proof)
    with pytest.raises(subject.NativeWriteUnresolved, match="lacks"):
        writer.confirm_receipt(**{**options, "deduplication_token": "different-event"})
    assert_no_proofs(proof)
    assert transport.call_count == 1


def test_another_existing_control_receipt_cannot_confirm_this_event(
    tmp_path, monkeypatch
):
    writer, proof, transport, _, options = control_setup(tmp_path, monkeypatch)
    writer.insert(**options)

    def acknowledged(*args, **kwargs):
        kwargs["before_send"]()
        return len(kwargs["values"])

    transport.side_effect = acknowledged
    other = {
        **options,
        "deduplication_token": "another-control-event",
        "rows": [{**options["rows"][0], "request_id": str(UUID(int=100))}],
    }
    writer.insert(**other)
    reset_proofs(proof)
    with pytest.raises(subject.NativeWriteUnresolved, match="differs"):
        writer.confirm_receipt(
            **{**options, "deduplication_token": other["deduplication_token"]}
        )
    assert_no_proofs(proof)
    assert transport.call_count == 2


@pytest.mark.parametrize(
    "field,value",
    [
        ("controlled_at", AT.replace(tzinfo=None)),
        ("controlled_at", AT + timedelta(microseconds=1)),
        ("control_sequence", True),
        ("control_sequence", 1.0),
        ("control_sequence", 1 << 64),
    ],
)
def test_timestamp_precision_and_strict_native_parameter_types(
    tmp_path, monkeypatch, field, value
):
    writer, proof, transport, _, options = control_setup(tmp_path, monkeypatch)
    writer.insert(**options)
    reset_proofs(proof)
    with pytest.raises((NativeWriteJournalError, subject.NativeWriteUnresolved)):
        writer.confirm_receipt(
            **{**options, "rows": [{**options["rows"][0], field: value}]}
        )
    assert_no_proofs(proof)
    assert transport.call_count == 1


@pytest.mark.parametrize("action,targetless", [("disable", True), ("rollback", False)])
def test_event_scope_not_latest_build_controls_which_receipt_is_confirmed(
    tmp_path, monkeypatch, action, targetless
):
    writer, proof, transport, _, options = control_setup(
        tmp_path, monkeypatch, action=action, targetless=targetless
    )
    writer.insert(**options)
    if targetless:
        options = {
            **options,
            "rows": [{**options["rows"][0], "target_build_token": UUID(int=0)}],
        }
    scope = NativeWriteScope.from_rows(TABLE, options["rows"], proof.identity)
    assert scope.kind == ("control" if targetless else "build")
    latest = NativeWriteScope(
        scope.organization_id, scope.workspace_id, 1, 1, 10, str(UUID(int=90))
    )
    reset_proofs(proof)
    with NativeWriteJournal(tmp_path) as journal, journal.scope(latest, create=True):
        assert writer.confirm_receipt(**options) is None
    assert proof.cover.call_args.args[1] == load(writer, options).rows
    assert transport.call_count == 1


@pytest.mark.parametrize("state", ["prepared", "sent"])
def test_follow_confirmation_does_not_drain_or_lock_a_sibling_active_attempt(
    tmp_path, monkeypatch, state
):
    writer, proof, transport, _, options = control_setup(tmp_path, monkeypatch)
    writer.insert(**options)
    active = {
        **options,
        "table": f"`{writer.database}`.`{ACTIVE}`",
        "rows": [row_for(ACTIVE)],
        "columns": _COLUMNS[ACTIVE],
        "deduplication_token": "sibling-active",
    }
    if state == "prepared":
        proof.attest.side_effect = RuntimeError("not admitted to dispatch")
    else:
        transport.side_effect = lost_ack
    with pytest.raises((RuntimeError, TimeoutError)):
        writer.insert(**active)
    original = load(writer, active, ACTIVE)
    assert original.state == state
    proof.attest.side_effect = None
    reset_proofs(proof)
    monkeypatch.setattr(
        writer,
        "_recover_scoped",
        Mock(side_effect=AssertionError("must not drain build")),
    )
    before = transport.call_count
    assert writer.confirm_receipt(**options) is None
    assert load(writer, active, ACTIVE).encode() == original.encode()
    proof.settled.assert_not_called()
    assert proof.cover.call_args.args[0] == TABLE
    scope = NativeWriteScope.from_rows(TABLE, options["rows"], proof.identity)
    with NativeWriteJournal(tmp_path) as journal, journal.scope(scope) as scoped:
        assert [entry.deduplication_token for entry in scoped.pending()] == [
            "sibling-active"
        ]
    assert transport.call_count == before


def test_confirmation_uses_nonblocking_scope_lock(tmp_path, monkeypatch):
    writer, proof, transport, _, options = control_setup(tmp_path, monkeypatch)
    writer.insert(**options)
    reset_proofs(proof)
    scope = NativeWriteScope.from_rows(TABLE, options["rows"], proof.identity)
    with NativeWriteJournal(tmp_path) as journal, journal.scope(scope):
        with pytest.raises(NativeWriteJournalBusy):
            writer.confirm_receipt(**options)
    assert_no_proofs(proof)
    assert transport.call_count == 1


def test_frozen_policy_must_match_before_any_receipt_proof(tmp_path, monkeypatch):
    writer, proof, transport, _, options = control_setup(tmp_path, monkeypatch)
    scope = NativeWriteScope.from_rows(TABLE, options["rows"], proof.identity)
    digest = native_parameters_sha256(options["columns"], options["rows"])
    with (
        NativeWriteJournal(tmp_path) as journal,
        journal.scope(scope, create=True) as scoped,
    ):
        with scoped.session(TABLE, options["deduplication_token"]) as session:
            session.prepare(
                database=writer.database,
                member=writer.member.name,
                user=writer.driver.user,
                admission_sha256=writer.admission_sha256,
                columns=options["columns"],
                rows=options["rows"],
                sql=native_insert_sql(
                    writer.database, TABLE, options["columns"], quorum=2
                ),
                settings={
                    "async_insert": 0,
                    "insert_quorum": 2,
                    "insert_quorum_parallel": 1,
                    "insert_quorum_timeout": 1000,
                    "max_execution_time": 5,
                    "insert_block_size": 16384,
                    "insert_deduplicate": 0,
                    "insert_deduplication_token": options["deduplication_token"],
                    "log_comment": digest,
                },
            )
    with pytest.raises(subject.NativeWriteUnresolved, match="settings"):
        writer.confirm_receipt(**options)
    assert_no_proofs(proof)
    transport.assert_not_called()


def test_foreign_descriptor_directory_cannot_supply_receipts(tmp_path, monkeypatch):
    writer, proof, transport, _, options = control_setup(tmp_path, monkeypatch)
    writer.insert(**options)
    foreign = tmp_path / "foreign"
    foreign.mkdir(mode=0o700)
    make_proof(foreign, replicas=1)
    with NativeWriteJournal(foreign):
        pass
    writer.directory = foreign
    reset_proofs(proof)
    with pytest.raises(subject.NativeWriteUnresolved, match="installation/admission"):
        writer.confirm_receipt(**options)
    assert_no_proofs(proof)
    assert transport.call_count == 1


def test_changed_driver_cannot_confirm_an_original_receipt(tmp_path, monkeypatch):
    writer, proof, transport, _, options = control_setup(tmp_path, monkeypatch)
    writer.insert(**options)
    writer.driver.host = "unadmitted.invalid"
    reset_proofs(proof)
    with pytest.raises(ValueError, match="direct native endpoint"):
        writer.confirm_receipt(**options)
    assert_no_proofs(proof)
    assert transport.call_count == 1
