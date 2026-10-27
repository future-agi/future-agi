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
from django.core.exceptions import ImproperlyConfigured

DEFAULT_EVAL_LOGGER_TABLE = "tracer_eval_logger"
SUPPORTED_EVAL_LOGGER_TABLES = frozenset(
    {
        DEFAULT_EVAL_LOGGER_TABLE,
        "tracer_eval_logger_v2",
    }
)


def _resolve_eval_logger_table(table: str | None = None) -> str:
    """Resolve a supported physical table without interpolating config input."""

    resolved = (
        getattr(settings, "CH25_EVAL_LOGGER_TABLE", DEFAULT_EVAL_LOGGER_TABLE)
        if table is None
        else table
    )
    if not isinstance(resolved, str) or resolved not in SUPPORTED_EVAL_LOGGER_TABLES:
        raise ImproperlyConfigured(
            "CH25_EVAL_LOGGER_TABLE must select a supported eval-logger table."
        )
    return resolved


def eval_logger_version_column(table: str | None = None) -> str:
    """Return the physical ReplacingMergeTree version column for *table*."""

    resolved = _resolve_eval_logger_table(table)
    return "_version" if resolved.endswith("_v2") else "_peerdb_version"


def eval_logger_live_state_columns(table: str | None = None) -> tuple[str, ...]:
    """Columns required to test live state after physical-version collapse.

    Readers must project these columns through their candidate-scoped
    ``LIMIT 1 BY id`` subquery and apply the live predicate outside it.  Moving
    the predicate into the inner scan can resurrect an older live version when
    the newest row is a tombstone.
    """

    resolved = _resolve_eval_logger_table(table)
    if resolved.endswith("_v2"):
        return ("is_deleted",)
    return ("_peerdb_is_deleted", "deleted")


def eval_logger_source(
    alias: str = "",
    include_cdc_tombstone_guard: bool = False,
    *,
    table: str | None = None,
) -> tuple[str, str]:
    """Return ``(table_name, not_deleted_predicate)`` for the configured table.

    ``alias`` prefixes the column references (e.g. ``"e"`` →
    ``e.is_deleted = 0``) for queries that alias the table in a JOIN.

    ``include_cdc_tombstone_guard`` adds the legacy CDC tombstone guard
    (``_peerdb_is_deleted = 0``) alongside the app ``deleted`` soft-delete
    filter. Only rewrite-EXCLUDED callers, or rewritten callers whose alias is
    listed in the v2 rewriter's ``_EVAL_LEGACY_COLUMN_MARKERS``, may pass True:
    the v2 rewriter renames ``_peerdb_is_deleted`` → ``is_deleted`` (which the
    legacy table lacks), so other rewritten fragments must keep the
    ``deleted``-only predicate (default).
    Residual tombstone visibility is accepted there. The version-only legacy
    engine's ``FINAL`` does not drop CDC tombstones, so unrewritten eval reads
    need this guard to match the display queries. No-op on the v2 table (no CDC
    columns).
    """
    table = _resolve_eval_logger_table(table)
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
        if include_cdc_tombstone_guard:
            predicate = f"{p}_peerdb_is_deleted = 0 AND {predicate}"
    return table, predicate


# A completed, non-errored eval row. Legacy rows mirrored with an empty status
# stay completed.
EVAL_COMPLETED_PREDICATE = (
    "error = 0 AND ifNull(output_str, '') != 'ERROR' "
    "AND ifNull(status, '') NOT IN ('pending', 'running', 'skipped', 'errored')"
)


def count_mode_choice_array(prefix: str = "") -> str:
    """Array-valued choices for count tiles, without reclassifying scores.

    The percentage path keeps its raw JSON projection. Count tiles additionally
    accept scalar choice labels, but numeric/string score outputs must not gain
    synthetic choice columns just because ``output_str`` is populated.
    """

    values = f"JSONExtract({prefix}output_str_list, 'Array(String)')"
    scalar = f"{prefix}output_str"
    return (
        f"if(notEmpty({values}), {values}, "
        f"if(ifNull({scalar}, '') != '' "
        f"AND isNull({prefix}output_bool) AND isNull({prefix}output_float) "
        f"AND isNull(toFloat64OrNull({scalar})), "
        f"[ifNull({scalar}, '')], []))"
    )


def latest_completed_eval_rows(
    collapsed_sql: str,
    live_predicate: str,
    partition_by: str,
    *,
    completed_predicate: str = EVAL_COMPLETED_PREDICATE,
    newest_first: str = "created_at DESC, toString(id) DESC",
) -> str:
    """FROM-source keeping only the newest completed row per *partition_by*.

    *collapsed_sql* is a parenthesised per-``id`` version-collapse subquery
    that projects the columns the predicates and ordering read. The live
    predicate still applies outside that collapse. Non-completed rows all pass
    through, so lifecycle and error markers keep their precedence. Ties break
    on ``(created_at, id)``, as in the trace-detail rollup's
    ``select_latest_completed``; readers whose collapse renames those columns
    pass their own *completed_predicate* and *newest_first*.
    """

    return f"""(
            SELECT
                *,
                row_number() OVER (
                    PARTITION BY {partition_by}, ({completed_predicate})
                    ORDER BY {newest_first}
                ) AS completed_rank
            FROM {collapsed_sql}
            WHERE {live_predicate}
        )
        WHERE NOT ({completed_predicate}) OR completed_rank = 1"""
