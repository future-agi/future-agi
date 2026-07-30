"""Test helpers for seeding CH tables directly (spans + model_hub_score).

Tests that exercise CH-backed endpoints used to seed PG via Django ORM and
rely on PeerDB CDC to propagate the row to CH. There's no CDC in the test
path post-CH25-cutover, so the endpoint reads return empty even when PG is
populated.

This module gives tests explicit, CH-direct seed functions: one call per
Django model instance and the same row lands in the corresponding CH table
the reader queries. No "magic" signals — tests opt in when they need CH
coverage.

Typical usage::

    from tracer.tests._ch_seed import seed_ch_span, seed_ch_score

    span = ObservationSpan.objects.create(...)
    seed_ch_span(span)                    # ← one new line
    response = auth_client.get("/some/ch-backed/endpoint/")

    score = Score.objects.create(...)
    seed_ch_score(score)                  # ← seed the CDC mirror
    response = auth_client.get("/some/score-reading/endpoint/")

Or seed many at once via ``seed_ch_spans([...])`` / ``seed_ch_scores([...])``.

The span helper goes through the same ``adapt()`` path the production
PG→CH backfill uses, so test rows have the same shape as real spans.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from tracer.services.clickhouse.v2 import get_v2_config
from tracer.services.clickhouse.v2.adapter import (
    CH_INSERT_COLUMNS,
    adapt,
    row_to_tuple,
)


def _pg_row_from_django_span(span: Any) -> dict[str, Any]:
    """Project a Django ``ObservationSpan`` instance into the dict shape
    ``adapter.adapt()`` expects. Mirrors what the PG→CH backfill reads.
    """
    project_id = getattr(span, "project_id", None)
    org_id = None
    project = getattr(span, "project", None)
    if project is not None:
        org_id = getattr(project, "organization_id", None)

    return {
        "id": str(span.id),
        "trace_id": str(getattr(span, "trace_id", "") or ""),
        "project_id": str(project_id) if project_id else None,
        "project_version_id": getattr(span, "project_version_id", None),
        "org_id": str(org_id) if org_id else None,
        "parent_span_id": getattr(span, "parent_span_id", None),
        "name": getattr(span, "name", None) or "",
        "observation_type": getattr(span, "observation_type", None) or "unknown",
        "operation_name": getattr(span, "operation_name", None),
        "status": getattr(span, "status", None),
        "status_message": getattr(span, "status_message", None),
        "start_time": getattr(span, "start_time", None),
        "end_time": getattr(span, "end_time", None),
        "latency_ms": getattr(span, "latency_ms", None),
        "model": getattr(span, "model", None),
        "provider": getattr(span, "provider", None),
        "prompt_tokens": getattr(span, "prompt_tokens", None),
        "completion_tokens": getattr(span, "completion_tokens", None),
        "total_tokens": getattr(span, "total_tokens", None),
        "cost": getattr(span, "cost", None),
        "input": getattr(span, "input", None),
        "output": getattr(span, "output", None),
        "span_attributes": getattr(span, "span_attributes", None) or {},
        "resource_attributes": getattr(span, "resource_attributes", None) or {},
        "metadata": getattr(span, "metadata", None) or {},
        "tags": getattr(span, "tags", None) or [],
        "span_events": getattr(span, "span_events", None) or [],
        "end_user_id": getattr(span, "end_user_id", None),
        # The backfill joins tracer_trace.session_id as trace_session_id.
        # ObservationSpan doesn't carry trace_session_id; resolve via trace FK.
        "trace_session_id": (
            getattr(span, "trace_session_id", None)
            or (getattr(getattr(span, "trace", None), "session_id", None))
        ),
        "prompt_version_id": getattr(span, "prompt_version_id", None),
        "prompt_label_id": getattr(span, "prompt_label_id", None),
        "custom_eval_config_id": getattr(span, "custom_eval_config_id", None),
        "semconv_source": getattr(span, "semconv_source", None),
        "model_parameters": getattr(span, "model_parameters", None) or {},
        "input_images": getattr(span, "input_images", None) or [],
        "eval_input": getattr(span, "eval_input", None) or {},
        "eval_attributes": getattr(span, "eval_attributes", None) or {},
        "eval_status": getattr(span, "eval_status", None),
        "service_name": getattr(span, "service_name", None) or "",
        "gen_ai_system": getattr(span, "gen_ai_system", None),
        "gen_ai_operation": getattr(span, "gen_ai_operation", None),
        "input_gcs_url": getattr(span, "input_gcs_url", None),
        "output_gcs_url": getattr(span, "output_gcs_url", None),
        "created_at": getattr(span, "created_at", None),
        "updated_at": getattr(span, "updated_at", None),
        "deleted": getattr(span, "deleted", False),
    }


def _get_ch_client():
    """Lazy clickhouse-connect client bound to the v2 (test or prod) cluster."""
    import clickhouse_connect

    cfg = get_v2_config()
    return clickhouse_connect.get_client(
        host=cfg["host"],
        port=cfg["http_port"],
        username=cfg["user"],
        password=cfg["password"],
        database=cfg["database"],
    )


def seed_ch_span(span_or_dict: Any, *, client: Any | None = None) -> None:
    """Insert ONE ObservationSpan into the CH ``spans`` table.

    Accepts either a Django ``ObservationSpan`` instance or a dict already
    matching the ``adapter.adapt()`` input shape. Caller-supplied ``client``
    is optional; we open a fresh one if omitted (cheap for one-off seeds).
    """
    seed_ch_spans([span_or_dict], client=client)


def seed_ch_spans(
    spans: Iterable[Any],
    *,
    client: Any | None = None,
) -> int:
    """Bulk-insert ObservationSpan rows into the CH ``spans`` table.

    Returns the number of rows inserted. Uses ``adapt()`` so the row shape
    matches the production PG→CH backfill exactly (same typed-Map split,
    same attributes-extra merge, same JSON serialisation).
    """
    rows: list[tuple] = []
    for s in spans:
        pg_row = s if isinstance(s, dict) else _pg_row_from_django_span(s)
        ch_row = adapt(pg_row)
        rows.append(row_to_tuple(ch_row))

    if not rows:
        return 0

    own_client = client is None
    if own_client:
        client = _get_ch_client()
    try:
        client.insert("spans", rows, column_names=list(CH_INSERT_COLUMNS))
    finally:
        if own_client:
            client.close()


_TRACE_SESSIONS_COLUMNS = [
    "project_id",
    "trace_session_id",
    "external_session_id",
    "first_seen",
    "version",
    "is_deleted",
]


def seed_ch_trace_sessions(
    sessions: Iterable[Any],
    *,
    client: Any | None = None,
) -> int:
    """Bulk-insert curated CH ``trace_sessions`` rows for test ``TraceSession``s.

    The post-P3c session reads (``trace_session_dict_reader.resolve_session_fields``,
    used by the session list/detail + the eval-task session dispatcher) read the
    curated CH ``trace_sessions`` RMT — populated in prod by the ingestion
    dual-write, but bypassed by PG-direct test fixtures. Without these rows a
    session resolves to "does not exist". Mirrors schema 018:
    ``trace_session_id`` == the PG ``TraceSession.id`` (P3a straight mirror),
    ``external_session_id`` == PG ``name``.
    """
    now = datetime.now(UTC)
    rows: list[tuple] = []
    for s in sessions:
        first_seen = getattr(s, "created_at", None) or now
        rows.append((str(s.project_id), str(s.id), s.name or "", first_seen, now, 0))
    if not rows:
        return 0

    own_client = client is None
    if own_client:
        client = _get_ch_client()
    try:
        client.insert("trace_sessions", rows, column_names=_TRACE_SESSIONS_COLUMNS)
    finally:
        if own_client:
            client.close()
    return len(rows)


_TRACES_COLUMNS = [
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
]


def seed_ch_trace(trace_or_dict: Any, *, client: Any | None = None) -> int:
    """Insert ONE trace into the CH ``traces`` table (schema 015) — the curated
    store the trace list endpoints + the eval engine's ``get_trace`` read."""
    return seed_ch_traces([trace_or_dict], client=client)


