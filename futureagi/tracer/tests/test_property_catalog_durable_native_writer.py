"""Real native attempt journal + writer; network/proof observations are fakes."""

from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

import pytest

from tracer.services.clickhouse.v2.property_catalog import (
    durable_native_writer as subject,
)
from tracer.services.clickhouse.v2.property_catalog.native_write_journal import (
    NativeWriteAttempt,
    NativeWriteJournal,
    NativeWriteJournalError,
)
from tracer.services.clickhouse.v2.property_catalog.native_write_proof import _COLUMNS
from tracer.tests.test_property_catalog_native_write_proof import make_proof, row_for

TABLE = "property_definition_catalog"
TOKEN = "exact-native-fixture-token"


def setup(tmp_path, monkeypatch, *, replicas=2, table=TABLE):
    proof, _ = make_proof(tmp_path, replicas=replicas)
    connections = []
    for connection in proof.connections:
        connection.driver.host = connection.native_member_host
        connection.driver.port = 9000
        connections.append(connection)
    proof.connections = tuple(connections)
    driver = SimpleNamespace(
        host=proof.connections[0].driver.host,
        port=9000,
        database=proof.admission.database,
        user="catalog_writer",
        server_enforced_readonly=False,
    )
    steps = []
    proof.attest = Mock(side_effect=lambda **_: steps.append("attest"))
    proof.cover = Mock(side_effect=lambda *a, **k: steps.append("cover"))
    proof.settled = Mock(return_value=False)

    def send(*args, **options):
        options["before_send"]()
        records = [
            path
            for path in (tmp_path / "native-write-attempts").glob("*.json")
            if not path.name.endswith(".scope.json")
        ]
        assert len(records) == 1
        saved = NativeWriteAttempt(records[0].read_bytes())
        assert saved.state == "sent" and saved.query_id == options["query_id"]
        assert list(saved.parameters) == options["values"]
        steps.append("send-after-sent-fsync")
        return len(options["values"])

    transport = Mock(side_effect=send)
    monkeypatch.setattr(subject, "insert_once", transport)
    writer = subject.DurableNativeCatalogWriter(
        driver,
        directory=tmp_path,
        proof=proof,
        member_name=proof.connections[0].name,
    )
    options = {
        "table": f"`{proof.admission.database}`.`{table}`",
        "rows": [row_for(table)],
        "columns": _COLUMNS[table],
        "timeout_ms": 5000,
        "deduplication_token": TOKEN,
    }
    return writer, proof, transport, steps, options


def load(tmp_path, table=TABLE):
    with NativeWriteJournal(tmp_path) as journal:
        with journal.session(table, TOKEN) as session:
            return session.load()


@pytest.mark.parametrize("replicas", [1, 2])
@pytest.mark.parametrize("table", tuple(_COLUMNS))
def test_all_seven_native_tables_complete_only_after_all_member_proof(
    tmp_path,
    monkeypatch,
    replicas,
    table,
):
    writer, proof, transport, steps, options = setup(
        tmp_path, monkeypatch, replicas=replicas, table=table
    )
    writer.insert(**options)
    attempt = load(tmp_path, table)
    assert attempt.state == "complete"
    assert steps == ["attest", "send-after-sent-fsync", "cover", "attest"]
    assert attempt.settings["insert_quorum"] == (replicas if replicas > 1 else 0)
    assert attempt.settings["async_insert"] == 0
    assert attempt["completion"]["members"] == tuple(c.name for c in proof.connections)
    assert attempt.acknowledgement["kind"] == "native_end_of_stream"
    assert transport.call_count == 1


def test_repeated_complete_write_only_reproves_no_insert(tmp_path, monkeypatch):
    writer, proof, transport, steps, options = setup(tmp_path, monkeypatch)
    writer.insert(**options)
    first = load(tmp_path)
    steps.clear()
    writer.insert(**{**options, "timeout_ms": 1000})
    assert steps == ["attest", "cover", "attest"]
    assert load(tmp_path).encode() == first.encode()
    assert transport.call_count == 1
    proof.settled.assert_not_called()


