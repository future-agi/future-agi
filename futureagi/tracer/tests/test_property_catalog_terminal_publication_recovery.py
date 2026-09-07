"""Publication proof-consumer contract; producer's remote proof has separate tests."""

from dataclasses import replace

import pytest

from tracer.services.clickhouse.v2.property_catalog.activation import (
    CatalogLifecycleMode,
)
from tracer.services.clickhouse.v2.property_catalog.native_write_journal import (
    NativeTerminalRepairReceipt,
)
from tracer.services.clickhouse.v2.property_catalog.publication_journal import (
    PublicationJournalError,
)
from tracer.services.clickhouse.v2.property_catalog.terminal_repair_intent import (
    FrozenTerminalRepairIntent,
)
from tracer.tests.test_property_catalog_activation_history import _set_mode
from tracer.tests.test_property_catalog_terminal_repair_intent import (
    publication_evidence,
)


def invalidated_publication(tmp_path):
    h, evidence = publication_evidence(tmp_path)
    intent = FrozenTerminalRepairIntent.from_evidence(**evidence)
    receipt = NativeTerminalRepairReceipt(intent.binding, ("a" * 64, "b" * 64))
    old_key, old_document = h.key(), h.document()
    h.rows.append(dict(intent.rows[0]))
    h.advance()
    _set_mode(h, CatalogLifecycleMode.INITIAL_BACKFILL, h.lease.catalog_revision)
    return h, receipt, old_key, old_document


def test_proven_terminal_predecessor_allows_new_initial_without_reusing_sequence(
    tmp_path, monkeypatch
):
    h, receipt, old_key, old_document = invalidated_publication(tmp_path)
    calls = []

    def confirmed(lease, marker):
        calls.append((lease, marker))
        return receipt

    monkeypatch.setattr(h.base.coordinator, "_confirm_terminal_marker", confirmed)
    result = h.activate()
    assert len(calls) == 1
    assert (
        result.record.activation_sequence
        == old_document["publication"]["record"]["activation_sequence"] + 1
    )
    assert result.record.lineage_anchor_revision == h.lease.catalog_revision
    assert h.document()["publication"]["previous_active"] is None
    assert h.document()["previous_marker"] == calls[0][1]
    assert h.base.coordinator._recovery_journal.load_record(old_key) == old_document
    assert h.rows[-2]["status"] == "disabled"
    assert len(h.sent) == 2


@pytest.mark.parametrize("proof", [None, True, False, {}, "complete"])
def test_boolean_or_untyped_completion_does_not_replace_active_proof(
    tmp_path, monkeypatch, proof
):
    h, _, _, _ = invalidated_publication(tmp_path)
    monkeypatch.setattr(
        h.base.coordinator, "_confirm_terminal_marker", lambda *args: proof
    )
    with pytest.raises(PublicationJournalError, match="not positively visible"):
        h.activate()
    assert len(h.sent) == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("organization_id", "99999999-9999-4999-8999-999999999999"),
        ("workspace_id", "99999999-9999-4999-8999-999999999999"),
        ("catalog_epoch", 65534),
        ("projection_version", 65534),
        ("catalog_revision", 99),
    ],
)
def test_exact_receipt_from_another_scope_cannot_supersede_marker(
    tmp_path, monkeypatch, field, value
):
    h, receipt, _, _ = invalidated_publication(tmp_path)
    scope = replace(receipt.binding.quarantine.scope, **{field: value})
    quarantine = replace(receipt.binding.quarantine, scope=scope)
    receipt = replace(receipt, binding=replace(receipt.binding, quarantine=quarantine))
    monkeypatch.setattr(
        h.base.coordinator, "_confirm_terminal_marker", lambda *args: receipt
    )
    with pytest.raises(PublicationJournalError, match="not positively visible"):
        h.activate()
    assert len(h.sent) == 1


def test_different_build_lease_receipt_cannot_supersede_marker(tmp_path, monkeypatch):
    h, receipt, _, _ = invalidated_publication(tmp_path)
    receipt = replace(
        receipt, binding=replace(receipt.binding, build_lease_sha256="e" * 64)
    )
    monkeypatch.setattr(
        h.base.coordinator, "_confirm_terminal_marker", lambda *args: receipt
    )
    with pytest.raises(PublicationJournalError, match="not positively visible"):
        h.activate()
    assert len(h.sent) == 1


def test_failed_terminal_coverage_leaves_old_marker_and_no_successor_write(
    tmp_path, monkeypatch
):
    h, _, old_key, old_document = invalidated_publication(tmp_path)

    def failed(*args):
        raise TimeoutError("terminal member coverage unresolved")

    monkeypatch.setattr(h.base.coordinator, "_confirm_terminal_marker", failed)
    with pytest.raises(TimeoutError, match="terminal member coverage"):
        h.activate()
    assert h.base.coordinator._recovery_journal.load_record(old_key) == old_document
    assert len(h.sent) == 1