def seed_ch_traces(traces: Iterable[Any], *, client: Any | None = None) -> int:
    """Bulk-insert curated CH ``traces`` rows for test ``Trace``s. JSON-ish
    columns (tags/metadata/input/output/error) are stored as strings, mirroring
    the production dual-write."""
    import json as _json

    now = datetime.now(UTC)

    def _s(v, empty):
        return _json.dumps(v) if v is not None else empty

    rows: list[tuple] = []
    for t in traces:
        if isinstance(t, dict):
            rows.append(tuple(t[c] for c in _TRACES_COLUMNS))
            continue
        pv = getattr(t, "project_version_id", None)
        sid = getattr(t, "session_id", None)
        rows.append(
            (
                str(t.id),
                str(t.project_id),
                str(pv) if pv else None,
                t.name or "",
                str(sid) if sid else None,
                getattr(t, "external_id", None),
                _s(getattr(t, "tags", None), "[]"),
                _s(getattr(t, "metadata", None), "{}"),
                _s(getattr(t, "input", None), ""),
                _s(getattr(t, "output", None), ""),
                _s(getattr(t, "error", None), ""),
                getattr(t, "error_analysis_status", None) or "PENDING",
                getattr(t, "created_at", None) or now,
                now,
                0,
            )
        )
    if not rows:
        return 0

    own_client = client is None
    if own_client:
        client = _get_ch_client()
    try:
        client.insert("traces", rows, column_names=_TRACES_COLUMNS)
    finally:
        if own_client:
            client.close()
    return len(rows)


