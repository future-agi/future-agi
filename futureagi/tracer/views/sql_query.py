from datetime import timedelta

from django.http import HttpResponse
from django.utils import timezone
from drf_yasg import openapi
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ViewSet

from accounts.authentication import workspace_read_only
from tfc.utils.api_contracts import validated_request
from tracer.serializers.sql_query import (
    SQLCatalogSerializer,
    SQLExecutionSerializer,
    SQLExportSerializer,
    SQLHistorySerializer,
    SQLListSerializer,
    SQLProjectSerializer,
    SQLRunSavedSerializer,
    SQLRunSerializer,
    SQLSavedListSerializer,
    SQLSavedSerializer,
    SQLSaveSerializer,
    SQLWidgetResponseSerializer,
    SQLWidgetSerializer,
)
from tracer.sql_workbench import service
from tracer.sql_workbench.access import AccessDenied, Actor, authorize
from tracer.sql_workbench.catalog import (
    CATALOG_VERSION,
    IDENTITY_SEMANTICS,
    CatalogUnavailable,
    catalog,
)
from tracer.sql_workbench.compiler import MAX_RESULT_ROWS, UnsafeQuery
from tracer.sql_workbench.runner import QueryUnavailable, derived_metrics_availability


class SQLViewMixin:
    permission_classes = [IsAuthenticated]
    lookup_value_regex = r"[0-9a-f-]{36}"

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        return response

    def actor(self):
        return Actor(
            self.request.user.id,
            getattr(getattr(self.request, "organization", None), "id", None),
            getattr(getattr(self.request, "workspace", None), "id", None),
        )

    def handle_exception(self, exc):
        if isinstance(exc, AccessDenied):
            exc = PermissionDenied(str(exc))
        elif isinstance(exc, (UnsafeQuery, ValueError)):
            exc = ValidationError({"detail": str(exc)})
        elif isinstance(exc, (QueryUnavailable, CatalogUnavailable)):
            return Response({"detail": str(exc)}, status=503)
        elif isinstance(exc, service.AdmissionFull):
            return Response({"detail": str(exc)}, status=429)
        return super().handle_exception(exc)


@workspace_read_only
class SQLQueryViewSet(SQLViewMixin, ViewSet):
    """Read-only scans; creating a personal execution record grants no write access."""

    @action(detail=False, methods=["get"])
    @validated_request(
        query_serializer=SQLProjectSerializer, responses={200: SQLCatalogSerializer}
    )
    def catalog(self, request):
        actor = self.actor()
        project_id = request.validated_query_data["project_id"]
        authorize(actor, project_id)
        try:
            authorize(actor, project_id, write=True)
            can_save = True
        except AccessDenied:
            can_save = False
        metrics_available, reason = derived_metrics_availability()
        return Response(
            {
                "version": CATALOG_VERSION,
                "identity_semantics": IDENTITY_SEMANTICS,
                "datasets": catalog(metrics_available, reason),
                "max_rows": MAX_RESULT_ROWS,
                "max_window_days": 31,
                "can_save": can_save,
            }
        )

    @action(detail=False, methods=["post"])
    @validated_request(
        request_serializer=SQLRunSerializer, responses={202: SQLExecutionSerializer}
    )
    def run(self, request):
        job = service.submit(self.actor(), **request.validated_data)
        return Response(service.snapshot(job), status=202)

    @action(detail=False, methods=["post"], url_path="run-saved")
    @validated_request(
        request_serializer=SQLRunSavedSerializer,
        responses={202: SQLExecutionSerializer},
    )
    def run_saved(self, request):
        data = request.validated_data
        saved = service.saved_query(
            self.actor(), data["project_id"], data["saved_query_id"]
        )
        end = data.get("end", timezone.now())
        start = data.get("start", end - timedelta(hours=saved.window_hours))
        job = service.submit(
            self.actor(),
            project_id=data["project_id"],
            sql=saved.sql,
            start=start,
            end=end,
        )
        return Response(service.snapshot(job), status=202)

    @validated_request(
        query_serializer=SQLListSerializer, responses={200: SQLHistorySerializer}
    )
    def list(self, request):
        return Response(service.history(self.actor(), **request.validated_query_data))

    @validated_request(
        query_serializer=SQLProjectSerializer, responses={200: SQLExecutionSerializer}
    )
    def retrieve(self, request, pk=None):
        return Response(
            service.snapshot(
                service.execution(
                    self.actor(), request.validated_query_data["project_id"], pk
                )
            )
        )

    @action(detail=True, methods=["post"])
    @validated_request(
        request_serializer=SQLProjectSerializer, responses={200: SQLExecutionSerializer}
    )
    def cancel(self, request, pk=None):
        return Response(
            service.cancel(self.actor(), request.validated_data["project_id"], pk)
        )

    @action(detail=True, methods=["get"])
    @validated_request(
        query_serializer=SQLExportSerializer,
        responses={
            200: openapi.Response(
                "Bounded result download", schema=openapi.Schema(type=openapi.TYPE_FILE)
            )
        },
    )
    def download(self, request, pk=None):
        data = request.validated_query_data
        payload, content_type = service.export_result(
            self.actor(), data["project_id"], pk, data["file_format"]
        )
        response = HttpResponse(payload, content_type=content_type)
        # No user-controlled filename or cacheable signed result URL.
        response["Content-Disposition"] = (
            f'attachment; filename="query-results.{data["file_format"]}"'
        )
        response["Cache-Control"] = "no-store"
        response["X-Content-Type-Options"] = "nosniff"
        return response


class SavedSQLQueryViewSet(SQLViewMixin, ViewSet):
    @validated_request(
        query_serializer=SQLListSerializer, responses={200: SQLSavedListSerializer}
    )
    def list(self, request):
        data = request.validated_query_data
        rows = list(
            service.saved_queries(self.actor(), data["project_id"]).order_by(
                "-created_at"
            )[data["offset"] : data["offset"] + 21]
        )
        return Response(
            {
                "items": SQLSavedSerializer(rows[:20], many=True).data,
                "has_more": len(rows) > 20,
            }
        )

    @validated_request(
        request_serializer=SQLSaveSerializer, responses={201: SQLSavedSerializer}
    )
    def create(self, request):
        return Response(
            SQLSavedSerializer(
                service.save_query(self.actor(), **request.validated_data)
            ).data,
            status=201,
        )

    @validated_request(
        query_serializer=SQLProjectSerializer, responses={200: SQLSavedSerializer}
    )
    def retrieve(self, request, pk=None):
        return Response(
            SQLSavedSerializer(
                service.saved_query(
                    self.actor(), request.validated_query_data["project_id"], pk
                )
            ).data
        )

    @validated_request(
        request_serializer=SQLSaveSerializer, responses={200: SQLSavedSerializer}
    )
    def partial_update(self, request, pk=None):
        return Response(
            SQLSavedSerializer(
                service.update_saved(self.actor(), pk, **request.validated_data)
            ).data
        )

    @validated_request(
        query_serializer=SQLProjectSerializer, responses={204: "Deleted"}
    )
    def destroy(self, request, pk=None):
        service.delete_saved(
            self.actor(), request.validated_query_data["project_id"], pk
        )
        return Response(status=204)

    @action(detail=True, methods=["post"], url_path="add-widget")
    @validated_request(
        request_serializer=SQLWidgetSerializer,
        responses={201: SQLWidgetResponseSerializer},
    )
    def add_widget(self, request, pk=None):
        widget = service.add_widget(self.actor(), saved_id=pk, **request.validated_data)
        return Response(
            {"id": widget.id, "dashboard_id": widget.dashboard_id}, status=201
        )
