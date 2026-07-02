"""curated_writer — app-level dual-write of the CURATED dimensions ``end_users``
/ ``trace_sessions`` into ClickHouse.

Sibling of ``trace_writer`` and the EXACT same pattern, applied to the two
CURATED dimensions of the CH-derived-dimensions migration (DESIGN §4 / §5).
In the legacy world these reached ClickHouse via PeerDB CDC (``tracer_enduser``
/ ``trace_session`` landing tables → ``enduser_dict`` / ``trace_session_dict``);
v2 removes CDC, so the curated entity needs a CH-native feed. The one-time
history is loaded by ``ch25_backfill_curated_dimensions``; this module keeps it
fresh on the ingest hot path.

TWO ENTRY POINTS, ONE COLUMN CONTRACT.

  • ``mirror_curated_dimensions_to_clickhouse`` (LIVE ingest, P3b flip): the
    ingest paths NO LONGER hold a PG ``EndUser`` / ``TraceSession`` object — the
    hot-path ``get_or_create`` is gone (DESIGN §8: PG write removed last). So this
    takes the already-computed DETERMINISTIC id (``deterministic_id.py``, DESIGN
    §3) plus the curated fields the collector already has on the span, and keys the
    CH row by that id. NO PG object, NO PG round-trip.
  • ``end_user_to_row`` / ``trace_session_to_row`` (HISTORICAL backfill): map a PG
    model INSTANCE → CH row. ``ch25_backfill_curated_dimensions`` imports these +
    ``_*_COLUMNS`` so the historical load and the live write share ONE column
    order / coercion definition. The backfill keeps the PG id verbatim (a straight
    mirror of legacy rows); the live path re-keys to the deterministic id. Both
    emit the same ``_*_COLUMNS`` tuple — the row-shape contract is in one place.

Design (mirrors ``trace_writer`` 1:1):
  • Post-commit. Callers schedule via ``transaction.on_commit`` so CH never
    sees an entity whose PG row rolled back (matches the old "CH after commit"
    CDC semantics). ``on_commit`` runs inline when there is no open transaction.
  • Best-effort. A CH hiccup must NEVER break — or slow — PG ingestion. Every
    failure (including the row mapping) is logged and swallowed; the periodic
    backfill re-run reconciles any gap. PG remains the system of record.
  • Idempotent + versioned. Both targets are ReplacingMergeTree(version) keyed
    on the entity id; ``version`` picks the merge winner. The live path passes the
    curated fields already on the span (one row per identity in the batch) so no PG
    read is needed — there is no PG entity row to read post-flip, and the
    deterministic id makes the CH row self-keying.
  • Flag-gated. Shares ``dual_write_enabled()`` with ``trace_writer`` — same
    migration gate (the CDC chain being dropped is what turns both on).

NOTE — ``version`` is a CH ``DateTime64(6,'UTC')`` (schema 017/018), NOT the
integer-ns ``_version`` of ``traces``. So unlike ``trace_writer`` we pass a
tz-aware ``datetime`` (never ``time.time_ns()``). Live writes use ``now()`` so a
later live mirror always out-versions an earlier one AND a backfill re-run (which
versions by ``updated_at``) can never clobber a fresher live update — the exact
latest-wins invariant ``trace_writer`` documents.
"""

from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog

from tracer.services.clickhouse.v2 import get_v2_config

# Reuse trace_writer's gate verbatim — same migration, same switch. Keeping a
# single definition means the curated dual-write turns on/off with the traces one.
from tracer.services.clickhouse.v2.trace_writer import dual_write_enabled

log = structlog.get_logger("ch25.curated_writer")

# Column order for the INSERTs — must match the schema (017 / 018). The backfill
# command imports these so the column/row contract is locked in ONE place.
_END_USER_COLUMNS: tuple[str, ...] = (
    "project_id",
    "end_user_id",
    "organization_id",
    "user_id",
    "user_id_type",
    "user_id_hash",
    "metadata",
    "first_seen",
    "version",
    "is_deleted",
)
_TRACE_SESSION_COLUMNS: tuple[str, ...] = (
    "project_id",
    "trace_session_id",
    "external_session_id",
    "first_seen",
    "version",
    "is_deleted",
)


