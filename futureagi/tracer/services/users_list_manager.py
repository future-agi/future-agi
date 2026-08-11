"""Business logic for the Observe Users list and CSV export.

HTTP-free layer between the request boundary and the response: scope resolution,
ClickHouse query/execute, row formatting, span-attribute enrichment, and CSV
serialization. ``UsersView`` keeps only (de)serialization and response building.
"""

import csv
import io
import json
from collections.abc import Iterator
from concurrent.futures import TimeoutError as FuturesTimeoutError
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import structlog

from tracer.services.clickhouse.list_cursor import ListCursor
from tracer.services.clickhouse.read_budget import (
    ReadDeadline,
    ReadDeadlineExceeded,
    is_clickhouse_query_error,
    is_read_budget_error,
)
from tracer.services.clickhouse.v2.query_builders.user_list import (
    UserListQueryBuilderV2,
)
from tracer.services.clickhouse.v2.query_service import V2AnalyticsQueryService

logger = structlog.get_logger(__name__)


# (header, source field) — column order is the frontend export contract.
USERS_EXPORT_COLUMNS = [
    ("User ID", "user_id"),
    ("User ID Type", "user_id_type"),
    ("User ID Hash", "user_id_hash"),
    ("First Active", "activated_at"),
    ("Last Active", "last_active"),
    ("No. of Traces", "num_traces"),
    ("No. of Sessions", "num_sessions"),
    ("Avg Session Duration (s)", "avg_session_duration"),
    ("Total Tokens", "total_tokens"),
    ("Total Cost ($)", "total_cost"),
    ("Avg Latency / Trace (ms)", "avg_trace_latency"),
    ("No. of LLM Calls", "num_llm_calls"),
    ("Guardrails Triggered", "num_guardrails_triggered"),
    ("Evals Pass Rate (%)", "bool_eval_pass_rate"),
    ("Input Tokens", "input_tokens"),
    ("Output Tokens", "output_tokens"),
]


# CSV-injection guard: a cell starting with one of these executes as a formula
# in Excel/Sheets, so customer-controlled strings get a leading quote prefixed.
_CSV_FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")

_SKIP_ATTR_PREFIXES = (
    "raw.",
    "llm.input_messages",
    "llm.output_messages",
    "input.value",
    "output.value",
)

# Production telemetry reads share the infrastructure's hard 30-second ceiling.
# Each phase receives only the request's remaining time, so sequential work
# cannot extend the endpoint beyond that wall.
USER_LIST_WALL_DEADLINE_MS = 30_000
USER_LIST_PRESENCE_TIMEOUT_MS = 30_000
USER_LIST_QUERY_TIMEOUT_MS = 30_000
USER_LIST_ENRICHMENT_TIMEOUT_MS = 30_000
USER_EXPORT_WALL_DEADLINE_MS = 30_000
USER_LIST_CANDIDATE_BATCH_SIZE = 25
USER_LIST_MAX_CANDIDATE_BATCHES = 8

_USER_LIST_READ_SETTINGS = {
    "max_threads": 1,
    "max_block_size": 8192,
    "read_overflow_mode": "throw",
    "max_bytes_to_read": 512 * 1024 * 1024,
    "max_memory_usage": 36 * 1024 * 1024 * 1024,
    "timeout_overflow_mode": "throw",
}
_USER_LIST_RESULT_BYTES = 32 * 1024 * 1024
_USER_LIST_ATTR_RESULT_ROWS = 50_000
_USER_LIST_ATTRIBUTE_KEY_BATCH_SIZE = 4
_USER_LIST_ATTRIBUTE_MIN_BUCKET = timedelta(minutes=1)

_USER_LIST_EXTRA_METRIC_FIELDS = frozenset(
    {
        "num_sessions",
        "avg_session_duration",
        "avg_trace_latency",
        "num_llm_calls",
        "num_guardrails_triggered",
        "num_active_days",
        "num_traces_with_errors",
    }
)
_USER_LIST_EVAL_FIELDS = frozenset(
    {"eval_score", "bool_eval_pass_rate", "avg_output_float"}
)
# ``requested_columns`` was added after this endpoint had already published all
# built-in metrics.  An omitted projection must therefore retain that legacy
# contract; an explicitly supplied empty list remains the bounded opt-out used
# by projection-aware callers.  Custom attributes cannot be part of this
# compatibility set because there is no finite key list to project.
_USER_LIST_OMITTED_PROJECTION_FIELDS = (
    _USER_LIST_EXTRA_METRIC_FIELDS | _USER_LIST_EVAL_FIELDS
)

# Hard cap on export rows. Bounds worker memory + latency for the large-workspace
# case this feature targets (matches agentcc's MAX_EXPORT_ROWS); a hit is logged
# and signalled in-band rather than silently truncating the download.
MAX_EXPORT_ROWS = 10_000


@dataclass(frozen=True)
class UserCursorRead:
    """One exact bounded Users page plus opaque transport state."""

    payload: dict[str, Any]
    window_start: datetime
    window_end: datetime
    checkpoint_order: tuple[Any, ...] | None
    seen_rows: int
    has_more: bool
    unseen_row_proven: bool


def _read_settings(*, max_result_rows: int) -> dict[str, int | str]:
    """Return hard server-side bounds for one user-list ClickHouse read."""

    if max_result_rows <= 0:
        raise ValueError("max_result_rows must be positive")
    return {
        **_USER_LIST_READ_SETTINGS,
        "max_result_rows": int(max_result_rows),
        "max_result_bytes": _USER_LIST_RESULT_BYTES,
        "result_overflow_mode": "throw",
    }


