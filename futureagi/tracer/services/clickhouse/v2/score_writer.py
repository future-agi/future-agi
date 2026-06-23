"""App-level PG -> CH mirror for unified-annotation Scores (TH-5642).

Annotation Scores are written to PG ``model_hub_score`` (the unified Score
model), but the observe annotation filters read them back from a CH
``model_hub_score`` subquery (``filters.py`` ``_score_*`` builders). That CH
table was populated only by the PeerDB CDC chain, dropped by default
(``CH25_DROP_LEGACY_CDC_CHAIN``) — so CDC-off every annotation filter 500s with
UNKNOWN_TABLE. This is the steady-state replacement: a ``Score`` post-save
mirror that upserts the row into ``model_hub_score_v2`` (schema 020,
ReplacingMergeTree on ``_version``), gated by the same ``dual_write_enabled()``
flag as the trace/eval mirrors so PeerDB stacks are untouched. The read side
flips via the ``score_source()`` seam (env ``CH25_SCORE_TABLE``).

Column names match the legacy filter-read contract (incl. the plain ``deleted``
column) so the existing filter SQL is reused verbatim — only the table swaps.
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

# CDC-off home for unified-annotation Scores (schema 020). Deployments flip
# CH25_SCORE_TABLE to this same table for the READ side (score_source()).
_SCORE_V2_TABLE = "model_hub_score_v2"

_SCORE_COLUMNS = (
    "id",
    "source_type",
    "trace_id",
    "observation_span_id",
    "trace_session_id",
    "project_id",
    "label_id",
    "value",
    "annotator_id",
    "organization_id",
    "deleted",
    "deleted_at",
    "created_at",
    "updated_at",
    "_version",
)


def _json_str(value: Any) -> str:
    """Serialize the JSON ``value`` column; CH column is String, not nullable."""
    if value is None:
        return "{}"
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, default=str)
    except (TypeError, ValueError):
        return "{}"


def _uuid_or_none(value: Any) -> str | None:
    return str(value) if value else None


def _score_to_row(s) -> list[Any]:
    version = int(s.updated_at.timestamp() * 1_000_000) if s.updated_at else 0
    return [
        str(s.id),
        getattr(s, "source_type", "") or "",
        _uuid_or_none(s.trace_id),
        str(s.observation_span_id) if s.observation_span_id else None,
        _uuid_or_none(s.trace_session_id),
        _uuid_or_none(getattr(s, "project_id", None)),
        _uuid_or_none(s.label_id),
        _json_str(s.value),
        _uuid_or_none(s.annotator_id),
        _uuid_or_none(getattr(s, "organization_id", None)),
        1 if s.deleted else 0,
        s.deleted_at,
        s.created_at,
        s.updated_at,
        version,
    ]


def mirror_scores_to_clickhouse(score_ids: Iterable[Any]) -> None:
    """Upsert the current PG state of the given Score ids into CH.

    Best-effort: never raises. Re-reads PG so the mirrored row is the committed
    state (covers create + edit + soft-delete in one place). Call inside
    ``transaction.on_commit`` (wired via the Score post-save signal).
    """
    if not dual_write_enabled():
        return
    ids = [str(i) for i in score_ids if i]
    if not ids:
        return
    try:
        from model_hub.models.score import Score

        manager = getattr(Score, "all_objects", Score.objects)
        rows = [_score_to_row(s) for s in manager.filter(id__in=ids)]
        if not rows:
            return
        _get_client().insert(_SCORE_V2_TABLE, rows, column_names=list(_SCORE_COLUMNS))
    except Exception as e:  # noqa: BLE001 — best-effort by design
        log.warning("score_dual_write_failed", err=str(e), n=len(ids))
        _reset_client()


def _on_score_saved(sender, instance, **kwargs) -> None:
    """post_save receiver — mirror every Score write into CH after commit.

    Catches all Score write sites transparently (the CDC it replaces did the
    same). Gated + best-effort inside the mirror; on_commit so the mirrored row
    is the committed state.
    """
    from django.db import transaction

    score_id = instance.id
    transaction.on_commit(lambda: mirror_scores_to_clickhouse([score_id]))


def connect_score_mirror() -> None:
    """Wire the Score -> CH mirror. Called from tracer.apps.ready()."""
    from django.db.models.signals import post_save

    from model_hub.models.score import Score

    post_save.connect(
        _on_score_saved,
        sender=Score,
        dispatch_uid="model_hub_score_ch_mirror",
    )
