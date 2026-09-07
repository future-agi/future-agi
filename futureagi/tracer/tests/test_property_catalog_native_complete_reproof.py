"""Complete stays immutable; exact READY reproof survives offline restart."""

import json
from datetime import timedelta

import pytest

from tracer.services.clickhouse.v2.property_catalog import (
    native_write_journal as journal_module,
)
from tracer.services.clickhouse.v2.property_catalog.durable_native_writer import (
    NativeWriteUnresolved,
)
from tracer.services.clickhouse.v2.property_catalog.native_publication import (
    NativePublicationBarrier,
)
from tracer.services.clickhouse.v2.property_catalog.native_write_proof import (
    NativeWriteProofError,
)
from tracer.tests.test_property_catalog_native_recovery import (
    ACTIVE,
    DEFINITION,
    SOURCE,
    attempt,
    candidates,
    closure_case,
    native_scope,
    recover,
    request,
)
from tracer.tests.test_property_catalog_native_recovery import case as recovery_case

DELIVERY = "property_catalog_deliveries"
CHECKPOINT = "property_catalog_checkpoints"


@pytest.fixture
def case(tmp_path, monkeypatch):
    return recovery_case.__wrapped__(tmp_path, monkeypatch)


def index_path(c):
    with c.writer._journal() as journal, journal.scope(native_scope(c)) as scoped:
        return c.writer.directory / "native-write-attempts" / (scoped._key + ".json")


def complete(c, table=DEFINITION):
    c.writer.insert(**c.options)
    options = c.options if table == SOURCE else request(c, table, "reproof-original")
    if table != SOURCE:
        c.writer.insert(**options)
    return options


def call(c, options, kind):
    kwargs = dict(options)
    return getattr(c.writer, kind)(**kwargs)


@pytest.mark.parametrize(
    "kind,table",
    [
        ("insert", DEFINITION),
        ("insert", SOURCE),
        ("confirm_insert", DELIVERY),
        ("confirm_insert", CHECKPOINT),
    ],
)
@pytest.mark.parametrize("failure", ["attest", "cover"])
def test_complete_failure_retained_before_first_proof_then_positive_restart(
    case, kind, table, failure
):
    c = case
    options = complete(c, table)
    token = options["deduplication_token"]
    original = attempt(c, table, token).encode()
    path = index_path(c)
    before = json.loads(path.read_bytes())
    anchor = path.with_suffix(".anchor").read_bytes()
    with c.writer._journal() as journal, journal.scope(native_scope(c)) as scoped:
        assert journal.clear_recovery_scope(
            scoped, expected_generation=scoped.generation
        )

    def fail(*args, **kwargs):
        # Read bytes directly while the writer owns BUILD/table locks.
        document = json.loads(path.read_bytes())
        assert document["version"] == 4
        assert document["generation"] == before["generation"]
        assert set(document) == set(before)
        entry = document["pending"][journal_module._key(table, token)]
        assert entry["stage"] == "ready" and entry["prepared"] is None
        assert entry["query_id"] == json.loads(original)["query_id"]
        raise NativeWriteProofError("fresh proof lost")

    probe = getattr(c.proof, failure)
    old = probe.side_effect
    probe.side_effect = fail
    sent = c.transport.call_count
    with pytest.raises(NativeWriteProofError, match="fresh proof lost"):
        call(c, options, kind)
    assert candidates(c) == (native_scope(c),)
    assert attempt(c, table, token).encode() == original
    assert path.with_suffix(".anchor").read_bytes() == anchor
    probe.side_effect = old
    c.h.restart()
    c.proof.settled.reset_mock()
    assert recover(c) == ()
    assert candidates(c) == ()
    assert json.loads(path.read_bytes())["version"] == 4
    assert json.loads(path.read_bytes())["generation"] == before["generation"]
    assert attempt(c, table, token).encode() == original
    assert c.transport.call_count == sent
    c.proof.settled.assert_not_called()


