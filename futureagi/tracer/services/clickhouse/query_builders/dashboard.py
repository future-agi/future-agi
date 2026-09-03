"""
Dashboard Query Builder for ClickHouse.

Translates a widget ``query_config`` dict into one or more ClickHouse SQL
queries.  Unlike the other builders this class does NOT extend
:class:`BaseQueryBuilder` because it operates on multiple project IDs and
produces multiple queries (one per metric).

Supports four metric types:
- **system_metric** -- columns on the ``spans`` table (latency, tokens, cost, etc.)
- **eval_metric** -- aggregates from ``tracer_eval_logger FINAL``
- **annotation_metric** -- aggregates from ``model_hub_score FINAL``
- **custom_attribute** -- ``span_attr_num`` / ``span_attr_str`` map columns on ``spans``
"""

import logging
import re
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

from tracer.constants.dashboard import DASHBOARD_NUMERIC_ONLY_AGGREGATIONS
from tracer.services.clickhouse.eval_expressions import (
    EVAL_FALSY_OUTPUTS,
    EVAL_NUMERIC_OUTPUT_PATTERN,
    EVAL_TRUTHY_OUTPUTS,
    eval_has_structured_score,
    sql_str_set,
)
from tracer.services.clickhouse.eval_logger_table import (
    eval_logger_live_state_columns,
    eval_logger_source,
    eval_logger_version_column,
)
from tracer.services.clickhouse.query_builders.expressions import (
    annotation_numeric_value_expr,
)
from tracer.services.clickhouse.query_builders.latest_filter_predicates import (
    compile_span_attribute_row_predicate,
)
from tracer.services.clickhouse.trace_project_scope import (
    latest_live_project_traces_sql,
    latest_live_trace_projects_sql,
)
from tracer.services.clickhouse.v2.id_remap_sql import (
    NIL_UUID,
    bounded_survivor_map_subquery,
    resolved_id_expr,
)

logger = logging.getLogger(__name__)

DASHBOARD_QUERY_METADATA_FIELDS = (
    "query_complete",
    "query_status",
    "query_sampled",
    "query_error_code",
    "query_sampling_strategy",
    "query_sampling_interval_seconds",
    "query_sample_limit",
    "query_sample_per_bucket",
)

# Allowed characters for ClickHouse map keys: alphanumeric, dots, underscores, hyphens
_SAFE_ATTR_KEY_RE = re.compile(r"^[a-zA-Z0-9._\-]+$")

# ClickHouse excludes MATERIALIZED columns from ``alias.*`` unless the
# session-level ``asterisk_include_materialized_columns`` setting is enabled.
# Eval queries consume four materialized fields after the physical-latest
# subquery, so keep the projection explicit and independent of session
# settings.  Limit this to columns used by the dashboard query rather than
# coupling the read to every physical column in the CDC table.
_USAGE_EVAL_LATEST_COLUMNS = (
    "id",
    "organization_id",
    "workspace_id",
    "status",
    "eval_score",
    "eval_output_str",
    "eval_trace_id",
    "eval_dataset_id",
    "source",
    "source_id",
    "deleted",
    "created_at",
    "_peerdb_is_deleted",
    "_peerdb_version",
)


def _usage_eval_latest_projection(alias: str) -> str:
    return ",\n                        ".join(
        f"{alias}.{column}" for column in _USAGE_EVAL_LATEST_COLUMNS
    )


def _sanitize_attr_key(key: str) -> str:
    """Validate an attribute key is safe for use in ClickHouse map access expressions."""
    if not key or not _SAFE_ATTR_KEY_RE.match(key):
        raise ValueError(f"Invalid attribute key: {key!r}")
    return key


def _snap_to_hour(dt: datetime) -> datetime:
    """Truncate a datetime to the hour (ClickHouse ``toStartOfHour``)."""
    return dt.replace(minute=0, second=0, microsecond=0)


# ---------------------------------------------------------------------------
# Metric resolution tables
# ---------------------------------------------------------------------------

SYSTEM_METRICS: dict[str, tuple[str, str]] = {
    "project": ("spans", "project_id"),
    "latency": ("spans", "latency_ms"),
    "error_rate": ("spans", "CASE WHEN status='ERROR' THEN 1.0 ELSE 0.0 END"),
    "tokens": ("spans", "total_tokens"),
    "input_tokens": ("spans", "prompt_tokens"),
    "output_tokens": ("spans", "completion_tokens"),
    "time_to_first_token": (
        "spans",
        "span_attr_num['gen_ai.server.time_to_first_token']",
    ),
    "cost": ("spans", "cost"),
    "session_count": (
        "spans",
        "nullIf(trace_session_id, toUUID('00000000-0000-0000-0000-000000000000'))",
    ),
    "user_count": (
        "spans",
        # dictGetOrDefault cannot take NULL keys; keep both branches as String.
        "if(end_user_id IS NULL "
        "OR end_user_id = toUUID('00000000-0000-0000-0000-000000000000'), "
        "NULL, "
        "dictGetOrDefault("
        "'end_users_dict', 'user_id', "
        "assumeNotNull(end_user_id), "
        "toString(assumeNotNull(end_user_id))))",
    ),
    "trace_count": ("spans", "trace_id"),
    "span_count": ("spans", "id"),
    # String dimensions (for breakdown/filter)
    "model": ("spans", "model"),
    "status": ("spans", "status"),
    "service_name": ("spans", "service_name"),
    "span_kind": ("spans", "observation_type"),
    "provider": ("spans", "provider"),
    "session": ("spans", "trace_session_id"),
    "user": ("spans", "end_user_id"),
    "user_id_type": ("spans", "end_user_id"),  # resolved via dict in column map
    # Prompt dimensions
    "prompt_name": ("spans", "prompt_version_id"),
    "prompt_version": ("spans", "prompt_version_id"),
    "prompt_label": ("spans", "prompt_label_id"),
    # Tags
    "tag": ("spans", "tags"),
}

# These are cataloged SYSTEM_METRIC *filters*, but they are not physical
# columns on ``spans`` and therefore must not be added to ``SYSTEM_METRICS``.
# Treating them as ordinary system metrics either dropped the predicate on
# legacy widgets or rejected the current registry-bound payload.  They are
# trace relations whose boolean value is compiled below as exact membership or
# anti-membership over the authoritative eval/annotation stores.
PRESENCE_SYSTEM_METRIC_FILTERS = frozenset({"has_eval", "has_annotation"})

METRIC_UNITS: dict[str, str] = {
    "latency": "ms",
    "error_rate": "%",
    "tokens": "tokens",
    "input_tokens": "tokens",
    "output_tokens": "tokens",
    "time_to_first_token": "ms",
    "cost": "$",
    "session_count": "",
    "user_count": "",
    "trace_count": "",
    "span_count": "",
    "model": "",
    "status": "",
    "service_name": "",
    "span_kind": "",
    "provider": "",
    "session": "",
    "user": "",
    "user_id_type": "",
    "prompt_name": "",
    "prompt_version": "",
    "prompt_label": "",
    "tag": "",
}

# Metrics whose column expression emits a 0/1 indicator per row. The
# averaging aggregations get rescaled to a percentage at query time via
# ``rescale_rate_to_percent`` so the result matches the ``%`` unit.
_RATE_INDICATOR_METRICS = frozenset({"error_rate"})

# Covered by dashboard_attr_rollup. Adding one: extend the MV's ARRAY JOIN list too.
_ROLLUP_COVERED_ATTRS = frozenset({"final_status", "country"})

# Rollup is hour-resolution; sub-hour granularities keep the spans scan.
_ROLLUP_GRANULARITIES = frozenset({"hour", "day", "week", "month", "year"})

# Metrics that are non-numeric identifiers — force count_distinct aggregation
_COUNT_DISTINCT_METRICS = frozenset(
    {
        "project",
        "session",
        "user",
        "user_id_type",
        "session_count",
        "user_count",
        "trace_count",
        "span_count",
        "model",
        "status",
        "service_name",
        "span_kind",
        "provider",
        "prompt_name",
        "prompt_version",
        "prompt_label",
        "tag",
    }
)

# Aggregations that produce an "average-like" result (mean, median, any
# percentile). Rate metrics that store a 0/1 indicator per row need their
# averaging result multiplied by 100 so the value matches the declared
# ``%`` unit. sum/count/min/max are intentionally excluded — for a 0/1
# indicator they keep their natural meaning (count of matching rows for
# sum/count; bounded 0/1 for min/max).
AVERAGING_AGGREGATIONS = frozenset(
    {"avg", "median", "p25", "p50", "p75", "p90", "p95", "p99"}
)


def _eval_source_bucket_expr(exclude: str) -> str:
    """Map eval source values to fallback labels for project/dataset breakdowns."""
    buckets: list[tuple[str, str]] = [
        ("tracer", "(trace)"),
        ("feedback", "(feedback)"),
        ("tracer_composite", "(composite)"),
        ("dataset_evaluation", "(dataset)"),
        ("experiment", "(experiment)"),
        ("prompt_template", "(prompt)"),
        ("eval_playground", "(playground)"),
        ("eval_playground_test", "(playground)"),
        ("standalone_v2", "(sdk)"),
        ("simulate", "(simulation)"),
        ("simulate_tool_evaluation", "(simulation)"),
        ("voice_call", "(simulation)"),
        ("text_call", "(simulation)"),
        ("fix_your_agent", "(fix-your-agent)"),
        ("trace_error_analysis", "(error-analysis)"),
        ("error_localizer", "(error-analysis)"),
        ("run_prompt_improve", "(prompt-improve)"),
        ("composite_eval", "(composite)"),
        ("composite_eval_adhoc", "(composite)"),
        ("composite_eval_dataset", "(composite)"),
    ]
    excluded_self = {
        "project": {"tracer"},
        "dataset": {"dataset_evaluation"},
    }.get(exclude, set())
    parts = ["multiIf("]
    for source, label in buckets:
        if source in excluded_self:
            continue
        parts.append(f"e.source = '{source}', '{label}', ")
    parts.append("e.source = '', '(unknown)', ")
    parts.append(f"'(no {exclude})')")
    return "".join(parts)


def eval_pass_expr(score_col: str, output_str_col: str) -> str:
    """SQL predicate: this eval row reads as a pass, by score or by output text."""
    return (
        f"({score_col} >= 1.0 OR lower({output_str_col}) IN "
        f"{sql_str_set(EVAL_TRUTHY_OUTPUTS)})"
    )


def eval_pass_fail_label_expr(score_col: str, output_str_col: str) -> str:
    """Return the canonical public PASS_FAIL label for an eval row."""
    return f"if({eval_pass_expr(score_col, output_str_col)}, 'Passed', 'Failed')"


def eval_fail_expr(score_col: str, output_str_col: str) -> str:
    """SQL predicate: this eval row reads as a fail."""
    return (
        f"({score_col} < 1.0 AND lower({output_str_col}) NOT IN "
        f"{sql_str_set(EVAL_TRUTHY_OUTPUTS)})"
    )


def rescale_rate_to_percent(agg_expr: str, aggregation: str) -> str:
    """Wrap *agg_expr* in ``(... ) * 100`` for averaging aggregations.

    Used by metrics whose column expression emits a 0/1 indicator per
    row (``error_rate``, ``cell_error_rate``, ``success_rate``,
    ``failure_rate``) so widgets that render them with a ``%`` suffix
    show ``42%`` rather than ``0.42%``. Non-averaging aggregations are
    returned unchanged so e.g. ``sum(failure_rate)`` still reports a
    failure count.
    """
    if aggregation in AVERAGING_AGGREGATIONS:
        return f"({agg_expr}) * 100"
    return agg_expr


AGGREGATIONS: dict[str, str] = {
    "avg": "avg({col})",
    "median": "quantileExact(0.5)({col})",
    "max": "max({col})",
    "min": "min({col})",
    "p25": "quantileExact(0.25)({col})",
    "p50": "quantileExact(0.5)({col})",
    "p75": "quantileExact(0.75)({col})",
    "p90": "quantileExact(0.9)({col})",
    "p95": "quantileExact(0.95)({col})",
    "p99": "quantileExact(0.99)({col})",
    "count": "count()",
    "count_distinct": "uniqExact({col})",
    "sum": "sum({col})",
}


# Aggregations that require a numeric operand. ClickHouse raises "Illegal type
# String of argument for aggregate function ..." when these are applied to a
# text column (e.g. a string custom attribute). ``count`` / ``count_distinct``
# work on any type, so they are NOT listed here.
class InvalidMetricCombinationError(ValueError):
    """A metric's aggregation cannot be applied to its value type.

    e.g. averaging a text custom attribute. The message is user-facing — callers
    surface it per-widget so one nonsensical metric does not fail the whole
    dashboard query.
    """


FILTER_OPERATORS: dict[str, str] = {
    "less_than": "< %({prefix}{idx}_val)s",
    "greater_than": "> %({prefix}{idx}_val)s",
    "equal_to": "= %({prefix}{idx}_val)s",
    "not_equal_to": "!= %({prefix}{idx}_val)s",
    "greater_than_or_equal": ">= %({prefix}{idx}_val)s",
    "less_than_or_equal": "<= %({prefix}{idx}_val)s",
    "contains": "IN %({prefix}{idx}_val)s",
    "not_contains": "NOT IN %({prefix}{idx}_val)s",
    "str_contains": "LIKE %({prefix}{idx}_val)s",
    "str_not_contains": "NOT LIKE %({prefix}{idx}_val)s",
    "is_set": "!= ''",
    "is_not_set": "= ''",
    "is_numeric": "!= 0",
    "is_not_numeric": "= 0",
}

PRESET_RANGES: dict[str, timedelta | None] = {
    "30m": timedelta(minutes=30),
    "6h": timedelta(hours=6),
    "today": None,
    "yesterday": None,
    "7D": timedelta(days=7),
    "30D": timedelta(days=30),
    "3M": timedelta(days=90),
    "6M": timedelta(days=180),
    "12M": timedelta(days=365),
}

GRANULARITY_TO_CH: dict[str, str] = {
    "minute": "toStartOfMinute",
    "hour": "toStartOfHour",
    "day": "toStartOfDay",
    "week": "toMonday",
    "month": "toStartOfMonth",
    "year": "toStartOfYear",
}


def _prefix_spans_columns(clause: str) -> str:
    """Add 's.' prefix to spans table columns in a WHERE clause for JOINed queries.
    Only prefixes known column names outside SQL string literals, and avoids
    parameter references like ``%(project_ids)s``. Customer attribute keys
    and filter values are data: a key named ``start_time`` must never become
    ``s.start_time`` inside quotes.
    """

    # Odd pieces are complete SQL single-quoted literals (including doubled
    # quote escapes); rewrite only the even, executable SQL pieces.
    pieces = re.split(r"('(?:''|[^'])*')", clause)
    for piece_index in range(0, len(pieces), 2):
        sql = pieces[piece_index]
        for col in (
            "project_id",
            "trace_id",
            "_peerdb_is_deleted",
            "start_time",
            "created_at",
            "parent_span_id",
            "end_user_id",
            "trace_session_id",
            "user_id",
            "user_id_type",
        ):
            # Match bare column names not already qualified or inside a
            # ``%(...)s`` parameter placeholder.
            sql = re.sub(
                rf"(?<!\.)(?<!%\()(?<!\w){col}(?!\w)(?!s\))",
                f"s.{col}",
                sql,
            )
        pieces[piece_index] = sql
    return "".join(pieces)


_ID_RESOLVED_NAMES = frozenset(
    {
        "user_count",
        "session_count",
        "user",
        "session",
        "user_id_type",
    }
)

_USER_DIMENSION_NAMES = frozenset({"user_count", "user", "user_id_type"})
_SESSION_DIMENSION_NAMES = frozenset({"session_count", "session"})


# ClickHouse omits materialized columns from sp.*. The current dashboard
# dimensions only use stored columns, so no re-projection is needed.
_MATERIALIZED_DASHBOARD_COLS: tuple[str, ...] = ()


def _touched_survivor_map_subquery(*, remap_table: str, candidate_ids_sql: str) -> str:
    """Build a survivor map only for groups touched by request-scoped ids."""

    return f"""
        SELECT
            any_id,
            argMin(survivor_id, toString(survivor_id)) AS survivor_id
        FROM (
            SELECT
                arrayJoin(
                    arrayDistinct(arrayConcat(groupArray(old_id), [new_id]))
                ) AS any_id,
                argMin(old_id, toString(old_id)) AS survivor_id
            FROM {remap_table} FINAL
            WHERE new_id IN (
                SELECT DISTINCT new_id
                FROM {remap_table} FINAL
                WHERE old_id IN ({candidate_ids_sql})
                   OR new_id IN ({candidate_ids_sql})
            )
            GROUP BY new_id
        )
        GROUP BY any_id
    """