def _page_read_settings(*, max_result_rows: int) -> dict[str, Any]:
    """Return finite settings for one current-latest user-list statement."""

    return _read_settings(max_result_rows=max_result_rows)


def _log_user_read_failure(event: str, exc: Exception, **context: object) -> None:
    """Log operational reads compactly and programming defects with a stack."""

    if is_read_budget_error(exc) or is_clickhouse_query_error(exc):
        logger.warning(event, error_type=type(exc).__name__, **context)
        return
    logger.exception(event, error_type=type(exc).__name__, **context)


def _users_attr_enrichment_query(
    project_id=None,
    project_ids=None,
    *,
    attribute_keys: tuple[str, ...] | list[str] | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    candidate_end_user_id_map: dict[str, str] | None = None,
):
    """Project only requested keys for a finite Observe-Users page.

    The result is bounded by ``page users * requested keys``.  Physical span
    versions are collapsed before tombstones, reassignments, or attribute
    presence are evaluated.  For each user/key, the latest live span carrying
    that key wins deterministically by ``(start_time, id)``.
    """
    from tracer.services.clickhouse.v2.id_remap_sql import (
        bounded_survivor_map_subquery,
        literal_survivor_map_subquery,
        resolved_id_expr,
    )

    requested_keys = tuple(
        dict.fromkeys(str(key) for key in attribute_keys or () if key)
    )
    if not requested_keys:
        return "", {}

    params: dict = {"requested_attribute_keys": list(requested_keys)}
    project_clause = ""
    if project_id:
        params["attr_pid"] = str(project_id)
        project_clause = "AND spans.project_id = toUUID(%(attr_pid)s)"
    elif project_ids:
        params["attr_pids"] = tuple(str(value) for value in project_ids)
        project_clause = "AND spans.project_id IN %(attr_pids)s"

    finite_map = {
        str(any_id): str(survivor_id)
        for any_id, survivor_id in (candidate_end_user_id_map or {}).items()
        if any_id and survivor_id
    }
    if finite_map:
        params["candidate_remap_any_ids"] = list(finite_map)
        params["candidate_remap_survivor_ids"] = list(finite_map.values())
        eu_map = literal_survivor_map_subquery(
            any_ids_param="candidate_remap_any_ids",
            survivor_ids_param="candidate_remap_survivor_ids",
        )
    else:
        eu_map = bounded_survivor_map_subquery(
            "end_user_id_remap", candidate_param="eu_ids"
        )
    resolved = resolved_id_expr("latest_end_user_id", "eu_remap")
    if (start_date is None) != (end_date is None):
        raise ValueError("attribute enrichment window must be provided together")
    time_filter = ""
    if start_date is not None:
        params["attr_start_date"] = start_date
        params["attr_end_date"] = end_date
        time_filter = """
          AND start_time >= %(attr_start_date)s
          AND start_time < %(attr_end_date)s
        """
    sql = f"""
    WITH
    eu_survivor_map AS ({eu_map}),
    candidate_span_identities AS (
        SELECT DISTINCT project_id, trace_id, id, start_time
        FROM spans
        PREWHERE 1 = 1
          {project_clause}
          {time_filter}
          AND end_user_id IN %(eu_scan_ids)s
    ),
    latest_candidate_attribute_values AS (
        SELECT
            project_id,
            trace_id,
            id,
            start_time,
            attribute_key,
            argMax(tuple(end_user_id), _version).1 AS latest_end_user_id,
            argMax(
                tuple(
                    multiIf(
                        notEmpty(JSONExtractRaw(attributes_extra, attribute_key)),
                            JSONExtractRaw(attributes_extra, attribute_key),
                        mapContains(attrs_bool, attribute_key),
                            if(attrs_bool[attribute_key] != 0, 'true', 'false'),
                        mapContains(attrs_number, attribute_key),
                            if(
                                isFinite(attrs_number[attribute_key]),
                                toString(attrs_number[attribute_key]),
                                'null'
                            ),
                        mapContains(attrs_string, attribute_key),
                            toJSONString(attrs_string[attribute_key]),
                        ''
                    )
                ),
                _version
            ).1 AS latest_attribute_value_json,
            argMax(is_deleted, _version) AS latest_is_deleted
        FROM spans
        ARRAY JOIN %(requested_attribute_keys)s AS attribute_key
        PREWHERE 1 = 1
          {project_clause}
          {time_filter}
          AND (project_id, trace_id, id, start_time) IN (
              SELECT project_id, trace_id, id, start_time
              FROM candidate_span_identities
          )
        GROUP BY project_id, trace_id, id, start_time, attribute_key
    )
    SELECT
        toString({resolved}) AS end_user_id,
        attribute_key,
        arraySort(groupUniqArray(latest_attribute_value_json))
            AS attribute_values_json
    FROM latest_candidate_attribute_values
    LEFT JOIN eu_survivor_map AS eu_remap
        ON latest_end_user_id = eu_remap.any_id
    WHERE latest_is_deleted = 0
      AND {resolved} IN %(eu_ids)s
      AND notEmpty(latest_attribute_value_json)
    GROUP BY end_user_id, attribute_key
    """
    from tracer.services.clickhouse.v2.query_builders.filters import (
        _append_v2_settings,
    )

    return _append_v2_settings(sql), params


