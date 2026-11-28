"""Database-free checks for the frozen schema/provenance boundary.

These are not migration/restore or PostgreSQL trigger qualification.
"""

import ast
import hashlib
import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations"


def load_migration(name):
    spec = importlib.util.spec_from_file_location(name, MIGRATIONS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BACKFILL = load_migration("0141_attachment_value_backfill")
FENCES = load_migration("0142_attachment_value_fences")


def source():
    return {
        "source_generation_kind": "http_strong_etag",
        "source_generation": '"original-v1"',
        "source_byte_length": 32,
        "source_mime_type": "audio/wav",
        "source_duration_ms": 1,
        "source_expires_at": datetime.now(UTC) + timedelta(hours=1),
        "source_authority_generation": 0,
        "source_creator_kind": "user",
        "source_creator_id": str(uuid4()),
        "source_trace_id": str(uuid4()),
        "source_span_id": "0123456789abcdef",
        "source_direction": "mixed",
    }


def test_source_requires_original_evidence_and_complete_generation():
    with pytest.raises(RuntimeError, match="evidence reference"):
        BACKFILL.source_values({"source": source()})
    facts = source()
    del facts["source_generation"]
    with pytest.raises(RuntimeError, match="complete pinned source"):
        BACKFILL.source_values(
            {"source_evidence": "sealed fixture evidence", "source": facts}
        )


@pytest.mark.parametrize("revision", ["", "null", 'W/"weak"', "unquoted"])
def test_source_never_learns_a_new_or_weak_generation(revision):
    facts = source()
    facts["source_generation"] = revision
    with pytest.raises(RuntimeError):
        BACKFILL.source_values(
            {"source_evidence": "sealed fixture evidence", "source": facts}
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_byte_length", True),
        ("source_byte_length", 0),
        ("source_authority_generation", True),
        ("source_authority_generation", -1),
        ("source_duration_ms", 0),
    ],
)
def test_source_integer_facts_are_real_bounded_integers(field, value):
    facts = source()
    facts[field] = value
    with pytest.raises(RuntimeError):
        BACKFILL.source_values(
            {"source_evidence": "sealed fixture evidence", "source": facts}
        )


def test_absent_owner_mapping_is_not_a_default_binding():
    assert BACKFILL.owner_map({"owners": {}}, "Attachment", uuid4()) is None
    with pytest.raises(RuntimeError, match="key/version"):
        BACKFILL.storage_proof(
            {"storage": {"serving": {"key": "original/fixture", "version": "v2"}}},
            "serving",
            "original/fixture",
            "v1",
        )


def test_population_requires_inventory_before_any_database_write():
    apps = Mock()
    apps.get_model.return_value.objects.using.return_value.exists.return_value = True
    connection = SimpleNamespace(alias="default")
    with patch.object(BACKFILL, "settings", SimpleNamespace()):
        with pytest.raises(RuntimeError, match="populated install requires"):
            BACKFILL.provenance(apps, connection)


def test_historical_migration_fingerprints_are_exact():
    assert len(BACKFILL.HISTORICAL_FILES) == 4
    for name, expected in BACKFILL.HISTORICAL_FILES.items():
        assert (
            hashlib.sha256((MIGRATIONS / (name + ".py")).read_bytes()).hexdigest()
            == expected
        )


def test_historical_facts_are_not_read_from_current_services():
    for filename in (
        "0141_attachment_value_backfill.py",
        "0142_attachment_value_fences.py",
    ):
        text = (MIGRATIONS / filename).read_text()
        assert "from tracer.services" not in text
        assert "from_settings(" not in text
        assert "FI_ATTACHMENTS_BINDING_ACCESS" not in text
    assert "row.reservation_expires_at or row.deadline" in ast.unparse(
        ast.parse((MIGRATIONS / "0141_attachment_value_backfill.py").read_text())
    )


