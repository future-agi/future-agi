"""
Span Attribute Discovery APIs for ClickHouse.

Endpoints:
1. GET /api/traces/span-attribute-keys/ - Discover all attribute keys for a project
2. GET /api/traces/span-attribute-values/ - Get top values for an attribute key
3. GET /api/traces/span-attribute-detail/<key>/ - Full detail for a specific attribute key
"""

from dataclasses import asdict

import structlog
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
from tracer.services.clickhouse.span_attribute_lookups import (
    list_attribute_keys_for_project,
)

logger = structlog.get_logger(__name__)

ERROR_RESPONSES = {
    400: ApiTextErrorResponseSerializer,
    404: ApiTextErrorResponseSerializer,
    500: ApiTextErrorResponseSerializer,
    503: ApiTextErrorResponseSerializer,
}


class SpanAttributeKeysView(APIView):
    """
    Discover all span attribute keys for a project.

    Returns every distinct key across the string, number, and boolean attribute
    maps together with its inferred type and occurrence count.

    GET /api/traces/span-attribute-keys/?project_id=<uuid>
    """

    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @validated_request(
        query_serializer=SpanAttributeProjectQuerySerializer,
        responses={200: SpanAttributeKeysResponseSerializer, **ERROR_RESPONSES},
    )
    def get(self, request, *args, **kwargs):
        if not is_clickhouse_enabled():
            return self._gm.custom_error_response(503, "ClickHouse is not enabled")

        project_id = str(request.validated_query_data["project_id"])

        try:
            keys = list_attribute_keys_for_project(project_id)
            return Response(
                {"result": [asdict(k) for k in keys]}, status=200
            )
        except Exception as e:
            logger.error(
                "span_attribute_keys_failed",
                project_id=project_id,
                error=str(e),
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
        if not is_clickhouse_enabled():
            return self._gm.custom_error_response(503, "ClickHouse is not enabled")

        query_params = request.validated_query_data
        project_id = str(query_params["project_id"])
        key = query_params["key"]
        q = query_params.get("q")
        limit = query_params.get("limit", 50)

        params = {
            "project_id": project_id,
            "key": key,
            "limit": limit,
        }

        if q:
            query = """
                SELECT attrs_string[%(key)s] AS value, count() AS cnt
                FROM spans
                WHERE project_id = %(project_id)s
                  AND mapContains(attrs_string, %(key)s)
                  AND attrs_string[%(key)s] != ''
                  AND attrs_string[%(key)s] LIKE %(q_pattern)s
                GROUP BY value
                ORDER BY cnt DESC
                LIMIT %(limit)s
            """
            params["q_pattern"] = f"%{q}%"
        else:
            query = """
                SELECT attrs_string[%(key)s] AS value, count() AS cnt
                FROM spans
                WHERE project_id = %(project_id)s
                  AND mapContains(attrs_string, %(key)s)
                  AND attrs_string[%(key)s] != ''
                GROUP BY value
                ORDER BY cnt DESC
                LIMIT %(limit)s
            """

        try:
            client = ClickHouseClient()
            rows, column_types, query_time_ms = client.execute_read(query, params)

            result = [{"value": row[0], "count": row[1]} for row in rows]

            logger.info(
                "span_attribute_values_fetched",
                project_id=project_id,
                key=key,
                value_count=len(result),
                query_time_ms=query_time_ms,
            )

            return Response({"result": result}, status=200)

        except Exception as e:
            logger.error(
                "span_attribute_values_failed",
                project_id=project_id,
                key=key,
                error=str(e),
            )
            return self._gm.internal_server_error_response(
                "Failed to fetch span attribute values"
            )


class SpanAttributeDetailView(APIView):
    """
    Full detail for a specific span attribute key.

    Determines the attribute type by probing which map contains the key, then
    returns type-appropriate statistics:
      - string: top values with percentages
      - number: min, max, avg, p50, p95
      - boolean: true/false distribution

    GET /api/traces/span-attribute-detail/?project_id=<uuid>&key=<attr_key>
    """

    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @validated_request(
        query_serializer=SpanAttributeDetailQuerySerializer,
        responses={200: SpanAttributeDetailResponseSerializer, **ERROR_RESPONSES},
    )
    def get(self, request, *args, **kwargs):
        if not is_clickhouse_enabled():
            return self._gm.custom_error_response(503, "ClickHouse is not enabled")

        query_params = request.validated_query_data
        project_id = str(query_params["project_id"])
        key = query_params["key"]

        params = {"project_id": project_id, "key": key}

        try:
            client = ClickHouseClient()
            attr_type = self._detect_type(client, params)

            if attr_type == "string":
                return self._string_detail(client, params)
            elif attr_type == "number":
                return self._number_detail(client, params)
            elif attr_type == "boolean":
                return self._boolean_detail(client, params)
            else:
                return self._gm.not_found(f"Attribute key '{key}' not found in project")

        except Exception as e:
            logger.error(
                "span_attribute_detail_failed",
                project_id=project_id,
                key=key,
                error=str(e),
            )
            return self._gm.internal_server_error_response(
                "Failed to fetch span attribute detail"
            )

    def _detect_type(self, client: ClickHouseClient, params: dict) -> str | None:
        """Determine which attribute map contains the given key."""
        type_query = """
            SELECT
                countIf(mapContains(attrs_string, %(key)s))  AS str_cnt,
                countIf(mapContains(attrs_number, %(key)s))  AS num_cnt,
                countIf(mapContains(attrs_bool, %(key)s)) AS bool_cnt
            FROM spans
            WHERE project_id = %(project_id)s
        """
        rows, _, _ = client.execute_read(type_query, params)

        if not rows:
            return None

        str_cnt, num_cnt, bool_cnt = rows[0]
        max_cnt = max(str_cnt, num_cnt, bool_cnt)

        if max_cnt == 0:
            return None
        if str_cnt == max_cnt:
            return "string"
        if num_cnt == max_cnt:
            return "number"
        return "boolean"

    def _string_detail(self, client: ClickHouseClient, params: dict) -> Response:
        """Return top values with percentages for a string attribute."""
        query = """
            SELECT
                attrs_string[%(key)s] AS value,
                count() AS cnt
            FROM spans
            WHERE project_id = %(project_id)s
              AND mapContains(attrs_string, %(key)s)
              AND attrs_string[%(key)s] != ''
            GROUP BY value
            ORDER BY cnt DESC
            LIMIT 100
        """
        rows, _, query_time_ms = client.execute_read(query, params)

        total_count = sum(row[1] for row in rows)
        unique_values = len(rows)
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
            "span_attribute_string_detail_fetched",
            project_id=params["project_id"],
            key=params["key"],
            unique_values=unique_values,
            query_time_ms=query_time_ms,
        )

        return Response(
            {
                "key": params["key"],
                "type": "string",
                "count": total_count,
                "unique_values": unique_values,
                "top_values": top_values,
            },
            status=200,
        )

    def _number_detail(self, client: ClickHouseClient, params: dict) -> Response:
        """Return numeric statistics for a number attribute."""
        query = """
            SELECT
                count()                                          AS cnt,
                min(attrs_number[%(key)s])                      AS min_val,
                max(attrs_number[%(key)s])                      AS max_val,
                avg(attrs_number[%(key)s])                      AS avg_val,
                quantile(0.50)(attrs_number[%(key)s])           AS p50,
                quantile(0.95)(attrs_number[%(key)s])           AS p95
            FROM spans
            WHERE project_id = %(project_id)s
              AND mapContains(attrs_number, %(key)s)
        """
        rows, _, query_time_ms = client.execute_read(query, params)

        if not rows:
            return self._gm.not_found("No data found for this attribute")

        row = rows[0]

        logger.info(
            "span_attribute_number_detail_fetched",
            project_id=params["project_id"],
            key=params["key"],
            count=row[0],
            query_time_ms=query_time_ms,
        )

        return Response(
            {
                "key": params["key"],
                "type": "number",
                "count": row[0],
                "min": row[1],
                "max": row[2],
                "avg": round(row[3], 4) if row[3] is not None else None,
                "p50": round(row[4], 4) if row[4] is not None else None,
                "p95": round(row[5], 4) if row[5] is not None else None,
            },
            status=200,
        )

    def _boolean_detail(self, client: ClickHouseClient, params: dict) -> Response:
        """Return true/false distribution for a boolean attribute."""
        query = """
            SELECT
                attrs_bool[%(key)s] AS value,
                count() AS cnt
            FROM spans
            WHERE project_id = %(project_id)s
              AND mapContains(attrs_bool, %(key)s)
            GROUP BY value
            ORDER BY cnt DESC
        """
        rows, _, query_time_ms = client.execute_read(query, params)

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
            "span_attribute_boolean_detail_fetched",
            project_id=params["project_id"],
            key=params["key"],
            count=total_count,
            query_time_ms=query_time_ms,
        )

        return Response(
            {
                "key": params["key"],
                "type": "boolean",
                "count": total_count,
                "unique_values": len(rows),
                "top_values": top_values,
            },
            status=200,
        )
