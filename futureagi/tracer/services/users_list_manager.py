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
from tracer.services.clickhouse.query_builders.filters import (
    EvalFilterMetadata,
    resolve_eval_filter_metadata,
)
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
from tracer.services.user_attribute_contract import unsupported_user_attribute_keys

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

# Interactive telemetry reads reserve HTTP serialization/transport time inside
# the ten-second product SLA. Every phase shares one eight-second request wall.
# Each phase receives only the request's remaining time, so sequential work
# cannot extend the endpoint beyond that wall.
USER_LIST_WALL_DEADLINE_MS = 8_000
USER_LIST_PRESENCE_TIMEOUT_MS = 8_000
USER_LIST_QUERY_TIMEOUT_MS = 8_000
USER_LIST_ENRICHMENT_TIMEOUT_MS = 8_000
# Users CSV is a synchronous bounded-page contract.  Reuse the cursor reader's
# finite candidate/hydration pipeline instead of starting a separate broad
# export scan.  Twenty rows leaves the same transport margin as the other
# cursor-backed exports whose selectors may need several finite statements.
USER_EXPORT_PAGE_SIZE = 20
# Keep the unfiltered first replay aligned with the default grid page. Optional
# session metrics run as their own finite statement, so this exact usage replay
# no longer carries session remap/aggregation state over the full span scan.
USER_LIST_CANDIDATE_BATCH_SIZE = 25
USER_LIST_ATTRIBUTE_FILTER_CANDIDATE_BATCH_SIZE = 8
USER_LIST_REFILL_MIN_CANDIDATES = 4
USER_LIST_REFILL_MAX_CANDIDATES = 8
USER_LIST_REFILL_MIN_BUDGET_MS = 3_000
USER_LIST_MAX_CANDIDATE_BATCHES = 8

