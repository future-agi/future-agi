"""
API endpoints for Imagine dynamic analysis.

POST /tracer/imagine-analysis/  — trigger analysis for widgets
GET  /tracer/imagine-analysis/  — poll for results
"""

import structlog
from django.core.exceptions import ValidationError
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from tfc.utils.api_contracts import validated_request
from tfc.utils.api_serializers import ApiErrorResponseSerializer
from tfc.utils.general_methods import GeneralMethods
from tracer.models.imagine_analysis import ImagineAnalysis
from tracer.models.saved_view import SavedView
from tracer.models.trace import Trace

logger = structlog.get_logger(__name__)

ERROR_RESPONSES = {
    400: ApiErrorResponseSerializer,
    403: ApiErrorResponseSerializer,
    404: ApiErrorResponseSerializer,
    500: ApiErrorResponseSerializer,
}


class WidgetAnalysisSerializer(serializers.Serializer):
    widget_id = serializers.CharField(max_length=100)
    prompt = serializers.CharField(max_length=8000)


class TriggerAnalysisSerializer(serializers.Serializer):
    saved_view_id = serializers.UUIDField()
    trace_id = serializers.CharField(max_length=255)
    project_id = serializers.UUIDField()
    widgets = WidgetAnalysisSerializer(many=True)
    force = serializers.BooleanField(default=False)


class ImagineAnalysisQuerySerializer(serializers.Serializer):
    saved_view_id = serializers.UUIDField()
    trace_id = serializers.CharField(max_length=255)


class ImagineAnalysisItemSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    widget_id = serializers.CharField(max_length=100)
    status = serializers.ChoiceField(choices=ImagineAnalysis.STATUS_CHOICES)
    content = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    error = serializers.CharField(required=False, allow_null=True, allow_blank=True)


class ImagineAnalysisResultSerializer(serializers.Serializer):
    analyses = ImagineAnalysisItemSerializer(many=True)


class ImagineAnalysisResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = ImagineAnalysisResultSerializer()