class UsersListManager:
    """Owns the Observe Users list + CSV export business logic."""

    def __init__(
        self,
        *,
        organization_id: str,
        allowed_project_ids: list[str],
        project_id: str | None = None,
        search: str | None = None,
        filters: list[dict] | None = None,
        sort_params: list[dict] | None = None,
        requested_columns: list[str] | None = None,
        attribute_keys: list[str] | None = None,
    ):
        self.organization_id = str(organization_id)
        self.project_id = str(project_id) if project_id else None
        self.search = search
        self.filters = filters or []
        self.sort_params = sort_params or []
        requested_column_source = (
            _USER_LIST_OMITTED_PROJECTION_FIELDS
            if requested_columns is None
            else requested_columns
        )
        self.requested_columns = frozenset(
            UserListQueryBuilderV2.OUTPUT_FILTER_MAP.get(
                str(column),
                "bool_eval_pass_rate" if str(column) == "eval_score" else str(column),
            )
            for column in requested_column_source
            if column
        )
        requested_attribute_keys = [str(key) for key in (attribute_keys or ()) if key]
        for item in self.filters:
            if UserListQueryBuilderV2._is_date_filter(item):
                continue
            column_id = item.get("column_id") or item.get("columnId")
            if column_id and column_id not in UserListQueryBuilderV2.OUTPUT_FILTER_MAP:
                requested_attribute_keys.append(str(column_id))
        self.attribute_keys = tuple(dict.fromkeys(requested_attribute_keys))
        filter_columns = {
            UserListQueryBuilderV2.OUTPUT_FILTER_MAP.get(
                str(item.get("column_id") or item.get("columnId")),
                (
                    "bool_eval_pass_rate"
                    if str(item.get("column_id") or item.get("columnId"))
                    == "eval_score"
                    else str(item.get("column_id") or item.get("columnId"))
                ),
            )
            for item in self.filters
            if (item.get("column_id") or item.get("columnId"))
        }
        self.metric_keys = frozenset(
            (self.requested_columns | filter_columns) & _USER_LIST_EXTRA_METRIC_FIELDS
        )
        self.needs_evals = bool(
            (self.requested_columns | filter_columns) & _USER_LIST_EVAL_FIELDS
        )
        self.scoped_project_ids, self.empty_scope = self._resolve_scope(
            self.project_id, allowed_project_ids
        )

    @staticmethod
    def _resolve_scope(
        project_id: str | None, allowed_project_ids: list[str]
    ) -> tuple[list[str], bool]:
        """Intersect the requested project with the caller's allowed projects.

        An out-of-scope project collapses to ``empty_scope`` — never an org-wide
        scan (CH25: the curated source has no ``workspace_id`` column to filter).
        """
        allowed_strs = {str(p) for p in allowed_project_ids}
        if project_id:
            if project_id in allowed_strs:
                return [project_id], False
            return [], True
        scoped = [str(p) for p in allowed_project_ids]
        return scoped, not scoped

    def _fetch_rows(
        self,
        *,
        limit: int | None,
        offset: int | None,
        deadline: ReadDeadline,
        max_rows: int | None = None,
    ) -> tuple[list[dict], int, UserListQueryBuilderV2]:
        analytics = V2AnalyticsQueryService()
        builder = UserListQueryBuilderV2(
            organization_id=self.organization_id,
            project_ids=self.scoped_project_ids,
            search=self.search,
            limit=limit,
            offset=offset,
            max_rows=max_rows,
            filters=self.filters,
            sort_params=self.sort_params,
            empty_scope=self.empty_scope,
        )
        if self.empty_scope:
            return [], 0, builder
        physical_query, physical_params = builder.build_physical_user_presence_query()
        physical_presence = analytics.execute_ch_query(
            physical_query,
            physical_params,
            timeout_ms=deadline.remaining_ms(USER_LIST_PRESENCE_TIMEOUT_MS),
            settings=_read_settings(max_result_rows=1),
        )
        if not physical_presence.data:
            return [], 0, builder
        query, params = builder.build_candidate_page_query()
        result_row_cap = max_rows or limit or 1
        result = analytics.execute_ch_query(
            query,
            params,
            timeout_ms=deadline.remaining_ms(USER_LIST_QUERY_TIMEOUT_MS),
            settings=_read_settings(max_result_rows=result_row_cap),
        )
        formatted = builder.format_rows(result.data)
        return formatted["table"], formatted["total_count"], builder

    def _read_page_metrics(
        self,
        rows: list[dict],
        builder: UserListQueryBuilderV2,
        deadline: ReadDeadline,
        *,
        timeout_cap_ms: int | None = USER_LIST_ENRICHMENT_TIMEOUT_MS,
    ) -> dict[str, dict]:
        """Return latest-row raw metrics for the already finite user page."""

        end_user_ids = [r.get("end_user_id") for r in rows if r.get("end_user_id")]
        if not end_user_ids or not self.metric_keys:
            return {}
        queries = builder.build_requested_page_metric_queries(
            [str(value) for value in end_user_ids], self.metric_keys
        )
        analytics = V2AnalyticsQueryService()
        merged: dict[str, dict] = {}
        for query, params, _fields in queries:
            result = analytics.execute_ch_query(
                query,
                params,
                timeout_ms=deadline.remaining_ms(timeout_cap_ms),
                settings=_page_read_settings(max_result_rows=max(1, len(end_user_ids))),
            )
            for row in result.data:
                key = str(row.get("end_user_id", ""))
                merged.setdefault(key, {}).update(row)
        return merged

    @staticmethod
    def _apply_page_metrics(rows: list[dict], metrics: dict[str, dict]) -> None:
        fields = (
            "num_sessions",
            "avg_session_duration",
            "avg_trace_latency",
            "num_llm_calls",
            "num_guardrails_triggered",
            "num_active_days",
            "num_traces_with_errors",
        )
        for entry in rows:
            metric_row = metrics.get(str(entry.get("end_user_id", "")), {})
            for field in fields:
                if field in metric_row:
                    entry[field] = metric_row.get(field, 0) or 0

    def _read_span_attributes(
        self,
        rows: list[dict],
        deadline: ReadDeadline,
        *,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        candidate_scan_ids: list[str] | None = None,
        candidate_end_user_id_map: dict[str, str] | None = None,
    ) -> dict[str, dict[str, object]]:
        """Return page-user attributes under the request-owned wall deadline."""

        end_user_ids = [r.get("end_user_id") for r in rows if r.get("end_user_id")]
        if not end_user_ids or not self.attribute_keys:
            return {}
        analytics = V2AnalyticsQueryService()
        # Keep every latest-per-span value for filter semantics.  Collapsing a
        # key to only the newest span makes an exact ``in``/``equals`` filter
        # miss users that have the requested value on another live span.  The
        # query itself returns one row per user/key with a distinct value array;
        # Python merges exact arrays across key batches and adaptive time
        # buckets without changing positive/negative/null predicate semantics.
        collected: dict[str, dict[str, dict[str, object]]] = {}

        def _collect(rows_to_collect: list[dict]) -> None:
            for attr_row in rows_to_collect:
                uid = str(attr_row.get("end_user_id", ""))
                key = str(attr_row.get("attribute_key", ""))
                if not uid or not key or key.startswith(_SKIP_ATTR_PREFIXES):
                    continue
                raw_values = attr_row.get("attribute_values_json")
                if raw_values is None:
                    # Compatibility with a rolling deploy/test double using
                    # the earlier one-value projected response.
                    raw_values = [attr_row.get("attribute_value_json", "")]
                elif not isinstance(raw_values, (list, tuple)):
                    raw_values = [raw_values]
                values = collected.setdefault(uid, {}).setdefault(key, {})
                for raw in raw_values:
                    try:
                        value = json.loads(raw) if isinstance(raw, str) else raw
                    except (json.JSONDecodeError, TypeError):
                        value = raw
                    if isinstance(value, str) and len(value) > 500:
                        continue
                    if isinstance(value, (dict, list)):
                        value = json.dumps(
                            value,
                            sort_keys=True,
                            separators=(",", ":"),
                            ensure_ascii=False,
                        )
                    elif isinstance(value, bool):
                        value = str(value).lower()
                    values[self._canonical_filter_value(value)] = value

        def _read_key_bucket(
            keys: tuple[str, ...],
            bucket_start: datetime | None,
            bucket_end: datetime | None,
        ) -> None:
            attr_query, attr_params = _users_attr_enrichment_query(
                project_id=self.project_id,
                project_ids=self.scoped_project_ids,
                attribute_keys=keys,
                start_date=bucket_start,
                end_date=bucket_end,
                candidate_end_user_id_map=candidate_end_user_id_map,
            )
            attr_params["eu_ids"] = tuple(str(e) for e in end_user_ids)
            attr_params["eu_scan_ids"] = tuple(
                str(value) for value in (candidate_scan_ids or end_user_ids)
            )
            try:
                attr_result = analytics.execute_ch_query(
                    attr_query,
                    attr_params,
                    timeout_ms=deadline.remaining_ms(USER_LIST_ENRICHMENT_TIMEOUT_MS),
                    settings=_page_read_settings(
                        max_result_rows=max(1, len(end_user_ids) * len(keys))
                    ),
                )
            except Exception as exc:
                can_split = (
                    is_read_budget_error(exc)
                    and bucket_start is not None
                    and bucket_end is not None
                    and bucket_end - bucket_start > _USER_LIST_ATTRIBUTE_MIN_BUCKET
                )
                if not can_split:
                    raise
                midpoint = bucket_start + (bucket_end - bucket_start) / 2
                _read_key_bucket(keys, bucket_start, midpoint)
                _read_key_bucket(keys, midpoint, bucket_end)
                return
            _collect(list(attr_result.data or ()))

        for key_start in range(
            0, len(self.attribute_keys), _USER_LIST_ATTRIBUTE_KEY_BATCH_SIZE
        ):
            _read_key_bucket(
                self.attribute_keys[
                    key_start : key_start + _USER_LIST_ATTRIBUTE_KEY_BATCH_SIZE
                ],
                start_date,
                end_date,
            )

        user_attrs: dict[str, dict[str, object]] = {}
        for uid, attributes in collected.items():
            for key, canonical_values in attributes.items():
                ordered_values = [
                    canonical_values[value_key]
                    for value_key in sorted(canonical_values)
                ]
                user_attrs.setdefault(uid, {})[key] = (
                    ordered_values[0] if len(ordered_values) == 1 else ordered_values
                )
        return user_attrs

    @staticmethod
    def _apply_span_attributes(
        rows: list[dict],
        user_attrs: dict[str, dict[str, object]],
    ) -> None:
        for entry in rows:
            end_user_id = str(entry.get("end_user_id", ""))
            for key, value in user_attrs.get(end_user_id, {}).items():
                if key in entry:
                    continue
                entry[key] = value

    def _read_evals(
        self,
        rows: list[dict],
        builder: UserListQueryBuilderV2,
        deadline: ReadDeadline,
    ) -> dict[str, dict]:
        """Return page-user eval metrics under the shared request deadline."""

        end_user_ids = [r.get("end_user_id") for r in rows if r.get("end_user_id")]
        if not end_user_ids or not self.needs_evals:
            return {}
        from tracer.models.custom_eval_config import CustomEvalConfig

        allowed_eval_config_ids_by_project: dict[str, list[str]] = {}
        for (
            config_project_id,
            config_id,
        ) in CustomEvalConfig.no_workspace_objects.filter(
            project_id__in=self.scoped_project_ids,
            deleted=False,
        ).values_list("project_id", "id"):
            allowed_eval_config_ids_by_project.setdefault(
                str(config_project_id), []
            ).append(str(config_id))
        if not allowed_eval_config_ids_by_project:
            return {}
        eval_query, eval_params = builder.build_eval_query(
            [str(e) for e in end_user_ids],
            allowed_eval_config_ids_by_project=allowed_eval_config_ids_by_project,
        )
        if not eval_query:
            return {}
        analytics = V2AnalyticsQueryService()
        eval_result = analytics.execute_ch_query(
            eval_query,
            eval_params,
            timeout_ms=deadline.remaining_ms(USER_LIST_ENRICHMENT_TIMEOUT_MS),
            settings=_page_read_settings(max_result_rows=max(1, len(end_user_ids))),
        )
        return {str(row.get("end_user_id", "")): row for row in eval_result.data}

    @staticmethod
    def _apply_evals(rows: list[dict], eval_map: dict[str, dict]) -> None:
        for entry in rows:
            end_user_id = str(entry.get("end_user_id", ""))
            eval_row = eval_map.get(end_user_id, {})
            entry["bool_eval_pass_rate"] = eval_row.get("bool_eval_pass_rate", 0)
            entry["avg_output_float"] = eval_row.get("avg_output_float", 0)

    def _enrich_rows(
        self,
        rows: list[dict],
        builder: UserListQueryBuilderV2,
        deadline: ReadDeadline,
        *,
        start_date: datetime | None,
        end_date: datetime | None,
        candidate_scan_ids: list[str] | None = None,
        candidate_end_user_id_map: dict[str, str] | None = None,
    ) -> None:
        """Run only explicitly requested finite enrichments."""

        # ClickHouse read caps apply per statement, while concurrent statements
        # add their resident memory.  Run optional page enrichments serially so
        # one request cannot multiply the 256 MiB ceiling by three.
        if self.metric_keys:
            metrics = self._read_page_metrics(rows, builder, deadline)
            self._apply_page_metrics(rows, metrics)
        if self.attribute_keys:
            attributes = self._read_span_attributes(
                rows,
                deadline,
                start_date=start_date,
                end_date=end_date,
                candidate_scan_ids=candidate_scan_ids,
                candidate_end_user_id_map=candidate_end_user_id_map,
            )
            self._apply_span_attributes(rows, attributes)
        if self.needs_evals:
            evals = self._read_evals(rows, builder, deadline)
            self._apply_evals(rows, evals)

    @staticmethod
    def _frozen_filters(
        filters: list[dict],
        *,
        window_start: datetime,
        window_end: datetime,
    ) -> list[dict]:
        return [
            *[
                item
                for item in filters
                if not UserListQueryBuilderV2._is_date_filter(item)
            ],
            {
                "column_id": "created_at",
                "filter_config": {
                    "filter_type": "datetime",
                    "filter_op": "between",
                    "filter_value": [window_start, window_end],
                },
            },
        ]

    def _read_dimension_candidates(
        self,
        *,
        deadline: ReadDeadline,
        limit: int,
        before_first_seen: datetime | None,
        before_end_user_id: str | None,
    ) -> list[dict]:
        builder = UserListQueryBuilderV2(
            organization_id=self.organization_id,
            project_ids=self.scoped_project_ids,
            search=self.search,
            empty_scope=self.empty_scope,
        )
        query, params = builder.build_dimension_candidate_query(
            limit=limit,
            before_first_seen=before_first_seen,
            before_end_user_id=before_end_user_id,
        )
        result = V2AnalyticsQueryService().execute_ch_query(
            query,
            params,
            timeout_ms=deadline.remaining_ms(USER_LIST_QUERY_TIMEOUT_MS),
            settings=_page_read_settings(max_result_rows=limit),
        )
        candidates = list(result.data or [])
        candidate_ids = [
            str(row.get("end_user_id")) for row in candidates if row.get("end_user_id")
        ]
        if not candidate_ids:
            return candidates

        # The dimension is deliberately scanned in raw key order so the hot
        # query never materializes the global many-to-one remap. Classify only
        # this finite page: alias rows advance the cursor but are never
        # published, while ids absent from the remap are canonical by default.
        remap_query, remap_params = builder.build_dimension_survivor_query(
            candidate_ids
        )
        remap_result = V2AnalyticsQueryService().execute_ch_query(
            remap_query,
            remap_params,
            timeout_ms=deadline.remaining_ms(USER_LIST_QUERY_TIMEOUT_MS),
            settings=_page_read_settings(max_result_rows=_USER_LIST_ATTR_RESULT_ROWS),
        )
        survivor_by_id = {
            str(row.get("any_id")): str(row.get("survivor_id"))
            for row in (remap_result.data or [])
            if row.get("any_id") and row.get("survivor_id")
        }
        for candidate in candidates:
            candidate_id = str(candidate.get("end_user_id", ""))
            survivor_id = survivor_by_id.get(candidate_id, candidate_id)
            candidate["_is_survivor_candidate"] = survivor_id == candidate_id
            if survivor_id == candidate_id:
                candidate["_candidate_scan_end_user_ids"] = tuple(
                    any_id
                    for any_id, mapped_survivor in survivor_by_id.items()
                    if mapped_survivor == survivor_id
                ) or (candidate_id,)
        return candidates

    def _read_exact_candidate_rows(
        self,
        *,
        candidate_ids: list[str],
        candidate_scan_ids: list[str] | None = None,
        candidate_end_user_id_map: dict[str, str] | None = None,
        frozen_filters: list[dict],
        window_start: datetime,
        window_end: datetime,
        deadline: ReadDeadline,
    ) -> list[dict]:
        if not candidate_ids:
            return []
        date_filters = [
            item
            for item in frozen_filters
            if UserListQueryBuilderV2._is_date_filter(item)
        ]
        builder = UserListQueryBuilderV2(
            organization_id=self.organization_id,
            project_ids=self.scoped_project_ids,
            filters=date_filters,
            limit=len(candidate_ids),
            offset=0,
            candidate_end_user_ids=candidate_ids,
            candidate_scan_end_user_ids=candidate_scan_ids or candidate_ids,
            candidate_end_user_id_map=candidate_end_user_id_map,
            empty_scope=self.empty_scope,
        )
        query, params = builder.build_candidate_page_query()
        result = V2AnalyticsQueryService().execute_ch_query(
            query,
            params,
            timeout_ms=deadline.remaining_ms(USER_LIST_QUERY_TIMEOUT_MS),
            settings=_page_read_settings(max_result_rows=max(1, len(candidate_ids))),
        )
        rows = builder.format_rows(result.data)["table"]
        if not rows:
            return []
        self._enrich_rows(
            rows,
            builder,
            deadline,
            start_date=window_start,
            end_date=window_end,
            candidate_scan_ids=candidate_scan_ids,
            candidate_end_user_id_map=candidate_end_user_id_map,
        )
        return rows

    @staticmethod
    def _candidate_value_matches(candidate: Any, op: str | None, expected: Any) -> bool:
        if isinstance(candidate, (list, tuple, set)):
            values = list(candidate)
            if op in {"not_equals", "not_in", "not_contains", "not_between"}:
                positive_op = {
                    "not_equals": "equals",
                    "not_in": "in",
                    "not_contains": "contains",
                    "not_between": "between",
                }[str(op)]
                return all(
                    not UsersListManager._candidate_value_matches(
                        value, positive_op, expected
                    )
                    for value in values
                )
            return any(
                UsersListManager._candidate_value_matches(value, op, expected)
                for value in values
            )
        if op == "is_null":
            return candidate is None
        if op == "is_not_null":
            return candidate is not None
        if op in {"in", "not_in"}:
            expected_values = expected if isinstance(expected, list) else [expected]
            left = UsersListManager._canonical_filter_value(candidate)
            matched = any(
                left == UsersListManager._canonical_filter_value(value)
                for value in expected_values
            )
            return not matched if op == "not_in" else matched
        if op in {"equals", "not_equals"}:
            left = UsersListManager._canonical_filter_value(candidate)
            right = UsersListManager._canonical_filter_value(expected)
            matched = left == right
            return not matched if op == "not_equals" else matched
        if op in {"contains", "not_contains", "starts_with", "ends_with"}:
            left = UsersListManager._canonical_filter_value(candidate or "").lower()
            right = UsersListManager._canonical_filter_value(expected or "").lower()
            if op == "starts_with":
                return left.startswith(right)
            if op == "ends_with":
                return left.endswith(right)
            matched = right in left
            return not matched if op == "not_contains" else matched
        if op in {
            "greater_than",
            "greater_than_or_equal",
            "less_than",
            "less_than_or_equal",
        }:
            try:
                left = float(candidate)
                right = float(expected)
            except (TypeError, ValueError):
                return False
            if op == "greater_than":
                return left > right
            if op == "greater_than_or_equal":
                return left >= right
            if op == "less_than":
                return left < right
            return left <= right
        if op in {"between", "not_between"}:
            if not isinstance(expected, (list, tuple)) or len(expected) != 2:
                return False
            try:
                matched = expected[0] <= candidate <= expected[1]
            except TypeError:
                left = str(candidate)
                matched = str(expected[0]) <= left <= str(expected[1])
            return not matched if op == "not_between" else matched
        # The request serializer rejects unknown operators.  Internal callers
        # still fail closed here so a future validation/routing regression can
        # never turn an unsupported predicate into an unfiltered successful
        # page.
        return False

    @staticmethod
    def _canonical_filter_value(value: Any) -> str:
        if isinstance(value, (dict, list)):
            return json.dumps(
                value,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
        if isinstance(value, bool):
            return str(value).lower()
        if isinstance(value, (int, float, Decimal)):
            numeric = Decimal(str(value))
            if numeric.is_finite():
                if numeric == 0:
                    return "0"
                return format(numeric.normalize(), "f")
            return str(value).lower()
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.lower() in {"true", "false"}:
                return stripped.lower()
            if stripped.startswith(("{", "[")):
                try:
                    structured = json.loads(stripped)
                except (json.JSONDecodeError, TypeError):
                    pass
                else:
                    if isinstance(structured, (dict, list)):
                        return json.dumps(
                            structured,
                            sort_keys=True,
                            separators=(",", ":"),
                            ensure_ascii=False,
                        )
        return str(value)

    def _row_matches_filters(self, row: dict[str, Any]) -> bool:
        for item in self.filters:
            if UserListQueryBuilderV2._is_date_filter(item):
                continue
            config = item.get("filter_config") or {}
            column_id = item.get("column_id") or item.get("columnId")
            if not column_id:
                continue
            if column_id == "eval_score":
                key = "bool_eval_pass_rate"
            else:
                key = UserListQueryBuilderV2.OUTPUT_FILTER_MAP.get(column_id, column_id)
            if not self._candidate_value_matches(
                row.get(key),
                config.get("filter_op") or config.get("filterOp"),
                config.get("filter_value", config.get("filterValue")),
            ):
                return False
        return True

    def list_cursor_payload(
        self,
        *,
        page_size: int,
        cursor: ListCursor | None = None,
    ) -> UserCursorRead:
        """Return exact rows from a bounded, signed dimension continuation.

        The list is intentionally candidate ordered.  It never samples or
        publishes a partially hydrated user; an unfinished dimension scan is
        represented only by ``has_more`` plus the next opaque cursor.
        """

        deadline = ReadDeadline.start(USER_LIST_WALL_DEADLINE_MS)
        base_builder = UserListQueryBuilderV2(
            organization_id=self.organization_id,
            project_ids=self.scoped_project_ids,
            filters=self.filters,
            empty_scope=self.empty_scope,
        )
        if cursor is None:
            window_start, window_end = base_builder.parse_time_range(self.filters)
            frozen_filters = self._frozen_filters(
                self.filters,
                window_start=window_start,
                window_end=window_end,
            )
            seen_before = 0
            before_first_seen = None
            before_end_user_id = None
        else:
            window_start, window_end = cursor.window_start, cursor.window_end
            frozen_filters = self._frozen_filters(
                self.filters,
                window_start=window_start,
                window_end=window_end,
            )
            seen_before = cursor.seen_rows
            if len(cursor.order) != 2:
                raise ValueError("user list cursor order is invalid")
            before_first_seen = cursor.order[0]
            before_end_user_id = str(cursor.order[1])

        published: list[dict] = []
        checkpoint: tuple[Any, ...] | None = None
        has_more = False
        unseen_row_proven = False

        for _ in range(USER_LIST_MAX_CANDIDATE_BATCHES):
            try:
                candidate_rows = self._read_dimension_candidates(
                    deadline=deadline,
                    limit=USER_LIST_CANDIDATE_BATCH_SIZE + 1,
                    before_first_seen=before_first_seen,
                    before_end_user_id=before_end_user_id,
                )
                if not candidate_rows:
                    has_more = False
                    break

                batch = candidate_rows[:USER_LIST_CANDIDATE_BATCH_SIZE]
                dimension_has_more = len(candidate_rows) > len(batch)
                candidate_ids = [
                    str(row["end_user_id"])
                    for row in batch
                    if row.get("_is_survivor_candidate", True)
                ]
                candidate_scan_ids = list(
                    dict.fromkeys(
                        scan_id
                        for row in batch
                        if row.get("_is_survivor_candidate", True)
                        for scan_id in row.get(
                            "_candidate_scan_end_user_ids",
                            (str(row["end_user_id"]),),
                        )
                    )
                )
                candidate_end_user_id_map = {
                    str(scan_id): str(row["end_user_id"])
                    for row in batch
                    if row.get("_is_survivor_candidate", True)
                    for scan_id in row.get(
                        "_candidate_scan_end_user_ids",
                        (str(row["end_user_id"]),),
                    )
                }
                exact_rows = self._read_exact_candidate_rows(
                    candidate_ids=candidate_ids,
                    candidate_scan_ids=candidate_scan_ids,
                    candidate_end_user_id_map=candidate_end_user_id_map,
                    frozen_filters=frozen_filters,
                    window_start=window_start,
                    window_end=window_end,
                    deadline=deadline,
                )
                exact_by_id = {
                    str(row.get("end_user_id")): row
                    for row in exact_rows
                    if row.get("end_user_id")
                }
                consumed = 0
                for candidate in batch:
                    consumed += 1
                    row = exact_by_id.get(str(candidate.get("end_user_id")))
                    if row is None or not self._row_matches_filters(row):
                        continue
                    published.append(row)
                    if len(published) == page_size:
                        unseen_row_proven = any(
                            (
                                exact_by_id.get(str(later.get("end_user_id")))
                                is not None
                                and self._row_matches_filters(
                                    exact_by_id[str(later.get("end_user_id"))]
                                )
                            )
                            for later in batch[consumed:]
                        )
                        break

                consumed_row = batch[consumed - 1]
                checkpoint = (
                    consumed_row["first_seen"],
                    str(consumed_row["end_user_id"]),
                )
                before_first_seen = checkpoint[0]
                before_end_user_id = checkpoint[1]
                unconsumed_candidates = consumed < len(batch)
                has_more = bool(
                    unconsumed_candidates
                    or dimension_has_more
                    or len(batch) == USER_LIST_CANDIDATE_BATCH_SIZE
                )
                if len(published) == page_size:
                    break
                if (
                    not dimension_has_more
                    and len(batch) < USER_LIST_CANDIDATE_BATCH_SIZE
                ):
                    has_more = False
                    break
            except (FuturesTimeoutError, ReadDeadlineExceeded):
                if checkpoint is None:
                    raise
                has_more = True
                break
            except Exception as exc:
                if checkpoint is None or not is_read_budget_error(exc):
                    raise
                has_more = True
                break
        else:
            has_more = checkpoint is not None

        seen_rows = seen_before + len(published)
        lower_bound = seen_rows + (1 if has_more and unseen_row_proven else 0)
        total_pages = (lower_bound + page_size - 1) // page_size
        payload = {
            "table": published,
            "total_count": lower_bound,
            "total_pages": total_pages,
            "count_is_lower_bound": has_more,
            "has_more": has_more,
            # Every published row completed exact latest-state hydration and
            # every requested predicate. ``has_more`` describes only the
            # dimension traversal; it must not relabel an exact list page as an
            # incomplete/sampled result in shared UI state handling.
            "query_complete": True,
            "query_status": "complete",
        }
        return UserCursorRead(
            payload=payload,
            window_start=window_start,
            window_end=window_end,
            checkpoint_order=checkpoint,
            seen_rows=seen_rows,
            has_more=has_more,
            unseen_row_proven=unseen_row_proven,
        )

    def list_payload(self, *, page_size: int, current_page: int) -> dict:
        """Paginated list response: rows + span/eval enrichment + page totals."""
        deadline = ReadDeadline.start(USER_LIST_WALL_DEADLINE_MS)
        try:
            rows, count, builder = self._fetch_rows(
                limit=page_size,
                offset=current_page * page_size,
                deadline=deadline,
            )
            if rows:
                parsed_window = builder.parse_time_range(self.filters)
                # Real query builders always return a two-item window.  Keep
                # this boundary tolerant of builder test doubles (and older
                # injected builders) so enrichment failures remain the error
                # being surfaced instead of an incidental unpacking error.
                if isinstance(parsed_window, (list, tuple)) and len(parsed_window) == 2:
                    window_start, window_end = parsed_window
                else:
                    window_start = window_end = None
                self._enrich_rows(
                    rows,
                    builder,
                    deadline,
                    start_date=window_start,
                    end_date=window_end,
                )
        except (FuturesTimeoutError, ReadDeadlineExceeded) as exc:
            _log_user_read_failure(
                "users_list_deadline_exceeded",
                exc,
                organization_id=self.organization_id,
                project_id=self.project_id,
            )
            raise
        except Exception as exc:
            _log_user_read_failure(
                "users_list_read_failed",
                exc,
                organization_id=self.organization_id,
                project_id=self.project_id,
            )
            # The HTTP boundary emits the sanitized retryable response.  Never
            # turn an arbitrary programming defect into a successful empty or
            # partially enriched user page.
            raise
        total_pages = (count // page_size) + (1 if count % page_size > 0 else 0)
        return {"table": rows, "total_count": count, "total_pages": total_pages}

    @classmethod
    def _format_export_cell(cls, value: Any):
        if value is None:
            return ""
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, str) and value.startswith(_CSV_FORMULA_TRIGGERS):
            return "'" + value
        return value

    def iter_export_csv(self) -> Iterator[str]:
        """Stream the export as CSV text, header row first.

        The header is yielded BEFORE the ClickHouse fetch so the socket stays
        warm while the (slow) query runs — a buffered response would leave it
        idle past the LB read timeout. Rows are hard-capped at
        ``MAX_EXPORT_ROWS``; a cap hit or a mid-stream failure is logged and
        signalled in-band, since headers are already sent and the status can no
        longer change (otherwise a partial body reads as a clean 200).
        """
        buffer = io.StringIO()
        writer = csv.writer(buffer)

        def _drain() -> str:
            chunk = buffer.getvalue()
            buffer.seek(0)
            buffer.truncate()
            return chunk

        writer.writerow([header for header, _ in USERS_EXPORT_COLUMNS])
        yield _drain()

        try:
            # Fetch cap + 1 so a full page can be distinguished from a truncation.
            deadline = ReadDeadline.start(USER_EXPORT_WALL_DEADLINE_MS)
            rows, _, builder = self._fetch_rows(
                limit=None,
                offset=None,
                max_rows=MAX_EXPORT_ROWS + 1,
                deadline=deadline,
            )
            if rows:
                metrics = self._read_page_metrics(
                    rows,
                    builder,
                    deadline,
                    timeout_cap_ms=None,
                )
                self._apply_page_metrics(rows, metrics)
        except Exception as exc:
            _log_user_read_failure(
                "users_export_failed",
                exc,
                organization_id=self.organization_id,
                project_id=self.project_id,
            )
            writer.writerow(
                ["# export failed before completion; data may be incomplete"]
            )
            yield _drain()
            return

        truncated = len(rows) > MAX_EXPORT_ROWS
        if truncated:
            rows = rows[:MAX_EXPORT_ROWS]
            logger.warning(
                "users_export_truncated",
                organization_id=self.organization_id,
                project_id=self.project_id,
                max_rows=MAX_EXPORT_ROWS,
            )

        for row in rows:
            writer.writerow(
                [
                    self._format_export_cell(row.get(field))
                    for _, field in USERS_EXPORT_COLUMNS
                ]
            )
            yield _drain()

        if truncated:
            writer.writerow([f"# export truncated at {MAX_EXPORT_ROWS} rows"])
            yield _drain()