_USER_LIST_READ_SETTINGS = {
    "max_threads": 1,
    "max_block_size": 8192,
    "read_overflow_mode": "throw",
    "max_bytes_to_read": 36 * 1024 * 1024 * 1024,
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


def _page_replay_read_settings(*, max_result_rows: int) -> dict[str, Any]:
    """Parallelize only finite page-scoped latest-state replays."""

    return {
        **_page_read_settings(max_result_rows=max_result_rows),
        "max_threads": 8,
    }


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
    candidate_text_values_by_key: dict[str, tuple[str, ...]] | None = None,
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
    candidate_value_params: dict[str, tuple[str, ...] | str] = {}
    candidate_value_clauses: list[str] = []
    for index, (attribute_key, raw_values) in enumerate(
        sorted((candidate_text_values_by_key or {}).items())
    ):
        values = tuple(dict.fromkeys(str(value).lower() for value in raw_values))
        if not attribute_key or not values:
            continue
        key_param = f"candidate_attribute_key_{index}"
        values_param = f"candidate_attribute_values_{index}"
        candidate_value_params[key_param] = str(attribute_key)
        candidate_value_params[values_param] = values
        candidate_value_clauses.append(
            f"""
            mapContains(attrs_string, %({key_param})s)
            AND lowerUTF8(attrs_string[%({key_param})s]) IN %({values_param})s
            """
        )
    params.update(candidate_value_params)
    candidate_value_filter = (
        "AND (" + ") OR (".join(candidate_value_clauses) + ")"
        if candidate_value_clauses
        else ""
    )
    sql = f"""
    WITH
    eu_survivor_map AS ({eu_map}),
    candidate_span_identities AS (
        SELECT DISTINCT
            project_id,
            observation_type,
            service_name,
            toStartOfHour(start_time) AS identity_hour,
            trace_id,
            id
        FROM spans
        PREWHERE 1 = 1
          {project_clause}
          {time_filter}
          AND end_user_id IN %(eu_scan_ids)s
          {candidate_value_filter}
    ),
    latest_candidate_attribute_values AS (
        SELECT
            project_id,
            observation_type,
            service_name,
            toStartOfHour(start_time) AS identity_hour,
            trace_id,
            id,
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
            argMax(
                tuple(
                    multiIf(
                        notEmpty(JSONExtractRaw(attributes_extra, attribute_key)),
                            'json',
                        mapContains(attrs_bool, attribute_key), 'boolean',
                        mapContains(attrs_number, attribute_key), 'number',
                        mapContains(attrs_string, attribute_key), 'string',
                        ''
                    )
                ),
                _version
            ).1 AS latest_attribute_value_type,
            argMax(is_deleted, _version) AS latest_is_deleted
        FROM spans
        ARRAY JOIN %(requested_attribute_keys)s AS attribute_key
        PREWHERE 1 = 1
          {project_clause}
          {time_filter}
          AND (
              project_id,
              observation_type,
              service_name,
              toStartOfHour(start_time),
              trace_id,
              id
          ) IN (
              SELECT
                  project_id,
                  observation_type,
                  service_name,
                  identity_hour,
                  trace_id,
                  id
              FROM candidate_span_identities
          )
        GROUP BY
            project_id,
            observation_type,
            service_name,
            identity_hour,
            trace_id,
            id,
            attribute_key
    )
    SELECT
        toString({resolved}) AS end_user_id,
        attribute_key,
        arraySort(
            groupUniqArray(
                tuple(latest_attribute_value_type, latest_attribute_value_json)
            )
        ) AS attribute_typed_values
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
        self.relation_filters = tuple(
            item
            for item in self.filters
            if UserListQueryBuilderV2._is_relation_filter(item)
        )
        attribute_filter_items: dict[str, list[dict[str, Any]]] = {}
        for item in self.filters:
            if UserListQueryBuilderV2._is_date_filter(item):
                continue
            if UserListQueryBuilderV2._is_relation_filter(item):
                continue
            column_id = item.get("column_id") or item.get("columnId")
            if column_id and column_id not in UserListQueryBuilderV2.OUTPUT_FILTER_MAP:
                attribute_key = str(column_id)
                requested_attribute_keys.append(attribute_key)
                attribute_filter_items.setdefault(attribute_key, []).append(item)
        self.attribute_keys = tuple(dict.fromkeys(requested_attribute_keys))
        unsupported_attribute_keys = unsupported_user_attribute_keys(
            self.attribute_keys
        )
        if unsupported_attribute_keys:
            raise ValueError(
                "Observe Users does not support payload attribute keys: "
                + ", ".join(unsupported_attribute_keys)
            )
        self._attribute_value_types_by_user: dict[
            str, dict[str, dict[str, frozenset[str]]]
        ] = {}
        exact_text_filters: dict[str, tuple[str, ...]] = {}
        for attribute_key, items in attribute_filter_items.items():
            values: list[str] = []
            acceleratable = True
            for item in items:
                config = item.get("filter_config") or {}
                operation = config.get("filter_op") or config.get("filterOp")
                filter_type = config.get("filter_type") or config.get("filterType")
                raw_value = config.get("filter_value", config.get("filterValue"))
                raw_values = raw_value if isinstance(raw_value, list) else [raw_value]
                value_types = config.get("attribute_value_types")
                if (
                    operation not in {"equals", "in"}
                    or filter_type not in {"text", "string"}
                    or not raw_values
                    or any(not isinstance(value, str) for value in raw_values)
                    or (
                        value_types is not None
                        and (
                            not isinstance(value_types, list)
                            or len(value_types) != len(raw_values)
                            or any(
                                value_type not in {None, "string"}
                                for value_type in value_types
                            )
                        )
                    )
                ):
                    acceleratable = False
                    break
                values.extend(raw_values)
            if acceleratable and values:
                exact_text_filters[attribute_key] = tuple(
                    dict.fromkeys(value.lower() for value in values)
                )
        # This is only a candidate accelerator. A latest-state replay still
        # decides every value before Python applies the complete filter list.
        self.attribute_exact_text_filters = exact_text_filters
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
            and not UserListQueryBuilderV2._is_relation_filter(item)
        }
        self.metric_keys = frozenset(
            (self.requested_columns | filter_columns) & _USER_LIST_EXTRA_METRIC_FIELDS
        )
        # The fast presentation count uses latest physical session ids without
        # canonical session-remap folding. A num_sessions predicate must keep
        # the separate remap-aware metric replay so membership stays exact.
        self.approximate_num_sessions = bool(
            "num_sessions" in self.requested_columns
            and not {"num_sessions", "avg_session_duration"} & filter_columns
        )
        self.needs_evals = bool(
            (self.requested_columns | filter_columns) & _USER_LIST_EVAL_FIELDS
        )
        self.filters_need_enrichment = bool(
            self.relation_filters
            or attribute_filter_items
            or filter_columns
            & (_USER_LIST_EXTRA_METRIC_FIELDS | _USER_LIST_EVAL_FIELDS)
        )
        self._relation_eval_metadata_cache: dict[str, EvalFilterMetadata] | None = None
        self._relation_matching_user_ids: set[str] = set()
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
        embedded_fields = getattr(builder, "embedded_page_metric_fields", frozenset())
        if not isinstance(embedded_fields, (set, frozenset)):
            embedded_fields = frozenset()
        queries = builder.build_requested_page_metric_queries(
            [str(value) for value in end_user_ids],
            self.metric_keys - embedded_fields,
        )
        if not queries:
            return {}
        analytics = V2AnalyticsQueryService()
        merged: dict[str, dict] = {}
        for query, params, _fields in queries:
            result = analytics.execute_ch_query(
                query,
                params,
                timeout_ms=deadline.remaining_ms(timeout_cap_ms),
                settings=_page_replay_read_settings(
                    max_result_rows=max(1, len(end_user_ids))
                ),
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
        collected: dict[
            str,
            dict[str, dict[tuple[str, str], tuple[object, str]]],
        ] = {}

        def _collect(rows_to_collect: list[dict]) -> None:
            for attr_row in rows_to_collect:
                uid = str(attr_row.get("end_user_id", ""))
                key = str(attr_row.get("attribute_key", ""))
                if not uid or not key:
                    continue
                typed_values = attr_row.get("attribute_typed_values")
                if typed_values is not None:
                    raw_values = [
                        (str(value_type or ""), raw_value)
                        for value_type, raw_value in typed_values
                    ]
                else:
                    legacy_values = attr_row.get("attribute_values_json")
                    raw_values = None
                    if legacy_values is not None:
                        if not isinstance(legacy_values, (list, tuple)):
                            legacy_values = [legacy_values]
                        raw_values = [("", raw_value) for raw_value in legacy_values]
                if raw_values is None:
                    # Compatibility with a rolling deploy/test double using
                    # the earlier one-value projected response.
                    raw_values = [("", attr_row.get("attribute_value_json", ""))]
                values = collected.setdefault(uid, {}).setdefault(key, {})
                for storage_type, raw in raw_values:
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
                    if not storage_type:
                        storage_type = self._inferred_attribute_storage_type(value)
                    canonical = self._canonical_filter_value(value)
                    values[(storage_type, canonical)] = (value, storage_type)

        def _read_key_bucket(
            keys: tuple[str, ...],
            bucket_start: datetime | None,
            bucket_end: datetime | None,
            *,
            candidate_text_values_by_key: dict[str, tuple[str, ...]] | None = None,
        ) -> None:
            attr_query, attr_params = _users_attr_enrichment_query(
                project_id=self.project_id,
                project_ids=self.scoped_project_ids,
                attribute_keys=keys,
                start_date=bucket_start,
                end_date=bucket_end,
                candidate_end_user_id_map=candidate_end_user_id_map,
                candidate_text_values_by_key=candidate_text_values_by_key,
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
                    settings=_page_replay_read_settings(
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
                _read_key_bucket(
                    keys,
                    bucket_start,
                    midpoint,
                    candidate_text_values_by_key=candidate_text_values_by_key,
                )
                _read_key_bucket(
                    keys,
                    midpoint,
                    bucket_end,
                    candidate_text_values_by_key=candidate_text_values_by_key,
                )
                return
            _collect(list(attr_result.data or ()))

        accelerated_keys = tuple(
            key
            for key in self.attribute_keys
            if key in self.attribute_exact_text_filters
        )
        ordinary_keys = tuple(
            key
            for key in self.attribute_keys
            if key not in self.attribute_exact_text_filters
        )
        key_batches = [
            ((key,), {key: self.attribute_exact_text_filters[key]})
            for key in accelerated_keys
        ]
        key_batches.extend(
            (
                ordinary_keys[
                    key_start : key_start + _USER_LIST_ATTRIBUTE_KEY_BATCH_SIZE
                ],
                None,
            )
            for key_start in range(
                0, len(ordinary_keys), _USER_LIST_ATTRIBUTE_KEY_BATCH_SIZE
            )
        )
        for keys, candidate_values in key_batches:
            _read_key_bucket(
                keys,
                start_date,
                end_date,
                candidate_text_values_by_key=candidate_values,
            )

        user_attrs: dict[str, dict[str, object]] = {}
        for uid, attributes in collected.items():
            for key, typed_values in attributes.items():
                ordered_records = [
                    typed_values[value_key] for value_key in sorted(typed_values)
                ]
                ordered_values = [value for value, _storage_type in ordered_records]
                user_attrs.setdefault(uid, {})[key] = (
                    ordered_values[0] if len(ordered_values) == 1 else ordered_values
                )
                types_by_value: dict[str, set[str]] = {}
                for value, storage_type in ordered_records:
                    types_by_value.setdefault(
                        self._canonical_filter_value(value), set()
                    ).add(storage_type)
                self._attribute_value_types_by_user.setdefault(uid, {})[key] = {
                    value: frozenset(storage_types)
                    for value, storage_types in types_by_value.items()
                }
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
            settings=_page_replay_read_settings(
                max_result_rows=max(1, len(end_user_ids))
            ),
        )
        return {str(row.get("end_user_id", "")): row for row in eval_result.data}

    def _relation_eval_metadata(self) -> dict[str, EvalFilterMetadata]:
        """Resolve custom-eval metadata once for all finite cursor batches."""

        if self._relation_eval_metadata_cache is None:
            eval_ids = {
                str(item.get("column_id") or item.get("columnId"))
                for item in self.relation_filters
                if UserListQueryBuilderV2._filter_col_type(item) == "EVAL_METRIC"
                and (item.get("column_id") or item.get("columnId"))
            }
            self._relation_eval_metadata_cache = {
                eval_id: resolve_eval_filter_metadata(
                    eval_id,
                    self.scoped_project_ids,
                )
                for eval_id in eval_ids
            }
        return self._relation_eval_metadata_cache

    def _read_relation_filter_matches(
        self,
        rows: list[dict],
        builder: UserListQueryBuilderV2,
        deadline: ReadDeadline,
    ) -> set[str]:
        """Return finite page users satisfying all eval/annotation filters."""

        if not rows or not self.relation_filters:
            return {str(row.get("end_user_id")) for row in rows if row.get("end_user_id")}
        query, params = builder.build_relation_filter_user_query(
            self.relation_filters,
            eval_filter_metadata=self._relation_eval_metadata(),
        )
        if not query:
            return set()
        result = V2AnalyticsQueryService().execute_ch_query(
            query,
            params,
            timeout_ms=deadline.remaining_ms(USER_LIST_ENRICHMENT_TIMEOUT_MS),
            settings=_page_replay_read_settings(max_result_rows=max(1, len(rows))),
        )
        return {
            str(row.get("end_user_id"))
            for row in result.data or ()
            if row.get("end_user_id")
        }

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
        # one request cannot multiply the 36 GiB ceiling by three.
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
        window_start: datetime,
        window_end: datetime,
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
            window_start=window_start,
            window_end=window_end,
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
        # touched groups and retain their greatest raw (time, id) tuple. That
        # tuple emits the canonical survivor; every lower alias is consumed as
        # a cursor checkpoint without becoming a duplicate public row.
        remap_query, remap_params = builder.build_dimension_survivor_query(
            candidate_ids,
            window_start=window_start,
            window_end=window_end,
        )
        remap_result = V2AnalyticsQueryService().execute_ch_query(
            remap_query,
            remap_params,
            timeout_ms=deadline.remaining_ms(USER_LIST_QUERY_TIMEOUT_MS),
            settings=_page_read_settings(max_result_rows=_USER_LIST_ATTR_RESULT_ROWS),
        )
        survivor_by_id: dict[str, str] = {}
        aliases_by_survivor: dict[str, set[str]] = {}
        group_order_by_survivor: dict[str, tuple[Any, str]] = {}
        for row in remap_result.data or []:
            any_id = str(row.get("any_id") or "")
            survivor_id = str(row.get("survivor_id") or "")
            if not any_id or not survivor_id:
                continue
            survivor_by_id[any_id] = survivor_id
            aliases_by_survivor.setdefault(survivor_id, set()).add(any_id)
            group_order_time = row.get("group_order_time")
            group_order_id = str(row.get("group_order_id") or "")
            if group_order_time is not None and group_order_id:
                group_order_by_survivor[survivor_id] = (
                    group_order_time,
                    group_order_id,
                )
        for candidate in candidates:
            candidate_id = str(candidate.get("end_user_id", ""))
            candidate_order_time = candidate.get("first_seen")
            survivor_id = survivor_by_id.get(candidate_id, candidate_id)
            candidate["_candidate_order_time"] = candidate_order_time
            candidate["_candidate_order_id"] = candidate_id
            group_order = group_order_by_survivor.get(
                survivor_id,
                (candidate_order_time, candidate_id),
            )
            is_group_max = (
                candidate_order_time == group_order[0]
                and candidate_id == group_order[1]
            )
            candidate["_is_survivor_candidate"] = is_group_max
            if is_group_max:
                scan_ids = aliases_by_survivor.get(survivor_id, set()) | {
                    candidate_id,
                    survivor_id,
                }
                candidate["_candidate_scan_end_user_ids"] = tuple(sorted(scan_ids))
                # Exact replay and the public response are survivor keyed. The
                # hidden raw fields above remain the only cursor order source.
                candidate["end_user_id"] = survivor_id
        return candidates

    def _exact_candidate_builder(
        self,
        *,
        candidate_ids: list[str],
        candidate_scan_ids: list[str] | None,
        candidate_end_user_id_map: dict[str, str] | None,
        frozen_filters: list[dict],
    ) -> UserListQueryBuilderV2:
        date_filters = [
            item
            for item in frozen_filters
            if UserListQueryBuilderV2._is_date_filter(item)
        ]
        return UserListQueryBuilderV2(
            organization_id=self.organization_id,
            project_ids=self.scoped_project_ids,
            search=self.search,
            filters=date_filters,
            limit=len(candidate_ids),
            offset=0,
            candidate_end_user_ids=candidate_ids,
            candidate_scan_end_user_ids=candidate_scan_ids or candidate_ids,
            candidate_end_user_id_map=candidate_end_user_id_map,
            include_num_sessions=self.approximate_num_sessions,
            empty_scope=self.empty_scope,
        )

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
        enrich_rows: bool = True,
    ) -> list[dict]:
        if not candidate_ids:
            return []
        builder = self._exact_candidate_builder(
            candidate_ids=candidate_ids,
            candidate_scan_ids=candidate_scan_ids,
            candidate_end_user_id_map=candidate_end_user_id_map,
            frozen_filters=frozen_filters,
        )
        query, params = builder.build_candidate_page_query()
        result = V2AnalyticsQueryService().execute_ch_query(
            query,
            params,
            timeout_ms=deadline.remaining_ms(USER_LIST_QUERY_TIMEOUT_MS),
            settings=_page_replay_read_settings(
                max_result_rows=max(1, len(candidate_ids))
            ),
        )
        rows = builder.format_rows(result.data)["table"]
        if not rows:
            return []
        if self.relation_filters:
            relation_matches = self._read_relation_filter_matches(
                rows,
                builder,
                deadline,
            )
            self._relation_matching_user_ids.update(relation_matches)
            rows = [
                row
                for row in rows
                if str(row.get("end_user_id", "")) in relation_matches
            ]
            if not rows:
                return []
        if self.approximate_num_sessions:
            for row in rows:
                row["num_sessions_is_approximate"] = True
        if enrich_rows:
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
    def _candidate_value_matches(
        candidate: Any,
        op: str | None,
        expected: Any,
        *,
        case_insensitive: bool = False,
    ) -> bool:
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
                        value,
                        positive_op,
                        expected,
                        case_insensitive=case_insensitive,
                    )
                    for value in values
                )
            return any(
                UsersListManager._candidate_value_matches(
                    value,
                    op,
                    expected,
                    case_insensitive=case_insensitive,
                )
                for value in values
            )
        if op == "is_null":
            return candidate is None
        if op == "is_not_null":
            return candidate is not None
        if op in {"in", "not_in"}:
            expected_values = expected if isinstance(expected, list) else [expected]
            left = UsersListManager._canonical_filter_value(candidate)
            if case_insensitive:
                left = left.lower()
            matched = any(
                left
                == (
                    UsersListManager._canonical_filter_value(value).lower()
                    if case_insensitive
                    else UsersListManager._canonical_filter_value(value)
                )
                for value in expected_values
            )
            return not matched if op == "not_in" else matched
        if op in {"equals", "not_equals"}:
            left = UsersListManager._canonical_filter_value(candidate)
            right = UsersListManager._canonical_filter_value(expected)
            if case_insensitive:
                left, right = left.lower(), right.lower()
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
    def _inferred_attribute_storage_type(value: Any) -> str:
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, (int, float, Decimal)):
            return "number"
        if isinstance(value, str):
            return "string"
        return "json"

    def _attribute_value_matches(
        self,
        *,
        row: dict[str, Any],
        key: str,
        config: dict[str, Any],
    ) -> bool:
        candidate = row.get(key)
        operation = config.get("filter_op") or config.get("filterOp")
        expected = config.get("filter_value", config.get("filterValue"))
        filter_type = str(config.get("filter_type") or config.get("filterType") or "")
        default_storage_type = {
            "text": "string",
            "string": "string",
            "number": "number",
            "boolean": "boolean",
            "array": "json",
            "map": "json",
        }.get(filter_type)
        candidate_values = (
            list(candidate)
            if isinstance(candidate, (list, tuple, set))
            else [candidate]
        )
        type_map = self._attribute_value_types_by_user.get(
            str(row.get("end_user_id", "")), {}
        ).get(key, {})

        def storage_types(value: Any) -> frozenset[str]:
            recorded = type_map.get(self._canonical_filter_value(value))
            if recorded:
                return recorded
            return frozenset({self._inferred_attribute_storage_type(value)})

        selected_types = config.get("attribute_value_types")
        if selected_types is not None and operation in {"in", "not_in"}:
            expected_values = expected if isinstance(expected, list) else [expected]
            matches = any(
                selected_type in storage_types(value)
                and self._candidate_value_matches(
                    value,
                    "equals",
                    expected_value,
                    case_insensitive=selected_type == "string",
                )
                for value in candidate_values
                for expected_value, selected_type in zip(
                    expected_values,
                    selected_types,
                    strict=True,
                )
            )
            return not matches if operation == "not_in" else matches

        typed_values = [
            value
            for value in candidate_values
            if default_storage_type is None
            or default_storage_type in storage_types(value)
        ]
        if operation == "is_null":
            return not typed_values or all(value is None for value in typed_values)
        if operation == "is_not_null":
            return any(value is not None for value in typed_values)
        return self._candidate_value_matches(
            typed_values,
            operation,
            expected,
            case_insensitive=default_storage_type == "string",
        )

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
            if UserListQueryBuilderV2._is_relation_filter(item):
                if (
                    str(row.get("end_user_id", ""))
                    not in self._relation_matching_user_ids
                ):
                    return False
                continue
            config = item.get("filter_config") or {}
            column_id = item.get("column_id") or item.get("columnId")
            if not column_id:
                continue
            if column_id == "eval_score":
                key = "bool_eval_pass_rate"
            else:
                key = UserListQueryBuilderV2.OUTPUT_FILTER_MAP.get(column_id, column_id)
            if column_id not in UserListQueryBuilderV2.OUTPUT_FILTER_MAP:
                matched = self._attribute_value_matches(row=row, key=key, config=config)
            else:
                matched = self._candidate_value_matches(
                    row.get(key),
                    config.get("filter_op") or config.get("filterOp"),
                    config.get("filter_value", config.get("filterValue")),
                    case_insensitive=(
                        config.get("filter_type") or config.get("filterType")
                    )
                    in {"text", "string"},
                )
            if not matched:
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
        presentation_candidate_map: dict[str, str] = {}
        for _ in range(USER_LIST_MAX_CANDIDATE_BATCHES):
            if self.attribute_exact_text_filters:
                candidate_batch_size = USER_LIST_ATTRIBUTE_FILTER_CANDIDATE_BATCH_SIZE
            elif published:
                try:
                    # Reserve enough wall for an up-to-eight-user refill plus
                    # its finite seed/remap. The advancing checkpoint makes the
                    # already exact rows resumable when that budget is absent.
                    deadline.remaining_ms(floor_ms=USER_LIST_REFILL_MIN_BUDGET_MS)
                except ReadDeadlineExceeded:
                    has_more = True
                    break
                remaining_slots = page_size - len(published)
                # The first production batch retained 22/25 candidates. Use a
                # conservative 80% survival floor for refills, while keeping
                # every incremental replay between four and eight users.
                candidate_batch_size = min(
                    USER_LIST_REFILL_MAX_CANDIDATES,
                    max(
                        USER_LIST_REFILL_MIN_CANDIDATES,
                        (remaining_slots * 5 + 3) // 4,
                    ),
                )
            else:
                candidate_batch_size = USER_LIST_CANDIDATE_BATCH_SIZE
            try:
                candidate_rows = self._read_dimension_candidates(
                    deadline=deadline,
                    limit=candidate_batch_size + 1,
                    before_first_seen=before_first_seen,
                    before_end_user_id=before_end_user_id,
                    window_start=window_start,
                    window_end=window_end,
                )
                if not candidate_rows:
                    has_more = False
                    break

                batch = candidate_rows[:candidate_batch_size]
                dimension_has_more = len(candidate_rows) > len(batch)
                candidate_ids = [
                    str(row["end_user_id"])
                    for row in batch
                    if row.get("_is_survivor_candidate", True)
                ]
                candidate_ids = list(dict.fromkeys(candidate_ids))
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
                presentation_candidate_map.update(candidate_end_user_id_map)
                exact_rows = self._read_exact_candidate_rows(
                    candidate_ids=candidate_ids,
                    candidate_scan_ids=candidate_scan_ids,
                    candidate_end_user_id_map=candidate_end_user_id_map,
                    frozen_filters=frozen_filters,
                    window_start=window_start,
                    window_end=window_end,
                    deadline=deadline,
                    enrich_rows=self.filters_need_enrichment,
                )
                exact_by_id = {
                    str(row.get("end_user_id")): row
                    for row in exact_rows
                    if row.get("end_user_id")
                }
                consumed = 0
                for candidate in batch:
                    consumed += 1
                    if not candidate.get("_is_survivor_candidate", True):
                        continue
                    row = exact_by_id.get(str(candidate.get("end_user_id")))
                    if row is None or not self._row_matches_filters(row):
                        continue
                    published.append(row)
                    if len(published) == page_size:
                        unseen_row_proven = any(
                            (
                                later.get("_is_survivor_candidate", True)
                                and exact_by_id.get(str(later.get("end_user_id")))
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
                    consumed_row.get(
                        "_candidate_order_time",
                        consumed_row["first_seen"],
                    ),
                    str(
                        consumed_row.get(
                            "_candidate_order_id",
                            consumed_row["end_user_id"],
                        )
                    ),
                )
                before_first_seen = checkpoint[0]
                before_end_user_id = checkpoint[1]
                unconsumed_candidates = consumed < len(batch)
                has_more = bool(
                    unconsumed_candidates
                    or dimension_has_more
                    or len(batch) == candidate_batch_size
                )
                if len(published) == page_size:
                    break
                if not dimension_has_more and len(batch) < candidate_batch_size:
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

        if (
            published
            and not self.filters_need_enrichment
            and (self.metric_keys or self.attribute_keys or self.needs_evals)
        ):
            published_ids = list(
                dict.fromkeys(
                    str(row["end_user_id"])
                    for row in published
                    if row.get("end_user_id")
                )
            )
            published_id_set = set(published_ids)
            published_candidate_map = {
                scan_id: survivor_id
                for scan_id, survivor_id in presentation_candidate_map.items()
                if survivor_id in published_id_set
            }
            published_scan_ids = list(published_candidate_map) or list(published_ids)
            builder = self._exact_candidate_builder(
                candidate_ids=published_ids,
                candidate_scan_ids=published_scan_ids,
                candidate_end_user_id_map=published_candidate_map,
                frozen_filters=frozen_filters,
            )
            self._enrich_rows(
                published,
                builder,
                deadline,
                start_date=window_start,
                end_date=window_end,
                candidate_scan_ids=published_scan_ids,
                candidate_end_user_id_map=published_candidate_map,
            )

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
            "query_exact": False,
            "query_provenance": "span_user_rollup_end_users_candidate",
            "ordering_exact": False,
            "approximate_fields": (
                ["num_sessions"] if self.approximate_num_sessions else []
            ),
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

    def iter_export_csv(self, *, cursor_read: UserCursorRead) -> Iterator[str]:
        """Serialize one already-materialized bounded Users cursor page.

        The HTTP boundary completes the cursor read before constructing its
        streaming response.  A read timeout can therefore remain a typed 503
        instead of becoming a misleading HTTP 200 containing only a header and
        an in-band failure row.  This method performs no database reads.
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

        payload = cursor_read.payload
        rows = list(payload.get("table") or ())

        for row in rows:
            writer.writerow(
                [
                    self._format_export_cell(row.get(field))
                    for _, field in USERS_EXPORT_COLUMNS
                ]
            )
            yield _drain()

        truncated = bool(
            payload.get("has_more")
            or payload.get("count_is_lower_bound")
            or payload.get("query_complete") is False
        )
        inexact_candidates = bool(
            payload.get("query_exact") is False
            or payload.get("ordering_exact") is False
        )
        raw_approximate_fields = payload.get("approximate_fields") or ()
        if isinstance(raw_approximate_fields, str):
            raw_approximate_fields = (raw_approximate_fields,)
        approximate_fields = tuple(
            dict.fromkeys(str(field) for field in raw_approximate_fields if field)
        )

        marker = ""
        if truncated:
            marker = (
                f"# export truncated after {len(rows)} rows; "
                "refine filters to export a complete bounded page"
            )
            if inexact_candidates:
                marker += "; candidate membership or ordering is inexact"
        elif inexact_candidates:
            marker = (
                "# export candidate membership or ordering is inexact; "
                "results are not an exact ordered population"
            )
        if approximate_fields:
            approximation = "approximate fields: " + ", ".join(approximate_fields)
            marker = f"{marker}; {approximation}" if marker else f"# {approximation}"
        if marker:
            writer.writerow([marker])
            yield _drain()
