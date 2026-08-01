"""
Span Attribute Discovery APIs for ClickHouse.

Endpoints:
1. GET /api/traces/span-attribute-keys/ - Discover all attribute keys for a project
2. GET /api/traces/span-attribute-values/ - Get top values for an attribute key
3. GET /api/traces/span-attribute-detail/<key>/ - Full detail for a specific attribute key
"""

import time
from collections import Counter
from dataclasses import asdict
from datetime import UTC, datetime, timedelta

import structlog
from django.conf import settings
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from tfc.utils.api_contracts import validated_request
from tfc.utils.api_serializers import ApiTextErrorResponseSerializer
from tfc.utils.general_methods import GeneralMethods
from tracer.serializers.span_attributes import (
    SpanAttributeDetailQuerySerializer,
    SpanAttributeDetailResponseSerializer,
    SpanAttributeKeysResponseSerializer,
    SpanAttributeProjectQuerySerializer,
    SpanAttributeValuesQuerySerializer,
    SpanAttributeValuesResponseSerializer,
)
from tracer.services.clickhouse.client import ClickHouseClient, is_clickhouse_enabled
from tracer.services.clickhouse.query_service import (
    GUARANTEED_ROOT_SPAN_ATTRIBUTE_TYPES,
    merge_guaranteed_span_attribute_keys,
)
from tracer.services.clickhouse.read_budget import is_read_budget_error
from tracer.services.clickhouse.span_attribute_lookups import (
    find_attribute_key_for_project,
    list_attribute_keys_for_project,
)
from tracer.utils.workspace_scope import project_queryset_for_request

logger = structlog.get_logger(__name__)

SPAN_ATTRIBUTE_VALUES_LOOKBACK_DAYS = 7
SPAN_ATTRIBUTE_READ_TIMEOUT_MS = 750
# The existing aggregate contains more keys, but this endpoint discovers
# values across all spans. Only verified root attributes are semantically
# equivalent to the root-span rollup.
SPAN_ATTRIBUTE_ROLLUP_KEYS = frozenset({"final_status"})
SPAN_ATTRIBUTE_VALUE_SAMPLE_MAX_ROWS = 2000
SPAN_ATTRIBUTE_READ_SETTINGS = {
    "timeout_overflow_mode": "throw",
    "max_threads": 2,
    "max_memory_usage": 256 * 1024 * 1024,
    "max_bytes_to_read": 1024 * 1024 * 1024,
    "read_overflow_mode": "throw",
    "max_result_rows": SPAN_ATTRIBUTE_VALUE_SAMPLE_MAX_ROWS,
    "result_overflow_mode": "throw",
}

ERROR_RESPONSES = {
    400: ApiTextErrorResponseSerializer,
    404: ApiTextErrorResponseSerializer,
    500: ApiTextErrorResponseSerializer,
    503: ApiTextErrorResponseSerializer,
}


def _project_is_in_request_scope(request, project_id: str) -> bool:
    """Fail closed unless the project belongs to the active org/workspace."""
    return project_queryset_for_request(request).filter(id=project_id).exists()


def _attribute_values_lookback_days() -> int:
    """Use the shared picker lookback while keeping bad config safely bounded."""
    configured = getattr(
        settings,
        "FILTER_VALUES_DEFAULT_LOOKBACK_DAYS",
        SPAN_ATTRIBUTE_VALUES_LOOKBACK_DAYS,
    )
    try:
        return min(max(int(configured), 1), 30)
    except (TypeError, ValueError):
        return SPAN_ATTRIBUTE_VALUES_LOOKBACK_DAYS


def _attribute_values_window() -> tuple[datetime, datetime]:
    window_end = timezone.now().astimezone(UTC)
    return window_end - timedelta(days=_attribute_values_lookback_days()), window_end


