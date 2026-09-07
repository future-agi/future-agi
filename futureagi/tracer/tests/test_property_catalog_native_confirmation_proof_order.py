"""One exact target-last proof, real writer/BUILD journals, offline observations."""

import json

import pytest

from tracer.services.clickhouse.v2.property_catalog import (
    durable_native_writer as native,
)
from tracer.services.clickhouse.v2.property_catalog.native_publication import (
    NativePublicationBarrier,
)
from tracer.services.clickhouse.v2.property_catalog.native_write_journal import (
    NativeWriteJournalBusy,
    _key,
)
from tracer.services.clickhouse.v2.property_catalog.native_write_proof import (
    NativeWriteProofError,
)
from tracer.tests.test_property_catalog_native_complete_reproof import (
    CHECKPOINT,
    DELIVERY,
    complete,
    index_path,
)
from tracer.tests.test_property_catalog_native_recovery import (
    ACTIVE,
    DEFINITION,
    attempt,
    closure_case,
    native_scope,
    pending,
    request,
)
from tracer.tests.test_property_catalog_native_recovery import case as recovery_case


@pytest.fixture(params=[DELIVERY, CHECKPOINT, ACTIVE])
def target(request, tmp_path, monkeypatch):
    table = request.param
    if table == ACTIVE:
        c, _, _ = closure_case(tmp_path, monkeypatch)
        token = c.token

        def operation():
            return NativePublicationBarrier(c.writer).confirm(c.record)
    else:
        c = recovery_case.__wrapped__(tmp_path, monkeypatch)
        options = complete(c, table)
        token = options["deduplication_token"]

        def operation():
            return c.writer.confirm_insert(**options)

    c.transport = native.insert_once
    return c, table, token, operation


def sibling(c, target_table, token, *, prepared=False):
    # Place the target before its sibling in pending's actual hash order, so
    # proving the whole list then proving the target again cannot pass this test.
    sibling_token = next(
        name
        for name in (f"sibling-{i}" for i in range(256))
        if _key(DEFINITION, name) > _key(target_table, token)
    )
    options = request(c, DEFINITION, sibling_token)
    pending(c, options, prepared=prepared)
    return sibling_token


def observe(c, table, token):
    events = []
    path = index_path(c)
    original = attempt(c, table, token)

    def attest(**kwargs):
        events.append("attest")
        # Direct read avoids reentering the BUILD/table locks held by the writer.
        assert _key(table, token) in json.loads(path.read_bytes())["pending"]
        with c.writer._journal() as journal:
            with pytest.raises(NativeWriteJournalBusy):
                with journal.scope(native_scope(c)):
                    pytest.fail("confirmation released BUILD during proof")

    def cover(observed_table, rows, **kwargs):
        events.append(("cover", observed_table))
        if observed_table == table:
            assert rows == original.rows
            assert kwargs["columns"] == original.columns
            assert set(json.loads(path.read_bytes())["pending"]) == {_key(table, token)}

    c.proof.attest.side_effect = attest
    c.proof.cover.side_effect = cover
    c.proof.settled.side_effect = lambda *a, **k: events.append("settled") or True
    return events


def test_complete_target_proved_once_with_fresh_before_after_attestation(target):
    c, table, token, operation = target
    original = attempt(c, table, token).encode()
    generation = json.loads(index_path(c).read_bytes())["generation"]
    sent = c.transport.call_count
    events = observe(c, table, token)
    operation()
    assert events == ["attest", ("cover", table), "attest"]
    assert attempt(c, table, token).encode() == original
    with c.writer._journal() as journal, journal.scope(native_scope(c)) as scoped:
        assert scoped.pending() == ()
        assert scoped.generation == generation
    assert c.transport.call_count == sent


def test_sibling_settles_before_exact_target_only_once_last(target):
    c, table, token, operation = target
    other = sibling(c, table, token)
    original = attempt(c, table, token).encode()
    sent = c.transport.call_count
    events = observe(c, table, token)
    operation()
    assert events == [
        "attest",
        "settled",
        ("cover", DEFINITION),
        "attest",
        ("cover", table),
        "attest",
    ]
    assert attempt(c, DEFINITION, other).state == "complete"
    assert attempt(c, table, token).encode() == original
    assert c.transport.call_count == sent


@pytest.mark.parametrize("prepared", [False, True])
def test_unresolved_sibling_blocks_target_proof_and_preserves_ready(target, prepared):
    c, table, token, operation = target
    other = sibling(c, table, token, prepared=prepared)
    original = attempt(c, table, token).encode()
    sent = c.transport.call_count
    events = observe(c, table, token)
    c.proof.settled.return_value = False
    c.proof.settled.side_effect = lambda *a, **k: False
    with pytest.raises(native.NativeWriteUnresolved):
        operation()
    assert events == ["attest"]
    with c.writer._journal() as journal, journal.scope(native_scope(c)) as scoped:
        assert {entry.deduplication_token for entry in scoped.pending()} == {
            token,
            other,
        }
    assert attempt(c, table, token).encode() == original
    assert c.transport.call_count == sent


@pytest.mark.parametrize("failure", ["coverage", "final_attestation"])
def test_target_failure_after_siblings_keeps_ready_for_fresh_restart(target, failure):
    c, table, token, operation = target
    other = sibling(c, table, token)
    original = attempt(c, table, token).encode()
    sent = c.transport.call_count
    events = observe(c, table, token)
    attest, cover = c.proof.attest.side_effect, c.proof.cover.side_effect

    def fail_attest(**kwargs):
        attest(**kwargs)
        if failure == "final_attestation" and events.count("attest") == 3:
            raise NativeWriteProofError("target final attestation failed")

    def fail_cover(observed_table, rows, **kwargs):
        cover(observed_table, rows, **kwargs)
        if failure == "coverage" and observed_table == table:
            raise NativeWriteProofError("target coverage failed")

    c.proof.attest.side_effect = fail_attest
    c.proof.cover.side_effect = fail_cover
    with pytest.raises(NativeWriteProofError, match="target"):
        operation()
    assert events.count(("cover", table)) == 1
    assert attempt(c, DEFINITION, other).state == "complete"
    with c.writer._journal() as journal, journal.scope(native_scope(c)) as scoped:
        assert [entry.deduplication_token for entry in scoped.pending()] == [token]
    assert attempt(c, table, token).encode() == original
    assert c.transport.call_count == sent
    c.h.restart()
    events = observe(c, table, token)
    operation()
    assert events == ["attest", ("cover", table), "attest"]
    assert attempt(c, table, token).encode() == original
    assert c.transport.call_count == sent
