from collections import defaultdict

import structlog
from django.contrib.postgres.aggregates import ArrayAgg
from django.db.models import (
    Avg,
    Count,
    DecimalField,
    FloatField,
    Max,
    Min,
    Q,
    Sum,
    Value,
)
from django.db.models.functions import Coalesce
from django.http import StreamingHttpResponse
from django.utils.dateparse import parse_datetime
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ReadOnlyModelViewSet

from accounts.utils import get_request_organization
from agentcc.models import AgentccCustomPropertySchema, AgentccRequestLog
from agentcc.models.request_log import RequestLogTag
from agentcc.serializers.contracts import AgentccErrorResponseSerializer
from agentcc.serializers.request_log import (
    AgentccRequestLogDetailSerializer,
    AgentccRequestLogMetadataValuesResponseSerializer,
    AgentccRequestLogMetadataValuesSerializer,
    AgentccRequestLogSerializer,
    AgentccSessionSerializer,
)
from agentcc.serializers.request_queries import GatewayRequestLogQuerySerializer
from agentcc.services.export import MAX_EXPORT_ROWS, export_csv, export_json
from agentcc.services.request_log_metadata import TAG_SEPARATOR, get_metadata_values
from tfc.utils.api_contracts import ExplicitQueryAutoSchema, validated_request
from tfc.utils.base_viewset import BaseModelViewSetMixinWithUserOrg
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)


def _parse_bool(value):
    """Parse a string boolean query param."""
    if value is None:
        return None
    return value.lower() in ("true", "1")


def _parse_int(value, default=None):
    """Parse an integer query param safely."""
    if value is None:
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def _parse_csv(value):
    """Split a comma-separated query param into its non-empty parts."""
    return [part.strip() for part in (value or "").split(",") if part.strip()]


def _parse_tags(value):
    """Parse ``key:value,key:value`` into pairs. A tag without a key is rejected."""
    pairs = []
    for tag in _parse_csv(value):
        key, separator, tag_value = tag.partition(TAG_SEPARATOR)
        if not separator or not key.strip():
            raise ValueError(f"Invalid tag '{tag}', expected key:value")
        pairs.append((key.strip(), tag_value.strip()))
    return pairs


def _metadata_matches_any(key, values):
    """Rows whose metadata[key] is one of values, as containment the GIN index serves."""
    match = Q()
    for value in values:
        match |= Q(metadata__contains={key: value})
    return match


def _apply_search(queryset, raw_query):
    """Apply request-log search semantics shared by list/search/export paths."""
    q = (raw_query or "").strip()
    if len(q) < 2:
        return queryset

    return queryset.filter(
        Q(model__icontains=q)
        | Q(provider__icontains=q)
        | Q(error_message__icontains=q)
        | Q(request_id__icontains=q)
        | Q(user_id__icontains=q)
        | Q(session_id__icontains=q)
    )