def _covered_attribute_rollup_window(
    window_start: datetime, window_end: datetime
) -> tuple[datetime, datetime] | None:
    """Return the exact complete-hour interval covered by the picker rollup."""
    if not getattr(settings, "TRACE_FILTER_VALUES_ATTR_ROLLUP_ENABLED", False):
        return None

    covered_since = getattr(settings, "DASHBOARD_ATTR_ROLLUP_COVERED_SINCE", None)
    if not isinstance(covered_since, datetime):
        return None
    if covered_since.tzinfo is None:
        covered_since = covered_since.replace(tzinfo=UTC)
    else:
        covered_since = covered_since.astimezone(UTC)

    # The MV updates the active hourly aggregate as spans arrive. Comparing the
    # bucket timestamp with the real request end includes that active hour, so a
    # newly ingested value does not disappear from the picker until the next
    # hour. Keep the real end in response metadata rather than claiming future
    # coverage by rounding it up.
    rollup_end = window_end
    rollup_start = window_start.replace(minute=0, second=0, microsecond=0)
    if rollup_start < covered_since or rollup_start >= rollup_end:
        return None
    return rollup_start, rollup_end


def _covered_attribute_detail_rollup_window(
    window_start: datetime, window_end: datetime
) -> tuple[datetime, datetime] | None:
    """Return an exact rollup interval suitable for complete detail counts.

    Picker suggestions intentionally include partial boundary hours so recent
    options do not disappear. Detail statistics cannot claim completeness over
    that expanded interval, so only exact full-hour request boundaries qualify.
    """
    if any(
        (value.minute, value.second, value.microsecond) != (0, 0, 0)
        for value in (window_start, window_end)
    ):
        return None
    rollup_window = _covered_attribute_rollup_window(window_start, window_end)
    if rollup_window != (window_start, window_end):
        return None
    return rollup_window


