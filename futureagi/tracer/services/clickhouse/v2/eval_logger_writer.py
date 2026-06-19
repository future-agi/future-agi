"""App-level PG -> CH mirror for eval verdicts (TH-5642).

Eval verdicts are written to PG ``tracer_eval_logger`` (EvalLogger.objects
.create at ~11 sites), but every display reads them back from CH via
``eval_logger_source()``. That CH table was populated only by the PeerDB CDC
chain, which is dropped by default (``CH25_DROP_LEGACY_CDC_CHAIN``) — so with no
CDC the eval columns/panels go blank. This is the steady-state replacement: an
``EvalLogger`` post-save mirror that upserts the verdict into the v2 CH table
``tracer_eval_logger_v2`` (the CDC-off read target, ReplacingMergeTree on
``_version``/``is_deleted``), gated by the same ``dual_write_enabled()`` flag as
the trace mirror so PeerDB stacks are untouched.

Mirrors the column order of ee/internal/scripts/sync_pg_to_clickhouse.py plus the
v2-only columns (trace_session_id, target_type, deleted_at, is_deleted,
_version).
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

import structlog

from tracer.services.clickhouse.v2.trace_writer import (
    _get_client,
    _reset_client,
    dual_write_enabled,
)

log = structlog.get_logger(__name__)

# The v2 table is the CDC-off home for eval verdicts (schema 011_eval_logger_v2).
# Deployments flip CH25_EVAL_LOGGER_TABLE to this same table for the READ side.
_EVAL_LOGGER_V2_TABLE = "tracer_eval_logger_v2"

_EVAL_COLUMNS = (
    "id",
    "trace_id",
    "observation_span_id",
    "trace_session_id",
    "target_type",
    "custom_eval_config_id",
    "eval_type_id",
    "output_bool",
    "output_float",
    "output_str",
    "output_str_list",
    "error",
    "error_message",
    "eval_explanation",
    "output_metadata",
    "results_tags",
    "results_explanation",
    "eval_tags",
    "eval_id",
    "eval_task_id",
    "created_at",
    "updated_at",
    "deleted_at",
    "is_deleted",
    "_version",
)


def _json_str(value: Any) -> str:
    """Serialize a JSON column value; the CH columns are String, not nullable."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, default=str)
    except (TypeError, ValueError):
        return ""


def _uuid_or_none(value: Any) -> str | None:
    return str(value) if value else None


def _eval_to_row(e) -> list[Any]:
    version = int(e.updated_at.timestamp() * 1_000_000) if e.updated_at else 0
    output_bool = None
    if e.output_bool is not None:
        output_bool = 1 if e.output_bool else 0
    return [
        str(e.id),
        _uuid_or_none(e.trace_id),
        str(e.observation_span_id) if e.observation_span_id else None,
        _uuid_or_none(e.trace_session_id),
        e.target_type or "",
        _uuid_or_none(e.custom_eval_config_id),
        e.eval_type_id,
        output_bool,
        e.output_float,
        e.output_str,
        _json_str(e.output_str_list),
        1 if e.error else 0,
        e.error_message,
        e.eval_explanation,
        _json_str(e.output_metadata),
        _json_str(e.results_tags),
        _json_str(e.results_explanation),
        _json_str(e.eval_tags),
        e.eval_id,
        e.eval_task_id,
        e.created_at,
        e.updated_at,
        e.deleted_at,
        1 if e.deleted else 0,
        version,
    ]


def mirror_eval_loggers_to_clickhouse(eval_logger_ids: Iterable[Any]) -> None:
    """Upsert the current PG state of the given EvalLogger ids into CH.

    Best-effort: never raises. Re-reads PG so the row mirrored is exactly what
    committed. Call inside ``transaction.on_commit`` from the EvalLogger write
    sites (wired via a post-save signal).
    """
    if not dual_write_enabled():
        return
    ids = [str(i) for i in eval_logger_ids if i]
    if not ids:
        return
    try:
        from tracer.models.observation_span import EvalLogger

        manager = getattr(EvalLogger, "all_objects", EvalLogger.objects)
        rows = [_eval_to_row(e) for e in manager.filter(id__in=ids)]
        if not rows:
            return
        _get_client().insert(
            _EVAL_LOGGER_V2_TABLE, rows, column_names=list(_EVAL_COLUMNS)
        )
    except Exception as e:  # noqa: BLE001 — best-effort by design
        log.warning("eval_logger_dual_write_failed", err=str(e), n=len(ids))
        _reset_client()


def _on_eval_logger_saved(sender, instance, **kwargs) -> None:
    """post_save receiver — mirror every EvalLogger write into CH after commit.

    Catches all ~11 EvalLogger.objects.create sites transparently (the CDC it
    replaces did the same). Gated + best-effort inside the mirror; on_commit so
    the mirrored row is the committed state.
    """
    from django.db import transaction

    eval_id = instance.id
    transaction.on_commit(lambda: mirror_eval_loggers_to_clickhouse([eval_id]))


def connect_eval_logger_mirror() -> None:
    """Wire the EvalLogger -> CH mirror. Called from tracer.apps.ready()."""
    from django.db.models.signals import post_save

    from tracer.models.observation_span import EvalLogger

    post_save.connect(
        _on_eval_logger_saved,
        sender=EvalLogger,
        dispatch_uid="eval_logger_ch_mirror",
    )