def test_lost_ack_remains_sent_until_original_completion_and_never_replays(
    tmp_path, monkeypatch
):
    writer, proof, transport, _, options = setup(tmp_path, monkeypatch)

    def lost(*args, **kwargs):
        kwargs["before_send"]()
        raise TimeoutError("full native acknowledgement lost")

    transport.side_effect = lost
    with pytest.raises(TimeoutError):
        writer.insert(**options)
    first = load(tmp_path)
    assert first.state == "sent"
    with pytest.raises(subject.NativeWriteUnresolved, match="still unresolved"):
        writer.insert(**options)
    assert load(tmp_path).encode() == first.encode()
    proof.cover.assert_not_called()
    proof.settled.return_value = True
    writer.insert(**options)
    final = load(tmp_path)
    assert final.query_id == first.query_id and final.state == "complete"
    assert final.acknowledgement["kind"] == "query_log_finish"
    assert transport.call_count == 1


def test_failed_coverage_keeps_acknowledged_and_retry_is_proof_only(
    tmp_path, monkeypatch
):
    writer, proof, transport, _, options = setup(tmp_path, monkeypatch)
    proof.cover.side_effect = RuntimeError("replica unavailable")
    with pytest.raises(RuntimeError, match="replica unavailable"):
        writer.insert(**options)
    assert load(tmp_path).state == "acknowledged"
    proof.cover.side_effect = None
    writer.insert(**options)
    assert load(tmp_path).state == "complete"
    assert transport.call_count == 1


def test_changed_topology_after_insert_does_not_mark_complete(tmp_path, monkeypatch):
    writer, proof, transport, _, options = setup(tmp_path, monkeypatch)
    proof.attest.side_effect = [None, RuntimeError("topology changed")]
    with pytest.raises(RuntimeError, match="topology changed"):
        writer.insert(**options)
    assert load(tmp_path).state == "acknowledged"
    assert transport.call_count == 1


@pytest.mark.parametrize("ack", [None, True, 0, "1", 2])
def test_nonexact_native_ack_is_uncertain_not_success(tmp_path, monkeypatch, ack):
    writer, proof, transport, _, options = setup(tmp_path, monkeypatch)

    def send(*a, **k):
        k["before_send"]()
        return ack

    transport.side_effect = send
    with pytest.raises(subject.NativeWriteUnresolved, match="full acknowledgement"):
        writer.insert(**options)
    assert load(tmp_path).state == "sent"
    proof.cover.assert_not_called()


def test_prepared_before_failed_attestation_resumes_same_statement(
    tmp_path, monkeypatch
):
    writer, proof, transport, _, options = setup(tmp_path, monkeypatch)
    proof.attest.side_effect = RuntimeError("no admission")
    with pytest.raises(RuntimeError, match="no admission"):
        writer.insert(**options)
    first = load(tmp_path)
    assert first.state == "prepared"
    transport.assert_not_called()
    proof.attest.side_effect = None
    writer.insert(**options)
    assert load(tmp_path).query_id == first.query_id
    assert dict(load(tmp_path).settings) == dict(first.settings)


def test_existing_token_changed_payload_never_reaches_transport(tmp_path, monkeypatch):
    writer, proof, transport, _, options = setup(tmp_path, monkeypatch)
    writer.insert(**options)
    proof.attest.reset_mock()
    rows = [{**options["rows"][0], "name": "different"}]
    with pytest.raises(NativeWriteJournalError, match="conflict"):
        writer.insert(**{**options, "rows": rows})
    assert transport.call_count == 1
    proof.attest.assert_not_called()


def test_workspace_change_in_later_chunk_rejected_before_any_journal_or_read(
    tmp_path, monkeypatch
):
    writer, proof, transport, _, options = setup(tmp_path, monkeypatch)
    rows = [options["rows"][0]] * 32 + [
        {**options["rows"][0], "workspace_id": str(UUID(int=55))}
    ]
    with pytest.raises(RuntimeError, match="mix tenant/build"):
        writer.insert(**{**options, "rows": rows})
    assert not (tmp_path / "native-write-attempts").exists()
    proof.attest.assert_not_called()
    transport.assert_not_called()


def test_retargeted_writer_cannot_bypass_admission(tmp_path, monkeypatch):
    writer, proof, transport, _, options = setup(tmp_path, monkeypatch)
    writer.driver.host = "load-balancer.invalid"
    with pytest.raises(ValueError, match="direct native endpoint"):
        writer.insert(**options)
    proof.attest.assert_not_called()
    transport.assert_not_called()
