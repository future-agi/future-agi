"""Offline frozen repair evidence, composed with the real publication journal."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

import pytest

from tracer.services.clickhouse.v2.property_catalog import (
    publication_journal,
)
from tracer.services.clickhouse.v2.property_catalog import (
    terminal_repair_intent as subject,
)
from tracer.services.clickhouse.v2.property_catalog.codec import (
    canonical_json,
    framed_sha256,
)
from tracer.services.clickhouse.v2.property_catalog.native_write_journal import (
    NativeQuarantineIntent,
    NativeQuarantineReason,
    NativeScopeQuarantine,
    NativeWriteScope,
    _cell,
    native_parameters_sha256,
)
from tracer.services.clickhouse.v2.property_catalog.native_write_proof import _COLUMNS
from tracer.services.clickhouse.v2.property_catalog.state_store import _activation_row
from tracer.services.clickhouse.v2.property_catalog.write_admission import _canonical
from tracer.tests.test_property_catalog_publication_journal import PublicationHarness

ACTIVE = "property_catalog_activations"
SOURCE = "property_catalog_source_streams"
OTHER_UUID = "99999999-9999-4999-8999-999999999999"
Intent = subject.FrozenTerminalRepairIntent
Error = subject.TerminalRepairIntentError


def _pending(document):
    record = publication_journal._record(document["publication"]["record"])
    return NativeQuarantineIntent(
        table=ACTIVE,
        deduplication_token=(
            f"property-catalog-activation-v1:{record.build_token}:"
            f"{record.activation_sha256}"
        ),
        query_id=OTHER_UUID,
        parameters_sha256=native_parameters_sha256(
            _COLUMNS[ACTIVE], (_activation_row(record),)
        ),
        intent_sha256="b" * 64,
    )


def _rehash_publication(document):
    publication = document["publication"]
    body = {key: value for key, value in publication.items() if key != "sha256"}
    publication["sha256"] = framed_sha256(
        publication_journal.PUBLICATION_FORMAT,
        document["database"],
        canonical_json(document["lease"]),
        canonical_json(body, max_bytes=publication_journal.MAX_PUBLICATION_BYTES),
    )
    publication_journal._validate(document)


def publication_evidence(directory):
    """Return (real journal/coordinator harness, complete from_evidence kwargs).

    The document is exactly the harness's stored, resolved ACTIVE publication;
    native writer tests can replace the quarantine with their real capture.
    """
    harness = PublicationHarness(directory)
    lease = harness.lease
    # Replace PublicationHarness's synthetic FENCED shortcut with the actual
    # open -> draining -> fenced coordinator path and its immutable deadline.
    synthetic = harness.base.client.stream_rows.pop()
    assert synthetic["status"] == "fenced" and synthetic["envelope_version"] == 0
    coordinator = harness.base.coordinator
    for stream in lease.build_plan.streams:
        coordinator.open_stream(
            lease=lease,
            source_adapter=stream.source_adapter,
            producer_stream_id=stream.producer_stream_id,
        )
    hot = next(
        stream.key
        for stream in lease.build_plan.streams
        if stream.role.value == "hot_values"
    )
    coordinator.begin_drain_intent(
        lease=lease,
        completed_stream_proofs=tuple(
            proof for proof in harness.fence.stream_proofs if proof.key != hot
        ),
        drain_deadline=lease.expires_at,
        now=harness.base.clock(),
    )
    harness.fence = coordinator.fence(
        lease=lease,
        stream_proofs=harness.fence.stream_proofs,
        checkpoint_state_sha256s=harness.fence.checkpoint_state_sha256s,
        final_manifest_sha256=harness.fence.manifest_sha256,
        drain_deadline=lease.expires_at,
        now=harness.base.clock(),
    )
    harness.activate()
    document = harness.document()
    scope = NativeWriteScope(
        lease.organization_id,
        lease.workspace_id,
        lease.catalog_epoch,
        lease.projection_version,
        lease.catalog_revision,
        lease.build_token,
    )
    return harness, {
        "database": harness.client.catalog_database,
        "quarantine": NativeScopeQuarantine(
            scope=scope,
            admission_sha256="a" * 64,
            generation=1,
            pending=(_pending(document),),
            reason=NativeQuarantineReason.PUBLICATION_BLOCKED,
        ),
        "lease": lease,
        "reservation": deepcopy(harness.base.client.stream_rows[-1]),
        "publication_document": document,
        "repaired_at": lease.expires_at + timedelta(seconds=1, microseconds=654321),
    }


def test_fixture_uses_real_drain_and_fenced_reservation(tmp_path):
    harness, values = publication_evidence(tmp_path)
    lease = values["lease"]
    reservations = [
        row
        for batch in harness.base.client.inserts
        for row in batch
        if row["build_token"] == lease.build_token and row["envelope_version"] == 0
    ]
    assert [row["status"] for row in reservations] == ["open", "draining", "fenced"]
    assert [row["_version"] for row in reservations] == [1, 2, 3]
    assert all(row["drain_deadline"] == lease.expires_at for row in reservations)
    assert values["reservation"] == reservations[-1]
    assert values["publication_document"] == harness.document()
    intent = Intent.from_evidence(**values)
    assert native_parameters_sha256(_COLUMNS[SOURCE], (intent.reservation,)) == (
        native_parameters_sha256(_COLUMNS[SOURCE], (values["reservation"],))
    )
    assert intent.publication_document == harness.document()


@pytest.fixture
def evidence(tmp_path):
    _, evidence = publication_evidence(tmp_path)
    # A non-initial sequence catches accidental replacement by the terminal
    # version. The real journal validates this self-anchored INITIAL record.
    document = evidence["publication_document"]
    document["publication"]["record"].update(activation_sequence=17, version=17)
    _rehash_publication(document)
    evidence["quarantine"] = replace(
        evidence["quarantine"], pending=(_pending(document),)
    )
    return evidence


def _no_active(evidence, *, admitted=False):
    result = dict(evidence)
    result["quarantine"] = replace(evidence["quarantine"], generation=0, pending=())
    if admitted:
        result["publication_document"] = {
            **deepcopy(evidence["publication_document"]),
            "phase": "admitted",
            "publication": None,
        }
    else:
        result["publication_document"] = None
    return result


def _rehashed(document):
    body = {key: value for key, value in document.items() if key != "sha256"}
    return (
        _canonical({**body, "sha256": hashlib.sha256(_canonical(body)).hexdigest()})
        + b"\n"
    )


@pytest.mark.parametrize("phase", ["prepared", "armed", "resolved"])
def test_exact_disabled_then_failed_rows_and_binding(evidence, phase):
    evidence["publication_document"]["phase"] = phase
    intent = Intent.from_evidence(**evidence)
    publication = evidence["publication_document"]["publication"]
    active = _activation_row(publication_journal._record(publication["record"]))
    disabled, failed = intent.rows
    assert dict(disabled) == {
        **active,
        "status": "disabled",
        "_version": subject.UINT64_MAX,
        "updated_at": evidence["repaired_at"],
    }
    assert disabled["activation_sequence"] == 17 < disabled["_version"]
    assert dict(failed) == {
        **evidence["reservation"],
        "status": "failed",
        "_version": subject.UINT64_MAX,
        "updated_at": evidence["repaired_at"],
        "gap_count": 1,
        "gap_reasons": failed["gap_reasons"],
    }
    assert len(failed["gap_reasons"]) == 1
    reason = failed["gap_reasons"][0]
    assert len(reason.encode()) < 512
    assert json.loads(reason)["reason"] == evidence["quarantine"].reason.value
    assert len(json.loads(reason)["evidence_sha256"]) == 64
    assert intent.binding.quarantine == evidence["quarantine"]
    assert intent.binding.build_lease_sha256 == evidence["lease"].build_lease_sha256
    assert intent.binding.publication_sha256 == publication["sha256"]
    assert [write.table for write in intent.binding.writes] == [ACTIVE, SOURCE]
    for write, row in zip(intent.binding.writes, intent.rows, strict=True):
        assert tuple(row) == _COLUMNS[write.table]
        assert write.parameters_sha256 == native_parameters_sha256(
            _COLUMNS[write.table], (row,)
        )
        assert write.deduplication_token == (
            f"property-catalog-terminal-repair-v1:{intent.lease.build_token}:{write.table}"
        )
    assert intent.encode() == Intent.from_evidence(**evidence).encode()


@pytest.mark.parametrize("admitted", [False, True])
def test_absent_or_admitted_publication_only_repairs_source(evidence, admitted):
    evidence = _no_active(evidence, admitted=admitted)
    intent = Intent.decode(Intent.from_evidence(**evidence).encode())
    assert [write.table for write in intent.binding.writes] == [SOURCE]
    assert len(intent.rows) == 1 and intent.rows[0]["status"] == "failed"
    assert intent.binding.publication_sha256 is None
    assert intent.publication_document == evidence["publication_document"]


@pytest.mark.parametrize(
    "status", ["open", "draining", "fenced", "complete", "gap", "failed"]
)
def test_complete_original_reservation_status_is_retained_only_as_evidence(
    evidence, status
):
    evidence["reservation"]["status"] = status
    intent = Intent.from_evidence(**evidence)
    assert intent.reservation["status"] == status
    assert intent.rows[-1]["status"] == "failed"


def test_terminal_version_dominates_largest_admissible_original_versions(evidence):
    evidence["reservation"]["_version"] = subject.UINT64_MAX - 1
    document = evidence["publication_document"]
    document["publication"]["record"].update(
        activation_sequence=subject.UINT64_MAX - 1, version=subject.UINT64_MAX - 1
    )
    _rehash_publication(document)
    evidence["quarantine"] = replace(
        evidence["quarantine"], pending=(_pending(document),)
    )
    intent = Intent.decode(Intent.from_evidence(**evidence).encode())
    assert intent.rows[0]["activation_sequence"] == subject.UINT64_MAX - 1
    assert all(row["_version"] == subject.UINT64_MAX for row in intent.rows)


def test_restart_api_preserves_original_native_types_and_microseconds(evidence):
    for field in (
        "organization_id",
        "workspace_id",
        "build_token",
        "producer_stream_id",
    ):
        evidence["reservation"][field] = UUID(evidence["reservation"][field])
    evidence["reservation"]["gap_reasons"] = ["original evidence", "<untouched>&"]
    evidence["reservation"]["gap_count"] = 2
    evidence["reservation"]["updated_at"] = evidence["repaired_at"] - timedelta(
        microseconds=1
    )
    intent = Intent.from_evidence(**evidence)
    restored = Intent.decode(intent.encode())
    assert restored == intent
    assert restored.encode() == intent.encode()
    assert restored.database == evidence["database"]
    assert restored.sha256 == hashlib.sha256(intent.encode()).hexdigest()
    assert restored.lease == evidence["lease"]
    assert restored.publication_document == evidence["publication_document"]
    assert restored.reservation == {
        **evidence["reservation"],
        "gap_reasons": ("original evidence", "<untouched>&"),
    }
    assert type(restored.reservation["build_token"]) is UUID
    assert type(restored.rows[-1]["build_token"]) is UUID
    assert type(restored.rows[-1]["updated_at"]) is datetime
    assert (
        restored.rows[-1]["updated_at"].microsecond
        == evidence["repaired_at"].microsecond
    )


def test_input_and_returned_evidence_mutations_cannot_change_intent(evidence):
    evidence["reservation"]["gap_reasons"] = ["original"]
    intent = Intent.from_evidence(**evidence)
    raw, digest = intent.encode(), intent.sha256
    evidence["reservation"]["gap_reasons"].append("mutation")
    evidence["reservation"]["status"] = "complete"
    evidence["publication_document"]["publication"]["record"]["status"] = "disabled"
    evidence["publication_document"]["publication"]["checkpoint_states"].clear()
    returned_document = intent.publication_document
    returned_document["publication"]["record"]["version"] = 100
    returned_document["publication"]["checkpoint_states"].clear()
    returned_row = intent.reservation
    returned_row["status"] = "gap"
    assert intent.reservation["gap_reasons"] == ("original",)
    assert intent.reservation["status"] == "fenced"
    assert intent.publication_document["publication"]["record"]["status"] == "active"
    assert intent.publication_document["publication"]["record"]["version"] == 17
    assert len(intent.publication_document["publication"]["checkpoint_states"]) == 10
    with pytest.raises(TypeError):
        intent.rows[-1]["status"] = "complete"
    with pytest.raises(TypeError):
        intent.rows[-1]["gap_reasons"][0] = "mutation"
    with pytest.raises(FrozenInstanceError):
        intent.lease = evidence["lease"]
    with pytest.raises(FrozenInstanceError):
        intent.lease.build_token = OTHER_UUID
    with pytest.raises(FrozenInstanceError):
        intent.binding.publication_sha256 = "c" * 64
    with pytest.raises(FrozenInstanceError):
        intent.binding.writes[0].parameters_sha256 = "c" * 64
    assert intent.encode() == raw and intent.sha256 == digest
    assert Intent.decode(raw) == intent


@pytest.mark.parametrize(
    "field,value",
    [
        ("organization_id", OTHER_UUID),
        ("workspace_id", OTHER_UUID),
        ("build_token", OTHER_UUID),
        ("catalog_revision", 123),
        ("catalog_epoch", 123),
        ("projection_version", 123),
        ("producer_stream_id", OTHER_UUID),
        ("source_adapter", "span_ingest"),
        ("envelope_version", 1),
        ("envelope_version", True),
        ("build_plan_json", "{}"),
        ("build_lease_sha256", "c" * 64),
        ("started_at", datetime(2000, 1, 1, tzinfo=UTC)),
        ("drain_deadline", datetime(2000, 1, 1, tzinfo=UTC)),
        ("status", "active"),
        ("_version", 0),
        ("_version", -1),
        ("_version", True),
        ("_version", subject.UINT64_MAX),
        ("_version", subject.UINT64_MAX + 1),
    ],
)
def test_rejects_reservation_not_bound_to_complete_original_lease(
    evidence, field, value
):
    evidence["reservation"][field] = value
    with pytest.raises(Error):
        Intent.from_evidence(**evidence)


@pytest.mark.parametrize("field", ["started_at", "drain_deadline"])
def test_lease_time_comparison_does_not_drop_microseconds(evidence, field):
    evidence["reservation"][field] += timedelta(microseconds=1)
    with pytest.raises(Error, match="lease/plan"):
        Intent.from_evidence(**evidence)


@pytest.mark.parametrize("kind", ["head", "missing", "extra"])
def test_rejects_partial_visible_head_or_changed_columns(evidence, kind):
    row = evidence["reservation"]
    if kind == "head":
        evidence["reservation"] = {
            key: row[key] for key in ("build_token", "status", "_version")
        }
    elif kind == "missing":
        del row["fenced_at"]
    else:
        row["unknown"] = "metadata"
    with pytest.raises(Error):
        Intent.from_evidence(**evidence)


@pytest.mark.parametrize("admitted", [False, True])
def test_pending_active_requires_frozen_publication(evidence, admitted):
    evidence["publication_document"] = _no_active(evidence, admitted=admitted)[
        "publication_document"
    ]
    with pytest.raises(Error, match="pending ACTIVE"):
        Intent.from_evidence(**evidence)


@pytest.mark.parametrize(
    "field,value",
    [
        ("deduplication_token", "other-original-active"),
        ("parameters_sha256", "c" * 64),
    ],
)
def test_pending_active_requires_exact_original_token_and_native_digest(
    evidence, field, value
):
    original = evidence["quarantine"]
    evidence["quarantine"] = replace(
        original, pending=(replace(original.pending[0], **{field: value}),)
    )
    with pytest.raises(Error, match="pending ACTIVE"):
        Intent.from_evidence(**evidence)


def test_other_pending_table_does_not_authorize_any_additional_write(evidence):
    pending = replace(
        evidence["quarantine"].pending[0], table="property_catalog_checkpoints"
    )
    evidence = _no_active(evidence)
    evidence["quarantine"] = replace(
        evidence["quarantine"], generation=1, pending=(pending,)
    )
    intent = Intent.from_evidence(**evidence)
    assert intent.binding.quarantine.pending == (pending,)
    assert [write.table for write in intent.binding.writes] == [SOURCE]


@pytest.mark.parametrize(
    "kind", ["checksum", "status", "phase", "database", "lease", "max_version"]
)
def test_publication_revalidated_even_with_recomputed_checksum(evidence, kind):
    document = evidence["publication_document"]
    if kind == "checksum":
        document["publication"]["sha256"] = "c" * 64
    elif kind == "status":
        document["publication"]["record"]["status"] = "disabled"
    elif kind == "phase":
        document["phase"] = "unknown"
    elif kind == "database":
        document["database"] = "property_catalog_dev_other"
        _rehash_publication(document)
    elif kind == "lease":
        document["lease"]["issued_at"] = (
            evidence["lease"].issued_at - timedelta(microseconds=1)
        ).isoformat(timespec="microseconds")
        _rehash_publication(document)
    else:
        document["publication"]["record"].update(
            activation_sequence=subject.UINT64_MAX, version=subject.UINT64_MAX
        )
        _rehash_publication(document)
        evidence["quarantine"] = replace(
            evidence["quarantine"], pending=(_pending(document),)
        )
    with pytest.raises(Error):
        Intent.from_evidence(**evidence)


def test_quarantine_must_identify_exact_lease(evidence):
    original = evidence["quarantine"]
    evidence["quarantine"] = replace(
        original, scope=replace(original.scope, build_token=OTHER_UUID)
    )
    with pytest.raises(Error, match="quarantined scope"):
        Intent.from_evidence(**evidence)


@pytest.mark.parametrize(
    "database", [None, "", "default", "property_catalog_dev_a; SELECT 1"]
)
def test_invalid_database_rejected_with_intent_error(evidence, database):
    with pytest.raises(Error):
        Intent.from_evidence(**{**evidence, "database": database})


@pytest.mark.parametrize(
    "value",
    [
        None,
        "2026-01-01",
        datetime(2026, 1, 1),
        datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=1))),
    ],
)
def test_repair_time_requires_exact_utc_datetime(evidence, value):
    with pytest.raises(Error):
        Intent.from_evidence(**{**evidence, "repaired_at": value})


def test_repair_cannot_predate_lease_issuance(evidence):
    with pytest.raises(Error, match="predates frozen lease"):
        Intent.from_evidence(
            **{
                **evidence,
                "repaired_at": evidence["lease"].issued_at - timedelta(microseconds=1),
            }
        )
    assert Intent.from_evidence(
        **{**evidence, "repaired_at": evidence["lease"].issued_at}
    )


def test_bounded_original_evidence_and_restart_bytes(evidence):
    evidence["reservation"]["gap_reasons"] = (
        "x" * subject.MAX_TERMINAL_REPAIR_INTENT_BYTES,
    )
    with pytest.raises(Error, match="byte bound"):
        Intent.from_evidence(**evidence)
    with pytest.raises(Error, match="bound"):
        Intent.decode(b"x" * (subject.MAX_TERMINAL_REPAIR_INTENT_BYTES + 1))


@pytest.mark.parametrize(
    "kind",
    [
        "empty",
        "str",
        "newline",
        "whitespace",
        "duplicate",
        "version",
        "checksum",
        "extra",
        "invalid_json",
    ],
)
def test_restart_rejects_noncanonical_or_corrupt_encoding(evidence, kind):
    raw = Intent.from_evidence(**evidence).encode()
    document = json.loads(raw)
    if kind == "empty":
        raw = b""
    elif kind == "str":
        raw = raw.decode()
    elif kind == "newline":
        raw = raw[:-1]
    elif kind == "whitespace":
        raw = b" " + raw
    elif kind == "duplicate":
        raw = b'{"version":1,' + raw[1:]
    elif kind == "invalid_json":
        raw = b"\xff\n"
    elif kind == "version":
        document["version"] = True
        raw = _rehashed(document)
    elif kind == "extra":
        document["unknown"] = None
        raw = _rehashed(document)
    else:
        document["sha256"] = "c" * 64
        raw = _canonical(document) + b"\n"
    with pytest.raises(Error):
        Intent.decode(raw)


@pytest.mark.parametrize(
    "kind",
    [
        "row",
        "token",
        "digest",
        "order",
        "scope",
        "original",
        "typed_cell",
        "evidence_fields",
    ],
)
def test_restart_rederives_intent_instead_of_trusting_rehashed_rows(evidence, kind):
    document = json.loads(Intent.from_evidence(**evidence).encode())
    if kind == "row":
        document["rows"][0][_COLUMNS[ACTIVE].index("status")] = _cell("active")
    elif kind == "token":
        document["binding"]["writes"][0]["deduplication_token"] = "other-token"
    elif kind == "digest":
        document["binding"]["writes"][0]["parameters_sha256"] = "c" * 64
    elif kind == "order":
        document["rows"].reverse()
        document["binding"]["writes"].reverse()
    elif kind == "scope":
        document["evidence"]["quarantine"]["scope"]["build_token"] = OTHER_UUID
    elif kind == "original":
        document["evidence"]["reservation"][
            _COLUMNS[SOURCE].index("build_lease_sha256")
        ] = _cell("c" * 64)
    elif kind == "typed_cell":
        document["evidence"]["reservation"][_COLUMNS[SOURCE].index("_version")] = [
            "uint64",
            "03",
        ]
    else:
        document["evidence"]["new_authority"] = True
    with pytest.raises(Error):
        Intent.decode(_rehashed(document))


def test_direct_construction_cannot_bypass_evidence():
    with pytest.raises(TypeError, match="from_evidence or decode"):
        Intent()