def _resolved_spans_source(
    alias: str | None = None,
    *,
    latest_state: bool = False,
    include_end_user_dimension: bool = False,
    physical_end_user_filter: str = "",
    physical_trace_session_filter: str = "",
    resolve_end_user_id: bool = True,
    resolve_trace_session_id: bool = True,
) -> str:
    """Return a spans source with user/session ids resolved through id-remap.

    The v2 dashboard may additionally request the curated end-user label/type.
    Resolve those from the project-scoped ``end_users`` table rather than the
    optional ``end_users_dict``: ``FINAL`` selects the newest entity version,
    the tombstone predicate keeps only live rows, and the same survivor map used
    for spans collapses pre/post-cutover ids without fan-out.
    """
    out_alias = alias or "spans"
    remap_ctes: list[str] = []
    eu_join = ""
    resolved_eu = "sp.end_user_id"
    if resolve_end_user_id:
        candidate_eu_sql = (
            "SELECT DISTINCT end_user_id FROM dashboard_candidate_end_user_ids"
        )
        eu_map = _touched_survivor_map_subquery(
            remap_table="end_user_id_remap",
            candidate_ids_sql=candidate_eu_sql,
        )
        remap_ctes.extend(
            (
                "dashboard_candidate_end_user_ids AS ("
                "SELECT DISTINCT end_user_id FROM spans "
                "PREWHERE project_id IN %(project_ids)s "
                "AND start_time >= %(start_date)s "
                "AND start_time < %(end_date)s "
                "WHERE isNotNull(end_user_id) "
                f"AND end_user_id != toUUID('{NIL_UUID}')"
                ")",
                f"eu_survivor_map AS ({eu_map})",
            )
        )
        eu_join = (
            "LEFT JOIN eu_survivor_map AS eu_remap ON sp.end_user_id = eu_remap.any_id"
        )
        resolved_eu = resolved_id_expr("sp.end_user_id", "eu_remap")

    ts_join = ""
    resolved_ts = "sp.trace_session_id"
    if resolve_trace_session_id:
        candidate_ts_sql = (
            "SELECT DISTINCT trace_session_id "
            "FROM dashboard_candidate_trace_session_ids"
        )
        ts_map = _touched_survivor_map_subquery(
            remap_table="trace_session_id_remap",
            candidate_ids_sql=candidate_ts_sql,
        )
        remap_ctes.extend(
            (
                "dashboard_candidate_trace_session_ids AS ("
                "SELECT DISTINCT trace_session_id FROM spans "
                "PREWHERE project_id IN %(project_ids)s "
                "AND start_time >= %(start_date)s "
                "AND start_time < %(end_date)s "
                "WHERE isNotNull(trace_session_id) "
                f"AND trace_session_id != toUUID('{NIL_UUID}')"
                ")",
                f"ts_survivor_map AS ({ts_map})",
            )
        )
        ts_join = (
            "LEFT JOIN ts_survivor_map AS ts_remap "
            "ON sp.trace_session_id = ts_remap.any_id"
        )
        resolved_ts = resolved_id_expr("sp.trace_session_id", "ts_remap")

    excluded_columns = ["project_id"]
    projected_columns = [f"sp.{c} AS {c}" for c in _MATERIALIZED_DASHBOARD_COLS]
    if resolve_end_user_id:
        excluded_columns.append("end_user_id")
        projected_columns.append(f"{resolved_eu} AS end_user_id")
    if resolve_trace_session_id:
        excluded_columns.append("trace_session_id")
        projected_columns.append(f"{resolved_ts} AS trace_session_id")

    dimension_join = ""
    if include_end_user_dimension:
        resolved_curated_eu = resolved_id_expr("eu.end_user_id", "eu_dimension_remap")
        exact_or_latest = f"tuple(eu.end_user_id = {resolved_curated_eu}, eu.version)"
        dimension_join = (
            "LEFT JOIN ("
            "SELECT "
            "eu.project_id AS project_id, "
            f"{resolved_curated_eu} AS resolved_end_user_id, "
            f"argMax(eu.user_id, {exact_or_latest}) AS user_id, "
            f"argMax(tuple(eu.user_id_type), {exact_or_latest}).1 AS user_id_type "
            "FROM end_users AS eu FINAL "
            "LEFT JOIN eu_survivor_map AS eu_dimension_remap "
            "ON eu.end_user_id = eu_dimension_remap.any_id "
            "WHERE eu.project_id IN %(project_ids)s "
            "AND eu.is_deleted = 0 "
            "GROUP BY project_id, resolved_end_user_id"
            ") AS eu_dimension "
            "ON sp.project_id = eu_dimension.project_id "
            f"AND {resolved_eu} = eu_dimension.resolved_end_user_id"
        )
        projected_columns.extend(
            (
                "ifNull(eu_dimension.user_id, '') AS user_id",
                "ifNull(eu_dimension.user_id_type, '') AS user_id_type",
            )
        )

    # A positive curated-user filter can be resolved to a finite set of raw
    # span end_user_id values before the wide span row is enriched. Keeping
    # this predicate inside the derived source lets ClickHouse use the
    # existing project/time primary key and end_user_id bloom/projection. The
    # outer predicate is deliberately retained as a semantic guard.
    inner_scope = ""
    physical_filters = [
        predicate
        for predicate in (physical_end_user_filter, physical_trace_session_filter)
        if predicate
    ]
    if physical_filters:
        inner_scope = (
            " PREWHERE sp.project_id IN %(project_ids)s "
            "AND sp.start_time >= %(start_date)s "
            "AND sp.start_time < %(end_date)s "
            "WHERE sp._peerdb_is_deleted = 0"
            + "".join(f" AND ({predicate})" for predicate in physical_filters)
        )

    additional_projection = (
        f", {', '.join(projected_columns)}" if projected_columns else ""
    )
    spans_source = "spans AS sp FINAL" if latest_state else "spans AS sp"
    with_clause = f"WITH {', '.join(remap_ctes)} " if remap_ctes else ""
    return (
        f"({with_clause}SELECT sp.project_id AS project_id, "
        f"sp.* EXCEPT ({', '.join(excluded_columns)})"
        f"{additional_projection} "
        f"FROM {spans_source} {eu_join} {ts_join} {dimension_join}"
        f"{inner_scope}) AS {out_alias}"
    )


