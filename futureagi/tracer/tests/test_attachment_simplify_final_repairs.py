"""Source-isolated checks for the final recovery repairs, without a database.

Receipt cases execute the actual backfill decision AST, not a copied algorithm.
SQL assertions inspect source structure only; PostgreSQL trigger behavior and
migration/restore correctness still require the coordinated database suite.
"""

import ast
import importlib.util
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

from tracer.services.attachments.interfaces import SourceFacts

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
BACKFILL_PATH = MIGRATIONS / "0141_attachment_value_backfill.py"
TREE = ast.parse(BACKFILL_PATH.read_text())
FENCES_TREE = ast.parse((MIGRATIONS / "0142_attachment_value_fences.py").read_text())
SQL = ast.literal_eval(
    next(
        node.value
        for node in FENCES_TREE.body
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "FORWARD" for t in node.targets)
    )
)
spec = importlib.util.spec_from_file_location("repair_backfill", BACKFILL_PATH)
BACKFILL = importlib.util.module_from_spec(spec)
spec.loader.exec_module(BACKFILL)


def receipt_decision(*, verified=False, audit=None, item=None, state="deleted"):
    """Execute just the real decision/validation nodes, no ORM simulation."""
    copy_uploads = next(
        node
        for node in TREE.body
        if isinstance(node, ast.FunctionDef) and node.name == "copy_uploads"
    )
    blocks = [
        node
        for node in ast.walk(copy_uploads)
        if isinstance(node, ast.If)
        and ast.unparse(node.test) == "asset.verified_blob_id"
        and ast.unparse(node.body[0]) == "outcome = 'ready'"
    ]
    validations = [
        node
        for node in ast.walk(copy_uploads)
        if isinstance(node, ast.If)
        and ast.unparse(node.test).startswith("outcome not in TERMINAL")
    ]
    assert len(blocks) == len(validations) == 1
    module = ast.Module(body=[blocks[0], validations[0]], type_ignores=[])
    namespace = {
        "asset": SimpleNamespace(verified_blob_id=UUID(int=1) if verified else None),
        "audit": SimpleNamespace(reason=audit) if audit is not None else None,
        "item": item,
        "row": SimpleNamespace(state=state),
        "evidence_ref": BACKFILL.evidence_ref,
        "fail": BACKFILL.fail,
        "TERMINAL": BACKFILL.TERMINAL,
    }
    exec(compile(module, str(BACKFILL_PATH), "exec"), namespace)
    return namespace["outcome"]


def test_verified_deleted_owner_keeps_ready_receipt_not_later_state():
    assert receipt_decision(verified=True, audit="failed") == "ready"


@pytest.mark.parametrize("outcome", ["failed", "rejected", "expired", "deleted"])
def test_deleted_owner_recovers_accepted_original_audit(outcome):
    assert receipt_decision(audit=outcome) == outcome


@pytest.mark.parametrize("outcome", ["failed", "rejected", "expired", "deleted"])
def test_deleted_owner_recovers_sealed_terminal_evidence(outcome):
    assert (
        receipt_decision(
            item={"terminal_outcome": outcome, "terminal_evidence": "sealed-original"}
        )
        == outcome
    )


@pytest.mark.parametrize("state", ["failed", "rejected", "expired"])
def test_unambiguous_historical_terminal_state_is_original_evidence(state):
    assert receipt_decision(state=state) == state


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({}, "deleted missing receipt"),
        ({"state": "ready"}, "deleted missing receipt"),
        ({"item": {"terminal_outcome": "failed"}}, "evidence reference"),
        ({"audit": "ready"}, "no recoverable outcome"),
        ({"audit": "unknown"}, "no recoverable outcome"),
        (
            {"item": {"terminal_outcome": "ready", "terminal_evidence": "sealed"}},
            "no recoverable outcome",
        ),
    ],
)
def test_missing_or_contradictory_original_outcome_fails_closed(kwargs, message):
    with pytest.raises(RuntimeError, match=message):
        receipt_decision(**kwargs)