def test_reservation_vectors_are_complete_without_bool_or_clamping():
    capacity = dict(zip(BACKFILL.VECTOR, (1, 2, 3, 4, 5, 6), strict=True))
    actual = BACKFILL.live_vector(
        {"capacity": capacity, "capacity_evidence": "reconciled fixture"}
    )
    assert actual == {"reservation_" + k: v for k, v in capacity.items()}
    capacity["transfers"] = True
    with pytest.raises(RuntimeError, match="nonnegative"):
        BACKFILL.live_vector(
            {"capacity": capacity, "capacity_evidence": "reconciled fixture"}
        )


def test_cleanup_requires_horizon_and_actual_checked_stamp():
    now = datetime.now(UTC)
    with pytest.raises(RuntimeError, match="precedes"):
        BACKFILL.cleanup_values(
            {"cleanup_not_before": now}, minimum=now + timedelta(seconds=1)
        )
    with pytest.raises(RuntimeError, match="beyond write horizon"):
        BACKFILL.cleanup_values(
            {
                "cleanup_not_before": now,
                "cleanup_complete": True,
                "cleanup_evidence": "fixture",
                "cleanup_checked_at": now - timedelta(seconds=1),
            },
            minimum=now,
        )
    with pytest.raises(RuntimeError, match="aware timestamp"):
        BACKFILL.stamp(datetime.now())


def test_identity_digest_includes_resource_incarnation_and_is_timezone_stable():
    values = {name: name for name in BACKFILL.BINDING_FIELDS}
    t = datetime(2026, 1, 1, tzinfo=UTC)
    values.update(quarantine_created_at=t, serving_created_at=t)
    first = BACKFILL.identity_digest(values)
    values["serving_created_at"] = t + timedelta(microseconds=1)
    assert BACKFILL.identity_digest(values) != first
    values["serving_created_at"] = t.isoformat()
    assert BACKFILL.identity_digest(values) == first
    values["serving_owner_identity"] += "\n"
    with pytest.raises(RuntimeError):
        BACKFILL.identity_digest(values)


def test_target_has_three_real_consolidations_not_legacy_model_aliases():
    source_text = (ROOT / "models/attachment.py").read_text()
    names = {
        node.name
        for node in ast.parse(source_text).body
        if isinstance(node, ast.ClassDef)
    }
    assert not names & {"AttachmentBlob", "UploadReservation", "AttachmentUsageReceipt"}
    contraction = load_migration("0143_attachment_value_contraction")
    assert {
        op.name
        for op in contraction.Migration.operations
        if type(op).__name__ == "DeleteModel"
    } == {"AttachmentBlob", "UploadReservation", "AttachmentUsageReceipt"}
    assert contraction.Migration.operations[0].reverse_code is None


def test_fences_keep_legacy_protection_but_remove_blob_query_dependency():
    assert "CREATE OR REPLACE FUNCTION attachment_record_fence()" in FENCES.FORWARD
    assert "FROM tracer_attachmentblob" not in FENCES.FORWARD
    assert "attachment blob immutable" in FENCES.FORWARD
    assert "attachment snapshot immutable" in FENCES.FORWARD
    assert FENCES.FORWARD.count("CREATE TRIGGER attachment_v1_guard") == 16


def test_migration_review_is_not_fabricated():
    for name in (
        "0140_attachment_value_expansion",
        "0141_attachment_value_backfill",
        "0142_attachment_value_fences",
        "0143_attachment_value_contraction",
    ):
        migration = load_migration(name)
        assert bool(migration.UPDATE_SAFETY["reviewer"]) == bool(
            migration.UPDATE_SAFETY["review"]
        )
        assert migration.UPDATE_SAFETY["reviewer"] != "reviewer-login"
        assert (
            migration.UPDATE_SAFETY["review"]
            != "https://github.com/future-agi/future-agi/pull/123#discussion_r456"
        )
        assert migration.UPDATE_SAFETY["large_volume_rehearsal"] is True