class ImagineAnalysisView(APIView):
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    def _get_scoped_saved_view(self, request, saved_view_id, project_id=None):
        queryset = SavedView.objects.select_related("project").filter(
            id=saved_view_id,
            project__deleted=False,
        )
        if project_id:
            queryset = queryset.filter(project_id=project_id)
        return queryset.get()

    def _get_scoped_trace(self, saved_view, trace_id, organization):
        from tracer.services.clickhouse.v2 import get_reader

        if saved_view.project.organization_id != organization.id:
            raise Trace.DoesNotExist

        # PG metadata is optional for collector-ingested traces. If it exists,
        # retain the legacy identity check before consulting ClickHouse.
        trace = (
            Trace.no_workspace_objects.only("id", "project_id")
            .filter(id=trace_id, deleted=False)
            .first()
        )
        if trace is not None:
            if trace.project_id != saved_view.project_id:
                raise Trace.DoesNotExist
            return trace

        reader = get_reader()
        try:
            spans = reader.list_by_trace(
                str(trace_id),
                project_id=str(saved_view.project_id),
                include_heavy=False,
            )
            if not spans:
                raise Trace.DoesNotExist
        finally:
            reader.close()
        return None

    @validated_request(
        request_serializer=TriggerAnalysisSerializer,
        responses={200: ImagineAnalysisResponseSerializer, **ERROR_RESPONSES},
    )
    def post(self, request):
        """Trigger analysis for widgets. Creates DB records + starts Temporal workflows."""
        data = request.validated_data
        org = getattr(request, "organization", None) or request.user.organization

        # Validate saved view exists
        try:
            saved_view = self._get_scoped_saved_view(
                request,
                data["saved_view_id"],
                project_id=data["project_id"],
            )
        except SavedView.DoesNotExist:
            return self._gm.not_found("Saved view not found")

        # Validate before creating records, returning cached content, or
        # starting workflows. The authorized saved view owns the binding.
        try:
            self._get_scoped_trace(saved_view, data["trace_id"], org)
        except (Trace.DoesNotExist, ValidationError):
            return self._gm.not_found("Trace not found")

        results = []
        for widget in data["widgets"]:
            # Check if analysis already exists for this trace+widget
            analysis, created = ImagineAnalysis.objects.get_or_create(
                saved_view=saved_view,
                widget_id=widget["widget_id"],
                trace_id=data["trace_id"],
                defaults={
                    "project_id": saved_view.project_id,
                    "organization": org,
                    "prompt": widget["prompt"],
                    "status": "pending",
                },
            )

            if not created:
                if (
                    not data["force"]
                    and analysis.status == "completed"
                    and analysis.content
                    and (analysis.workflow_id or "").startswith("imagine-pb1-")
                ):
                    results.append(
                        {
                            "id": str(analysis.id),
                            "widget_id": widget["widget_id"],
                            "status": analysis.status,
                            "content": analysis.content,
                        }
                    )
                    continue

                # Check if "running" is stale (>2 min old)
                from django.utils import timezone

                is_stale = (
                    analysis.status == "running"
                    and analysis.updated_at
                    and (timezone.now() - analysis.updated_at).total_seconds() > 120
                )

                if analysis.status == "running" and not is_stale:
                    results.append(
                        {
                            "id": str(analysis.id),
                            "widget_id": widget["widget_id"],
                            "status": "running",
                        }
                    )
                    continue

                # Explicit reruns, failed/stale/pending rows, and unversioned
                # completions reset. Active runs remain deduplicated above.
                analysis.prompt = widget["prompt"]
                analysis.status = "pending"
                analysis.content = None
                analysis.error = None
                analysis.save(
                    update_fields=["prompt", "status", "content", "error", "updated_at"]
                )

            from tfc.temporal.imagine.client import start_imagine_analysis

            # Match the starter's versioned ID before dispatch: a fast worker
            # must see its persisted provenance when saving the result.
            workflow_id = f"imagine-pb1-{analysis.id}"
            bound_analysis = ImagineAnalysis.objects.filter(
                id=analysis.id,
                saved_view=saved_view,
                project_id=saved_view.project_id,
                organization=org,
            )
            bound_analysis.update(workflow_id=workflow_id)

            try:
                start_imagine_analysis(
                    analysis_id=str(analysis.id),
                    trace_id=data["trace_id"],
                    org_id=str(org.id),
                    project_id=str(saved_view.project_id),
                    prompt=widget["prompt"],
                )
                # Do not overwrite a result saved before dispatch returned.
                bound_analysis.filter(workflow_id=workflow_id, status="pending").update(
                    status="running"
                )
                result_status = "running"
            except Exception as e:
                logger.error(
                    "imagine_analysis_start_failed",
                    error=str(e),
                    analysis_id=str(analysis.id),
                )
                bound_analysis.filter(workflow_id=workflow_id, status="pending").update(
                    status="failed", error=str(e)[:500]
                )
                result_status = "failed"

            results.append(
                {
                    "id": str(analysis.id),
                    "widget_id": widget["widget_id"],
                    "status": result_status,
                }
            )

        return Response({"status": True, "result": {"analyses": results}})

    @validated_request(
        query_serializer=ImagineAnalysisQuerySerializer,
        responses={200: ImagineAnalysisResponseSerializer, **ERROR_RESPONSES},
    )
    def get(self, request):
        """Poll for analysis results."""
        saved_view_id = request.validated_query_data["saved_view_id"]
        trace_id = request.validated_query_data["trace_id"]
        org = getattr(request, "organization", None) or request.user.organization

        try:
            saved_view = self._get_scoped_saved_view(request, saved_view_id)
        except SavedView.DoesNotExist:
            return self._gm.not_found("Saved view not found")

        # Old rows may have been admitted with a trace from another project.
        # Do not expose their stored content merely because the view is local.
        try:
            self._get_scoped_trace(saved_view, trace_id, org)
        except (Trace.DoesNotExist, ValidationError):
            return self._gm.not_found("Trace not found")

        analyses = ImagineAnalysis.no_workspace_objects.filter(
            saved_view=saved_view,
            trace_id=trace_id,
            project=saved_view.project,
            organization=org,
            deleted=False,
        ).values("id", "widget_id", "status", "content", "error", "workflow_id")

        for analysis in analyses:
            if not (analysis["workflow_id"] or "").startswith("imagine-pb1-"):
                # Status cannot certify legacy content or diagnostics, even
                # for running rows. Polling stays read-only; POST retains its
                # existing wait/reset rules for unmarked analyses.
                analysis.update(status="pending", content=None, error=None)

        return Response(
            {
                "status": True,
                "result": {
                    "analyses": [
                        {
                            "id": str(a["id"]),
                            "widget_id": a["widget_id"],
                            "status": a["status"],
                            "content": a["content"],
                            "error": a["error"],
                        }
                        for a in analyses
                    ]
                },
            }
        )