@dataclass(frozen=True)
class CuratedEndUser:
    """The curated ``end_users`` fields the LIVE ingest path has on the span
    after the P3b flip — no PG ``EndUser`` object exists anymore.

    ``end_user_id`` is the DETERMINISTIC id (``deterministic_end_user_id``); the
    rest are the SDK-sourced curated fields already parsed off the span (same
    values the dropped ``get_or_create`` would have stored). ``user_id_type`` is
    the normalized value (``get_user_id_type``), kept None on absence (Nullable
    column) — IMPORTANT: it must be the SAME normalized value fed to
    ``deterministic_end_user_id`` so the row's key matches its id.
    """

    project_id: uuid.UUID
    end_user_id: uuid.UUID
    organization_id: Any
    user_id: str
    user_id_type: str | None = None
    user_id_hash: str | None = None
    metadata: Any = None


@dataclass(frozen=True)
class CuratedSession:
    """The curated ``trace_sessions`` fields the LIVE ingest path has on the span
    after the P3b flip — no PG ``TraceSession`` object exists anymore.

    ``trace_session_id`` is the DETERMINISTIC id (``deterministic_trace_session_id``);
    ``external_session_id`` is the session name the id was computed from.
    """

    project_id: uuid.UUID
    trace_session_id: uuid.UUID
    external_session_id: str


_client = None
_client_lock = threading.Lock()


def _get_client():
    """Lazily build a cached clickhouse-connect client. Reset on error so a
    transient CH outage doesn't wedge the cached handle permanently. Mirrors
    ``trace_writer._get_client`` (kept separate so the two writers don't share
    mutable state across a reset)."""
    global _client
    if _client is not None:
        return _client
    with _client_lock:
        if _client is None:
            import clickhouse_connect

            cfg = get_v2_config()
            _client = clickhouse_connect.get_client(
                host=cfg["host"],
                port=cfg["http_port"],
                username=cfg["user"],
                password=cfg["password"] or "",
                database=cfg["database"],
                send_receive_timeout=15,
            )
    return _client


def _reset_client() -> None:
    global _client
    with _client_lock:
        try:
            if _client is not None:
                _client.close()
        except Exception:
            pass
        _client = None


def _metadata_to_text(v: Any) -> str:
    """PG ``EndUser.metadata`` is a JSONField (dict / list / str / None).

    The CH ``end_users.metadata`` column is a non-null String holding JSON, so
    coerce: None → '{}', dict/list → json.dumps, str → trust as-is. Shared with
    the backfill command so live + historical rows serialize identically.
    """
    if v is None:
        return "{}"
    if isinstance(v, str):
        return v
    return json.dumps(v, default=str, ensure_ascii=False)


def _version_value(obj, *, version_from_updated_at: bool) -> datetime:
    """Pick the ReplacingMergeTree merge winner as a tz-aware ``datetime``.

    Mirrors ``trace_writer._trace_to_row``'s flag, but the column is
    ``DateTime64(6,'UTC')`` (not integer-ns), so we return a ``datetime``:

    • Live dual-write (default): wall-clock ``now()`` UTC — a later live mirror
      always wins, and it is always >= any historical ``updated_at`` a backfill
      re-run would carry, so the backfill can never clobber a fresher live edit.
    • Backfill (``version_from_updated_at=True``): the row's own ``updated_at``
      (tz-coerced), so a re-run is an idempotent latest-wins no-op.
    """
    if version_from_updated_at:
        updated = obj.updated_at
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=UTC)
        return updated
    return datetime.now(UTC)


def end_user_to_row(eu, *, version_from_updated_at: bool = False) -> list[Any]:
    """Map an ``EndUser`` model instance to a CH ``end_users`` row (column order
    above). P3a: ``end_user_id`` = PG ``id`` (no re-key). ``first_seen`` =
    ``created_at``; NULL ``user_id_hash``/``metadata`` coerce to '' / '{}'
    (non-null String columns); ``user_id_type`` stays None (the column / dict
    attr is Nullable).

    Reads ONLY local fields + the raw FK ids ``project_id`` / ``organization_id``
    — never ``eu.project.id`` / ``eu.organization.id`` (those would fire a PG
    SELECT per entity on the hot path, the exact round-trip this migration
    removes).
    """
    return [
        str(eu.project_id),
        str(eu.id),  # end_user_id = PG id (no re-key)
        str(eu.organization_id),
        eu.user_id or "",
        eu.user_id_type,  # Nullable — keep None as-is
        eu.user_id_hash or "",  # non-null String → '' on NULL
        _metadata_to_text(eu.metadata),
        eu.created_at,  # first_seen
        _version_value(eu, version_from_updated_at=version_from_updated_at),
        1 if getattr(eu, "deleted", False) else 0,
    ]