class AgentccRequestLogViewSet(BaseModelViewSetMixinWithUserOrg, ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = AgentccRequestLogSerializer
    queryset = AgentccRequestLog.no_workspace_objects.all()
    _gm = GeneralMethods()

    def get_serializer_class(self):
        if self.action == "retrieve":
            return AgentccRequestLogDetailSerializer
        return AgentccRequestLogSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        return self._apply_filters(queryset)

    def _apply_filters(self, queryset):
        """Apply all supported query param filters."""
        params = self.request.query_params

        # Exact match filters
        user_id = params.get("user_id")
        if user_id:
            queryset = queryset.filter(user_id=user_id)

        session_id = params.get("session_id")
        if session_id:
            queryset = queryset.filter(session_id=session_id)

        api_key_id = params.get("api_key_id")
        if api_key_id:
            queryset = queryset.filter(api_key_id=api_key_id)

        request_id = params.get("request_id")
        if request_id:
            queryset = queryset.filter(request_id=request_id)

        # Multi-value filters (comma-separated, OR within field)
        models = _parse_csv(params.get("model"))
        if models:
            queryset = queryset.filter(model__in=models)

        providers = _parse_csv(params.get("provider"))
        if providers:
            queryset = queryset.filter(provider__in=providers)

        for tag in RequestLogTag:
            values = _parse_csv(params.get(tag.value))
            if values:
                queryset = queryset.filter(_metadata_matches_any(tag.value, values))

        # Custom tags: any listed value of a key matches, and every key must match
        tag_values = defaultdict(list)
        for key, value in _parse_tags(params.get("tags")):
            tag_values[key].append(value)
        for key, values in tag_values.items():
            queryset = queryset.filter(_metadata_matches_any(key, values))

        # Status code filter (single or range)
        parsed = []
        for code in _parse_csv(params.get("status_code")):
            try:
                parsed.append(int(code))
            except (ValueError, TypeError):
                pass
        if parsed:
            queryset = queryset.filter(status_code__in=parsed)

        min_status_code = _parse_int(params.get("min_status_code"))
        if min_status_code is not None:
            queryset = queryset.filter(status_code__gte=min_status_code)

        max_status_code = _parse_int(params.get("max_status_code"))
        if max_status_code is not None:
            queryset = queryset.filter(status_code__lte=max_status_code)

        # Boolean filters
        is_error = params.get("is_error")
        if is_error not in (None, ""):
            queryset = queryset.filter(is_error=_parse_bool(is_error))

        cache_hit = params.get("cache_hit")
        if cache_hit not in (None, ""):
            queryset = queryset.filter(cache_hit=_parse_bool(cache_hit))

        fallback_used = params.get("fallback_used")
        if fallback_used not in (None, ""):
            queryset = queryset.filter(fallback_used=_parse_bool(fallback_used))

        guardrail_triggered = params.get("guardrail_triggered")
        if guardrail_triggered not in (None, ""):
            queryset = queryset.filter(
                guardrail_triggered=_parse_bool(guardrail_triggered)
            )

        is_stream = params.get("is_stream")
        if is_stream not in (None, ""):
            queryset = queryset.filter(is_stream=_parse_bool(is_stream))

        # Date range filters
        started_after = params.get("started_after")
        if started_after:
            dt = parse_datetime(started_after)
            if dt:
                queryset = queryset.filter(started_at__gte=dt)

        started_before = params.get("started_before")
        if started_before:
            dt = parse_datetime(started_before)
            if dt:
                queryset = queryset.filter(started_at__lte=dt)

        # Numeric range filters
        min_latency = _parse_int(params.get("min_latency"))
        if min_latency is not None:
            queryset = queryset.filter(latency_ms__gte=min_latency)

        max_latency = _parse_int(params.get("max_latency"))
        if max_latency is not None:
            queryset = queryset.filter(latency_ms__lte=max_latency)

        min_cost = params.get("min_cost")
        if min_cost is not None:
            try:
                queryset = queryset.filter(cost__gte=float(min_cost))
            except (ValueError, TypeError):
                pass

        max_cost = params.get("max_cost")
        if max_cost is not None:
            try:
                queryset = queryset.filter(cost__lte=float(max_cost))
            except (ValueError, TypeError):
                pass

        min_tokens = _parse_int(params.get("min_tokens"))
        if min_tokens is not None:
            queryset = queryset.filter(total_tokens__gte=min_tokens)

        max_tokens = _parse_int(params.get("max_tokens"))
        if max_tokens is not None:
            queryset = queryset.filter(total_tokens__lte=max_tokens)

        search_query = params.get("q") or params.get("search")
        queryset = _apply_search(queryset, search_query)

        # Ordering
        ordering = params.get("ordering")
        if ordering:
            allowed = {
                "started_at",
                "-started_at",
                "latency_ms",
                "-latency_ms",
                "cost",
                "-cost",
                "total_tokens",
                "-total_tokens",
                "status_code",
                "-status_code",
            }
            if ordering in allowed:
                queryset = queryset.order_by(ordering)

        return queryset

    @validated_request(
        query_serializer=GatewayRequestLogQuerySerializer,
        auto_schema=ExplicitQueryAutoSchema,
    )
    def list(self, request, *args, **kwargs):
        try:
            queryset = self.get_queryset()
            page = self.paginate_queryset(queryset)
            if page is not None:
                serializer = AgentccRequestLogSerializer(page, many=True)
                return self.get_paginated_response(serializer.data)
            serializer = AgentccRequestLogSerializer(queryset, many=True)
            return self._gm.success_response(serializer.data)
        except Exception as e:
            logger.exception("request_log_list_error", error=str(e))
            return self._gm.bad_request(str(e))

    def retrieve(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            return self._gm.success_response(
                AgentccRequestLogDetailSerializer(instance).data
            )
        except Exception as e:
            logger.exception("request_log_retrieve_error", error=str(e))
            return self._gm.not_found("Request log not found")

    @action(detail=False, methods=["get"])
    def search(self, request):
        """Full-text search across model, provider, error_message, request_id."""
        try:
            q = request.query_params.get("q", "").strip()
            if not q or len(q) < 2:
                return self._gm.bad_request(
                    "Search query must be at least 2 characters"
                )

            queryset = self.get_queryset()

            page = self.paginate_queryset(queryset)
            if page is not None:
                serializer = AgentccRequestLogSerializer(page, many=True)
                return self.get_paginated_response(serializer.data)
            serializer = AgentccRequestLogSerializer(queryset, many=True)
            return self._gm.success_response(serializer.data)
        except Exception as e:
            logger.exception("request_log_search_error", error=str(e))
            return self._gm.bad_request(str(e))

    @validated_request(
        responses={
            200: AgentccRequestLogMetadataValuesResponseSerializer,
            400: AgentccErrorResponseSerializer,
        },
    )
    @action(
        detail=False,
        methods=["get"],
        url_path="metadata-values",
        pagination_class=None,
    )
    def metadata_values(self, request):
        """Application, service and custom tag values seen in recent requests."""
        try:
            tag_keys = (
                AgentccCustomPropertySchema.no_workspace_objects.filter(
                    organization=get_request_organization(request)
                )
                .exclude(name__in=[tag.value for tag in RequestLogTag])
                .order_by("name")
                .values_list("name", flat=True)
            )
            values = get_metadata_values(super().get_queryset(), tag_keys)
            return self._gm.success_response(
                AgentccRequestLogMetadataValuesSerializer(values).data
            )
        except Exception as e:
            logger.exception("request_log_metadata_values_error", error=str(e))
            return self._gm.bad_request("Could not load filter options")

    @action(detail=False, methods=["get"])
    def sessions(self, request):
        """Aggregate request logs by session_id."""
        try:
            queryset = self.get_queryset().exclude(
                Q(session_id="") | Q(session_id__isnull=True)
            )

            ordering = request.query_params.get("ordering") or "-last_request_at"
            allowed_ordering = {
                "last_request_at",
                "-last_request_at",
                "request_count",
                "-request_count",
                "total_cost",
                "-total_cost",
            }
            if ordering not in allowed_ordering:
                ordering = "-last_request_at"

            sessions = (
                queryset.values("session_id")
                .annotate(
                    request_count=Count("id"),
                    total_cost=Coalesce(
                        Sum("cost"), Value(0), output_field=DecimalField()
                    ),
                    total_tokens=Coalesce(Sum("total_tokens"), Value(0)),
                    avg_latency=Coalesce(
                        Avg("latency_ms"), Value(0), output_field=FloatField()
                    ),
                    first_request_at=Min("started_at"),
                    last_request_at=Max("started_at"),
                    error_count=Count("id", filter=Q(is_error=True)),
                    models=ArrayAgg("model", distinct=True),
                    providers=ArrayAgg("provider", distinct=True),
                )
                .order_by(ordering)
            )

            page = self.paginate_queryset(list(sessions))
            if page is not None:
                serializer = AgentccSessionSerializer(page, many=True)
                return self.get_paginated_response(serializer.data)

            result = list(sessions)
            serializer = AgentccSessionSerializer(result, many=True)
            return self._gm.success_response(serializer.data)
        except Exception as e:
            logger.exception("request_log_sessions_error", error=str(e))
            return self._gm.bad_request(str(e))

    @action(
        detail=False,
        methods=["get"],
        url_path=r"sessions/(?P<session_id>[^/.]+)",
    )
    def session_detail(self, request, session_id=None):
        """Get all logs for a specific session."""
        try:
            queryset = self.get_queryset().filter(session_id=session_id)
            if not queryset.exists():
                return self._gm.not_found("Session not found")

            page = self.paginate_queryset(queryset)
            if page is not None:
                serializer = AgentccRequestLogSerializer(page, many=True)
                return self.get_paginated_response(serializer.data)
            serializer = AgentccRequestLogSerializer(queryset, many=True)
            return self._gm.success_response(serializer.data)
        except Exception as e:
            logger.exception("request_log_session_detail_error", error=str(e))
            return self._gm.bad_request(str(e))

    @action(detail=False, methods=["get"])
    def export(self, request):
        """Export filtered request logs as CSV or JSON."""
        try:
            queryset = self.get_queryset()
            count = queryset.count()

            if count == 0:
                return self._gm.bad_request("No data to export")

            if count > MAX_EXPORT_ROWS:
                return self._gm.bad_request(
                    f"Export limited to {MAX_EXPORT_ROWS:,} rows. "
                    f"Current filter returns {count:,} rows. Please narrow your filters."
                )

            fmt = request.query_params.get("export_format", "csv").lower()

            if fmt == "json":
                response = StreamingHttpResponse(
                    export_json(queryset),
                    content_type="application/x-ndjson",
                )
                response["Content-Disposition"] = (
                    'attachment; filename="agentcc-logs.json"'
                )
            else:
                response = StreamingHttpResponse(
                    export_csv(queryset),
                    content_type="text/csv",
                )
                response["Content-Disposition"] = (
                    'attachment; filename="agentcc-logs.csv"'
                )

            return response
        except Exception as e:
            logger.exception("request_log_export_error", error=str(e))
            return self._gm.bad_request(str(e))
