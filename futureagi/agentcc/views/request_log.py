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

from agentcc.models import AgentccRequestLog
from agentcc.serializers.request_log import (
    AgentccRequestLogDetailSerializer,
    AgentccRequestLogSerializer,
    AgentccSessionSerializer,
)
from agentcc.services.export import MAX_EXPORT_ROWS, export_csv, export_json
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
    # TH-4667 (ai_tools bridge): _apply_filters (via get_queryset) honors
    # `search` (q/search full-text) — declared because the helper indirection
    # hides it from auto-detection. page/page_size auto-detect from the
    # paginator.
    mcp_list_params = {"search": "search"}

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
        model = params.get("model")
        if model:
            models = [m.strip() for m in model.split(",") if m.strip()]
            if models:
                queryset = queryset.filter(model__in=models)

        provider = params.get("provider")
        if provider:
            providers = [p.strip() for p in provider.split(",") if p.strip()]
            if providers:
                queryset = queryset.filter(provider__in=providers)

        # Status code filter (single or range)
        status_code = params.get("status_code")
        if status_code:
            codes = [c.strip() for c in status_code.split(",") if c.strip()]
            parsed = []
            for c in codes:
                try:
                    parsed.append(int(c))
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