def trace_session_to_row(s, *, version_from_updated_at: bool = False) -> list[Any]:
    """Map a ``TraceSession`` model instance to a CH ``trace_sessions`` row
    (column order above). P3a: ``trace_session_id`` = PG ``id`` (no re-key);
    ``external_session_id`` = PG ``name``; ``first_seen`` = ``created_at``.

    Reads ONLY local fields + the raw FK id ``project_id`` — never
    ``s.project.id`` (avoids a per-entity PG round-trip on the hot path).
    """
    return [
        str(s.project_id),
        str(s.id),  # trace_session_id = PG id (no re-key)
        s.name or "",  # external_session_id = PG name
        s.created_at,  # first_seen
        _version_value(s, version_from_updated_at=version_from_updated_at),
        1 if getattr(s, "deleted", False) else 0,
    ]


def _curated_end_user_to_row(eu: CuratedEndUser) -> list[Any]:
    """Map a live-ingest ``CuratedEndUser`` (deterministic id + curated fields) to
    a CH ``end_users`` row (column order ``_END_USER_COLUMNS``).

    Same column contract + coercions as ``end_user_to_row`` (the backfill mapper),
    but keyed by the DETERMINISTIC ``end_user_id`` instead of a PG ``id``.
    ``first_seen`` has no PG source post-flip → ``now()`` (the entity's first
    observed activity in this stream); ``version`` = ``now()`` so a later live
    mirror always wins and a backfill re-run (``updated_at``-versioned) can never
    clobber it — the same latest-wins invariant the backfill mapper documents.
    """
    now = datetime.now(UTC)
    return [
        str(eu.project_id),
        str(eu.end_user_id),  # DETERMINISTIC id (not a PG id)
        str(eu.organization_id),
        eu.user_id or "",
        eu.user_id_type,  # Nullable — keep None as-is (normalized value)
        eu.user_id_hash or "",  # non-null String → '' on NULL
        _metadata_to_text(eu.metadata),
        now,  # first_seen
        now,  # version
        0,  # is_deleted — a freshly-ingested entity is never soft-deleted
    ]


def _curated_session_to_row(s: CuratedSession) -> list[Any]:
    """Map a live-ingest ``CuratedSession`` (deterministic id + external id) to a
    CH ``trace_sessions`` row (column order ``_TRACE_SESSION_COLUMNS``).

    Same column contract as ``trace_session_to_row`` (the backfill mapper), keyed
    by the DETERMINISTIC ``trace_session_id``. ``first_seen`` / ``version`` =
    ``now()`` (see ``_curated_end_user_to_row``).
    """
    now = datetime.now(UTC)
    return [
        str(s.project_id),
        str(s.trace_session_id),  # DETERMINISTIC id (not a PG id)
        s.external_session_id or "",  # external_session_id = session name
        now,  # first_seen
        now,  # version
        0,  # is_deleted
    ]


def mirror_curated_dimensions_to_clickhouse(
    end_users: Iterable[CuratedEndUser] | None = None,
    sessions: Iterable[CuratedSession] | None = None,
) -> None:
    """Upsert the given live-ingest curated dimensions into CH ``end_users`` /
    ``trace_sessions`` (one batched insert each), keyed by their DETERMINISTIC id.

    P3b flip: the ingest paths no longer hold a PG ``EndUser`` / ``TraceSession``
    object (the hot-path ``get_or_create`` is gone). Callers pass ``CuratedEndUser``
    / ``CuratedSession`` carrying the deterministic id (``deterministic_id.py``) +
    the curated fields already on the span. This keys the CH row by the
    deterministic id so it lines up with the ``end_user_id`` / ``trace_session_id``
    the flip also stamps onto the span/trace.

    Best-effort: never raises and never blocks — wrap the whole body (mapping
    included) so a CH outage or a malformed row can NEVER break or slow PG
    ingestion. Call inside ``transaction.on_commit`` from the ingest creators.
    """
    if not dual_write_enabled():
        return

    eu_list = [eu for eu in (end_users or []) if eu is not None]
    s_list = [s for s in (sessions or []) if s is not None]
    if not eu_list and not s_list:
        return

    try:
        client = _get_client()
        if eu_list:
            rows = [_curated_end_user_to_row(eu) for eu in eu_list]
            client.insert("end_users", rows, column_names=list(_END_USER_COLUMNS))
        if s_list:
            rows = [_curated_session_to_row(s) for s in s_list]
            client.insert(
                "trace_sessions", rows, column_names=list(_TRACE_SESSION_COLUMNS)
            )
    except Exception as e:  # noqa: BLE001 — best-effort by design
        log.warning(
            "curated_dual_write_failed",
            err=str(e),
            n_end_users=len(eu_list),
            n_sessions=len(s_list),
        )
        _reset_client()
