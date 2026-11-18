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
from datetime import UTC, timezone
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
    "id",
    "project_id",
    "project_version_id",
    "name",
    "session_id",
    "external_id",
    "tags",
    "metadata",
    "input",
    "output",
    "error",
    "error_analysis_status",
    "created_at",
    "updated_at",
    "is_deleted",
    "_version",
)

_client = None
_client_lock = threading.Lock()


def _truthy(v: str | None) -> bool:
    return (
        str(v).strip().lower() in ("1", "true", "yes", "on") if v is not None else False
    )


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
        updated = updated.replace(tzinfo=UTC)
    version_ns = (
        int(updated.timestamp() * 1_000_000_000)
        if version_from_updated_at
        else time.time_ns()
    )
    return [
        str(t.id),
        str(t.project_id),
        _as_uuid_str(t.project_version_id),
        t.name,
        _as_uuid_str(t.session_id),
        t.external_id or None,
        json.dumps(
            t.tags if t.tags is not None else [], default=str, ensure_ascii=False
        ),
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


def _freeze_intent_trace_rows(rows):
    from datetime import datetime

    from tracer.services.derived_metrics.base_list_current_source import (
        MAX_BYTES,
        MAX_ROWS,
        SourceConflict,
        canonical_bytes,
        sha256,
    )

    if not isinstance(rows, (list, tuple)) or not 1 <= len(rows) <= MAX_ROWS:
        raise SourceConflict("bounded trace batch required")
    frozen = tuple(tuple(row) for row in rows)
    encoded, size = [], 0
    for row in frozen:
        if len(row) != len(_TRACE_COLUMNS):
            raise SourceConflict("trace column mismatch")
        values = []
        for value in row:
            if isinstance(value, datetime):
                if value.tzinfo is None:
                    raise SourceConflict("aware trace timestamp required")
                value = value.astimezone(UTC).isoformat()
            elif value is not None and type(value) not in (str, int):
                raise SourceConflict("immutable trace scalar required")
            values.append(value)
        data = canonical_bytes(values)
        size += len(data) + 1
        if size > MAX_BYTES - 2:
            raise SourceConflict("trace payload byte bound exceeded")
        encoded.append(data)
    return frozen, sha256(b"[" + b",".join(encoded) + b"]")


def trace_rows_sha256(rows) -> str:
    """Digest of the exact frozen trace batch, including versions and timestamps."""
    return _freeze_intent_trace_rows(rows)[1]


def _source_intent_lane():
    from django.conf import settings

    from tracer.services.derived_metrics.base_list_current_source import (
        SourceUnavailable,
    )

    if (
        getattr(settings, "FI_SESSIONS_SOURCE_INTENTS_ENABLED", False) is not True
        or getattr(settings, "FI_SESSIONS_SOURCE_INTENT_LANE", "")
        != "direct-local-mergetree-v1"
    ):
        raise SourceUnavailable("source delivery lane disabled or unsupported")


def inspect_trace_source_store(payload_sha256):
    """Inspect a direct local server. NOT producer/dictionary/replica enrollment.

    Deployment must pin this client's endpoint to a single server (no load
    balancer/failover). A hostname check alone cannot establish that topology.
    The default-OFF lane requires that separately reviewed routing prerequisite.
    """
    _source_intent_lane()
    from tracer.services.derived_metrics.base_list_current_source_transport import (
        lease_trace_source_client,
    )

    with lease_trace_source_client() as (client, _):
        return _inspect_trace_source_store(client, payload_sha256)


def _inspect_trace_source_store(client, payload_sha256):
    from uuid import UUID

    from tracer.services.derived_metrics.base_list_current_source import (
        SelectedStore,
        SourceUnavailable,
        Store,
        canonical_bytes,
        sha256,
    )

    server, database = client.query(
        "SELECT toString(serverUUID()), currentDatabase()"
    ).result_rows[0]
    tables = client.query(
        "SELECT engine, toString(uuid), create_table_query FROM system.tables "
        "WHERE database = currentDatabase() AND name = 'traces'"
    ).result_rows
    if len(tables) != 1 or tables[0][0] not in ("MergeTree", "ReplacingMergeTree"):
        raise SourceUnavailable(
            "only direct local synchronous trace stores are supported"
        )
    return SelectedStore(
        UUID(server),
        database,
        Store.TRACES,
        sha256(canonical_bytes(tables[0])),
        payload_sha256,
    )


def insert_trace_rows(rows, *, source_intent=None, selected_store=None):
    """Existing transport seam, optionally fenced by a committed A01 intent.

    With the rollout off the legacy mirror is unchanged. With it on, omission
    of the token fails BEFORE INSERT, even in the best-effort mirror caller.
    Caller must freeze rows and register complete old/new images first. Current
    PG mirror callers cannot supply old images and remain UNENROLLED; enabling
    this lane is not activation consent and would block those unfenced sends.
    No retry on uncertainty. No insert receipt certifies dictionary reloads.
    """
    from django.conf import settings

    from tracer.services.derived_metrics.base_list_current_source import (
        ReceiptKind,
        SelectedServerReceipt,
        SourceConflict,
        SourceUnavailable,
        acknowledge_selected_server,
        claim_dispatch,
        read_source_page,
    )

    enabled = getattr(settings, "FI_SESSIONS_SOURCE_INTENTS_ENABLED", False) is True
    if not enabled and source_intent is None and selected_store is None:
        return _get_client().insert("traces", rows, column_names=list(_TRACE_COLUMNS))
    _source_intent_lane()
    if source_intent is None or selected_store is None:
        raise SourceUnavailable("unregistered trace send blocked")
    frozen, digest = _freeze_intent_trace_rows(rows)
    if digest != selected_store.payload_sha256:
        raise SourceConflict("trace payload changed")
    # Validate bounded new physical/canonical membership against dispatched rows.
    # Old preimages still require the producer's independent completeness proof.
    from tracer.models.metric_source_intent import MetricSourceIntent

    intent = MetricSourceIntent.objects.using("default").get(id=source_intent.intent_id)
    images = []
    for ordinal in range(intent.page_count):
        images.extend(
            c["new"]
            for c in json.loads(read_source_page(source_intent, ordinal))["changes"]
            if c["new"] is not None and c["new"]["physical"]["store"] == "traces"
        )
    expected = sorted(
        (tuple(i["physical"]["key"]), i["canonical"]["session_id"], i["state"])
        for i in images
    )
    if any(r[14] not in (0, 1) for r in frozen):
        raise SourceConflict("invalid trace tombstone state")
    actual = sorted(
        (
            (str(r[1]), r[12].astimezone(UTC).strftime("%Y%m"), str(r[0])),
            str(r[4]) if r[4] else None,
            "tombstone" if r[14] == 1 else "live",
        )
        for r in frozen
    )
    if expected != actual or any(
        str(r[1]) != str(source_intent.scope.project_id) for r in frozen
    ):
        raise SourceConflict("trace rows disagree with registered membership")
    from tracer.services.derived_metrics.base_list_current_source_transport import (
        lease_trace_source_client,
    )

    with lease_trace_source_client() as (client, pool):
        if _inspect_trace_source_store(client, digest) != selected_store:
            raise SourceConflict("selected trace store changed")
        # Resolve driver schema BEFORE arming the one-shot request, never
        # accidentally treat its DESCRIBE as the source mutation.
        context = client.create_insert_context(
            "traces", column_names=list(_TRACE_COLUMNS), data=frozen
        )
        ticket = claim_dispatch(source_intent, selected_store)  # commits BEFORE INSERT
        context.settings = {
            "async_insert": 0,
            "wait_for_async_insert": 1,
            "wait_end_of_query": 1,
            "query_id": str(ticket.receipt_id),
        }
        pool.arm(str(ticket.receipt_id))
        summary = client.insert(context=context)
        # 1.1.1 exposes query_id as a METHOD. The owned pool additionally proves
        # EOF, empty success body and absence of any ambiguous transport retry.
        if summary.query_id() != str(ticket.receipt_id):
            raise SourceUnavailable("insert lacks exact terminal query acknowledgement")
        pool.disarm()
        if _inspect_trace_source_store(client, digest) != selected_store:
            raise SourceUnavailable("selected trace store changed during delivery")
        receipt = SelectedServerReceipt(
            ticket, ReceiptKind.LOCAL_SYNCHRONOUS_INSERT, str(ticket.receipt_id)
        )
        acknowledge_selected_server(source_intent, (receipt,))
        return receipt


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
        rows = [_trace_to_row(t) for t in manager.using("default").filter(id__in=ids)]
        if not rows:
            return
        insert_trace_rows(rows)
    except Exception as e:  # noqa: BLE001 — best-effort by design
        log.warning("trace_dual_write_failed", err=str(e), n=len(ids))
        _reset_client()
