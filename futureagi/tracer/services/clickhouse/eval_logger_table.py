"""Resolve the eval-logger CH table + its not-deleted predicate.

The CH25 spans cutover intentionally left the legacy peerdb CDC table
``tracer_eval_logger`` in place (read directly by the trace/voice/user
eval-config discovery queries). A v2 table ``tracer_eval_logger_v2`` is
prepared — it drops the peerdb columns (``_peerdb_is_deleted`` / ``deleted``)
in favour of the unified ``is_deleted`` ReplacingMergeTree marker.

``settings.CH25_EVAL_LOGGER_TABLE`` selects which one the read paths use, so a
peerdb-backed deployment keeps the legacy table (default) while a CH-direct
stack flips to v2 without code edits. See
``v2/schema/011_eval_logger_v2.sql`` + ``docs/CH25_MIGRATION.md``.
"""

from __future__ import annotations

from django.conf import settings


def score_source() -> str:
    """Return the CH table the annotation-filter subqueries read.

    The unified-annotation ``model_hub_score`` CH table was a PeerDB CDC mirror,
    dropped CDC-off — so the observe annotation filters (has_annotation /
    annotator / per-label value) 500 with UNKNOWN_TABLE. The app now dual-writes
    Score → ``model_hub_score_v2`` (schema 020, ReplacingMergeTree); CDC-off
    deployments flip ``CH25_SCORE_TABLE`` to it. Column names match the legacy
    filter-read contract (incl. the plain ``deleted`` column), so the existing
    filter SQL is reused verbatim — only the table name swaps. Default keeps the
    legacy table so PeerDB stacks are byte-for-byte unchanged.
    """
    return getattr(settings, "CH25_SCORE_TABLE", "model_hub_score")


def end_user_source() -> tuple[str, str, str]:
    """Return ``(table, id_column, not_deleted_predicate)`` for the curated
    EndUser dimension read by the filter builder's user_id / user_id_type
    subquery.

    CDC-off the legacy PeerDB landing table ``tracer_enduser`` (``id`` +
    ``_peerdb_is_deleted``/``deleted``) is gone; the v2 collector-written
    ``end_users`` RMT (schema 017) replaces it with ``end_user_id`` and a single
    ``is_deleted`` marker. Selected by ``CH25_END_USER_TABLE`` so a PeerDB-backed
    deployment keeps the legacy table (default) while a CH-direct stack flips to
    v2 without code edits — same template as ``eval_logger_source``.

    NOTE: the legacy predicate intentionally differs from ``eval_logger_source``
    (no ``OR deleted IS NULL``) to stay byte-identical to the enduser SQL it
    replaces.
    """
    table = getattr(settings, "CH25_END_USER_TABLE", "tracer_enduser")
    if table.endswith("end_users"):
        return table, "end_user_id", "is_deleted = 0"
    return table, "id", "_peerdb_is_deleted = 0 AND deleted = 0"


def eval_logger_source(alias: str = "") -> tuple[str, str]:
    """Return ``(table_name, not_deleted_predicate)`` for the configured table.

    ``alias`` prefixes the column references (e.g. ``"e"`` →
    ``e.is_deleted = 0``) for queries that alias the table in a JOIN.
    """
    table = getattr(settings, "CH25_EVAL_LOGGER_TABLE", "tracer_eval_logger")
    p = f"{alias}." if alias else ""
    if table.endswith("_v2"):
        # v2: single is_deleted marker, no peerdb CDC columns.
        predicate = f"{p}is_deleted = 0"
    else:
        # legacy peerdb CDC table.
        predicate = (
            f"{p}_peerdb_is_deleted = 0 AND ({p}deleted = 0 OR {p}deleted IS NULL)"
        )
    return table, predicate
