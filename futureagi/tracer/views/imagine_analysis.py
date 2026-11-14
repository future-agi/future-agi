"""
API endpoints for Imagine dynamic analysis.

POST /tracer/imagine-analysis/  — trigger analysis for widgets
GET  /tracer/imagine-analysis/  — poll for results
"""

import structlog
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from tfc.utils.api_contracts import validated_request
from tfc.utils.api_serializers import ApiErrorResponseSerializer
from tfc.utils.general_methods import GeneralMethods
from tracer.models.imagine_analysis import ImagineAnalysis
from tracer.models.saved_view import SavedView

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

        results = []
        for widget in data["widgets"]:
            # Check if analysis already exists for this trace+widget
            analysis, created = ImagineAnalysis.objects.get_or_create(
                saved_view=saved_view,
                widget_id=widget["widget_id"],
                trace_id=data["trace_id"],
                defaults={
                    "project_id": data["project_id"],
                    "organization": org,
                    "prompt": widget["prompt"],
                    "status": "pending",
                },
            )

            if not created:
                if analysis.status == "completed" and analysis.content:
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

                # Failed, stale, or pending — reset and re-run
                analysis.prompt = widget["prompt"]
                analysis.status = "pending"
                analysis.content = None
                analysis.error = None
                analysis.save(
                    update_fields=["prompt", "status", "content", "error", "updated_at"]
                )

            from tfc.temporal.imagine.client import start_imagine_analysis

            try:
                workflow_id = start_imagine_analysis(
                    analysis_id=str(analysis.id),
                    trace_id=data["trace_id"],
                    org_id=str(org.id),
                    prompt=widget["prompt"],
                )
                ImagineAnalysis.objects.filter(id=analysis.id).update(
                    status="running", workflow_id=workflow_id
                )
                result_status = "running"
            except Exception as e:
                logger.error(
                    "imagine_analysis_start_failed",
                    error=str(e),
                    analysis_id=str(analysis.id),
                )
                ImagineAnalysis.objects.filter(id=analysis.id).update(
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

        analyses = ImagineAnalysis.no_workspace_objects.filter(
            saved_view=saved_view,
            trace_id=trace_id,
            project=saved_view.project,
            organization=org,
            deleted=False,
        ).values("id", "widget_id", "status", "content", "error")

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