def test_generic_complete_coverage_loss_blocks_live_then_exact_expiry_repair(case):
    c = case
    options = complete(c)
    original = attempt(c, DEFINITION, options["deduplication_token"]).encode()
    sent = c.transport.call_count

    def cover(table, *args, **kwargs):
        if table == DEFINITION:
            raise NativeWriteProofError("definition coverage lost")

    c.proof.cover.side_effect = cover
    with pytest.raises(NativeWriteProofError):
        c.writer.insert(**options)
    with pytest.raises(NativeWriteUnresolved, match="unexpired"):
        recover(c, now=c.lease.expires_at - timedelta(microseconds=1))
    with c.writer._journal() as journal, journal.scope(native_scope(c)) as scoped:
        assert scoped.quarantine_binding is None
        assert [entry.table for entry in scoped.pending()] == [DEFINITION]
    assert c.transport.call_count == sent
    ((intent, receipt),) = recover(c)
    assert intent.binding == receipt.binding
    assert [entry.table for entry in intent.binding.quarantine.pending] == [DEFINITION]
    assert attempt(c, DEFINITION, options["deduplication_token"]).encode() == original
    assert c.transport.call_count == sent + 1  # Only terminal failed source.
    assert json.loads(index_path(c).read_bytes())["version"] == 6
    with c.writer._journal() as journal, journal.scope(native_scope(c)) as scoped:
        for field in ("quarantine", "terminal_repair"):
            document = scoped._read_index()
            del document[field]
            with pytest.raises(journal_module.NativeWriteJournalError):
                scoped._save(document)


def test_complete_before_forget_crash_returns_reference_and_reproves(case, monkeypatch):
    c = case
    options = c.options
    with monkeypatch.context() as patch:
        patch.setattr(
            journal_module.NativeWriteScopeSession,
            "_forget_completed",
            lambda *a: (_ for _ in ()).throw(OSError("forget crash")),
        )
        with pytest.raises(OSError, match="forget crash"):
            c.writer.insert(**options)
    original = attempt(c).encode()
    with c.writer._journal() as journal, journal.scope(native_scope(c)) as scoped:
        assert len(scoped.pending()) == 1
        assert scoped._read_index()["version"] == 4
        assert scoped.generation == 1
    c.proof.cover.reset_mock()
    assert recover(c) == ()
    assert c.proof.cover.call_count == 1
    assert attempt(c).encode() == original
    assert c.transport.call_count == 1


def test_ready_fsync_failure_stops_before_proof_and_recovers_exact_reference(
    case, monkeypatch
):
    c = case
    options = complete(c)
    path = index_path(c)
    original = attempt(c, DEFINITION, options["deduplication_token"]).encode()
    atomic = journal_module._atomic

    def crash(directory, name, raw):
        atomic(directory, name, raw)
        if name == path.name and json.loads(raw)["version"] == 4:
            raise OSError("after READY fsync")

    for probe in (c.proof.attest, c.proof.cover, c.proof.settled):
        probe.reset_mock()
    sent = c.transport.call_count
    with monkeypatch.context() as patch:
        patch.setattr(journal_module, "_atomic", crash)
        with pytest.raises(OSError, match="READY fsync"):
            c.writer.insert(**options)
    for probe in (c.proof.attest, c.proof.cover, c.proof.settled):
        probe.assert_not_called()
    assert recover(c) == ()
    assert attempt(c, DEFINITION, options["deduplication_token"]).encode() == original
    assert c.transport.call_count == sent


def test_retention_idempotent_exact_and_generation_neutral(case):
    c = case
    options = complete(c)
    token = options["deduplication_token"]
    with c.writer._journal() as journal, journal.scope(native_scope(c)) as scoped:
        generation = scoped.generation
        with scoped.session(DEFINITION, token) as session:
            saved = session.load()
            scoped._retain_complete(session, saved)
            first = scoped._read_index()
            scoped._retain_complete(session, saved)
            assert scoped._read_index() == first
            with pytest.raises(journal_module.NativeWriteJournalError):
                scoped._retain_complete(session, object())
        assert scoped.generation == generation
        assert len(scoped.pending()) == 1
        assert not journal.clear_recovery_scope(scoped, expected_generation=generation)
    assert recover(c) == ()


def test_reproof_pending_bound_rejects_before_first_proof(case, monkeypatch):
    c = case
    first = complete(c)
    second = request(c, DEFINITION, "second-complete")
    c.writer.insert(**second)
    monkeypatch.setattr(journal_module, "MAX_SCOPE_PENDING", 1)
    c.proof.cover.side_effect = NativeWriteProofError("keep first pending")
    with pytest.raises(NativeWriteProofError):
        c.writer.insert(**first)
    before = index_path(c).read_bytes()
    c.proof.attest.reset_mock()
    c.proof.cover.reset_mock()
    sent = c.transport.call_count
    with pytest.raises(journal_module.NativeWriteJournalError, match="pending bound"):
        c.writer.insert(**second)
    c.proof.attest.assert_not_called()
    c.proof.cover.assert_not_called()
    assert c.transport.call_count == sent
    assert index_path(c).read_bytes() == before