def _escaped_like_pattern(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _attribute_root_span_clause(key: str) -> str:
    if key in SPAN_ATTRIBUTE_ROLLUP_KEYS:
        return "AND (parent_span_id IS NULL OR parent_span_id = '') "
    return ""


def _utc_iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _attribute_key_payload(key) -> dict:
    payload = asdict(key)
    return {name: value for name, value in payload.items() if value is not None}


class SpanAttributeKeysView(APIView):
    """
    Browse or exactly probe span attribute keys for a project.

    The default response is a bounded sample across the three typed Maps.
    Supplying ``q`` performs an exact key-existence/type probe.

    GET /api/traces/span-attribute-keys/?project_id=<uuid>[&q=<exact_key>]
    """

    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @validated_request(
        query_serializer=SpanAttributeProjectQuerySerializer,
        responses={200: SpanAttributeKeysResponseSerializer, **ERROR_RESPONSES},
    )
    def get(self, request, *args, **kwargs):
        query_params = request.validated_query_data
        project_id = str(query_params["project_id"])
        exact_key = query_params.get("q")
        if not _project_is_in_request_scope(request, project_id):
            return self._gm.not_found("Project not found")

        if not is_clickhouse_enabled():
            return self._gm.custom_error_response(503, "ClickHouse is not enabled")

        window_start, window_end = _attribute_values_window()
        try:
            if exact_key:
                key = find_attribute_key_for_project(
                    project_id,
                    exact_key,
                    window_start=window_start,
                    window_end=window_end,
                )
                return Response(
                    {
                        "result": [_attribute_key_payload(key)] if key else [],
                        "query_complete": True,
                        "query_status": "complete",
                        "query_window_start": _utc_iso(window_start),
                        "query_window_end": _utc_iso(window_end),
                    },
                    status=200,
                )

            keys = list_attribute_keys_for_project(
                project_id,
                window_start=window_start,
                window_end=window_end,
            )
            return Response(
                {
                    "result": [_attribute_key_payload(k) for k in keys],
                    # Discovery deliberately samples at most 10k rows from
                    # each typed Map so a whale tenant cannot trigger the
                    # Code 396/159 full-map explosion. Guaranteed root keys
                    # (including final_status) and saved eval mappings are
                    # merged separately, but arbitrary rare keys may still be
                    # outside this sample; never label it as exhaustive.
                    "query_complete": False,
                    "query_status": "sampled",
                    "query_error_code": "sample_limit",
                    "query_window_start": _utc_iso(window_start),
                    "query_window_end": _utc_iso(window_end),
                },
                status=200,
            )
        except Exception as e:
            logger.warning(
                "span_attribute_keys_failed",
                project_id=project_id,
                error_type=type(e).__name__,
            )
            if is_read_budget_error(e):
                return Response(
                    {
                        "result": (
                            []
                            if exact_key
                            else merge_guaranteed_span_attribute_keys(
                                [], include_counts=True
                            )
                        ),
                        "query_complete": False,
                        "query_status": "degraded",
                        "query_error_code": "read_budget_exceeded",
                        "query_window_start": _utc_iso(window_start),
                        "query_window_end": _utc_iso(window_end),
                    },
                    status=200,
                )
            return self._gm.internal_server_error_response(
                "Failed to fetch span attribute keys"
            )


class SpanAttributeValuesView(APIView):
    """
    Get top values for a specific span attribute key.

    Returns the most frequent values for the given string attribute key,
    with optional prefix search filtering.

    GET /api/traces/span-attribute-values/?project_id=<uuid>&key=<attr_key>[&q=<search>][&limit=50]
    """

    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @validated_request(
        query_serializer=SpanAttributeValuesQuerySerializer,
        responses={200: SpanAttributeValuesResponseSerializer, **ERROR_RESPONSES},
    )
    def get(self, request, *args, **kwargs):
        query_params = request.validated_query_data
        project_id = str(query_params["project_id"])
        key = query_params["key"]
        q = query_params.get("q")
        limit = query_params.get("limit", 50)

        if not _project_is_in_request_scope(request, project_id):
            return self._gm.not_found("Project not found")

        if not is_clickhouse_enabled():
            return self._gm.custom_error_response(503, "ClickHouse is not enabled")

        window_start, window_end = _attribute_values_window()
        params = {
            "project_id": project_id,
            "key": key,
            "limit": limit,
            "window_start": window_start,
            "window_end": window_end,
        }
        used_rollup = False

        search_clause = ""
        if q:
            params["q_pattern"] = _escaped_like_pattern(q)
            search_clause = "AND attrs_string[%(key)s] LIKE %(q_pattern)s "

        rollup_window = (
            _covered_attribute_rollup_window(window_start, window_end)
            if key in SPAN_ATTRIBUTE_ROLLUP_KEYS
            else None
        )
        if rollup_window is not None:
            used_rollup = True
            rollup_start, rollup_end = rollup_window
            params = {
                "project_id": project_id,
                "key": key,
                "limit": limit,
                "window_start": rollup_start,
                "window_end": rollup_end,
            }
            rollup_search_clause = ""
            if q:
                params["q_pattern"] = _escaped_like_pattern(q)
                rollup_search_clause = "AND attr_value LIKE %(q_pattern)s "
            query = (
                "SELECT attr_value AS value, countMerge(n) AS cnt "
                "FROM dashboard_attr_rollup "
                "WHERE project_id = %(project_id)s "
                "AND attr_key = %(key)s "
                "AND hour >= %(window_start)s "
                "AND hour < %(window_end)s "
                "AND attr_value != '' "
                f"{rollup_search_clause}"
                "GROUP BY value "
                "ORDER BY cnt DESC, value "
                "LIMIT %(limit)s"
            )
        else:
            sample_limit = max(
                1000,
                min(limit * 10, SPAN_ATTRIBUTE_VALUE_SAMPLE_MAX_ROWS),
            )
            params["sample_limit"] = sample_limit
            root_clause = _attribute_root_span_clause(key)
            query = (
                "SELECT attrs_string[%(key)s] AS value "
                "FROM spans "
                "PREWHERE project_id = %(project_id)s "
                "AND start_time >= %(window_start)s "
                "AND start_time < %(window_end)s "
                "AND is_deleted = 0 "
                "WHERE mapContains(attrs_string, %(key)s) "
                "AND attrs_string[%(key)s] != '' "
                f"{root_clause}"
                f"{search_clause}"
                "LIMIT %(sample_limit)s"
            )

        try:
            client = ClickHouseClient()
            rows, _, query_time_ms = client.execute_read(
                query,
                params,
                timeout_ms=SPAN_ATTRIBUTE_READ_TIMEOUT_MS,
                settings=SPAN_ATTRIBUTE_READ_SETTINGS,
            )

            if used_rollup:
                result = [{"value": row[0], "count": row[1]} for row in rows]
                query_complete = True
            else:
                counts = Counter(
                    str(row[0]) for row in rows if row and row[0] not in (None, "")
                )
                result = [
                    {"value": value, "count": count}
                    for value, count in sorted(
                        counts.items(),
                        key=lambda item: (-item[1], item[0].casefold()),
                    )[:limit]
                ]
                query_complete = len(rows) < params["sample_limit"]

            logger.info(
                "span_attribute_values_fetched",
                project_id=project_id,
                key=key,
                value_count=len(result),
                query_time_ms=query_time_ms,
            )

            payload = {
                "result": result,
                "query_complete": query_complete,
                "query_status": "complete" if query_complete else "sampled",
                "query_window_start": _utc_iso(params["window_start"]),
                "query_window_end": _utc_iso(params["window_end"]),
            }
            if not query_complete:
                payload["query_error_code"] = "sample_limit"
            return Response(payload, status=200)

        except Exception as e:
            logger.warning(
                "span_attribute_values_failed",
                project_id=project_id,
                key=key,
                error_type=type(e).__name__,
            )
            if is_read_budget_error(e):
                return Response(
                    {
                        "result": [],
                        "query_complete": False,
                        "query_status": "degraded",
                        "query_error_code": "read_budget_exceeded",
                        "query_window_start": _utc_iso(params["window_start"]),
                        "query_window_end": _utc_iso(params["window_end"]),
                    },
                    status=200,
                )
            return self._gm.internal_server_error_response(
                "Failed to fetch span attribute values"
            )


class SpanAttributeDetailView(APIView):
    """
    Full detail for a specific span attribute key.

    Uses a validated caller-supplied type when available. Legacy callers are
    supported by bounded per-map existence probes, with mixed types reported
    as ambiguous instead of selecting whichever row ClickHouse returns first.
    It then returns type-appropriate statistics:
      - string: top values with percentages
      - number: min, max, avg, p50, p95
      - boolean: true/false distribution

    GET /api/traces/span-attribute-detail/?project_id=<uuid>&key=<attr_key>&type=<type>
    """

    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @validated_request(
        query_serializer=SpanAttributeDetailQuerySerializer,
        responses={200: SpanAttributeDetailResponseSerializer, **ERROR_RESPONSES},
    )
    def get(self, request, *args, **kwargs):
        query_params = request.validated_query_data
        project_id = str(query_params["project_id"])
        key = query_params["key"]
        requested_type = query_params.get("type")

        if not _project_is_in_request_scope(request, project_id):
            return self._gm.not_found("Project not found")

        if not is_clickhouse_enabled():
            return self._gm.custom_error_response(503, "ClickHouse is not enabled")

        window_start, window_end = _attribute_values_window()
        params = {
            "project_id": project_id,
            "key": key,
            "window_start": window_start,
            "window_end": window_end,
        }

        guaranteed_type = GUARANTEED_ROOT_SPAN_ATTRIBUTE_TYPES.get(key)
        if (
            requested_type is not None
            and guaranteed_type is not None
            and requested_type != guaranteed_type
        ):
            return self._gm.bad_request(
                f"Attribute key has guaranteed type '{guaranteed_type}'; "
                "the requested type does not match"
            )

        attr_type = guaranteed_type or requested_type
        try:
            client = ClickHouseClient()
            rollup_window = (
                _covered_attribute_detail_rollup_window(window_start, window_end)
                if key in SPAN_ATTRIBUTE_ROLLUP_KEYS and attr_type == "string"
                else None
            )
            if rollup_window is not None:
                params["window_start"], params["window_end"] = rollup_window
                return self._string_rollup_detail(client, params)

            if attr_type is None:
                detected_types = self._detect_types(client, params)
                if len(detected_types) > 1:
                    return self._mixed_type_response(detected_types)
                attr_type = detected_types[0] if detected_types else None

            if attr_type == "string":
                return self._string_detail(client, params)
            elif attr_type == "number":
                return self._number_detail(client, params)
            elif attr_type == "boolean":
                return self._boolean_detail(client, params)
            else:
                return self._gm.not_found(f"Attribute key '{key}' not found in project")

        except Exception as e:
            logger.warning(
                "span_attribute_detail_failed",
                project_id=project_id,
                key=key,
                error_type=type(e).__name__,
            )
            if is_read_budget_error(e):
                payload = {
                    "key": key,
                    "query_complete": False,
                    "query_status": "degraded",
                    "query_error_code": "read_budget_exceeded",
                    "query_window_start": _utc_iso(window_start),
                    "query_window_end": _utc_iso(window_end),
                }
                if attr_type is not None:
                    payload["type"] = attr_type
                return Response(payload, status=200)
            return self._gm.internal_server_error_response(
                "Failed to fetch span attribute detail"
            )

    def _mixed_type_response(self, detected_types: tuple[str, ...]) -> Response:
        """Return a text-envelope ambiguity error without reflecting the key."""
        type_list = ", ".join(detected_types)
        return self._gm.bad_request(
            "Attribute key has multiple stored types "
            f"({type_list}); specify the type query parameter"
        )

    def _detect_types(self, client: ClickHouseClient, params: dict) -> tuple[str, ...]:
        """Return every typed Map containing the key under one shared deadline.

        A key may legally occur in more than one typed Map across spans. One
        combined ``LIMIT 1`` query therefore depends on physical row order and
        can dispatch the same request differently after merges. Independent
        existence probes keep each read bounded while checking all types in a
        stable order.
        """
        root_clause = _attribute_root_span_clause(params["key"])
        deadline = time.monotonic() + SPAN_ATTRIBUTE_READ_TIMEOUT_MS / 1000
        detected_types = []
        for attr_type, map_name in (
            ("string", "attrs_string"),
            ("number", "attrs_number"),
            ("boolean", "attrs_bool"),
        ):
            remaining_ms = int((deadline - time.monotonic()) * 1000)
            if remaining_ms <= 0:
                raise TimeoutError("Span attribute type probe deadline exceeded")
            type_query = f"""
                SELECT 1
                FROM spans
                PREWHERE project_id = %(project_id)s
                  AND start_time >= %(window_start)s
                  AND start_time < %(window_end)s
                  AND is_deleted = 0
                  {root_clause}
                WHERE mapContains({map_name}, %(key)s)
                LIMIT 1
            """
            rows, _, _ = client.execute_read(
                type_query,
                params,
                timeout_ms=min(SPAN_ATTRIBUTE_READ_TIMEOUT_MS, remaining_ms),
                settings=SPAN_ATTRIBUTE_READ_SETTINGS,
            )
            if rows:
                detected_types.append(attr_type)
        return tuple(detected_types)

    def _string_detail(self, client: ClickHouseClient, params: dict) -> Response:
        """Return representative top values from a bounded row sample."""
        root_clause = _attribute_root_span_clause(params["key"])
        query_params = {
            **params,
            "sample_limit": SPAN_ATTRIBUTE_VALUE_SAMPLE_MAX_ROWS,
        }
        query = f"""
            SELECT attrs_string[%(key)s] AS value
            FROM spans
            PREWHERE project_id = %(project_id)s
              AND start_time >= %(window_start)s
              AND start_time < %(window_end)s
              AND is_deleted = 0
              {root_clause}
            WHERE mapContains(attrs_string, %(key)s)
              AND attrs_string[%(key)s] != ''
            LIMIT %(sample_limit)s
        """
        rows, _, query_time_ms = client.execute_read(
            query,
            query_params,
            timeout_ms=SPAN_ATTRIBUTE_READ_TIMEOUT_MS,
            settings=SPAN_ATTRIBUTE_READ_SETTINGS,
        )
        if not rows:
            return self._gm.not_found("No data found for this attribute type")

        counts = Counter(
            str(row[0]) for row in rows if row and row[0] not in (None, "")
        )
        ordered_values = sorted(
            counts.items(),
            key=lambda item: (-item[1], item[0].casefold()),
        )
        sampled = len(rows) >= query_params["sample_limit"] or len(ordered_values) > 100
        total_count = sum(counts.values())
        unique_values = len(counts)
        top_values = [
            {
                "value": value,
                "count": count,
                "percentage": (
                    round(count / total_count * 100, 1) if total_count > 0 else 0
                ),
            }
            for value, count in ordered_values[:100]
        ]

        logger.info(
            "span_attribute_string_detail_fetched",
            project_id=params["project_id"],
            key=params["key"],
            unique_values=unique_values,
            query_time_ms=query_time_ms,
        )

        payload = {
            "key": params["key"],
            "type": "string",
            "count": total_count,
            "unique_values": unique_values,
            "top_values": top_values,
            "query_complete": not sampled,
            "query_status": "sampled" if sampled else "complete",
            "query_window_start": _utc_iso(params["window_start"]),
            "query_window_end": _utc_iso(params["window_end"]),
        }
        if sampled:
            payload["query_error_code"] = "sample_limit"
        return Response(payload, status=200)

    def _string_rollup_detail(self, client: ClickHouseClient, params: dict) -> Response:
        """Return complete covered-window counts from the root attribute rollup."""
        query = (
            "SELECT attr_value AS value, countMerge(n) AS cnt "
            "FROM dashboard_attr_rollup "
            "WHERE project_id = %(project_id)s "
            "AND attr_key = %(key)s "
            "AND hour >= %(window_start)s "
            "AND hour < %(window_end)s "
            "AND attr_value != '' "
            "GROUP BY value "
            "ORDER BY cnt DESC, value "
            "LIMIT 101"
        )
        rows, _, query_time_ms = client.execute_read(
            query,
            params,
            timeout_ms=SPAN_ATTRIBUTE_READ_TIMEOUT_MS,
            settings=SPAN_ATTRIBUTE_READ_SETTINGS,
        )
        if not rows:
            return self._gm.not_found(
                f"Attribute key '{params['key']}' not found in project"
            )

        sampled = len(rows) > 100
        rows = rows[:100]
        total_count = sum(row[1] for row in rows)
        top_values = [
            {
                "value": row[0],
                "count": row[1],
                "percentage": (
                    round(row[1] / total_count * 100, 1) if total_count > 0 else 0
                ),
            }
            for row in rows
        ]
        logger.info(
            "span_attribute_string_rollup_detail_fetched",
            project_id=params["project_id"],
            key=params["key"],
            unique_values=len(rows),
            query_time_ms=query_time_ms,
        )
        payload = {
            "key": params["key"],
            "type": "string",
            "count": total_count,
            "unique_values": len(rows),
            "top_values": top_values,
            "query_complete": not sampled,
            "query_status": "sampled" if sampled else "complete",
            "query_window_start": _utc_iso(params["window_start"]),
            "query_window_end": _utc_iso(params["window_end"]),
        }
        if sampled:
            payload["query_error_code"] = "sample_limit"
        return Response(payload, status=200)

    def _number_detail(self, client: ClickHouseClient, params: dict) -> Response:
        """Return numeric statistics over a bounded representative sample."""
        root_clause = _attribute_root_span_clause(params["key"])
        query_params = {
            **params,
            "sample_limit": SPAN_ATTRIBUTE_VALUE_SAMPLE_MAX_ROWS,
        }
        query = f"""
            SELECT
                count() AS cnt,
                min(value) AS min_val,
                max(value) AS max_val,
                avg(value) AS avg_val,
                quantile(0.50)(value) AS p50,
                quantile(0.95)(value) AS p95
            FROM
            (
                SELECT attrs_number[%(key)s] AS value
                FROM spans
                PREWHERE project_id = %(project_id)s
                  AND start_time >= %(window_start)s
                  AND start_time < %(window_end)s
                  AND is_deleted = 0
                  {root_clause}
                WHERE mapContains(attrs_number, %(key)s)
                LIMIT %(sample_limit)s
            )
        """
        rows, _, query_time_ms = client.execute_read(
            query,
            query_params,
            timeout_ms=SPAN_ATTRIBUTE_READ_TIMEOUT_MS,
            settings=SPAN_ATTRIBUTE_READ_SETTINGS,
        )

        if not rows or not rows[0] or rows[0][0] == 0:
            return self._gm.not_found("No data found for this attribute type")

        row = rows[0]
        sampled = row[0] >= query_params["sample_limit"]

        logger.info(
            "span_attribute_number_detail_fetched",
            project_id=params["project_id"],
            key=params["key"],
            count=row[0],
            query_time_ms=query_time_ms,
        )

        payload = {
            "key": params["key"],
            "type": "number",
            "count": row[0],
            "min": row[1],
            "max": row[2],
            "avg": round(row[3], 4) if row[3] is not None else None,
            "p50": round(row[4], 4) if row[4] is not None else None,
            "p95": round(row[5], 4) if row[5] is not None else None,
            "query_complete": not sampled,
            "query_status": "sampled" if sampled else "complete",
            "query_window_start": _utc_iso(params["window_start"]),
            "query_window_end": _utc_iso(params["window_end"]),
        }
        if sampled:
            payload["query_error_code"] = "sample_limit"
        return Response(payload, status=200)

    def _boolean_detail(self, client: ClickHouseClient, params: dict) -> Response:
        """Return true/false distribution from a bounded row sample."""
        root_clause = _attribute_root_span_clause(params["key"])
        query_params = {
            **params,
            "sample_limit": SPAN_ATTRIBUTE_VALUE_SAMPLE_MAX_ROWS,
        }
        query = f"""
            SELECT attrs_bool[%(key)s] AS value
            FROM spans
            PREWHERE project_id = %(project_id)s
              AND start_time >= %(window_start)s
              AND start_time < %(window_end)s
              AND is_deleted = 0
              {root_clause}
            WHERE mapContains(attrs_bool, %(key)s)
            LIMIT %(sample_limit)s
        """
        rows, _, query_time_ms = client.execute_read(
            query,
            query_params,
            timeout_ms=SPAN_ATTRIBUTE_READ_TIMEOUT_MS,
            settings=SPAN_ATTRIBUTE_READ_SETTINGS,
        )
        if not rows:
            return self._gm.not_found("No data found for this attribute type")

        counts = Counter(row[0] for row in rows if row)
        ordered_values = sorted(
            counts.items(),
            key=lambda item: (-item[1], str(item[0])),
        )
        total_count = sum(counts.values())
        top_values = [
            {
                "value": value,
                "count": count,
                "percentage": (
                    round(count / total_count * 100, 1) if total_count > 0 else 0
                ),
            }
            for value, count in ordered_values
        ]
        sampled = len(rows) >= query_params["sample_limit"]

        logger.info(
            "span_attribute_boolean_detail_fetched",
            project_id=params["project_id"],
            key=params["key"],
            count=total_count,
            query_time_ms=query_time_ms,
        )

        payload = {
            "key": params["key"],
            "type": "boolean",
            "count": total_count,
            "unique_values": len(counts),
            "top_values": top_values,
            "query_complete": not sampled,
            "query_status": "sampled" if sampled else "complete",
            "query_window_start": _utc_iso(params["window_start"]),
            "query_window_end": _utc_iso(params["window_end"]),
        }
        if sampled:
            payload["query_error_code"] = "sample_limit"
        return Response(payload, status=200)
