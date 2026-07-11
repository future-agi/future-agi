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
        # legacy peerdb CDC table. Filter on the app `deleted` column, not
        # `_peerdb_is_deleted`: the v2 rewriter renames `_peerdb_is_deleted` →
        # `is_deleted` (which this legacy table lacks), so `deleted` is the
        # rewrite-safe soft-delete marker (mirrors the model_hub_score reads).
        predicate = f"({p}deleted = 0 OR {p}deleted IS NULL)"
    return table, predicate