def test_closed_active_retention_invalidates_closed_check_until_fresh_proof(
    tmp_path, monkeypatch
):
    c, h, native = closure_case(tmp_path, monkeypatch)
    path = index_path(c)
    before = json.loads(path.read_bytes())
    closure = journal_module.NativeWriteScopeSession._closure(before["closure"])
    original = attempt(c, ACTIVE, c.token).encode()

    def fail(**kwargs):
        document = json.loads(path.read_bytes())
        assert (
            document["pending"][journal_module._key(ACTIVE, c.token)]["stage"]
            == "ready"
        )
        raise NativeWriteProofError("completion first attest")

    c.proof.attest.side_effect = fail
    with pytest.raises(NativeWriteProofError, match="completion first attest"):
        NativePublicationBarrier(c.writer).confirm(c.record)
    with c.writer._journal() as journal, journal.scope(native_scope(c)) as scoped:
        with pytest.raises(journal_module.NativeWriteJournalError, match="unresolved"):
            scoped.require_closed(closure)
        assert scoped.generation == before["generation"]
    c.proof.attest.side_effect = None
    assert recover(c) == ()
    with c.writer._journal() as journal, journal.scope(native_scope(c)) as scoped:
        scoped.require_closed(closure)
    assert attempt(c, ACTIVE, c.token).encode() == original


def test_unfinished_closure_rejects_other_complete_before_proof(tmp_path, monkeypatch):
    c, h, native = closure_case(tmp_path, monkeypatch, state="unsent")
    c.proof.attest.reset_mock()
    c.proof.cover.reset_mock()
    with pytest.raises(journal_module.NativeWriteJournalError, match="exact ACTIVE"):
        c.writer.insert(**c.options)
    c.proof.attest.assert_not_called()
    c.proof.cover.assert_not_called()


def test_v4_same_fields_legacy_version_gate_and_future_versions_fail_closed(case):
    c = case
    options = complete(c)
    path = index_path(c)
    old = json.loads(path.read_bytes())
    c.proof.cover.side_effect = NativeWriteProofError("retain")
    with pytest.raises(NativeWriteProofError):
        c.writer.insert(**options)
    document = json.loads(path.read_bytes())
    assert set(document) == set(old)
    assert document["version"] == 4

    # Exact pre-reproof decoder admission boundary: it fails before reaching
    # pending() and its old automatic Complete cleanup.
    def legacy_version_gate(raw):
        if json.loads(raw)["version"] not in {1, 2, 3}:
            raise journal_module.NativeWriteJournalError(
                "invalid canonical scope index"
            )
        pytest.fail("legacy reader admitted reproof semantics")

    with pytest.raises(journal_module.NativeWriteJournalError):
        legacy_version_gate(path.read_bytes())
    with c.writer._journal() as journal, journal.scope(native_scope(c)) as scoped:
        for version in (0, 7, True):
            bad = {**document, "version": version}
            bad["sha256"] = journal_module._hash(
                {k: v for k, v in bad.items() if k != "sha256"}
            )
            with pytest.raises(journal_module.NativeWriteJournalError):
                scoped._decode_index(journal_module._canonical(bad) + b"\n")
    assert path.read_bytes() == journal_module._canonical(document) + b"\n"


def test_ready_quarantine_upgrade_keeps_required_fields_and_version(case):
    c = case
    options = complete(c)
    c.proof.cover.side_effect = NativeWriteProofError("retain")
    with pytest.raises(NativeWriteProofError):
        c.writer.insert(**options)
    with c.writer._journal() as journal, journal.scope(native_scope(c)) as scoped:
        captured = scoped.capture_quarantine(
            journal_module.NativeQuarantineReason.UNRESOLVED_NATIVE_WRITE
        )
        scoped.quarantine(captured)
        assert len(scoped.pending()) == 1
        document = scoped._read_index()
        assert document["version"] == 5
        del document["quarantine"]
        with pytest.raises(journal_module.NativeWriteJournalError):
            scoped._save(document)
        assert scoped.quarantine_binding == captured