def truncate_ch_spans() -> None:
    """Wipe the CH ``spans`` table — call between tests that share fixtures.

    Cheap on a single-node test CH (sub-100ms for a few thousand rows).
    Idempotent; no-op if the table doesn't exist (e.g. before schema apply).
    """
    client = _get_ch_client()
    try:
        client.command("TRUNCATE TABLE IF EXISTS spans")
    finally:
        client.close()


# ---------------------------------------------------------------------------
# model_hub_score  (CDC mirror of PG model_hub.models.score.Score)
# ---------------------------------------------------------------------------

# Column order matches the CH DDL in tracer/services/clickhouse/schema.py
# (CDC_MODEL_HUB_SCORE). value is stored as a JSON-encoded String in CH.
_SCORE_INSERT_COLUMNS = [
    "id",
    "source_type",
    "trace_id",
    "observation_span_id",
    "trace_session_id",
    "call_execution_id",
    "dataset_row_id",
    "prototype_run_id",
    "queue_item_id",
    "project_id",
    "label_id",
    "value",
    "annotator_id",
    "score_source",
    "notes",
    "organization_id",
    "workspace_id",
    "deleted",
    "deleted_at",
    "created_at",
    "updated_at",
    "_peerdb_synced_at",
    "_peerdb_is_deleted",
    "_peerdb_version",
]


def _score_row_from_django(score: Any) -> tuple:
    """Build a CH-insert tuple from a Django ``Score`` instance.

    Resolves ``project_id`` from the source FK's project when not set
    directly on the score (the PG model stores project_id but it can be
    NULL on older rows).
    """
    import json
    from datetime import datetime

    now = datetime.now(UTC)

    # Resolve project_id — try Score.project_id first, then walk source FKs.
    project_id = getattr(score, "project_id", None)
    if project_id is None:
        for attr in ("trace", "observation_span", "trace_session", "call_execution"):
            source = getattr(score, attr, None)
            if source is not None:
                project_id = getattr(source, "project_id", None)
                if project_id is not None:
                    break

    def _uuid_or_none(val: Any) -> Any:
        return str(val) if val else None

    value_json = (
        json.dumps(score.value)
        if isinstance(score.value, dict)
        else str(score.value or "{}")
    )

    return (
        str(score.id),
        score.source_type or "",
        _uuid_or_none(score.trace_id),
        str(score.observation_span_id) if score.observation_span_id else None,
        _uuid_or_none(score.trace_session_id),
        _uuid_or_none(score.call_execution_id),
        _uuid_or_none(score.dataset_row_id),
        _uuid_or_none(score.prototype_run_id),
        _uuid_or_none(score.queue_item_id),
        _uuid_or_none(project_id),
        str(score.label_id),
        value_json,
        _uuid_or_none(score.annotator_id),
        score.score_source or "HUMAN",
        score.notes,
        str(score.organization_id),
        _uuid_or_none(score.workspace_id),
        1 if score.deleted else 0,
        score.deleted_at,
        score.created_at or now,
        score.updated_at or now,
        now,  # _peerdb_synced_at
        0,  # _peerdb_is_deleted
        1,  # _peerdb_version
    )


def seed_ch_score(score: Any, *, client: Any | None = None) -> None:
    """Insert ONE Score into the CH ``model_hub_score`` table.

    Accepts a Django ``Score`` instance. Caller-supplied ``client``
    is optional; we open a fresh one if omitted.
    """
    seed_ch_scores([score], client=client)


def seed_ch_scores(
    scores: Iterable[Any],
    *,
    client: Any | None = None,
) -> int:
    """Bulk-insert Score rows into the CH ``model_hub_score`` table.

    Returns the number of rows inserted.
    """
    rows: list[tuple] = []
    for s in scores:
        rows.append(_score_row_from_django(s))

    if not rows:
        return 0

    own_client = client is None
    if own_client:
        client = _get_ch_client()
    try:
        client.insert("model_hub_score", rows, column_names=_SCORE_INSERT_COLUMNS)
    finally:
        if own_client:
            client.close()

    return len(rows)


def truncate_ch_scores() -> None:
    """Wipe the CH ``model_hub_score`` table.

    Idempotent; no-op if the table doesn't exist.
    """
    client = _get_ch_client()
    try:
        client.command("TRUNCATE TABLE IF EXISTS model_hub_score")
    finally:
        client.close()
