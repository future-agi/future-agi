from datetime import UTC

from django.http import StreamingHttpResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.pagination import CursorPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from accounts.utils import get_user_organization
from agentcc.mcp_features import tool_call_unavailable
from agentcc.models import AgentccToolCall
from agentcc.serializers.mcp_tool_calls import (
    AgentccToolCallDetailSerializer,
    AgentccToolCallSerializer,
)
from agentcc.services import mcp_tool_call_export as export
from agentcc.services import siem_redactor
from agentcc.services.mcp_tool_call_outcomes import approvals_for
from tfc.utils.general_methods import GeneralMethods

# Postgres integer: the latency columns' upper bound.
_MAX_LATENCY_MS = 2**31 - 1


class MCPToolCallCursorPagination(CursorPagination):
    page_size = 50
    page_size_query_param = "limit"
    max_page_size = 100
    ordering = ("-started_at", "-id")


class _MCPToolCallView(APIView):
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @staticmethod
    def _organization(request):
        organization = getattr(request, "organization", None)
        if organization is None:
            organization = get_user_organization(getattr(request, "user", None))
        return organization

    def _base_queryset(self, request):
        organization = self._organization(request)
        if organization is None:
            return AgentccToolCall.objects.none()
        return AgentccToolCall.objects.filter(organization=organization)

    def _filtered_queryset(self, request):
        params = request.query_params
        queryset = self._base_queryset(request)
        filters = {
            "run_id": "agent_run_id",
            "agent_id": "agent_id",
            "api_key_id": "api_key_id",
            "server_id": "server_id",
            "tool": "tool",
            "outcome": "outcome",
        }
        for parameter, field in filters.items():
            value = params.get(parameter)
            if value:
                values = [part.strip() for part in value.split(",") if part.strip()]
                if len(values) == 1:
                    queryset = queryset.filter(**{field: values[0]})
                elif values:
                    queryset = queryset.filter(**{f"{field}__in": values})

        if "billing_mode" in params:
            billing_mode = params.get("billing_mode", "").strip()
            if billing_mode not in {"byok", "managed"}:
                raise ValueError("billing_mode must be byok or managed")
            queryset = queryset.filter(billing_mode=billing_mode)

        for parameter, lookup in (
            ("min_latency_ms", "latency_ms__gte"),
            ("max_latency_ms", "latency_ms__lte"),
        ):
            raw = params.get(parameter)
            if raw is None or raw == "":
                continue
            if not (raw.isascii() and raw.isdigit()) or int(raw) > _MAX_LATENCY_MS:
                raise ValueError(f"{parameter} must be a whole number of milliseconds")
            queryset = queryset.filter(**{lookup: int(raw)})

        for parameter, lookup in (
            ("from", "started_at__gte"),
            ("to", "started_at__lte"),
        ):
            raw = params.get(parameter)
            if not raw:
                continue
            parsed = parse_datetime(raw)
            if parsed is None:
                raise ValueError(f"{parameter} must be an ISO-8601 datetime")
            if timezone.is_naive(parsed):
                parsed = timezone.make_aware(parsed, timezone=UTC)
            queryset = queryset.filter(**{lookup: parsed})

        return queryset.order_by(*MCPToolCallCursorPagination.ordering)


class MCPToolCallListView(_MCPToolCallView):
    def get(self, request):
        refusal = tool_call_unavailable()
        if refusal is not None:
            return refusal
        try:
            queryset = self._filtered_queryset(request)
        except ValueError as exc:
            return self._gm.bad_request(str(exc))

        paginator = MCPToolCallCursorPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        approvals = approvals_for(self._organization(request), page or [])
        serializer = AgentccToolCallSerializer(
            page, many=True, context={"approvals": approvals}
        )
        return paginator.get_paginated_response(serializer.data)


class MCPToolCallDetailView(_MCPToolCallView):
    def get(self, request, pk):
        refusal = tool_call_unavailable()
        if refusal is not None:
            return refusal
        row = self._base_queryset(request).filter(pk=pk).first()
        if row is None:
            return self._gm.not_found("Tool call not found")
        approvals = approvals_for(self._organization(request), [row])
        serializer = AgentccToolCallDetailSerializer(
            row, context={"approvals": approvals}
        )
        return self._gm.success_response(serializer.data)


class MCPToolCallExportView(_MCPToolCallView):
    """The filtered view as CSV or NDJSON, at most MAX_EXPORT_ROWS rows.

    The filters are the list's. Its columns are closed metadata
    (mcp_tool_call_export); an empty view answers 400 export_empty, a larger
    one export_too_large unless max_rows asks for the newest rows. The
    download is audited before it streams.
    """

    def get(self, request):
        refusal = tool_call_unavailable(download=True)
        if refusal is not None:
            return refusal
        export_format = request.query_params.get("export_format", "csv").lower()
        if export_format not in ("csv", "json"):
            return self._gm.bad_request("export_format must be csv or json")
        try:
            queryset = self._filtered_queryset(request)
        except ValueError as exc:
            return self._gm.bad_request(str(exc))
        count = queryset.count()
        if count == 0:
            return self._gm.custom_error_response(
                400,
                "No tool calls match these filters, so there is nothing to export.",
                code="export_empty",
            )
        max_rows = request.query_params.get("max_rows", "")
        if max_rows:
            if not (max_rows.isascii() and max_rows.isdigit()) or int(max_rows) < 1:
                return self._gm.bad_request("max_rows must be a positive whole number")
            limit = min(int(max_rows), export.MAX_EXPORT_ROWS)
            queryset, count = queryset[:limit], min(count, limit)
        elif count > export.MAX_EXPORT_ROWS:
            return self._gm.custom_error_response(
                400,
                f"An export holds at most {export.MAX_EXPORT_ROWS:,} calls and these "
                f"filters match {count:,}. Narrow the filters to export them.",
                code="export_too_large",
            )
        try:
            export.record_export(
                self._organization(request),
                request.user,
                export_format,
                export.applied_filters(request.query_params),
                count,
            )
        except export.ExportAuditUnavailable:
            return self._gm.custom_error_response(
                503,
                "The export was not made because its audit record could not be written.",
                code="audit_unavailable",
            )
        # The org's privacy redactor applies to every exported value, as the
        # gateway applies it to its span scalars.
        redactor = siem_redactor.org_redactor(self._organization(request).id)
        if export_format == "json":
            response = StreamingHttpResponse(
                export.ndjson_lines(queryset, redactor),
                content_type="application/x-ndjson",
            )
        else:
            response = StreamingHttpResponse(
                export.csv_lines(queryset, redactor), content_type="text/csv"
            )
        response["Content-Disposition"] = (
            f'attachment; filename="mcp-tool-calls.{export_format}"'
        )
        return response