def test_empty_quoted_etag_maps_exactly_from_helper_to_backfill():
    now = datetime(2026, 11, 16, tzinfo=UTC)
    facts = SourceFacts(
        "RemoteUrlEnvelope",
        UUID(int=1),
        "user",
        UUID(int=2),
        "http_strong_etag",
        '""',
        10,
        "audio/wav",
        1,
        now + timedelta(hours=1),
        0,
        UUID(int=3),
        "0123456789abcdef",
        "mixed",
        None,
    )
    mapping = {
        "generation_kind": "source_generation_kind",
        "generation": "source_generation",
        "byte_length": "source_byte_length",
        "mime_type": "source_mime_type",
        "duration_ms": "source_duration_ms",
        "expires_at": "source_expires_at",
        "authority_generation": "source_authority_generation",
        "creator_kind": "source_creator_kind",
        "creator_id": "source_creator_id",
        "parent_trace_id": "source_trace_id",
        "parent_span_id": "source_span_id",
        "direction": "source_direction",
    }
    source = {target: getattr(facts, field) for field, target in mapping.items()}
    restored = BACKFILL.source_values({"source": source, "source_evidence": "sealed"})
    assert restored == source
    # These are source checks, not an executed PostgreSQL regular expression.
    assert SQL.count('!~ \'^"[^"]*"$\'') == 2
    assert '!~ \'^"[^"]+"$\'' not in SQL


def test_sql_source_freezes_identity_but_allows_live_plan_metadata():
    start = SQL.index("FOREACH field IN ARRAY ARRAY['reservation_id'")
    loop = SQL[start : SQL.index("END LOOP;", start)]
    fields = re.findall(r"'([^']+)'", loop.split("] LOOP")[0])
    assert fields == [
        "reservation_id",
        "reservation_created_at",
        "reservation_expires_at",
        "reservation_pool_key",
    ]
    assert (
        "FOREACH field IN ARRAY reservation_fields || ARRAY['reservation_settled_at'] LOOP"
        in SQL
    )


def test_sql_source_receipt_requires_ready_for_verified_and_zero_otherwise():
    assert (
        "a.verified_blob_id IS NOT NULL AND (NEW.usage_outcome<>'ready' OR NEW.usage_stored_bytes IS DISTINCT FROM a.verified_byte_length)"
        in SQL
    )
    assert (
        "a.verified_blob_id IS NULL AND (NEW.usage_outcome='ready' OR NEW.usage_stored_bytes<>0)"
        in SQL
    )


def test_sql_source_bounds_both_worker_leases():
    assert SQL.count("NEW.lease_until>clock_timestamp()+interval '15 minutes'") == 2
    assert (
        "NEW.lease_until>NEW.deadline OR NEW.lease_until>clock_timestamp()+interval '15 minutes'"
        in SQL
    )
    assert "NEW.lease_until>NEW.reservation_expires_at" in SQL


def test_sql_source_provider_enum_includes_all_terminal_states():
    start = SQL.index("ELSIF TG_TABLE_NAME='tracer_attachmentproviderrecording' THEN")
    guard = SQL[start : SQL.index("attachment provider state invalid", start)]
    match = re.search(r"NEW.provider_state NOT IN \(([^)]+)\)", guard)
    assert match
    assert re.findall(r"'([^']+)'", match.group(1)) == [
        "registered",
        "active",
        "ending",
        "complete",
        "failed",
        "aborted",
        "limit_reached",
    ]


def test_reconciled_counter_zip_is_complete_and_strict():
    calls = [
        n
        for n in ast.walk(TREE)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "zip"
        and n.args
        and ast.unparse(n.args[0]) == "COUNTERS"
    ]
    assert len(calls) == 1
    assert any(
        k.arg == "strict" and ast.literal_eval(k.value) is True
        for k in calls[0].keywords
    )
    assert len(BACKFILL.COUNTERS) == len(BACKFILL.VECTOR) == 6
