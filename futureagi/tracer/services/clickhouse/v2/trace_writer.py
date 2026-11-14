"""trace_writer — app-level dual-write of PG ``tracer_trace`` rows into CH ``traces``.

In the legacy/main world the CH copy of a trace arrived via PeerDB CDC, which
fed ``trace_dict`` (the source of every span's trace_name). The v2 migration
removes the CDC chain and the fi-collector only emits *spans* — so the trace
entity would have no path to ClickHouse. This module is that path: wherever
Django writes a Trace to PG (the D-027 source-of-truth write), we also upsert
it into the CH ``traces`` ReplacingMergeTree.

Design:
  • Post-commit. Callers schedule via ``transaction.on_commit`` so CH never
    sees a trace PG rolled back (matches the old "CH receives after the PG
    transaction commits" CDC semantics). ``on_commit`` runs inline when there
    is no open transaction, so the ``error_analysis.filter().update()`` path
    is covered too.
  • Best-effort. A CH hiccup must NEVER break PG ingestion. Every failure is
    logged and swallowed; the periodic ch25 validate/doctor + a re-run of the
    backfill reconcile any gap. PG remains the system of record.
  • Idempotent + amplification-aware. ReplacingMergeTree dedups by ``id``
    (latest ``_version`` wins, ``_version = updated_at`` in ns). Callers gate
    on created/changed so a 100-span trace doesn't fire 100 identical upserts.
  • Flag-gated. Enabled whenever the CDC chain is dropped (its replacement) or
    when CH25_TRACE_DUAL_WRITE is explicitly set.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import timezone
from typing import Any, Iterable

import structlog

from tracer.services.clickhouse.v2 import get_v2_config
# Reuse the span adapter's coercers so trace JSON is serialized identically
# (and we don't reintroduce the double-encode bug fixed on the span path).
from tracer.services.clickhouse.v2.adapter import (
    _as_uuid_str,
    _to_json_text,
    _value_or_empty_json,
)

log = structlog.get_logger("ch25.trace_writer")

# Column order for the INSERT — must match the `traces` table (schema 015).
_TRACE_COLUMNS: tuple[str, ...] = (
    "id", "project_id", "project_version_id", "name", "session_id",
    "external_id", "tags", "metadata", "input", "output", "error",
    "error_analysis_status", "created_at", "updated_at", "is_deleted", "_version",
)

_client = None
_client_lock = threading.Lock()


def _truthy(v: str | None) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "on") if v is not None else False


def dual_write_enabled() -> bool:
    """Mirror traces to CH when the CDC chain has been dropped (this path
    replaces it) or when explicitly switched on. An explicit ``false`` wins so
    the behaviour can be disabled even with the CDC flag set."""
    explicit = os.getenv("CH25_TRACE_DUAL_WRITE")
    if explicit is not None:
        return _truthy(explicit)
    return _truthy(os.getenv("CH25_DROP_LEGACY_CDC_CHAIN"))


def _get_client():
    """Lazily build a cached clickhouse-connect client. Reset on error so a
    transient CH outage doesn't wedge the cached handle permanently."""
    global _client
    if _client is not None:
        return _client
    with _client_lock:
        if _client is None:
            import clickhouse_connect
            cfg = get_v2_config()
            _client = clickhouse_connect.get_client(
                host=cfg["host"], port=cfg["http_port"],
                username=cfg["user"], password=cfg["password"] or "",
                database=cfg["database"], send_receive_timeout=15,
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


def _trace_to_row(t, *, version_from_updated_at: bool = False) -> list[Any]:
    """Map a Trace model instance to a CH `traces` row (column order above).

    ``_version`` chooses the ReplacingMergeTree merge winner:

    • Live dual-write (default): wall-clock ``now()`` in ns. We CANNOT use
      ``updated_at`` here — ``_bulk_update_traces`` calls Django ``bulk_update``
      with update_fields={input,output,session} and NO ``updated_at``, so the
      auto_now field is not bumped. A create-mirror then an update-mirror would
      otherwise carry the same ``updated_at`` → identical ``_version`` → the
      input/output update silently lost on merge. ``now()`` makes the later
      live write always win.
    • Backfill (``version_from_updated_at=True``): ``updated_at`` ns, so a
      re-run is idempotent AND can never clobber a newer live update — any live
      mirror's ``now()`` is >= the historical ``updated_at`` it backfills.
    """
    updated = t.updated_at
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    version_ns = int(updated.timestamp() * 1_000_000_000) if version_from_updated_at else time.time_ns()
    return [
        str(t.id),
        str(t.project_id),
        _as_uuid_str(t.project_version_id),
        t.name,
        _as_uuid_str(t.session_id),
        t.external_id or None,
        json.dumps(t.tags if t.tags is not None else [], default=str, ensure_ascii=False),
        _to_json_text(t.metadata),
        _value_or_empty_json(t.input),
        _value_or_empty_json(t.output),
        _value_or_empty_json(t.error),
        t.error_analysis_status or "PENDING",
        t.created_at,
        t.updated_at,
        1 if getattr(t, "deleted", False) else 0,
        version_ns,
    ]


def mirror_traces_to_clickhouse(trace_ids: Iterable[Any]) -> None:
    """Upsert the current PG state of the given trace ids into CH ``traces``.

    Best-effort: never raises. Re-reads PG so the row mirrored is exactly what
    committed (covers create + name promotion + status update in one place).
    Call inside ``transaction.on_commit`` from the PG write sites.
    """
    if not dual_write_enabled():
        return
    ids = [str(i) for i in trace_ids if i]
    if not ids:
        return
    try:
        from tracer.models.trace import Trace
        # all_objects: include soft-deleted so a deletion mirrors as is_deleted=1
        # (default `objects` excludes deleted → a delete would never propagate).
        manager = getattr(Trace, "all_objects", Trace.objects)
        rows = [_trace_to_row(t) for t in manager.filter(id__in=ids)]
        if not rows:
            return
        _get_client().insert("traces", rows, column_names=list(_TRACE_COLUMNS))
    except Exception as e:  # noqa: BLE001 — best-effort by design
        log.warning("trace_dual_write_failed", err=str(e), n=len(ids))
        _reset_client()