class DashboardQueryBuilder:
    """Translates a widget query_config into ClickHouse SQL.

    Does NOT extend BaseQueryBuilder because it operates on multiple
    project_ids and builds multiple queries (one per metric).
    """

    # dashboard_attr_rollup lives only in the v2 schema; the v2 subclass flips
    # this True. Base/v1 never routes to the rollup (fail-closed: missing table).
    _attr_rollup_available: bool = False

    # The legacy dashboard can still run against the CDC schema, where curated
    # user labels are dictionary-backed. The v2 subclass flips this on because
    # the direct-write ``end_users`` table is authoritative there.
    _direct_end_users_available: bool = False

    # The v1 dashboard retains the deployed trace dictionary. The direct-write
    # v2 subclass resolves annotation trace ownership from the authoritative
    # ``traces`` table so a locked read-only role needs no dictionary grants.
    _direct_trace_project_scope_available: bool = False

    # The legacy spans table is partitioned by created_at, so event-time
    # dashboard reads need a redundant created_at lower bound to prune old
    # partitions. The CH25 subclass disables this: its spans table is
    # partitioned by start_time, and retaining the legacy predicate prevents
    # eligible queries from reading the root-span projection.
    _spans_partitioned_by_created_at: bool = True

    # Direct-write CH25 spans are a ReplacingMergeTree. Callers may opt into a
    # latest-state source where correctness requires version collapse; the
    # latency-sensitive dashboard path keeps this off and reports that raw
    # physical-window provenance explicitly.
    _latest_state_spans_required: bool = False

    _DIRECT_USER_METRIC_EXPRESSIONS = {
        # Preserve the dictionary path's missing-label fallback: a live span
        # with an unresolved curated row still counts by its stable user UUID.
        "user_count": (
            "if(end_user_id IS NULL "
            "OR end_user_id = toUUID('00000000-0000-0000-0000-000000000000'), "
            "NULL, if(user_id = '', toString(assumeNotNull(end_user_id)), user_id))"
        ),
        "user": "if(user_id = '', toString(end_user_id), user_id)",
        "user_id_type": "user_id_type",
    }

    _DIRECT_USER_BREAKDOWN_EXPRESSIONS = {
        "user": "if(user_id = '', toString(end_user_id), user_id)",
        "user_id_type": "user_id_type",
    }

    def __init__(self, query_config: dict) -> None:
        self.config = query_config
        self.project_ids = query_config.get("project_ids", [])
        self.organization_id = query_config.get("organization_id", "")
        self.workspace_id = query_config.get("workspace_id", "")
        self.granularity = query_config.get("granularity", "day")
        self.metrics = query_config.get("metrics", [])
        self.global_filters = query_config.get("filters", [])
        self.breakdowns = query_config.get("breakdowns", [])
        raw_annotation_labels = query_config.get("annotation_label_ids_by_project")
        self.annotation_label_ids_by_project = (
            {
                str(project_id): tuple(
                    dict.fromkeys(str(label_id) for label_id in label_ids)
                )
                for project_id, label_ids in raw_annotation_labels.items()
            }
            if isinstance(raw_annotation_labels, dict)
            else None
        )

    def _resolve_eval_template_identity(
        self, payload: dict, fallback_identifier: str
    ) -> str:
        """Resolve a registry eval definition to the usage-table template ID.

        The public catalog distinguishes configured evals from templates. A
        configured eval UUID is not a template UUID, even though both share
        the same native dashboard metric family.
        """

        from tracer.utils.eval_helpers import resolve_eval_template_id

        property_id = str(payload.get("property_id") or "")
        if not property_id:
            return resolve_eval_template_id(
                fallback_identifier, organization_id=self.organization_id
            )

        from tracer.utils.property_registry import parse_property_registry_id

        try:
            decoded = parse_property_registry_id(property_id)
        except ValueError as exc:
            raise InvalidMetricCombinationError(
                "Invalid eval property identity."
            ) from exc

        definition_id = decoded["metric_name"]
        if decoded["property_kind"] == "eval_config":
            from tracer.models.custom_eval_config import CustomEvalConfig

            configs = CustomEvalConfig.objects.filter(
                id=definition_id,
                deleted=False,
                eval_template__deleted=False,
            )
            if self.organization_id:
                configs = configs.filter(project__organization_id=self.organization_id)
            if self.workspace_id:
                configs = configs.filter(project__workspace_id=self.workspace_id)
            if self.project_ids:
                configs = configs.filter(project_id__in=self.project_ids)
            template_id = configs.values_list("eval_template_id", flat=True).first()
        elif decoded["property_kind"] == "eval_template":
            from django.db.models import Q

            from model_hub.models.choices import OwnerChoices
            from model_hub.models.evals_metric import EvalTemplate

            templates = EvalTemplate.no_workspace_objects.filter(
                id=definition_id,
                deleted=False,
            )
            if self.organization_id:
                # Dashboard discovery, the eval list, and configured project
                # evals all expose global system templates. Preserve tenant
                # isolation for customer-owned templates while accepting that
                # shared system identity at query time as well.
                templates = templates.filter(
                    Q(organization_id=self.organization_id)
                    | Q(
                        organization_id__isnull=True,
                        owner=OwnerChoices.SYSTEM.value,
                    )
                )
            template_id = templates.values_list("id", flat=True).first()
        elif decoded["property_kind"] == "eval":
            # Read-only compatibility identities predate the config/template
            # split. Preserve their legacy resolution without publishing them
            # from new discovery responses.
            template_id = resolve_eval_template_id(
                definition_id, organization_id=self.organization_id
            )
        else:
            template_id = None

        if not template_id:
            raise InvalidMetricCombinationError(
                "The selected evaluation is not available in this workspace."
            )
        return str(template_id)

    # ------------------------------------------------------------------
    # Time range
    # ------------------------------------------------------------------

    def parse_time_range(self) -> tuple[datetime, datetime]:
        """Parse time range from preset or custom start/end."""
        tr = self.config.get("time_range") or self.config.get("timeRange") or {}
        preset = tr.get("preset")
        custom_start = tr.get("custom_start")
        custom_end = tr.get("custom_end")

        now = datetime.now(UTC)

        if custom_start and custom_end:
            return _parse_dt(custom_start), _parse_dt(custom_end)

        if preset == "today":
            return now.replace(hour=0, minute=0, second=0, microsecond=0), now
        if preset == "yesterday":
            yesterday = now - timedelta(days=1)
            return (
                yesterday.replace(hour=0, minute=0, second=0, microsecond=0),
                yesterday.replace(hour=23, minute=59, second=59, microsecond=999999),
            )

        delta = PRESET_RANGES.get(preset)
        if delta:
            return now - delta, now

        # Default: last 30 days
        return now - timedelta(days=30), now

    # ------------------------------------------------------------------
    # Single-metric query
    # ------------------------------------------------------------------

    def build_metric_query(self, metric: dict) -> tuple[str, dict]:
        """Build ClickHouse SQL for a single metric.

        Returns:
            (sql_string, params_dict)
        """
        metric_type = metric.get("type", "system_metric")
        metric_name = metric.get("id") or metric.get("name", "")
        aggregation = metric.get("aggregation", "avg")
        per_metric_filters = metric.get("filters", [])

        start_date, end_date = self.parse_time_range()
        bucket_fn = GRANULARITY_TO_CH.get(self.granularity, "toStartOfDay")

        params: dict[str, Any] = {
            "project_ids": self.project_ids,
            "start_date": start_date,
            "end_date": end_date,
        }

        if metric_type == "system_metric":
            # Normalize to lowercase for case-insensitive lookup
            metric_name_lower = metric_name.lower() if metric_name else metric_name
            if metric_name_lower in SYSTEM_METRICS:
                metric_name = metric_name_lower
            else:
                self._reject_unknown_cataloged_system_dimension(
                    metric,
                    metric_name,
                    role="metric",
                )
            return self._build_system_metric_query(
                metric_name, aggregation, bucket_fn, per_metric_filters, params
            )
        elif metric_type == "eval_metric":
            return self._build_eval_metric_query(
                metric, aggregation, bucket_fn, per_metric_filters, params
            )
        elif metric_type == "annotation_metric":
            return self._build_annotation_metric_query(
                metric, aggregation, bucket_fn, per_metric_filters, params
            )
        elif metric_type == "custom_attribute":
            return self._build_custom_attr_query(
                metric, aggregation, bucket_fn, per_metric_filters, params
            )
        else:
            raise ValueError(f"Unknown metric type: {metric_type}")

    @staticmethod
    def _reject_unknown_cataloged_system_dimension(
        payload: dict,
        metric_name: str,
        *,
        role: str,
    ) -> None:
        """Fail closed for an unknown registry-backed system definition.

        Historical widgets without ``property_id`` retain their compatibility
        behavior: an unknown metric may be an old custom attribute and an
        unknown dimension may be ignored. A current Property Registry identity
        is an explicit definition binding, so silently dropping or
        reinterpreting it would make the displayed query disagree with the
        executed query.
        """

        if payload.get("property_id"):
            raise InvalidMetricCombinationError(
                f"Unsupported cataloged system {role}: {metric_name}"
            )

    @staticmethod
    def _presence_filter_name(payload: dict) -> str:
        if (payload.get("metric_type") or payload.get("type")) != "system_metric":
            return ""
        return str(
            payload.get("metric_name") or payload.get("name") or payload.get("id") or ""
        ).lower()

    @classmethod
    def _is_presence_filter(cls, payload: dict) -> bool:
        return cls._presence_filter_name(payload) in PRESENCE_SYSTEM_METRIC_FILTERS

    @staticmethod
    def _presence_filter_value(payload: dict, metric_name: str) -> bool:
        operation = str(payload.get("operator") or "")
        if operation not in {"equal_to", "equals"}:
            raise InvalidMetricCombinationError(
                f"{metric_name} supports only the equals operation"
            )
        value = payload.get("value")
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
            return value.strip().lower() == "true"
        raise InvalidMetricCombinationError(f"{metric_name} requires a boolean value")

    def _eval_presence_relation(self) -> str:
        """Return exact project+trace identities with a latest-live eval row."""

        table, _ = eval_logger_source()
        version_column = eval_logger_version_column(table)
        live_columns = eval_logger_live_state_columns(table)
        _, live_predicate = eval_logger_source(
            "latest_eval",
            include_cdc_tombstone_guard=True,
            table=table,
        )
        projection = ", ".join(f"eval_scan.{column}" for column in live_columns)
        latest_evals = f"""
            SELECT
                eval_scan.id,
                eval_scan.trace_id,
                {projection}
            FROM {table} AS eval_scan
            WHERE eval_scan.trace_id IS NOT NULL
              AND eval_scan.trace_id !=
                  toUUID('00000000-0000-0000-0000-000000000000')
            ORDER BY eval_scan.{version_column} DESC
            LIMIT 1 BY eval_scan.id
        """

        if self._direct_trace_project_scope_available:
            scoped_traces = latest_live_project_traces_sql()
            return f"""
                WITH dashboard_presence_traces AS ({scoped_traces})
                SELECT DISTINCT tuple(
                    toString(dashboard_presence_trace.project_id),
                    toString(latest_eval.trace_id)
                )
                FROM ({latest_evals}) AS latest_eval
                INNER JOIN dashboard_presence_traces AS dashboard_presence_trace
                  ON dashboard_presence_trace.trace_id = latest_eval.trace_id
                WHERE {live_predicate}
            """

        # The legacy dashboard retains the deployed trace dictionary.  The
        # direct-write builder above deliberately avoids it because the locked
        # production read role has no dictionary grants.
        return f"""
            SELECT DISTINCT tuple(
                toString(dictGet('trace_dict', 'project_id', latest_eval.trace_id)),
                toString(latest_eval.trace_id)
            )
            FROM ({latest_evals}) AS latest_eval
            WHERE {live_predicate}
              AND dictGet('trace_dict', 'project_id', latest_eval.trace_id)
                  IN %(project_ids)s
        """

    def _annotation_presence_relation(self, params: dict[str, Any]) -> str:
        """Return identities complete for every configured project label.

        The authoritative label set is materialized from PostgreSQL after the
        project scope is authorized and is part of the exact-snapshot cache
        identity.  Missing metadata must fail closed; a simple Score-existence
        relation would make dashboard F6 disagree with trace/span/session APIs.
        """

        label_map = self.annotation_label_ids_by_project
        if label_map is None:
            raise InvalidMetricCombinationError(
                "Annotation completeness metadata is unavailable."
            )

        project_ids = tuple(dict.fromkeys(str(value) for value in self.project_ids))
        if any(project_id not in label_map for project_id in project_ids):
            raise InvalidMetricCombinationError(
                "Annotation completeness metadata is incomplete."
            )

        branches: list[str] = []
        empty_label_projects: list[str] = []
        organization_clause = ""
        if self.organization_id:
            params["annotation_presence_organization"] = str(self.organization_id)
            organization_clause = (
                "AND annotation_presence.organization_id = "
                "toUUID(%(annotation_presence_organization)s)"
            )
        for index, project_id in enumerate(project_ids):
            label_ids = label_map[project_id]
            if not label_ids:
                empty_label_projects.append(project_id)
                continue

            project_param = f"annotation_presence_project_{index}"
            project_scope_param = f"annotation_presence_project_scope_{index}"
            labels_param = f"annotation_presence_labels_{index}"
            label_count_param = f"annotation_presence_label_count_{index}"
            params[project_param] = project_id
            params[project_scope_param] = (project_id,)
            params[labels_param] = label_ids
            params[label_count_param] = len(label_ids)
            live_project_traces = latest_live_project_traces_sql(
                project_ids_param=project_scope_param
            )
            branches.append(
                f"""
                SELECT tuple(
                    toString(annotation_presence.tracer_project_id),
                    if(
                        annotation_presence_trace.project_id =
                            annotation_presence.tracer_project_id,
                        toString(annotation_presence_trace.trace_id),
                        annotation_presence_span.trace_id
                    )
                )
                FROM model_hub_score AS annotation_presence FINAL
                LEFT JOIN (
                    {live_project_traces}
                ) AS annotation_presence_trace
                  ON annotation_presence_trace.project_id =
                        annotation_presence.tracer_project_id
                 AND annotation_presence_trace.trace_id =
                        annotation_presence.trace_id
                LEFT JOIN (
                    SELECT project_id, id, trace_id
                    FROM spans FINAL
                    PREWHERE project_id = toUUID(%({project_param})s)
                    WHERE _peerdb_is_deleted = 0
                ) AS annotation_presence_span
                  ON annotation_presence_span.project_id =
                        annotation_presence.tracer_project_id
                 AND annotation_presence_span.id =
                        annotation_presence.observation_span_id
                PREWHERE annotation_presence.tracer_project_id =
                    toUUID(%({project_param})s)
                WHERE annotation_presence._peerdb_is_deleted = 0
                  AND annotation_presence.deleted = 0
                  {organization_clause}
                  AND annotation_presence.label_id IN %({labels_param})s
                  AND (
                        annotation_presence_trace.project_id =
                            annotation_presence.tracer_project_id
                        OR annotation_presence_span.trace_id != ''
                  )
                GROUP BY
                    annotation_presence.tracer_project_id,
                    if(
                        annotation_presence_trace.project_id =
                            annotation_presence.tracer_project_id,
                        toString(annotation_presence_trace.trace_id),
                        annotation_presence_span.trace_id
                    )
                HAVING uniqExact(annotation_presence.label_id) =
                    %({label_count_param})s
                """
            )

        if empty_label_projects:
            empty_projects_param = "annotation_presence_empty_projects"
            params[empty_projects_param] = tuple(empty_label_projects)
            if self._direct_trace_project_scope_available:
                live_traces = latest_live_project_traces_sql(
                    project_ids_param=empty_projects_param
                )
                branches.append(
                    f"""
                    SELECT tuple(toString(project_id), toString(trace_id))
                    FROM ({live_traces}) AS annotation_empty_label_traces
                    """
                )
            else:
                branches.append(
                    f"""
                    SELECT DISTINCT tuple(toString(project_id), trace_id)
                    FROM spans FINAL
                    PREWHERE project_id IN %({empty_projects_param})s
                    WHERE _peerdb_is_deleted = 0
                      AND trace_id != ''
                    """
                )

        if not branches:
            return "SELECT tuple('', '') WHERE 0"
        return " UNION ALL ".join(branches)

    def _build_presence_filter_predicates(
        self,
        filters: list[dict],
        *,
        outer_project_expr: str,
        outer_trace_expr: str,
        params: dict[str, Any],
    ) -> list[str]:
        """Compile every boolean relation leaf without silently dropping it."""

        predicates: list[str] = []
        for payload in filters:
            if payload.get("source", "traces") not in (
                "traces",
                "",
                "all",
                "both",
            ):
                continue
            metric_name = self._presence_filter_name(payload)
            if metric_name not in PRESENCE_SYSTEM_METRIC_FILTERS:
                continue
            required = self._presence_filter_value(payload, metric_name)
            relation = (
                self._eval_presence_relation()
                if metric_name == "has_eval"
                else self._annotation_presence_relation(params)
            )
            membership = "IN" if required else "NOT IN"
            predicates.append(
                f"(notEmpty(toString({outer_trace_expr})) AND "
                f"toString({outer_project_expr}) != "
                "'00000000-0000-0000-0000-000000000000' AND "
                f"tuple(toString({outer_project_expr}), "
                f"toString({outer_trace_expr})) {membership} ({relation}))"
            )
        return predicates

    def _names_reference_id(self, *names: str | None) -> bool:
        return any(
            (n or "").lower() in _ID_RESOLVED_NAMES for n in names if n is not None
        )

    def _query_references_id(
        self, metric_name: str | None, per_metric_filters: list[dict]
    ) -> bool:
        if self._names_reference_id(metric_name):
            return True
        for bd in self.breakdowns:
            if bd.get("type", "system_metric") != "system_metric":
                continue
            if self._names_reference_id(bd.get("name"), bd.get("id")):
                return True
        for f in self.global_filters + (per_metric_filters or []):
            f_type = f.get("metric_type") or f.get("type", "")
            if f_type and f_type != "system_metric":
                continue
            if self._names_reference_id(
                f.get("metric_name"), f.get("name"), f.get("id")
            ):
                return True
        return False

    def _query_references_user_dimension(
        self, metric_name: str | None, per_metric_filters: list[dict]
    ) -> bool:
        """Whether this query needs a curated user label/type, not only its id."""

        def is_user_dimension(*names: str | None) -> bool:
            return any(
                (name or "").lower() in _USER_DIMENSION_NAMES
                for name in names
                if name is not None
            )

        if is_user_dimension(metric_name):
            return True
        for breakdown in self.breakdowns:
            if breakdown.get("type", "system_metric") != "system_metric":
                continue
            if is_user_dimension(breakdown.get("name"), breakdown.get("id")):
                return True
        for item in self.global_filters + (per_metric_filters or []):
            filter_type = item.get("metric_type") or item.get("type", "")
            if filter_type and filter_type != "system_metric":
                continue
            if is_user_dimension(
                item.get("metric_name"), item.get("name"), item.get("id")
            ):
                return True
        return False

    def _query_references_session_dimension(
        self, metric_name: str | None, per_metric_filters: list[dict]
    ) -> bool:
        """Whether this query needs remap resolution for a session id."""

        def is_session_dimension(*names: str | None) -> bool:
            return any(
                (name or "").lower() in _SESSION_DIMENSION_NAMES
                for name in names
                if name is not None
            )

        if is_session_dimension(metric_name):
            return True
        for breakdown in self.breakdowns:
            if breakdown.get("type", "system_metric") != "system_metric":
                continue
            if is_session_dimension(breakdown.get("name"), breakdown.get("id")):
                return True
        for item in self.global_filters + (per_metric_filters or []):
            filter_type = item.get("metric_type") or item.get("type", "")
            if filter_type and filter_type != "system_metric":
                continue
            if is_session_dimension(
                item.get("metric_name"), item.get("name"), item.get("id")
            ):
                return True
        return False

    def _direct_user_physical_span_filter(
        self,
        per_metric_filters: list[dict],
        params: dict[str, Any] | None,
    ) -> str:
        """Resolve positive curated-user filters before scanning ``spans``.

        The public ``user`` dimension is an external label when a current live
        ``end_users`` row exists, otherwise the remap survivor UUID string.
        Resolve that exact value on the small project-scoped dimension first,
        expand every matched survivor to all old/new physical ids, and return
        a raw ``sp.end_user_id`` membership predicate. Negative/set filters
        retain the existing exact enrichment path because their candidate set
        can be effectively the whole tenant.
        """

        if not self._direct_end_users_available or params is None:
            return ""

        match_conditions: list[str] = []
        fallback_uuid_sets: list[set[str]] = []
        supported_operations = frozenset({"equal_to", "contains"})
        for filter_index, item in enumerate(
            self.global_filters + (per_metric_filters or [])
        ):
            if item.get("source", "traces") not in ("traces", ""):
                continue
            filter_type = item.get("metric_type") or item.get("type", "")
            filter_name = (
                item.get("metric_name") or item.get("name") or item.get("id", "")
            ).lower()
            operation = item.get("operator", "")
            value = item.get("value")
            if (
                filter_type != "system_metric"
                or filter_name != "user"
                or operation not in supported_operations
                or value is None
                or value == ""
                or value == []
            ):
                continue

            parameter_key = f"direct_user_filter_{filter_index}_val"
            operator = _get_operator_symbol(operation)
            if not operator:
                continue
            params[parameter_key] = _coerce_string_filter_value(value, operation)
            match_conditions.append(
                "if(curated_user_id = '', toString(resolved_end_user_id), "
                "curated_user_id) "
                f"{operator} %({parameter_key})s"
            )
            raw_values = value if isinstance(value, list) else [value]
            fallback_values: set[str] = set()
            for raw_value in raw_values:
                if not isinstance(raw_value, str):
                    continue
                try:
                    fallback_values.add(str(UUID(raw_value)))
                except ValueError:
                    continue
            fallback_uuid_sets.append(fallback_values)

        if not match_conditions:
            return ""

        dimension_candidate_ids = """
            SELECT filtered_dimension_candidate.end_user_id
            FROM end_users AS filtered_dimension_candidate FINAL
            WHERE filtered_dimension_candidate.project_id IN %(project_ids)s
              AND filtered_dimension_candidate.is_deleted = 0
        """
        dimension_remap = _touched_survivor_map_subquery(
            remap_table="end_user_id_remap",
            candidate_ids_sql=dimension_candidate_ids,
        )
        resolved_dimension_id = resolved_id_expr(
            "filtered_eu.end_user_id", "filtered_eu_remap"
        )
        exact_or_latest = (
            "tuple(filtered_eu.end_user_id = "
            f"{resolved_dimension_id}, filtered_eu.version)"
        )
        filtered_dimension = f"""
            SELECT
                filtered_eu.project_id AS project_id,
                {resolved_dimension_id} AS resolved_end_user_id,
                argMax(filtered_eu.user_id, {exact_or_latest}) AS curated_user_id
            FROM end_users AS filtered_eu FINAL
            LEFT JOIN ({dimension_remap}) AS filtered_eu_remap
              ON filtered_eu.end_user_id = filtered_eu_remap.any_id
            WHERE filtered_eu.project_id IN %(project_ids)s
              AND filtered_eu.is_deleted = 0
            GROUP BY project_id, resolved_end_user_id
            HAVING {" AND ".join(match_conditions)}
        """
        physical_id = (
            "if(user_filter_physical_map.any_id IS NULL "
            f"OR user_filter_physical_map.any_id = toUUID('{NIL_UUID}'), "
            "matched_user.resolved_end_user_id, user_filter_physical_map.any_id)"
        )
        curated_membership = f"""(sp.project_id, sp.end_user_id) IN (
            SELECT DISTINCT
                matched_user.project_id AS project_id,
                {physical_id} AS physical_end_user_id
            FROM ({filtered_dimension}) AS matched_user
            LEFT JOIN ({dimension_remap}) AS user_filter_physical_map
              ON user_filter_physical_map.survivor_id =
                 matched_user.resolved_end_user_id
        )"""

        # A missing curated row falls back to the *resolved survivor UUID*
        # string. Membership operators can preserve that contract without a
        # broad span scan: intersect UUID literals across multiple filters,
        # expand each surviving survivor through the remap, then let the outer
        # exact predicate reject any over-inclusive physical candidate.
        fallback_uuid_values = (
            set.intersection(*fallback_uuid_sets) if fallback_uuid_sets else set()
        )
        if len(fallback_uuid_values) > 64:
            # A large UUID candidate set is not safe to partially optimize:
            # omitting missing/tombstoned dimension rows would change public
            # fallback semantics. Keep the original exact outer plan instead.
            for parameter_key in tuple(params):
                if parameter_key.startswith("direct_user_filter_"):
                    params.pop(parameter_key)
            return ""
        if not fallback_uuid_values:
            return curated_membership

        fallback_param_keys: list[str] = []
        for fallback_index, fallback_value in enumerate(sorted(fallback_uuid_values)):
            fallback_key = f"direct_user_fallback_uuid_{fallback_index}"
            params[fallback_key] = fallback_value
            fallback_param_keys.append(fallback_key)
        fallback_tuple = tuple(sorted(fallback_uuid_values))
        params["direct_user_fallback_uuids"] = fallback_tuple
        fallback_map = bounded_survivor_map_subquery(
            "end_user_id_remap",
            candidate_param="direct_user_fallback_uuids",
        )
        literal_branches = " UNION ALL ".join(
            f"SELECT toUUID(%({parameter_key})s) AS physical_end_user_id"
            for parameter_key in fallback_param_keys
        )
        fallback_membership = f"""sp.end_user_id IN (
            SELECT any_id AS physical_end_user_id
            FROM ({fallback_map}) AS fallback_user_physical_map
            WHERE survivor_id IN %(direct_user_fallback_uuids)s
            UNION DISTINCT
            {literal_branches}
        )"""
        return f"({curated_membership} OR {fallback_membership})"

    def _direct_session_physical_span_filter(
        self,
        per_metric_filters: list[dict],
        params: dict[str, Any] | None,
    ) -> str:
        """Resolve finite positive session filters to physical span ids.

        The public session value is the remap survivor UUID. Expand that
        survivor back to every old/new physical id before scanning spans so
        ClickHouse can use the trace_session_id bloom index. The exact outer
        predicate remains in place as a semantic guard.
        """

        if params is None:
            return ""

        candidate_sets: list[set[str]] = []
        supported_operations = frozenset({"equal_to", "contains"})
        for item in self.global_filters + (per_metric_filters or []):
            if item.get("source", "traces") not in ("traces", ""):
                continue
            filter_type = item.get("metric_type") or item.get("type", "")
            filter_name = (
                item.get("metric_name") or item.get("name") or item.get("id", "")
            ).lower()
            operation = item.get("operator", "")
            value = item.get("value")
            if (
                filter_type != "system_metric"
                or filter_name != "session"
                or operation not in supported_operations
                or value is None
                or value == ""
                or value == []
            ):
                continue

            raw_values = value if isinstance(value, list) else [value]
            candidates: set[str] = set()
            for raw_value in raw_values:
                if not isinstance(raw_value, str):
                    continue
                try:
                    candidates.add(str(UUID(raw_value)))
                except ValueError:
                    continue
            candidate_sets.append(candidates)

        if not candidate_sets:
            return ""
        candidate_uuids = set.intersection(*candidate_sets)
        if len(candidate_uuids) > 64:
            return ""
        if not candidate_uuids:
            return "0"

        ordered_candidates = tuple(sorted(candidate_uuids))
        params["direct_session_filter_uuids"] = ordered_candidates
        literal_param_keys: list[str] = []
        for candidate_index, candidate_value in enumerate(ordered_candidates):
            parameter_key = f"direct_session_filter_uuid_{candidate_index}"
            params[parameter_key] = candidate_value
            literal_param_keys.append(parameter_key)

        physical_map = bounded_survivor_map_subquery(
            "trace_session_id_remap",
            candidate_param="direct_session_filter_uuids",
        )
        literal_branches = " UNION ALL ".join(
            f"SELECT toUUID(%({parameter_key})s) AS physical_trace_session_id"
            for parameter_key in literal_param_keys
        )
        return f"""sp.trace_session_id IN (
            SELECT any_id AS physical_trace_session_id
            FROM ({physical_map}) AS session_filter_physical_map
            WHERE survivor_id IN %(direct_session_filter_uuids)s
            UNION DISTINCT
            {literal_branches}
        )"""

    def _spans_source(
        self,
        metric_name: str | None,
        per_metric_filters: list[dict],
        alias: str,
        params: dict[str, Any] | None = None,
    ) -> str:
        """Return the spans FROM/JOIN source for the given alias — the id-remap
        resolved derived table when the query references an id, else the bare
        table (so id-free metrics stay byte-identical with zero added joins).

        ``alias`` is ``"spans"`` for the flat ``FROM spans`` shapes (the derived
        table is aliased back to ``spans``) or ``"s"`` for the JOINed shapes.
        """
        if self._query_references_id(metric_name, per_metric_filters):
            resolve_end_user_id = self._query_references_user_dimension(
                metric_name, per_metric_filters
            )
            physical_end_user_filter = self._direct_user_physical_span_filter(
                per_metric_filters, params
            )
            physical_trace_session_filter = self._direct_session_physical_span_filter(
                per_metric_filters, params
            )
            return _resolved_spans_source(
                None if alias == "spans" else alias,
                latest_state=self._latest_state_spans_required,
                include_end_user_dimension=(
                    self._direct_end_users_available
                    and self._query_references_user_dimension(
                        metric_name, per_metric_filters
                    )
                ),
                physical_end_user_filter=physical_end_user_filter,
                physical_trace_session_filter=physical_trace_session_filter,
                resolve_end_user_id=resolve_end_user_id,
                resolve_trace_session_id=self._query_references_session_dimension(
                    metric_name, per_metric_filters
                ),
            )
        if self._latest_state_spans_required:
            return "spans FINAL" if alias == "spans" else f"spans AS {alias} FINAL"
        return "spans" if alias == "spans" else f"spans AS {alias}"

    def _annotation_filter_spans_source(
        self,
        span_filters: list[dict],
        params: dict[str, Any],
    ) -> str:
        """Keep the V1 annotation-filter source byte-for-byte exact.

        V2 overrides this hook with its indexed immutable-identity replay.  The
        base builder must retain ``FINAL`` because it has no equivalent replay
        proof of latest physical span state.
        """

        return "spans AS s FINAL"

    @staticmethod
    def _qualify_span_expression(expression: str, alias: str) -> str:
        """Qualify known spans columns without touching SQL string literals."""

        columns = (
            "trace_session_id",
            "prompt_version_id",
            "completion_tokens",
            "prompt_label_id",
            "observation_type",
            "parent_span_id",
            "end_user_id",
            "user_id_type",
            "prompt_tokens",
            "service_name",
            "span_attributes_raw",
            "span_attr_bool",
            "span_attr_num",
            "span_attr_str",
            "total_tokens",
            "project_id",
            "latency_ms",
            "start_time",
            "trace_id",
            "user_id",
            "provider",
            "status",
            "model",
            "cost",
            "tags",
            "id",
        )
        pieces = re.split(r"('(?:''|[^'])*')", expression)
        for piece_index in range(0, len(pieces), 2):
            sql = pieces[piece_index]
            for column in columns:
                sql = re.sub(
                    rf"(?<!\.)(?<!\w){column}(?!\w)",
                    f"{alias}.{column}",
                    sql,
                )
            pieces[piece_index] = sql
        return "".join(pieces)

    def _system_metric_expression(
        self, metric_name: str, alias: str | None = None
    ) -> str:
        if (
            self._direct_end_users_available
            and metric_name in self._DIRECT_USER_METRIC_EXPRESSIONS
        ):
            expression = self._DIRECT_USER_METRIC_EXPRESSIONS[metric_name]
        else:
            expression = SYSTEM_METRICS[metric_name][1]
        if alias:
            return self._qualify_span_expression(expression, alias)
        return expression

    def _breakdown_column_expression(self, breakdown_name: str) -> str | None:
        if (
            self._direct_end_users_available
            and breakdown_name in self._DIRECT_USER_BREAKDOWN_EXPRESSIONS
        ):
            return self._DIRECT_USER_BREAKDOWN_EXPRESSIONS[breakdown_name]
        return self._BREAKDOWN_COL_MAP.get(breakdown_name)

    def _string_filter_column_expression(self, filter_name: str) -> str | None:
        if self._direct_end_users_available and filter_name == "user_count":
            return self._DIRECT_USER_METRIC_EXPRESSIONS["user_count"]
        if (
            self._direct_end_users_available
            and filter_name in self._DIRECT_USER_BREAKDOWN_EXPRESSIONS
        ):
            return self._DIRECT_USER_BREAKDOWN_EXPRESSIONS[filter_name]
        return self._STRING_FILTER_COL.get(filter_name)

    # ------------------------------------------------------------------
    # System metric
    # ------------------------------------------------------------------

    def _attr_rollup_window_covered(self, start_date: datetime) -> bool:
        """True only when the rollup flag is on AND the requested window starts
        within the backfilled-and-covered range — fail-closed on a fresh deploy
        (off until ops backfills the rollup and sets the coverage date)."""
        from django.conf import settings

        if not getattr(settings, "DASHBOARD_ATTR_ROLLUP_ENABLED", False):
            return False
        covered_since = getattr(settings, "DASHBOARD_ATTR_ROLLUP_COVERED_SINCE", None)
        if covered_since is None:
            return False
        if covered_since.tzinfo is None:
            covered_since = covered_since.replace(tzinfo=UTC)
        if start_date.tzinfo is None:
            start_date = start_date.replace(tzinfo=UTC)
        return start_date >= covered_since

    def _should_use_rollup(
        self,
        metric_name: str,
        aggregation: str,
        single_bd: dict | None,
        per_metric_filters: list[dict],
        start_date: datetime,
    ) -> bool:
        """True only for the covered latency-breakdown shape on a v2 build with the
        rollup enabled and the window inside coverage — fail-closed everywhere else."""
        return (
            self._attr_rollup_available
            and not self.config.get("require_versioned_snapshot", False)
            and metric_name == "latency"
            and aggregation == "avg"
            and self.granularity in _ROLLUP_GRANULARITIES
            and single_bd is not None
            and single_bd.get("type") == "custom_attribute"
            and single_bd.get("name") in _ROLLUP_COVERED_ATTRS
            and not per_metric_filters
            and not self.global_filters
            and self._attr_rollup_window_covered(start_date)
        )

    def _build_system_metric_query(
        self,
        metric_name: str,
        aggregation: str,
        bucket_fn: str,
        per_metric_filters: list[dict],
        params: dict,
    ) -> tuple[str, dict]:
        # Normalize: saved widgets may have capitalized names (e.g. "Latency")
        metric_name = metric_name.lower() if metric_name else metric_name

        # Covered latency-breakdown shape → the pre-aggregated rollup; anything
        # else falls through to the spans scan (fail-closed, see _should_use_rollup).
        single_bd = self.breakdowns[0] if len(self.breakdowns) == 1 else None
        if self._should_use_rollup(
            metric_name,
            aggregation,
            single_bd,
            per_metric_filters,
            params["start_date"],
        ):
            params = dict(params)
            params["attr_key"] = _sanitize_attr_key(single_bd["name"])
            # Rollup is hourly — snap the window to whole hours so no partial bucket.
            params["start_date"] = _snap_to_hour(params["start_date"])
            params["end_date"] = _snap_to_hour(params["end_date"])
            rollup_query = (
                f"SELECT {bucket_fn}(hour) AS time_bucket,\n"
                "       attr_value AS breakdown_value,\n"
                "       sumMerge(latency_sum) / countMerge(n) AS value\n"
                "FROM dashboard_attr_rollup\n"
                "WHERE project_id IN %(project_ids)s\n"
                "  AND attr_key = %(attr_key)s\n"
                "  AND hour >= %(start_date)s\n"
                "  AND hour < %(end_date)s\n"
                "GROUP BY time_bucket, breakdown_value\n"
                "ORDER BY time_bucket, breakdown_value"
            )
            return rollup_query, params

        if metric_name not in SYSTEM_METRICS:
            # Fallback: treat unknown system metrics as custom span attributes
            # (handles widgets saved with wrong type, e.g. span attribute saved as system_metric)
            logger.warning(
                "Unknown system metric '%s', treating as custom attribute",
                metric_name,
            )
            return self._build_custom_attr_query(
                {"attribute_key": metric_name, "attribute_type": "number"},
                aggregation,
                bucket_fn,
                per_metric_filters,
                params,
            )
        col_expr = self._system_metric_expression(metric_name)
        # Identifier metrics should count unique identities, not raw span rows.
        if metric_name in _COUNT_DISTINCT_METRICS and aggregation != "count_distinct":
            aggregation = "count_distinct"
        agg_expr = AGGREGATIONS.get(aggregation, "avg({col})").format(col=col_expr)

        # 0/1 indicator metrics → rescale averaging aggs to percent.
        if metric_name in _RATE_INDICATOR_METRICS:
            agg_expr = rescale_rate_to_percent(agg_expr, aggregation)

        select_parts = [f"{bucket_fn}(start_time) AS time_bucket"]
        group_parts = ["time_bucket"]
        order_parts = ["time_bucket"]

        select_parts.append(f"{agg_expr} AS value")

        where_clauses, params = self._build_where_clauses(
            "spans", "start_time", per_metric_filters, params
        )
        if metric_name == "latency":
            where_clauses.append("(parent_span_id IS NULL OR parent_span_id = '')")

        # Subquery filters from global + per-metric for non-system metrics
        subquery_clauses = self._build_subquery_filters(
            self.global_filters + per_metric_filters, params, "s_"
        )
        params.update(subquery_clauses[1])

        all_where = where_clauses
        if subquery_clauses[0]:
            all_where += subquery_clauses[0]

        spans_flat = self._spans_source(
            metric_name, per_metric_filters, "spans", params=params
        )
        spans_joined = self._spans_source(
            metric_name, per_metric_filters, "s", params=params
        )

        bd_infos = self._resolve_all_breakdowns(params)
        has_annotation_bd = any(b["type"] == "annotation" for b in bd_infos)
        presence_filter_inputs = self.global_filters + per_metric_filters
        flat_presence_predicates = self._build_presence_filter_predicates(
            presence_filter_inputs,
            outer_project_expr="spans.project_id",
            outer_trace_expr="spans.trace_id",
            params=params,
        )
        # A custom-attribute breakdown has values only on rows that carry the
        # selected Map key. Without this predicate ClickHouse must materialize
        # the complete Map value column for every span in the window, and rows
        # without the key become a misleading default empty/zero bucket. The
        # key predicate is also the expression covered by the deployed Map-key
        # bloom indexes.
        all_where.extend(
            b["presence_predicate"] for b in bd_infos if b.get("presence_predicate")
        )

        if bd_infos:
            bd_exprs = []
            join_clauses = []
            for b in bd_infos:
                bd_exprs.append(b["expr"])
                if b["join"]:
                    join_clauses.append(b["join"])

            if len(bd_exprs) == 1:
                bd_select = f"{bd_exprs[0]} AS breakdown_value"
            else:
                parts = ", ' / ', ".join(f"toString({e})" for e in bd_exprs)
                bd_select = f"concat({parts}) AS breakdown_value"

            if has_annotation_bd:
                agg_with_alias = self._qualify_span_expression(agg_expr, "s")
                joined_presence_predicates = self._build_presence_filter_predicates(
                    presence_filter_inputs,
                    outer_project_expr="s.project_id",
                    outer_trace_expr="s.trace_id",
                    params=params,
                )
                where_str = " AND ".join(
                    [
                        *(_prefix_spans_columns(c) for c in all_where),
                        *joined_presence_predicates,
                    ]
                )
                join_str = "\n".join(join_clauses)
                query = (
                    f"SELECT {bucket_fn}(s.start_time) AS time_bucket,\n"
                    f"       {bd_select},\n"
                    f"       {agg_with_alias} AS value\n"
                    f"FROM {spans_joined}\n"
                    f"{join_str}\n"
                    f"WHERE {where_str}\n"
                    f"GROUP BY time_bucket, breakdown_value\n"
                    f"ORDER BY time_bucket, breakdown_value"
                )
            else:
                flat_where = [*all_where, *flat_presence_predicates]
                select_parts_with_bd = [
                    f"{bucket_fn}(start_time) AS time_bucket",
                    bd_select,
                    f"{agg_expr} AS value",
                ]
                query = (
                    f"SELECT {', '.join(select_parts_with_bd)}\n"
                    f"FROM {spans_flat}\n"
                    f"WHERE {' AND '.join(flat_where)}\n"
                    f"GROUP BY time_bucket, breakdown_value\n"
                    f"ORDER BY time_bucket, breakdown_value"
                )
        else:
            flat_where = [*all_where, *flat_presence_predicates]
            query = (
                f"SELECT {', '.join(select_parts)}\n"
                f"FROM {spans_flat}\n"
                f"WHERE {' AND '.join(flat_where)}\n"
                f"GROUP BY {', '.join(group_parts)}\n"
                f"ORDER BY {', '.join(order_parts)}"
            )
        return query, params

    # ------------------------------------------------------------------
    # Eval metric
    # ------------------------------------------------------------------

    def _build_eval_metric_query(
        self,
        metric: dict,
        aggregation: str,
        bucket_fn: str,
        per_metric_filters: list[dict],
        params: dict,
    ) -> tuple[str, dict]:
        """Build eval metric query against usage_apicalllog (central eval table).

        All eval executions (tracer, dataset, simulation, SDK, playground) write
        to APICallLog with source_id = eval_template_id. The score is stored in
        the JSON ``config`` field as ``config.output.output``.

        The eval table acts as a **hub** — breakdowns and filters from any
        connected source (traces, datasets, simulations, other evals) are
        resolved by dynamically JOINing the relevant tables via keys in the
        config JSON (trace_id, dataset_id, etc.).
        """
        # Accept both config_id (legacy) and name (new) as the template identifier
        eval_template_id = metric.get("config_id") or metric.get("name", "")
        output_type = (metric.get("output_type") or "SCORE").upper()

        eval_template_id = self._resolve_eval_template_identity(
            metric, eval_template_id
        )

        params["eval_template_id"] = eval_template_id
        params["organization_id"] = self.organization_id
        params["workspace_id"] = self.workspace_id

        _output_str_lower = "lower(e.eval_output_str)"
        _truthy = sql_str_set(EVAL_TRUTHY_OUTPUTS)
        _falsy = sql_str_set(EVAL_FALSY_OUTPUTS)
        _is_pass = eval_pass_expr("e.eval_score", "e.eval_output_str")
        _is_fail = eval_fail_expr("e.eval_score", "e.eval_output_str")
        _unified_score = f"if({_is_pass}, 1.0, e.eval_score)"

        _EVAL_AGGREGATIONS: dict[str, str] = {
            "pass_rate": f"countIf({_is_pass}) / nullIf(count(), 0)",
            "fail_rate": f"countIf({_is_fail}) / nullIf(count(), 0)",
            "pass_count": f"countIf({_is_pass})",
            "fail_count": f"countIf({_is_fail})",
            "true_rate": f"countIf({_is_pass}) / nullIf(count(), 0)",
        }

        if aggregation in _EVAL_AGGREGATIONS:
            agg_expr = _EVAL_AGGREGATIONS[aggregation]
        elif output_type in ("CHOICE", "CHOICES"):
            agg_expr = "count()"
        else:
            if output_type == "PASS_FAIL":
                col_expr = _unified_score
            else:
                # Templates with no output_type still emit pass/fail strings,
                # bare numbers, or a structured object carrying a score.
                _has_number = (
                    f"match(e.eval_output_str, '{EVAL_NUMERIC_OUTPUT_PATTERN}') "
                    f"OR {eval_has_structured_score('e.eval_output_str')}"
                )
                col_expr = (
                    "if(e.eval_output_str = '', NULL, "
                    f"if({_output_str_lower} IN {_truthy}, 1.0, "
                    f"if({_output_str_lower} IN {_falsy}, 0.0, "
                    f"if({_has_number}, e.eval_score, NULL))))"
                )
            agg_expr = AGGREGATIONS.get(aggregation, "avg({col})").format(col=col_expr)

        select_parts = [f"{bucket_fn}(e.created_at) AS time_bucket"]
        group_parts = ["time_bucket"]
        order_parts = ["time_bucket"]
        select_parts.append(f"{agg_expr} AS value")

        # Scope to workspace when available, otherwise org
        if self.workspace_id:
            _scope_filter = "e.workspace_id = toUUID(%(workspace_id)s)"
            _usage_main_scope = (
                "usage_main_scan.workspace_id = toUUID(%(workspace_id)s)"
            )
        else:
            _scope_filter = "e.organization_id = toUUID(%(organization_id)s)"
            _usage_main_scope = (
                "usage_main_scan.organization_id = toUUID(%(organization_id)s)"
            )

        # Physical predicate first, then physical-latest, then live/status.
        # Table-level FINAL previously merged every usage row before the
        # template/time filters. On large tenants that was the Code 159 hot path.
        # Keeping the deletion predicates outside LIMIT 1 BY is required: an
        # inner live filter would resurrect a superseded row after a tombstone.
        # Resolve ownership from the narrow ``traces`` primary-key prefix.  The
        # previous candidate relation reread this same usage slice before the
        # main latest-state scan.  Large but otherwise healthy slices therefore
        # crossed the query-wide row budget only because every usage row was
        # consumed twice.  Project membership is immutable; latest-live and
        # ambiguous-ID handling remain exact in the project-bounded relation.
        trace_projects = latest_live_project_traces_sql()
        eval_source = f"""
            (
                SELECT
                    usage_main_latest.*,
                    usage_trace_project.project_id AS eval_project_id
                FROM (
                    SELECT {_usage_eval_latest_projection("usage_main_scan")}
                    FROM usage_apicalllog AS usage_main_scan
                    PREWHERE {_usage_main_scope}
                      AND usage_main_scan.source_id = %(eval_template_id)s
                      AND usage_main_scan.created_at >= %(start_date)s
                      AND usage_main_scan.created_at < %(end_date)s
                    ORDER BY usage_main_scan._peerdb_version DESC
                    LIMIT 1 BY usage_main_scan.id
                ) AS usage_main_latest
                LEFT JOIN ({trace_projects}) AS usage_trace_project
                  ON usage_trace_project.trace_id =
                     toUUIDOrZero(usage_main_latest.eval_trace_id)
                WHERE usage_main_latest._peerdb_is_deleted = 0
                  AND usage_main_latest.deleted = 0
                  AND usage_main_latest.status = 'success'
                  AND (
                    usage_main_latest.eval_trace_id = ''
                    OR usage_trace_project.project_id IN %(project_ids)s
                  )
                ORDER BY usage_main_latest.created_at DESC,
                         usage_main_latest.id DESC
                LIMIT 1 BY if(
                    usage_main_latest.eval_trace_id = '',
                    concat('row:', toString(usage_main_latest.id)),
                    concat('trace:', usage_main_latest.eval_trace_id)
                )
            ) AS e
        """

        where_parts = [
            _scope_filter,
            "e._peerdb_is_deleted = 0",
            "e.deleted = 0",
            "e.status = 'success'",
            "e.source_id = %(eval_template_id)s",
            "e.created_at >= %(start_date)s",
            "e.created_at < %(end_date)s",
        ]

        joins = []
        need_spans_join = False
        need_eval_join = {}

        _trace_id_expr = "e.eval_trace_id"

        bd_exprs = []
        for bd_idx, bd in enumerate(self.breakdowns):
            bd_name = (bd.get("name") or bd.get("id") or "").lower()
            bd_type = bd.get("type", "system_metric")

            if bd_name in ("source", "eval_source"):
                bd_expr = "if(e.source = '', '(not set)', e.source)"

            elif bd_name == "dataset":
                bd_expr = (
                    "if(e.eval_dataset_id != '', e.eval_dataset_id, "
                    + _eval_source_bucket_expr(exclude="dataset")
                    + ")"
                )

            elif bd_name == "project":
                _proj_uuid = "e.eval_project_id"
                bd_expr = (
                    f"if({_trace_id_expr} != '' "
                    f"AND {_proj_uuid} != toUUID('00000000-0000-0000-0000-000000000000'), "
                    f"toString({_proj_uuid}), "
                    + _eval_source_bucket_expr(exclude="project")
                    + ")"
                )
            elif bd_name in SYSTEM_METRICS:
                need_spans_join = True
                span_expr = self._system_metric_expression(bd_name, alias="s")
                bd_expr = f"if(s.trace_id = '', '(not set)', toString({span_expr}))"

            elif bd_name in (
                "model",
                "status",
                "service_name",
                "span_kind",
                "provider",
                "session",
                "user",
                "tag",
                "prompt_name",
                "prompt_version",
                "prompt_label",
            ):
                need_spans_join = True
                # Map common names to spans columns
                _span_col_map = {
                    "model": "model",
                    "status": "status",
                    "service_name": "service_name",
                    "span_kind": "observation_type",
                    "provider": "provider",
                    "session": "trace_session_id",
                    "user": "end_user_id",
                    "tag": "tags",
                    "prompt_name": "prompt_name",
                    "prompt_version": "prompt_version",
                    "prompt_label": "prompt_label",
                }
                scol = _span_col_map.get(bd_name, bd_name)
                bd_expr = f"if(s.trace_id = '', '(not set)', toString(s.{scol}))"

            elif bd_type == "eval_metric":
                ev_tid = bd.get("config_id") or bd.get("label_id") or bd_name
                ev_tid = self._resolve_eval_template_identity(bd, ev_tid)
                bd_output_type = (
                    bd.get("output_type") or bd.get("outputType") or ""
                ).upper()

                if ev_tid == eval_template_id:
                    if bd_output_type == "PASS_FAIL":
                        bd_expr = eval_pass_fail_label_expr(
                            "e.eval_score", "e.eval_output_str"
                        )
                    elif bd_output_type in ("CHOICE", "CHOICES"):
                        bd_expr = (
                            "if(e.eval_output_str = '', '(not set)', e.eval_output_str)"
                        )
                    else:
                        bd_expr = (
                            "if(e.eval_score = 0, '(not set)', "
                            "toString(round(e.eval_score * 100)))"
                        )
                else:
                    ev_alias = f"ev_bd{bd_idx}"
                    param_key = f"_ev_bd{bd_idx}_tid"
                    params[param_key] = ev_tid
                    need_eval_join[ev_alias] = param_key
                    if bd_output_type == "PASS_FAIL":
                        label_expr = eval_pass_fail_label_expr(
                            f"{ev_alias}.eval_score", f"{ev_alias}.eval_output_str"
                        )
                        bd_expr = (
                            f"if({ev_alias}.id IS NULL, '(not set)', {label_expr})"
                        )
                    elif bd_output_type in ("CHOICE", "CHOICES"):
                        bd_expr = (
                            f"if({ev_alias}.id IS NULL OR "
                            f"{ev_alias}.eval_output_str = '', '(not set)', "
                            f"{ev_alias}.eval_output_str)"
                        )
                    else:
                        bd_expr = (
                            f"if({ev_alias}.id IS NULL, '(not set)', "
                            f"toString(round({ev_alias}.eval_score * 100)))"
                        )

            elif bd_type == "custom_attribute":
                need_spans_join = True
                attr_key = _sanitize_attr_key(bd_name)
                bd_expr = f"if(s.span_attr_str['{attr_key}'] != '', s.span_attr_str['{attr_key}'], '(not set)')"

            elif bd_type == "system_metric":
                self._reject_unknown_cataloged_system_dimension(
                    bd,
                    bd_name,
                    role="breakdown",
                )
                bd_expr = "'(not set)'"

            else:
                bd_expr = "'(not set)'"

            bd_exprs.append(bd_expr)

        if bd_exprs:
            if len(bd_exprs) == 1:
                bd_select = f"{bd_exprs[0]} AS breakdown_value"
            else:
                parts = ", ' / ', ".join(f"toString({expr})" for expr in bd_exprs)
                bd_select = f"concat({parts}) AS breakdown_value"
            select_parts.append(bd_select)
            group_parts.append("breakdown_value")
            order_parts.append("breakdown_value")

        # --- Resolve filters (from any source) ---
        for i, f in enumerate(per_metric_filters + self.global_filters):
            f_type = f.get("metric_type") or f.get("type", "")
            f_name = f.get("metric_name") or f.get("name") or f.get("id", "")
            op = f.get("operator", "")
            val = f.get("value")

            # Canonical span-attribute filters carry their original type and
            # operation. Compile them before the legacy operator lookup so
            # boolean, array, map, null, and typed containment filters retain
            # the same semantics as trace/span listing APIs.
            canonical_filter = f.get("canonical_filter")
            if f_type == "custom_attribute" and isinstance(canonical_filter, dict):
                need_spans_join = True
                predicate, predicate_params = compile_span_attribute_row_predicate(
                    canonical_filter,
                    index=i,
                )
                where_parts.append(predicate)
                params.update(predicate_params)
                continue

            if self._is_presence_filter(f):
                where_parts.extend(
                    self._build_presence_filter_predicates(
                        [f],
                        outer_project_expr="e.eval_project_id",
                        outer_trace_expr="e.eval_trace_id",
                        params=params,
                    )
                )
                continue

            op_symbol = _get_operator_symbol(op)
            if not op_symbol:
                continue

            val_key = f"_evf_{i}_val"

            if f_type == "system_metric" and f_name.lower() in SYSTEM_METRICS:
                # Trace dimension filter → JOIN spans
                need_spans_join = True
                metric_key = f_name.lower()
                string_expression = self._string_filter_column_expression(metric_key)
                span_expr = (
                    self._qualify_span_expression(string_expression, alias="s")
                    if string_expression is not None
                    else self._system_metric_expression(metric_key, alias="s")
                )
                where_parts.append(f"{span_expr} {op_symbol} %({val_key})s")
                params[val_key] = (
                    _coerce_string_filter_value(val, op)
                    if string_expression is not None
                    else _coerce_filter_value(val, op)
                )

            elif f_type == "system_metric":
                self._reject_unknown_cataloged_system_dimension(
                    f,
                    f_name,
                    role="filter",
                )

            elif f_type == "eval_metric":
                ev_tid = f_name
                ev_tid = self._resolve_eval_template_identity(f, ev_tid)
                f_out_type = (f.get("output_type") or "SCORE").upper()

                if ev_tid == eval_template_id:
                    if f_out_type == "PASS_FAIL":
                        eval_col = eval_pass_fail_label_expr(
                            "e.eval_score", "e.eval_output_str"
                        )
                        where_parts.append(f"{eval_col} {op_symbol} %({val_key})s")
                        params[val_key] = _coerce_pass_fail_filter_value(val, op)
                    elif f_out_type in ("CHOICE", "CHOICES"):
                        where_parts.append(
                            f"e.eval_output_str {op_symbol} %({val_key})s"
                        )
                        params[val_key] = _coerce_string_filter_value(val, op)
                    else:
                        where_parts.append(f"e.eval_score {op_symbol} %({val_key})s")
                        params[val_key] = _coerce_filter_value(val, op)
                else:
                    ev_alias = f"ev_f{i}"
                    fkey = f"_evf_{i}_tid"
                    params[fkey] = ev_tid
                    need_eval_join[ev_alias] = fkey
                    if f_out_type == "PASS_FAIL":
                        ev_col = eval_pass_fail_label_expr(
                            f"{ev_alias}.eval_score", f"{ev_alias}.eval_output_str"
                        )
                        where_parts.append(f"{ev_col} {op_symbol} %({val_key})s")
                        params[val_key] = _coerce_pass_fail_filter_value(val, op)
                    elif f_out_type in ("CHOICE", "CHOICES"):
                        ev_col = f"{ev_alias}.eval_output_str"
                        where_parts.append(f"{ev_col} {op_symbol} %({val_key})s")
                        params[val_key] = _coerce_string_filter_value(val, op)
                    else:
                        ev_col = f"{ev_alias}.eval_score"
                        where_parts.append(f"{ev_col} {op_symbol} %({val_key})s")
                        params[val_key] = _coerce_filter_value(val, op)

            elif f_type == "custom_attribute":
                need_spans_join = True
                attr_key = _sanitize_attr_key(f_name)
                attr_type = f.get("attribute_type", "string")
                if attr_type == "number":
                    attr_map = "span_attr_num"
                elif attr_type == "boolean":
                    attr_map = "span_attr_bool"
                else:
                    attr_map = "span_attr_str"
                where_parts.append(
                    f"s.{attr_map}['{attr_key}'] {op_symbol} %({val_key})s"
                )
                params[val_key] = (
                    _coerce_string_filter_value(val, op)
                    if attr_type not in ("number", "boolean")
                    else _coerce_filter_value(val, op)
                )

        if need_spans_join:
            spans_joined = self._spans_source(
                None,
                per_metric_filters,
                "s",
                params=params,
            )
            # Scope the right-hand table before ClickHouse builds the JOIN.
            # The previous ``s.trace_id = e.eval_trace_id`` ON clause was
            # correlated only after the right side had been read, allowing an
            # eval + span-attribute dashboard to scan every tenant's spans.
            # Reuse the already tenant/template/time-bounded eval candidates;
            # converting their UUID projection back to String matches the
            # physical spans.trace_id type without changing membership.
            usage_trace_candidates = f"""
                SELECT DISTINCT
                    toUUIDOrZero(usage_trace_candidate.eval_trace_id) AS trace_id
                FROM usage_apicalllog AS usage_trace_candidate
                PREWHERE {_usage_main_scope.replace("usage_main_scan", "usage_trace_candidate")}
                  AND usage_trace_candidate.source_id = %(eval_template_id)s
                  AND usage_trace_candidate.created_at >= %(start_date)s
                  AND usage_trace_candidate.created_at < %(end_date)s
                WHERE usage_trace_candidate.eval_trace_id != ''
            """
            span_trace_candidates = (
                "SELECT toString(trace_id) AS trace_id FROM ("
                f"{usage_trace_candidates}"
                ") AS usage_span_trace_candidates"
            )
            bounded_spans_join = f"""(
                SELECT *
                FROM {spans_joined}
                WHERE s.project_id IN %(project_ids)s
                  AND s.trace_id IN ({span_trace_candidates})
                  AND s.parent_span_id = ''
                  AND s._peerdb_is_deleted = 0
            ) AS s"""
            joins.append(
                f"LEFT JOIN {bounded_spans_join} ON s.trace_id = {_trace_id_expr}"
            )

        _join_scope = (
            f"AND {'{alias}'}.workspace_id = toUUID(%(workspace_id)s)"
            if self.workspace_id
            else f"AND {'{alias}'}.organization_id = toUUID(%(organization_id)s)"
        )
        for ev_alias, param_key in need_eval_join.items():
            # Cross-eval JOIN: preselect only this config/range, collapse row
            # versions, then keep its latest successful attempt per trace.
            # The one-day edge buffer covers normal async eval ingestion skew
            # without restoring the previous unbounded all-history FINAL.
            scan_alias = f"usage_cross_{ev_alias}_scan"
            latest_alias = f"usage_cross_{ev_alias}_latest"
            cross_scope = (
                f"{scan_alias}.workspace_id = toUUID(%(workspace_id)s)"
                if self.workspace_id
                else f"{scan_alias}.organization_id = toUUID(%(organization_id)s)"
            )
            cross_source = f"""
                (
                    SELECT *
                    FROM (
                        SELECT {_usage_eval_latest_projection(scan_alias)}
                        FROM usage_apicalllog AS {scan_alias}
                        PREWHERE {cross_scope}
                          AND {scan_alias}.source_id = %({param_key})s
                          AND {scan_alias}.created_at >=
                              %(start_date)s - INTERVAL 1 DAY
                          AND {scan_alias}.created_at <
                              %(end_date)s + INTERVAL 1 DAY
                        ORDER BY {scan_alias}._peerdb_version DESC
                        LIMIT 1 BY {scan_alias}.id
                    ) AS {latest_alias}
                    WHERE {latest_alias}._peerdb_is_deleted = 0
                      AND {latest_alias}.deleted = 0
                      AND {latest_alias}.status = 'success'
                      AND {latest_alias}.eval_trace_id != ''
                    ORDER BY {latest_alias}.created_at DESC,
                             {latest_alias}.id DESC
                    LIMIT 1 BY {latest_alias}.eval_trace_id
                ) AS {ev_alias}
            """
            joins.append(
                f"LEFT JOIN {cross_source} "
                f"ON {ev_alias}.eval_trace_id = {_trace_id_expr} "
                f"AND {ev_alias}.source_id = %({param_key})s "
                f"{_join_scope.format(alias=ev_alias)} "
                f"AND {ev_alias}.status = 'success' "
                f"AND {ev_alias}._peerdb_is_deleted = 0 "
                f"AND {ev_alias}.deleted = 0"
            )

        join_str = "\n".join(joins)

        query = (
            f"SELECT {', '.join(select_parts)}\n"
            f"FROM {eval_source}\n"
            f"{join_str}\n"
            f"WHERE {' AND '.join(where_parts)}\n"
            f"GROUP BY {', '.join(group_parts)}\n"
            f"ORDER BY {', '.join(order_parts)}"
        )
        return query, params

    # ------------------------------------------------------------------
    # Annotation metric
    # ------------------------------------------------------------------

    def _build_annotation_metric_query(
        self,
        metric: dict,
        aggregation: str,
        bucket_fn: str,
        per_metric_filters: list[dict],
        params: dict,
    ) -> tuple[str, dict]:
        # The metric "name" is the annotation label UUID
        label_id = metric.get("label_id") or metric.get("name", "")
        params["annotation_label_id"] = label_id

        # model_hub_score stores the value as a JSON string.
        # The extraction depends on annotation type:
        output_type = (
            metric.get("output_type") or metric.get("outputType") or ""
        ).lower()
        # If output_type missing, look it up from PG
        if not output_type and label_id:
            try:
                from model_hub.models.develop_annotations import AnnotationsLabels

                lbl = (
                    AnnotationsLabels.objects.filter(id=label_id)
                    .values_list("type", flat=True)
                    .first()
                )
                if lbl:
                    output_type = lbl.lower()
            except Exception:
                pass
        if output_type in ("categorical", "choice"):
            # Categorical: count rows (each row = one annotation)
            agg_expr = "count()"
        elif output_type == "thumbs_up_down":
            # Stored as {"value": "up"|"down"} — percentage of "up".
            # countIf already skips NULL/missing rows, but we still want
            # the denominator to exclude rows where the key is absent.
            col_expr = "JSONExtract(a.value, 'value', 'Nullable(String)')"
            agg_expr = (
                f"countIf({col_expr} = 'up') * 100.0 / "
                f"greatest(countIf({col_expr} IS NOT NULL), 1)"
            )
        elif output_type == "text":
            # Text: just count annotations
            agg_expr = "count()"
        else:
            # Numeric/star: aggregate the float value, skipping NULLs so
            # missing/non-numeric payloads don't pull averages toward 0.
            col_expr = annotation_numeric_value_expr(alias="a", nullable=True)
            agg_expr = AGGREGATIONS.get(aggregation, "avg({col})").format(col=col_expr)

        select_parts = [f"{bucket_fn}(a.created_at) AS time_bucket"]
        group_parts = ["time_bucket"]
        order_parts = ["time_bucket"]
        select_parts.append(f"{agg_expr} AS value")

        # model_hub_score has no reliable tracer.Project foreign key for every
        # historical row. V1 retains the deployed trace dictionary; direct-write
        # V2 resolves the finite label/time candidate set through latest-live
        # ``traces`` rows so the production read-only role needs no dictionary
        # privilege. Span-attached scores continue to scope through ``spans``.
        if self.organization_id:
            params["annotation_organization_id"] = self.organization_id

        annotation_candidate_scope = ""
        if self.organization_id:
            annotation_candidate_scope = (
                "annotation_trace_candidate.organization_id = "
                "toUUID(%(annotation_organization_id)s) AND "
            )
        annotation_trace_candidates = f"""
                SELECT DISTINCT
                    annotation_trace_candidate.trace_id AS trace_id
                FROM model_hub_score AS annotation_trace_candidate
                PREWHERE {annotation_candidate_scope}
                  annotation_trace_candidate.label_id =
                      toUUID(%(annotation_label_id)s)
                  AND annotation_trace_candidate.created_at >= %(start_date)s
                  AND annotation_trace_candidate.created_at < %(end_date)s
                WHERE annotation_trace_candidate.trace_id IS NOT NULL
            """

        if self._direct_trace_project_scope_available:
            trace_projects = latest_live_trace_projects_sql(
                candidate_trace_ids_sql=annotation_trace_candidates
            )
            trace_project_join = (
                "LEFT JOIN ("
                f"{trace_projects}"
                ") AS annotation_trace_project "
                "ON annotation_trace_project.trace_id = a.trace_id"
            )
            trace_project_scope = (
                "(a.trace_id IS NOT NULL "
                "AND annotation_trace_project.trace_id = a.trace_id "
                "AND annotation_trace_project.project_id IN %(project_ids)s)"
            )
        else:
            trace_project_join = ""
            trace_project_scope = (
                "(a.trace_id IS NOT NULL "
                "AND dictGet('trace_dict', 'project_id', a.trace_id) "
                "IN %(project_ids)s)"
            )

        annotation_span_scope = ""
        if self.organization_id:
            annotation_span_scope = (
                "annotation_span_candidate.organization_id = "
                "toUUID(%(annotation_organization_id)s) AND "
            )
        annotation_span_candidates = f"""
            SELECT DISTINCT
                annotation_span_candidate.observation_span_id AS id
            FROM model_hub_score AS annotation_span_candidate
            PREWHERE {annotation_span_scope}
              annotation_span_candidate.label_id =
                  toUUID(%(annotation_label_id)s)
              AND annotation_span_candidate.created_at >= %(start_date)s
              AND annotation_span_candidate.created_at < %(end_date)s
            WHERE annotation_span_candidate.observation_span_id IS NOT NULL
              AND annotation_span_candidate.observation_span_id != ''
        """

        # Resolve observation-only score rows through a finite, project-scoped
        # latest-state span relation. The same bounded relation supplies the
        # subject trace ID used by dashboard filters, so observation-attached
        # annotations follow the same trace-filter semantics as trace-attached
        # rows without scanning every span in the project.
        annotation_span_projects = f"""
            SELECT
                annotation_span_latest.id AS id,
                tupleElement(annotation_span_latest.latest_state, 1) AS trace_id,
                tupleElement(annotation_span_latest.latest_state, 2) AS project_id
            FROM (
                SELECT
                    annotation_span_scan.id AS id,
                    uniqExact(
                        tuple(
                            annotation_span_scan.project_id,
                            annotation_span_scan.trace_id
                        )
                    ) AS identity_count,
                    argMax(
                        tuple(
                            annotation_span_scan.trace_id,
                            annotation_span_scan.project_id,
                            annotation_span_scan._peerdb_is_deleted
                        ),
                        annotation_span_scan._peerdb_version
                    ) AS latest_state
                FROM spans AS annotation_span_scan
                PREWHERE annotation_span_scan.project_id IN %(project_ids)s
                WHERE annotation_span_scan.id IN ({annotation_span_candidates})
                GROUP BY annotation_span_scan.id
            ) AS annotation_span_latest
            WHERE annotation_span_latest.identity_count = 1
              AND tupleElement(annotation_span_latest.latest_state, 3) = 0
        """
        annotation_span_join = (
            f"LEFT JOIN ({annotation_span_projects}) AS annotation_subject_span "
            "ON annotation_subject_span.id = a.observation_span_id"
        )
        annotation_subject_trace_id = (
            "if(a.trace_id IS NOT NULL, toString(a.trace_id), "
            "annotation_subject_span.trace_id)"
        )
        if self._direct_trace_project_scope_available:
            annotation_subject_project_id = (
                "if(a.trace_id IS NOT NULL, "
                "annotation_trace_project.project_id, "
                "annotation_subject_span.project_id)"
            )
        else:
            annotation_subject_project_id = (
                "if(a.trace_id IS NOT NULL, "
                "dictGet('trace_dict', 'project_id', a.trace_id), "
                "annotation_subject_span.project_id)"
            )
        annotation_subject_trace_candidates = f"""
            SELECT DISTINCT annotation_subject_candidate.trace_id AS trace_id
            FROM (
                SELECT toString(trace_id) AS trace_id
                FROM ({annotation_trace_candidates})
                UNION ALL
                SELECT trace_id
                FROM ({annotation_span_projects})
            ) AS annotation_subject_candidate
            WHERE annotation_subject_candidate.trace_id != ''
        """

        # Other source types (call_execution, dataset_row, …) are out of
        # scope for trace dashboards.
        where_parts = [
            (
                f"({trace_project_scope}"
                " OR "
                "(a.observation_span_id IS NOT NULL "
                "AND a.observation_span_id != '' "
                "AND annotation_subject_span.id = a.observation_span_id "
                "AND annotation_subject_span.project_id IN %(project_ids)s)"
                ")"
            ),
            "a.is_deleted = 0",
            "a.deleted = 0",
            "a.created_at >= %(start_date)s",
            "a.created_at < %(end_date)s",
            "a.label_id = toUUID(%(annotation_label_id)s)",
        ]

        if self.organization_id:
            where_parts.append(
                "a.organization_id = toUUID(%(annotation_organization_id)s)"
            )

        trace_filters = [
            item
            for item in self.global_filters + per_metric_filters
            if item.get("source", "traces") in ("traces", "", "all", "both")
        ]
        presence_filters = [
            item for item in trace_filters if self._is_presence_filter(item)
        ]
        span_filters = [
            item
            for item in trace_filters
            if (item.get("metric_type") or item.get("type"))
            in ("system_metric", "custom_attribute")
            and not self._is_presence_filter(item)
        ]
        membership_filters = [
            item
            for item in trace_filters
            if (item.get("metric_type") or item.get("type"))
            in ("eval_metric", "annotation_metric")
        ]
        if len(presence_filters) + len(span_filters) + len(membership_filters) != len(
            trace_filters
        ):
            raise InvalidMetricCombinationError(
                "Unsupported annotation dashboard trace filter"
            )

        where_parts.extend(
            self._build_presence_filter_predicates(
                presence_filters,
                outer_project_expr=annotation_subject_project_id,
                outer_trace_expr=annotation_subject_trace_id,
                params=params,
            )
        )

        joins = [trace_project_join, annotation_span_join]
        if span_filters:
            id_resolved_filters = [
                item
                for item in span_filters
                if (item.get("metric_type") or item.get("type")) == "system_metric"
                and (
                    item.get("metric_name") or item.get("name") or item.get("id", "")
                ).lower()
                in _ID_RESOLVED_NAMES
            ]
            if self._direct_end_users_available and id_resolved_filters:
                raise InvalidMetricCombinationError(
                    "Resolved user/session filters are not supported for annotation "
                    "dashboard metrics"
                )

            span_predicates = [
                "(s.parent_span_id IS NULL OR s.parent_span_id = '')",
                "s._peerdb_is_deleted = 0",
            ]

            for filter_index, item in enumerate(span_filters):
                filter_type = item.get("metric_type") or item.get("type", "")
                canonical_filter = item.get("canonical_filter")
                if filter_type == "custom_attribute" and isinstance(
                    canonical_filter, dict
                ):
                    predicate, predicate_params = compile_span_attribute_row_predicate(
                        canonical_filter,
                        index=filter_index,
                    )
                    span_predicates.append(
                        self._qualify_span_expression(predicate, alias="s")
                    )
                    params.update(predicate_params)
                    continue

                filter_name = (
                    item.get("metric_name") or item.get("name") or item.get("id", "")
                )
                operation = item.get("operator", "")
                if filter_type == "system_metric":
                    metric_key = filter_name.lower()
                    if metric_key not in SYSTEM_METRICS:
                        raise InvalidMetricCombinationError(
                            f"Unsupported annotation dashboard filter: {filter_name}"
                        )
                    string_expression = self._string_filter_column_expression(
                        metric_key
                    )
                    expression = self._qualify_span_expression(
                        string_expression or self._system_metric_expression(metric_key),
                        alias="s",
                    )
                    is_string_filter = string_expression is not None
                else:
                    attribute_key = _sanitize_attr_key(filter_name)
                    attribute_type = item.get("attribute_type", "string")
                    attribute_map = {
                        "number": "span_attr_num",
                        "boolean": "span_attr_bool",
                    }.get(attribute_type, "span_attr_str")
                    expression = f"s.{attribute_map}['{attribute_key}']"
                    is_string_filter = attribute_type not in ("number", "boolean")

                if operation in (
                    "is_set",
                    "is_not_set",
                    "is_numeric",
                    "is_not_numeric",
                ):
                    span_predicates.append(
                        f"{expression} {FILTER_OPERATORS[operation]}"
                    )
                    continue

                value = item.get("value")
                if operation in ("between", "not_between"):
                    if not isinstance(value, list) or len(value) != 2:
                        raise InvalidMetricCombinationError(
                            "Annotation dashboard range filters require two values"
                        )
                    low_key = f"_ann_span_filter_{filter_index}_low"
                    high_key = f"_ann_span_filter_{filter_index}_high"
                    negation = "NOT " if operation == "not_between" else ""
                    span_predicates.append(
                        f"{expression} {negation}BETWEEN %({low_key})s "
                        f"AND %({high_key})s"
                    )
                    coerce = (
                        _coerce_string_filter_value
                        if is_string_filter
                        else _coerce_filter_value
                    )
                    params[low_key] = coerce(value[0], "equal_to")
                    params[high_key] = coerce(value[1], "equal_to")
                    continue

                operator = _get_operator_symbol(operation)
                if not operator:
                    raise InvalidMetricCombinationError(
                        f"Unsupported annotation dashboard filter operation: {operation}"
                    )
                value_key = f"_ann_span_filter_{filter_index}_value"
                span_predicates.append(f"{expression} {operator} %({value_key})s")
                params[value_key] = (
                    _coerce_string_filter_value(value, operation)
                    if is_string_filter
                    else _coerce_filter_value(value, operation)
                )

            filtered_spans_source = self._annotation_filter_spans_source(
                span_filters,
                params,
            )
            filtered_trace_ids = f"""
                SELECT DISTINCT s.trace_id
                FROM {filtered_spans_source}
                PREWHERE s.project_id IN %(project_ids)s
                  AND s.trace_id IN ({annotation_subject_trace_candidates})
                WHERE {" AND ".join(span_predicates)}
            """
            where_parts.append(
                f"{annotation_subject_trace_id} IN ({filtered_trace_ids})"
            )

        if membership_filters:
            membership_clauses, membership_params = self._build_subquery_filters(
                membership_filters,
                params,
                "ann_metric_",
                trace_id_expr=annotation_subject_trace_id,
            )
            if len(membership_clauses) != len(membership_filters):
                raise InvalidMetricCombinationError(
                    "Unsupported annotation dashboard metric filter"
                )
            where_parts.extend(membership_clauses)
            params.update(membership_params)

        query = (
            f"SELECT {', '.join(select_parts)}\n"
            f"FROM model_hub_score AS a FINAL\n"
            f"{' '.join(join for join in joins if join)}\n"
            f"WHERE {' AND '.join(where_parts)}\n"
            f"GROUP BY {', '.join(group_parts)}\n"
            f"ORDER BY {', '.join(order_parts)}"
        )
        return query, params

    # ------------------------------------------------------------------
    # Custom attribute metric
    # ------------------------------------------------------------------

    def _build_custom_attr_query(
        self,
        metric: dict,
        aggregation: str,
        bucket_fn: str,
        per_metric_filters: list[dict],
        params: dict,
    ) -> tuple[str, dict]:
        attr_key = _sanitize_attr_key(metric.get("attribute_key", ""))
        attr_type = metric.get("attribute_type", "number")
        attr_key_param = "custom_metric_attr_key"
        params[attr_key_param] = attr_key

        if attr_type == "number":
            attr_map = "span_attr_num"
            col_expr = f"{attr_map}[%({attr_key_param})s]"
        elif attr_type in ("string", "text"):
            if aggregation in DASHBOARD_NUMERIC_ONLY_AGGREGATIONS:
                raise InvalidMetricCombinationError(
                    f"'{aggregation}' can't be applied to the text attribute "
                    f"'{attr_key}'. Use count or count distinct, or pick a "
                    f"numeric attribute."
                )
            attr_map = "span_attr_str"
            col_expr = f"{attr_map}[%({attr_key_param})s]"
        elif attr_type == "boolean":
            if aggregation in DASHBOARD_NUMERIC_ONLY_AGGREGATIONS:
                raise InvalidMetricCombinationError(
                    f"'{aggregation}' can't be applied to the boolean attribute "
                    f"'{attr_key}'. Use count or count distinct."
                )
            attr_map = "span_attr_bool"
            col_expr = f"{attr_map}[%({attr_key_param})s]"
        else:
            raise InvalidMetricCombinationError(
                "Structured array/map attributes can be filtered, but cannot be "
                "used as dashboard metric values."
            )

        agg_expr = AGGREGATIONS.get(aggregation, "avg({col})").format(col=col_expr)

        select_parts = [f"{bucket_fn}(start_time) AS time_bucket"]
        group_parts = ["time_bucket"]
        order_parts = ["time_bucket"]

        breakdown_infos = self._resolve_all_breakdowns(params)
        if any(breakdown["type"] == "annotation" for breakdown in breakdown_infos):
            raise InvalidMetricCombinationError(
                "Annotation breakdowns are not supported for custom-attribute "
                "dashboard metrics."
            )
        breakdown_expr = None
        if breakdown_infos:
            first_breakdown = breakdown_infos[0]
            breakdown_expr = (
                "__ANNOTATION_BREAKDOWN__"
                if first_breakdown["type"] == "annotation"
                else first_breakdown["expr"]
            )
        if breakdown_expr:
            select_parts.append(f"{breakdown_expr} AS breakdown_value")
            group_parts.append("breakdown_value")
            order_parts.append("breakdown_value")

        select_parts.append(f"{agg_expr} AS value")

        where_clauses, params = self._build_where_clauses(
            "spans", "start_time", per_metric_filters, params
        )
        # Missing Map keys are not metric observations. Besides preventing a
        # default empty string/zero from entering count/avg results, this lets
        # ClickHouse use the deployed typed-Map key bloom index instead of
        # deserializing the value Map for every span in the window.
        where_clauses.append(f"mapContains({attr_map}, %({attr_key_param})s)")
        where_clauses.extend(
            breakdown["presence_predicate"]
            for breakdown in breakdown_infos
            if breakdown.get("presence_predicate")
        )

        subquery_clauses = self._build_subquery_filters(
            self.global_filters + per_metric_filters, params, "ca_"
        )
        params.update(subquery_clauses[1])

        all_where = where_clauses
        if subquery_clauses[0]:
            all_where += subquery_clauses[0]
        all_where.extend(
            self._build_presence_filter_predicates(
                self.global_filters + per_metric_filters,
                outer_project_expr="spans.project_id",
                outer_trace_expr="spans.trace_id",
                params=params,
            )
        )

        spans_flat = self._spans_source(
            None,
            per_metric_filters,
            "spans",
            params=params,
        )

        query = (
            f"SELECT {', '.join(select_parts)}\n"
            f"FROM {spans_flat}\n"
            f"WHERE {' AND '.join(all_where)}\n"
            f"GROUP BY {', '.join(group_parts)}\n"
            f"ORDER BY {', '.join(order_parts)}"
        )
        return query, params

    # ------------------------------------------------------------------
    # Build all queries
    # ------------------------------------------------------------------

    def build_all_queries(self) -> list[tuple[str, dict, dict]]:
        """Build queries for all metrics.

        Returns:
            List of (sql, params, metric_info) tuples.
        """
        results = []
        for metric in self.metrics:
            sql, params = self.build_metric_query(metric)
            results.append((sql, params, self.metric_info(metric)))
        return results

    def metric_info(self, metric: dict) -> dict:
        """Build the response metadata for a single metric.

        Exposed so callers can construct the metric's ``metric_info`` without
        building its SQL — e.g. to attach a per-metric error when the build or
        execution fails, keeping the rest of the dashboard's widgets intact.
        """
        return {
            "id": metric.get("id", ""),
            "name": metric.get("display_name")
            or metric.get("displayName")
            or metric.get("name", ""),
            "type": metric.get("type", "system_metric"),
            "aggregation": metric.get("aggregation", "avg"),
        }

    # ------------------------------------------------------------------
    # Result formatting
    # ------------------------------------------------------------------

    def format_results(
        self,
        metric_results: list[tuple[dict, list[dict]]],
        project_name_map: dict[str, str] | None = None,
    ) -> dict:
        """Format raw ClickHouse results into the response format.

        Args:
            metric_results: List of (metric_info, rows) tuples where rows
                are dicts with ``time_bucket``, ``value``, and optionally
                ``breakdown_value`` keys.
            project_name_map: Optional mapping of project UUID strings to
                human-readable project names.

        Returns:
            Response dict with ``metrics``, ``time_range``, and ``granularity``.
        """
        start_date, end_date = self.parse_time_range()
        all_buckets = _generate_time_buckets(start_date, end_date, self.granularity)
        formatted_metrics = []

        # Check if any breakdown is by project (needs UUID→name resolution)
        has_project_breakdown = any(
            bd.get("name") == "project" for bd in self.breakdowns
        )

        for metric_info, rows in metric_results:
            metric_name = metric_info.get("name", "")
            metric_id = metric_info.get("id", "")
            unit = METRIC_UNITS.get(metric_name) or METRIC_UNITS.get(metric_id, "")

            # Group rows by breakdown value if present
            # Use a dict of {iso_timestamp: value} for easy merging
            series_data: dict[str, dict[str, Any]] = {}
            for row in rows:
                breakdown_key = str(row.get("breakdown_value", "total"))
                # Resolve project UUID to name if breaking down by project
                if has_project_breakdown and project_name_map:
                    if " / " in breakdown_key:
                        # Multi-breakdown: resolve each segment independently
                        parts = breakdown_key.split(" / ")
                        parts = [project_name_map.get(p, p) for p in parts]
                        breakdown_key = " / ".join(parts)
                    else:
                        breakdown_key = project_name_map.get(
                            breakdown_key, breakdown_key
                        )
                if breakdown_key not in series_data:
                    series_data[breakdown_key] = {}
                ts = row.get("time_bucket", "")
                if hasattr(ts, "isoformat"):
                    # CH may return date or naive datetime; convert to
                    # timezone-aware datetime so keys match _generate_time_buckets
                    if isinstance(ts, date) and not isinstance(ts, datetime):
                        ts = datetime(ts.year, ts.month, ts.day, tzinfo=UTC)
                    elif hasattr(ts, "tzinfo") and ts.tzinfo is None:
                        ts = ts.replace(tzinfo=UTC)
                    ts = ts.isoformat()
                val = row.get("value")
                if isinstance(val, float):
                    val = round(val, 6)
                series_data[breakdown_key][ts] = val

            if not series_data:
                series_data["total"] = {}

            # Keep the highest-volume series first; the frontend still limits
            # the initially visible chart series.
            MAX_SERIES = 100
            if "total" not in series_data:
                ranked = sorted(
                    series_data.items(),
                    key=lambda kv: sum(v for v in kv[1].values() if v is not None),
                    reverse=True,
                )
                if len(ranked) > MAX_SERIES:
                    ranked = ranked[:MAX_SERIES]
                series_data = dict(ranked)

            # Preserve volume order from ``series_data``.
            series = []
            for name, data_map in series_data.items():
                filled = []
                for bucket_ts in all_buckets:
                    filled.append(
                        {
                            "timestamp": bucket_ts,
                            "value": data_map[bucket_ts]
                            if bucket_ts in data_map
                            else None,
                        }
                    )
                series.append({"name": name, "data": filled})

            formatted_metric = {
                "id": metric_info.get("id", ""),
                "name": metric_name,
                "aggregation": metric_info.get("aggregation", "avg"),
                "unit": unit,
                "series": series,
            }
            for metadata_field in DASHBOARD_QUERY_METADATA_FIELDS:
                if metadata_field in metric_info:
                    formatted_metric[metadata_field] = metric_info[metadata_field]
            if metric_info.get("error"):
                formatted_metric["error"] = metric_info["error"]
            formatted_metrics.append(formatted_metric)

        return {
            "metrics": formatted_metrics,
            "time_range": {
                "start": start_date.isoformat(),
                "end": end_date.isoformat(),
            },
            "granularity": self.granularity,
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    # System metric breakdown column map
    _BREAKDOWN_COL_MAP = {
        "project": "toString(project_id)",
        "model": "model",
        "status": "status",
        "service_name": "service_name",
        "span_kind": "observation_type",
        "provider": "provider",
        "session": "toString(trace_session_id)",
        "user": "dictGetOrDefault('end_users_dict', 'user_id', end_user_id, toString(end_user_id))",
        "user_id_type": "dictGetOrDefault('end_users_dict', 'user_id_type', end_user_id, '')",
        "prompt_name": "dictGetOrDefault('prompt_dict', 'prompt_name', ifNull(prompt_version_id, toUUID('00000000-0000-0000-0000-000000000000')), '')",
        "prompt_version": "concat(dictGetOrDefault('prompt_dict', 'prompt_name', ifNull(prompt_version_id, toUUID('00000000-0000-0000-0000-000000000000')), ''), ' v', dictGetOrDefault('prompt_dict', 'template_version', ifNull(prompt_version_id, toUUID('00000000-0000-0000-0000-000000000000')), ''))",
        "prompt_label": "dictGetOrDefault('prompt_label_dict', 'name', ifNull(prompt_label_id, toUUID('00000000-0000-0000-0000-000000000000')), '')",
        "tag": "arrayJoin(JSONExtract(tags, 'Array(String)'))",
    }

    _STRING_FILTER_COL = {
        "project": "toString(project_id)",
        "trace_count": "trace_id",
        "span_count": "id",
        "session_count": (
            "toString(nullIf(trace_session_id, "
            "toUUID('00000000-0000-0000-0000-000000000000')))"
        ),
        "user_count": SYSTEM_METRICS["user_count"][1],
        "status": "status",
        "model": "model",
        "service_name": "service_name",
        "span_kind": "observation_type",
        "provider": "provider",
        "session": "toString(trace_session_id)",
        "user": "dictGetOrDefault('end_users_dict', 'user_id', end_user_id, toString(end_user_id))",
        "user_id_type": "dictGetOrDefault('end_users_dict', 'user_id_type', end_user_id, '')",
        "prompt_name": "dictGetOrDefault('prompt_dict', 'prompt_name', ifNull(prompt_version_id, toUUID('00000000-0000-0000-0000-000000000000')), '')",
        "prompt_version": "concat(dictGetOrDefault('prompt_dict', 'prompt_name', ifNull(prompt_version_id, toUUID('00000000-0000-0000-0000-000000000000')), ''), ' v', dictGetOrDefault('prompt_dict', 'template_version', ifNull(prompt_version_id, toUUID('00000000-0000-0000-0000-000000000000')), ''))",
        "prompt_label": "dictGetOrDefault('prompt_label_dict', 'name', ifNull(prompt_label_id, toUUID('00000000-0000-0000-0000-000000000000')), '')",
        "tag": "arrayJoin(JSONExtract(tags, 'Array(String)'))",
    }

    def _resolve_all_breakdowns(self, params: dict):
        """Resolve all breakdowns into a list of {type, expr, join_clause} dicts.

        For system/custom_attribute breakdowns: expr is a column expression on spans.
        For annotation breakdowns: expr is a value extraction + join_clause for LEFT JOIN.

        Returns:
            List of breakdown info dicts. Empty list if no breakdowns.
        """
        result = []
        ann_idx = 0
        custom_attr_idx = 0
        for bd in self.breakdowns:
            bd_type = bd.get("type", "system_metric")
            bd_name = bd.get("name", "")
            bd_source = bd.get("source", "traces")

            # source="all" can still be a trace-side dimension.
            if bd_source in ("datasets", "simulation"):
                continue

            if bd_type == "system_metric":
                breakdown_expr = self._breakdown_column_expression(bd_name)
                if breakdown_expr is not None:
                    result.append(
                        {
                            "type": "column",
                            "expr": breakdown_expr,
                            "join": None,
                        }
                    )
                elif bd_name in SYSTEM_METRICS:
                    col_expr = self._system_metric_expression(bd_name)
                    result.append({"type": "column", "expr": col_expr, "join": None})
                else:
                    self._reject_unknown_cataloged_system_dimension(
                        bd,
                        bd_name,
                        role="breakdown",
                    )

            elif bd_type == "custom_attribute":
                safe_name = _sanitize_attr_key(bd_name)
                attr_type = bd.get("attribute_type", "string")
                if attr_type == "number":
                    attr_map = "span_attr_num"
                elif attr_type == "boolean":
                    attr_map = "span_attr_bool"
                elif attr_type in ("string", "text"):
                    attr_map = "span_attr_str"
                else:
                    raise InvalidMetricCombinationError(
                        "Structured array/map attributes cannot be used as a "
                        "dashboard breakdown."
                    )
                param_key = f"_custom_bd_key_{custom_attr_idx}"
                custom_attr_idx += 1
                params[param_key] = safe_name
                expr = f"{attr_map}[%({param_key})s]"
                result.append(
                    {
                        "type": "column",
                        "expr": expr,
                        "join": None,
                        "presence_predicate": (
                            f"mapContains({attr_map}, %({param_key})s)"
                        ),
                    }
                )

            elif bd_type == "annotation_metric":
                label_id = bd.get("label_id") or bd_name
                output_type = (
                    bd.get("output_type") or bd.get("outputType") or ""
                ).lower()
                if not output_type and label_id:
                    try:
                        from model_hub.models.develop_annotations import (
                            AnnotationsLabels,
                        )

                        lbl = (
                            AnnotationsLabels.objects.filter(id=label_id)
                            .values_list("type", flat=True)
                            .first()
                        )
                        if lbl:
                            output_type = lbl.lower()
                    except Exception:
                        pass

                alias = f"ann{ann_idx}"
                param_key = f"_ann_bd_label_{ann_idx}"
                params[param_key] = label_id
                ann_idx += 1

                # ``id IS NULL`` distinguishes "no annotation row matched
                # the LEFT JOIN" from "row exists but value JSON is
                # missing the key" (which would otherwise extract as 0
                # / empty and silently bucket alongside real values).
                missing_check = f"{alias}.id IS NULL"
                if output_type in ("categorical", "choice"):
                    val_expr = (
                        f"arrayJoin(if({missing_check}, ['(not set)'], "
                        f"JSONExtract({alias}.value, 'selected', 'Array(String)')))"
                    )
                elif output_type == "thumbs_up_down":
                    val_expr = (
                        f"if({missing_check}, '(not set)', "
                        f"JSONExtractString({alias}.value, 'value'))"
                    )
                elif output_type == "text":
                    val_expr = (
                        f"if({missing_check}, '(not set)', "
                        f"JSONExtractString({alias}.value, 'text'))"
                    )
                else:
                    nullable_num = annotation_numeric_value_expr(
                        alias=alias, nullable=True
                    )
                    rounding = ", 1" if output_type in ("numeric", "star") else ""
                    val_expr = (
                        f"if({missing_check} OR {nullable_num} IS NULL, "
                        f"'(not set)', toString(round({nullable_num}{rounding})))"
                    )

                join_clause = (
                    f"LEFT JOIN model_hub_score AS {alias} "
                    f"ON toString({alias}.trace_id) = s.trace_id "
                    f"AND {alias}.label_id = toUUID(%({param_key})s) "
                    f"AND {alias}._peerdb_is_deleted = 0 "
                    f"AND {alias}.deleted = 0"
                )
                result.append(
                    {"type": "annotation", "expr": val_expr, "join": join_clause}
                )

            elif bd_type == "eval_metric":
                eval_template_id = bd.get("config_id") or bd.get("label_id") or bd_name
                eval_template_id = self._resolve_eval_template_identity(
                    bd, eval_template_id
                )
                output_type = (
                    bd.get("output_type") or bd.get("outputType") or ""
                ).upper()
                # Auto-detect output type from PG if missing
                if not output_type and eval_template_id:
                    try:
                        from model_hub.models.evals_metric import EvalTemplate

                        et = EvalTemplate.objects.filter(id=eval_template_id).first()
                        if et:
                            output_type = (
                                (et.config or {})
                                .get("output", "SCORE")
                                .upper()
                                .replace("/", "_")
                            )
                    except Exception:
                        pass

                alias = f"ev{ann_idx}"
                param_key = f"_ev_bd_cfg_{ann_idx}"
                params[param_key] = eval_template_id
                ann_idx += 1

                # Use materialized columns for fast extraction
                if output_type == "PASS_FAIL":
                    label_expr = eval_pass_fail_label_expr(
                        f"{alias}.eval_score", f"{alias}.eval_output_str"
                    )
                    val_expr = f"if({alias}.id IS NULL, '(not set)', {label_expr})"
                elif output_type in ("CHOICE", "CHOICES"):
                    val_expr = (
                        f"if({alias}.id IS NULL, '(not set)', {alias}.eval_output_str)"
                    )
                else:
                    # SCORE: show as percentage
                    val_expr = (
                        f"if({alias}.id IS NULL, '(not set)', "
                        f"toString(round({alias}.eval_score * 100)))"
                    )

                join_clause = (
                    f"LEFT JOIN usage_apicalllog AS {alias} FINAL "
                    f"ON {alias}.eval_trace_id = s.trace_id "
                    f"AND {alias}.source_id = %({param_key})s "
                    f"AND {alias}.status = 'success' "
                    f"AND {alias}._peerdb_is_deleted = 0"
                )
                result.append(
                    {"type": "annotation", "expr": val_expr, "join": join_clause}
                )

        return result

    def _breakdown_select(self, params: dict | None = None) -> str | None:
        """Return the SQL expression for the first breakdown, or None.
        Kept for backward compat — delegates to _resolve_all_breakdowns for single breakdown.
        """
        if not self.breakdowns:
            return None
        # For single-breakdown compat, just check first
        breakdowns = self._resolve_all_breakdowns(params if params is not None else {})
        if not breakdowns:
            return None
        bd = breakdowns[0]
        if bd["type"] == "annotation":
            return "__ANNOTATION_BREAKDOWN__"
        return bd["expr"]

    def _build_where_clauses(
        self,
        table: str,
        time_col: str,
        per_metric_filters: list[dict],
        params: dict,
    ) -> tuple[list[str], dict]:
        """Build base WHERE clauses for spans-based queries."""
        clauses = [
            "project_id IN %(project_ids)s",
            "_peerdb_is_deleted = 0",
            f"{time_col} >= %(start_date)s",
            f"{time_col} < %(end_date)s",
        ]

        # spans is partitioned by toYYYYMM(created_at) but the window is
        # filtered on start_time, so bound created_at too — otherwise no
        # partitions prune and the scan covers all history. Lower bound only
        # (created_at >= start_time always holds), so no in-window row drops.
        if time_col != "created_at" and self._spans_partitioned_by_created_at:
            clauses.append("created_at >= %(start_date)s - INTERVAL 1 DAY")

        # Apply global + per-metric system_metric filters directly
        # For string-comparable system metrics, use toString() to avoid UUID parse errors
        all_filters = self.global_filters + per_metric_filters
        # Skip filters that belong to other sources (e.g. simulation filters in trace queries)
        all_filters = [
            f for f in all_filters if f.get("source", "traces") in ("traces", "")
        ]
        idx = 0
        for f in all_filters:
            f_type = f.get("metric_type", "")
            if f_type == "system_metric":
                f_name = (f.get("metric_name", "") or "").lower()
                if f_name in PRESENCE_SYSTEM_METRIC_FILTERS:
                    # Relational pseudo-columns are compiled by the caller
                    # once its concrete outer project/trace aliases are known.
                    continue
                op = f.get("operator", "")
                val = f.get("value")
                # Use string-safe column for non-numeric metrics
                string_filter_col = self._string_filter_column_expression(f_name)
                is_string_filter = string_filter_col is not None
                if string_filter_col is not None:
                    col = string_filter_col
                elif f_name in SYSTEM_METRICS:
                    col = self._system_metric_expression(f_name)
                else:
                    self._reject_unknown_cataloged_system_dimension(
                        f,
                        f_name,
                        role="filter",
                    )
                    # Unknown filter metric — skip to prevent SQL injection
                    logger.warning("Skipping unknown filter metric: %s", f_name)
                    continue

                # No-value operators
                if op in ("is_set", "is_not_set", "is_numeric", "is_not_numeric"):
                    op_tpl = FILTER_OPERATORS.get(op)
                    if op_tpl:
                        clauses.append(f"{col} {op_tpl}")
                    continue

                # Skip filters with empty values
                if val is None or val == "" or val == []:
                    continue

                # Between operators need two params
                if op in ("between", "not_between"):
                    if isinstance(val, list) and len(val) == 2:
                        lo_key = f"f_{idx}_lo"
                        hi_key = f"f_{idx}_hi"
                        coerce = (
                            _coerce_string_filter_value
                            if is_string_filter
                            else _coerce_filter_value
                        )
                        params[lo_key] = coerce(val[0], "equal_to")
                        params[hi_key] = coerce(val[1], "equal_to")
                        neg = "NOT " if op == "not_between" else ""
                        clauses.append(
                            f"{col} {neg}BETWEEN %({lo_key})s AND %({hi_key})s"
                        )
                        idx += 1
                    continue

                op_tpl = FILTER_OPERATORS.get(op)
                if op_tpl:
                    param_key = f"f_{idx}_val"
                    clause = f"{col} {op_tpl.format(prefix='f_', idx=idx)}"
                    params[param_key] = (
                        _coerce_string_filter_value(val, op)
                        if is_string_filter
                        else _coerce_filter_value(val, op)
                    )
                    clauses.append(clause)
                    idx += 1
            elif f_type == "custom_attribute":
                canonical_filter = f.get("canonical_filter")
                if isinstance(canonical_filter, dict):
                    predicate, predicate_params = compile_span_attribute_row_predicate(
                        canonical_filter,
                        index=idx,
                    )
                    clauses.append(predicate)
                    params.update(predicate_params)
                    idx += 1
                    continue

                f_name = _sanitize_attr_key(f.get("metric_name", ""))
                op = f.get("operator", "")
                val = f.get("value")
                attr_type = f.get("attribute_type", "string")
                if attr_type == "number":
                    col = f"span_attr_num['{f_name}']"
                elif attr_type == "boolean":
                    col = f"span_attr_bool['{f_name}']"
                else:
                    col = f"span_attr_str['{f_name}']"

                if op in ("is_set", "is_not_set", "is_numeric", "is_not_numeric"):
                    op_tpl = FILTER_OPERATORS.get(op)
                    if op_tpl:
                        clauses.append(f"{col} {op_tpl}")
                    continue

                if val is None or val == "" or val == []:
                    continue

                if op in ("between", "not_between"):
                    if isinstance(val, list) and len(val) == 2:
                        lo_key = f"f_{idx}_lo"
                        hi_key = f"f_{idx}_hi"
                        coerce = (
                            _coerce_string_filter_value
                            if attr_type not in ("number", "boolean")
                            else _coerce_filter_value
                        )
                        params[lo_key] = coerce(val[0], "equal_to")
                        params[hi_key] = coerce(val[1], "equal_to")
                        neg = "NOT " if op == "not_between" else ""
                        clauses.append(
                            f"{col} {neg}BETWEEN %({lo_key})s AND %({hi_key})s"
                        )
                        idx += 1
                    continue

                op_tpl = FILTER_OPERATORS.get(op)
                if op_tpl:
                    param_key = f"f_{idx}_val"
                    clause = f"{col} {op_tpl.format(prefix='f_', idx=idx)}"
                    params[param_key] = (
                        _coerce_string_filter_value(val, op)
                        if attr_type not in ("number", "boolean")
                        else _coerce_filter_value(val, op)
                    )
                    clauses.append(clause)
                    idx += 1

        return clauses, params

    def _build_subquery_filters(
        self,
        filters: list[dict],
        params: dict,
        prefix: str,
        trace_id_expr: str = "trace_id",
    ) -> tuple[list[str], dict]:
        """Build IN-subquery clauses for eval/annotation metric filters on spans."""
        clauses: list[str] = []
        extra_params: dict[str, Any] = {}
        idx = 0

        for f in filters:
            f_type = f.get("metric_type", "")
            op = f.get("operator", "")
            val = f.get("value")
            op_symbol = _get_operator_symbol(op)
            if not op_symbol:
                continue

            val_key = f"{prefix}{idx}_val"

            if f_type == "eval_metric":
                eval_id_key = f"{prefix}eval_id_{idx}"
                eval_template_id = f.get("metric_name", "")

                eval_template_id = self._resolve_eval_template_identity(
                    f, eval_template_id
                )

                output_type = (f.get("output_type") or "SCORE").upper()
                scope_key = f"{prefix}scope_id_{idx}"
                scan_alias = f"usage_{prefix}eval_filter_scan_{idx}"
                latest_alias = f"usage_{prefix}eval_filter_latest_{idx}"
                if self.workspace_id:
                    _sub_scope = f"{scan_alias}.workspace_id = toUUID(%({scope_key})s)"
                    _sub_scope_val = self.workspace_id
                else:
                    _sub_scope = (
                        f"{scan_alias}.organization_id = toUUID(%({scope_key})s)"
                    )
                    _sub_scope_val = self.organization_id

                # ``usage_apicalllog FINAL`` used to merge the tenant's full
                # history before applying this filter. Restrict the physical
                # read to the immutable tenant/template/time dimensions, then
                # collapse row versions. Live/status predicates deliberately
                # stay outside LIMIT 1 BY so a tombstone cannot resurrect an
                # older successful version.
                if output_type == "PASS_FAIL":
                    # Use the same score-or-text predicate as the time-series
                    # and breakdown paths, but retain this branch's bounded
                    # tenant/template/time latest-row subquery. The columns
                    # are unqualified inside the single derived table so the
                    # shared expression remains byte-identical everywhere.
                    # Filter values exposed by the dashboard API are the
                    # canonical labels ``Passed`` / ``Failed``.  Keep the
                    # shared truth predicate, but compare its result as the
                    # same String domain so ClickHouse never receives a
                    # Float64-vs-String predicate.  Numeric 1/0 values from
                    # older saved widgets are normalized by the helper too.
                    eval_col = eval_pass_fail_label_expr(
                        "eval_score", "eval_output_str"
                    )
                    filter_value = _coerce_pass_fail_filter_value(val, op)
                elif output_type in ("CHOICE", "CHOICES"):
                    eval_col = f"{latest_alias}.eval_output_str"
                    filter_value = _coerce_string_filter_value(val, op)
                elif output_type == "SCORE":
                    eval_col = f"{latest_alias}.eval_score"
                    filter_value = _coerce_filter_value(val, op)
                else:
                    raise InvalidMetricCombinationError(
                        f"Unsupported eval filter output type: {output_type}"
                    )

                subquery = f"""{trace_id_expr} IN (
                    SELECT {latest_alias}.eval_trace_id
                    FROM (
                        SELECT {_usage_eval_latest_projection(scan_alias)}
                        FROM usage_apicalllog AS {scan_alias}
                        PREWHERE {_sub_scope}
                          AND {scan_alias}.source_id = %({eval_id_key})s
                          AND {scan_alias}.created_at >= %(start_date)s
                          AND {scan_alias}.created_at < %(end_date)s
                        ORDER BY {scan_alias}._peerdb_version DESC
                        LIMIT 1 BY {scan_alias}.id
                    ) AS {latest_alias}
                    WHERE {latest_alias}._peerdb_is_deleted = 0
                      AND {latest_alias}.deleted = 0
                      AND {latest_alias}.status = 'success'
                      AND {latest_alias}.eval_trace_id != ''
                      AND {eval_col} {op_symbol} %({val_key})s
                )"""
                clauses.append(subquery)
                extra_params[eval_id_key] = eval_template_id
                extra_params[scope_key] = _sub_scope_val
                extra_params[val_key] = filter_value
                idx += 1

            elif f_type == "annotation_metric":
                label_id_key = f"{prefix}label_id_{idx}"
                label_id = f.get("metric_name", "")
                ann_org_key = f"{prefix}ann_org_id_{idx}"
                annotation_alias = f"annotation_{prefix}filter_{idx}"
                output_type = (f.get("output_type") or "numeric").lower()
                if output_type in ("numeric", "number", "score", "star", "rating"):
                    filter_expr = annotation_numeric_value_expr(
                        alias=annotation_alias, nullable=True
                    )
                    filter_value = _coerce_filter_value(val, op)
                    filter_condition = (
                        f"{filter_expr} IS NOT NULL AND "
                        f"{filter_expr} {op_symbol} %({val_key})s"
                    )
                elif output_type in ("categorical", "choice", "choices"):
                    selected_expr = (
                        f"JSONExtract({annotation_alias}.value, 'selected', "
                        "'Array(String)')"
                    )
                    if op in (
                        "equal_to",
                        "not_equal_to",
                        "str_contains",
                        "str_not_contains",
                    ):
                        negation = (
                            "NOT " if op in ("not_equal_to", "str_not_contains") else ""
                        )
                        filter_condition = (
                            f"notEmpty({selected_expr}) AND "
                            f"{negation}has({selected_expr}, %({val_key})s)"
                        )
                        # Categorical membership uses has(Array(String),
                        # String); even the legacy str_contains spelling is
                        # exact array membership rather than SQL LIKE.
                        filter_value = str(val)
                    elif op in ("contains", "not_contains"):
                        negation = "NOT " if op == "not_contains" else ""
                        filter_condition = (
                            f"notEmpty({selected_expr}) AND "
                            f"{negation}hasAny({selected_expr}, %({val_key})s)"
                        )
                        filter_value = _coerce_string_filter_value(val, op)
                    else:
                        raise InvalidMetricCombinationError(
                            f"Unsupported categorical annotation filter operation: {op}"
                        )
                elif output_type in ("thumbs_up_down", "text", "string"):
                    json_key = "value" if output_type == "thumbs_up_down" else "text"
                    filter_expr = (
                        f"JSONExtract({annotation_alias}.value, '{json_key}', "
                        "'Nullable(String)')"
                    )
                    filter_value = _coerce_string_filter_value(val, op)
                    if output_type == "thumbs_up_down":
                        thumb_tokens = {
                            "thumbs up": "up",
                            "thumbs down": "down",
                            "thumbs_up": "up",
                            "thumbs_down": "down",
                            "up": "up",
                            "down": "down",
                            "true": "up",
                            "false": "down",
                        }

                        if isinstance(filter_value, list):
                            normalized_values = []
                            for thumb_value in filter_value:
                                if isinstance(thumb_value, bool):
                                    thumb_value = "up" if thumb_value else "down"
                                elif isinstance(thumb_value, str):
                                    thumb_value = thumb_tokens.get(
                                        thumb_value.strip().lower(), thumb_value
                                    )
                                normalized_values.append(thumb_value)
                            filter_value = normalized_values
                        elif isinstance(filter_value, bool):
                            filter_value = "up" if filter_value else "down"
                        elif isinstance(filter_value, str):
                            filter_value = thumb_tokens.get(
                                filter_value.strip().lower(), filter_value
                            )
                    filter_condition = (
                        f"{filter_expr} IS NOT NULL AND "
                        f"{filter_expr} {op_symbol} %({val_key})s"
                    )
                else:
                    raise InvalidMetricCombinationError(
                        f"Unsupported annotation filter output type: {output_type}"
                    )
                # Keep FINAL for score-table latest/tombstone semantics and
                # bound the candidate set before JSON extraction. Annotation
                # rows may attach directly to a trace or only to an observation
                # span; resolve the latter through a project-scoped, unique,
                # latest-live span identity and union trace IDs without fanout.
                filtered_annotations = f"""
                    SELECT
                        {annotation_alias}.trace_id AS trace_id,
                        {annotation_alias}.observation_span_id AS observation_span_id
                    FROM model_hub_score AS {annotation_alias} FINAL
                    PREWHERE {annotation_alias}.label_id =
                                 toUUID(%({label_id_key})s)
                      AND {annotation_alias}.organization_id =
                          toUUID(%({ann_org_key})s)
                      AND {annotation_alias}.created_at >= %(start_date)s
                      AND {annotation_alias}.created_at < %(end_date)s
                    WHERE {annotation_alias}._peerdb_is_deleted = 0
                      AND {annotation_alias}.deleted = 0
                      AND {filter_condition}
                """
                span_alias = f"annotation_{prefix}span_filter_scan_{idx}"
                latest_span_alias = f"annotation_{prefix}span_filter_latest_{idx}"
                candidate_alias = f"annotation_{prefix}candidates_{idx}"
                direct_trace_candidates = f"""
                    SELECT DISTINCT direct_candidate.trace_id AS trace_id
                    FROM {candidate_alias} AS direct_candidate
                    WHERE direct_candidate.trace_id IS NOT NULL
                """
                if self._direct_trace_project_scope_available:
                    direct_trace_projects = latest_live_trace_projects_sql(
                        candidate_trace_ids_sql=direct_trace_candidates
                    )
                    direct_trace_branch = f"""
                        SELECT toString(project_scoped_trace.trace_id) AS trace_id
                        FROM ({direct_trace_projects}) AS project_scoped_trace
                    """
                else:
                    direct_trace_branch = f"""
                        SELECT toString(direct_annotation.trace_id) AS trace_id
                        FROM {candidate_alias} AS direct_annotation
                        WHERE direct_annotation.trace_id IS NOT NULL
                          AND dictGet(
                                  'trace_dict',
                                  'project_id',
                                  direct_annotation.trace_id
                              ) IN %(project_ids)s
                    """
                subquery = f"""{trace_id_expr} IN (
                    WITH {candidate_alias} AS ({filtered_annotations})
                    SELECT DISTINCT annotation_membership.trace_id
                    FROM (
                        {direct_trace_branch}
                        UNION ALL
                        SELECT tupleElement(
                                   {latest_span_alias}.latest_state, 1
                               ) AS trace_id
                        FROM (
                            SELECT
                                {span_alias}.id AS id,
                                uniqExact(
                                    tuple(
                                        {span_alias}.project_id,
                                        {span_alias}.trace_id
                                    )
                                ) AS identity_count,
                                argMax(
                                    tuple(
                                        {span_alias}.trace_id,
                                        {span_alias}._peerdb_is_deleted
                                    ),
                                    {span_alias}._peerdb_version
                                ) AS latest_state
                            FROM spans AS {span_alias}
                            PREWHERE {span_alias}.project_id IN %(project_ids)s
                            WHERE {span_alias}.id IN (
                                SELECT observation_span_id
                                FROM {candidate_alias}
                                WHERE observation_span_id IS NOT NULL
                                  AND observation_span_id != ''
                            )
                            GROUP BY {span_alias}.id
                        ) AS {latest_span_alias}
                        WHERE {latest_span_alias}.identity_count = 1
                          AND tupleElement(
                                  {latest_span_alias}.latest_state, 2
                              ) = 0
                    ) AS annotation_membership
                    WHERE annotation_membership.trace_id != ''
                )"""
                clauses.append(subquery)
                extra_params[label_id_key] = label_id
                extra_params[ann_org_key] = self.organization_id
                extra_params[val_key] = filter_value
                idx += 1

        return clauses, extra_params


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

_OPERATOR_SYMBOLS: dict[str, str] = {
    "less_than": "<",
    "greater_than": ">",
    "equal_to": "=",
    "not_equal_to": "!=",
    "greater_than_or_equal": ">=",
    "less_than_or_equal": "<=",
    "contains": "IN",
    "not_contains": "NOT IN",
    "str_contains": "LIKE",
    "str_not_contains": "NOT LIKE",
}


def _generate_time_buckets(
    start: datetime, end: datetime, granularity: str
) -> list[str]:
    """Generate all time bucket ISO strings between *start* and *end*.

    Mirrors the ClickHouse ``toStartOf*`` bucketing so that the response
    includes every expected bucket — even those with no data (filled with null).
    """
    buckets: list[str] = []
    if granularity == "minute":
        cur = start.replace(second=0, microsecond=0)
        delta = timedelta(minutes=1)
    elif granularity == "hour":
        cur = start.replace(minute=0, second=0, microsecond=0)
        delta = timedelta(hours=1)
    elif granularity == "week":
        # toMonday — align to Monday
        cur = start - timedelta(days=start.weekday())
        cur = cur.replace(hour=0, minute=0, second=0, microsecond=0)
        delta = timedelta(weeks=1)
    elif granularity == "month":
        cur = start.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        delta = None  # handled specially
    elif granularity == "year":
        cur = start.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        delta = None  # handled specially
    else:
        # Default: day
        cur = start.replace(hour=0, minute=0, second=0, microsecond=0)
        delta = timedelta(days=1)

    if granularity == "month":
        while cur <= end:
            buckets.append(cur.isoformat())
            # Advance to next month
            if cur.month == 12:
                cur = cur.replace(year=cur.year + 1, month=1)
            else:
                cur = cur.replace(month=cur.month + 1)
    elif granularity == "year":
        while cur <= end:
            buckets.append(cur.isoformat())
            cur = cur.replace(year=cur.year + 1)
    else:
        while cur <= end:
            buckets.append(cur.isoformat())
            cur += delta

    return buckets


def _get_operator_symbol(op: str) -> str | None:
    """Return the SQL operator symbol for a filter operator name."""
    return _OPERATOR_SYMBOLS.get(op)


def _parse_dt(val: Any) -> datetime:
    """Parse a datetime from string or return as-is with UTC timezone.

    Always returns a timezone-aware (UTC) datetime so that callers
    produce consistent isoformat strings (with ``+00:00`` suffix).
    """
    dt: datetime | None = None
    if isinstance(val, datetime):
        dt = val
    elif isinstance(val, str):
        cleaned = val.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(cleaned)
        except (ValueError, AttributeError):
            pass
        if dt is None:
            for fmt in (
                "%Y-%m-%dT%H:%M:%S.%fZ",
                "%Y-%m-%dT%H:%M:%S.%f",
                "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d",
            ):
                try:
                    dt = datetime.strptime(val, fmt)
                    break
                except ValueError:
                    continue
    if dt is None:
        return datetime.now(UTC)
    # Ensure timezone-aware (UTC)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _coerce_filter_value(val: Any, operator: str) -> Any:
    """Coerce a filter value to the appropriate Python type for ClickHouse params."""
    if operator in ("contains", "not_contains"):
        if isinstance(val, list):
            return val
        return [val]
    if operator in ("str_contains", "str_not_contains"):
        s = str(val) if val else ""
        s = s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return f"%{s}%"
    if isinstance(val, str):
        try:
            return float(val)
        except ValueError:
            return val
    return val


def _coerce_string_filter_value(val: Any, operator: str) -> Any:
    """Keep identifier/dimension filters typed as String.

    Numeric-looking external ids (for example a curated user id of ``"123"``)
    must not be converted to Float64: ClickHouse cannot compare that parameter
    with the String expression used by user/session/model filters.
    """

    if operator in ("contains", "not_contains"):
        values = val if isinstance(val, list) else [val]
        return [str(value) for value in values]
    if operator in ("str_contains", "str_not_contains"):
        value = str(val) if val is not None else ""
        value = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return f"%{value}%"
    return str(val)


def _coerce_pass_fail_filter_value(val: Any, operator: str) -> Any:
    """Normalize PASS_FAIL filters to the public ``Passed``/``Failed`` labels.

    The filter-values endpoint emits labels, while legacy saved widgets may
    still contain numeric or boolean 1/0 values.  Normalize both forms before
    applying the ordinary String operator coercion.
    """

    def normalize(value: Any) -> Any:
        if isinstance(value, bool):
            return "Passed" if value else "Failed"
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if value == 1:
                return "Passed"
            if value == 0:
                return "Failed"
        if isinstance(value, str):
            token = value.strip().lower()
            if token in {"passed", "pass", "true", "1", "1.0"}:
                return "Passed"
            if token in {"failed", "fail", "false", "0", "0.0"}:
                return "Failed"
        return value

    normalized = (
        [normalize(item) for item in val] if isinstance(val, list) else normalize(val)
    )
    return _coerce_string_filter_value(normalized, operator)
