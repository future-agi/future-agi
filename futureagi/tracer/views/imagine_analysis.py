"""
API endpoints for Imagine dynamic analysis.

POST /tracer/imagine-analysis/  — trigger analysis for widgets
GET  /tracer/imagine-analysis/  — poll for results
"""

import structlog
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from tracer.models.imagine_analysis import ImagineAnalysis
from tracer.models.saved_view import SavedView

logger = structlog.get_logger(__name__)


class WidgetAnalysisSerializer(serializers.Serializer):
    widget_id = serializers.CharField(max_length=100)
    prompt = serializers.CharField(max_length=8000)


class TriggerAnalysisSerializer(serializers.Serializer):
    saved_view_id = serializers.UUIDField()
    trace_id = serializers.CharField(max_length=255)
    project_id = serializers.UUIDField(required=False, allow_null=True)
    widgets = WidgetAnalysisSerializer(many=True)


class ImagineAnalysisView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        """Trigger analysis for widgets. Creates DB records + starts Temporal workflows."""
        serializer = TriggerAnalysisSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data
        org = getattr(request, "organization", None) or request.user.organization

        # Validate saved view exists. Some callers (annotation/voice drawers)
        # are not on /observe/:projectId routes, so project_id may be absent
        # even though the saved view already carries the project.
        try:
            saved_view_qs = SavedView.objects.filter(
                id=data["saved_view_id"],
                deleted=False,
            )
            if data.get("project_id"):
                saved_view_qs = saved_view_qs.filter(project_id=data["project_id"])

            request_workspace = getattr(request, "workspace", None)
            if request_workspace:
                saved_view_qs = saved_view_qs.filter(workspace=request_workspace)
            else:
                saved_view_qs = saved_view_qs.filter(workspace__organization=org)

            saved_view = saved_view_qs.get()
        except SavedView.DoesNotExist:
            return Response(
                {"status": False, "error": "Saved view not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        project_id = data.get("project_id") or saved_view.project_id
        if not project_id:
            return Response(
                {
                    "status": False,
                    "error": "project_id is required for workspace-scoped saved views",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        results = []
        for widget in data["widgets"]:
            # Check if analysis already exists for this trace+widget
            analysis, created = ImagineAnalysis.objects.get_or_create(
                saved_view=saved_view,
                widget_id=widget["widget_id"],
                trace_id=data["trace_id"],
                defaults={
                    "project_id": project_id,
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

            result_error = None
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
                result_error = str(e)[:500]

            results.append(
                {
                    "id": str(analysis.id),
                    "widget_id": widget["widget_id"],
                    "status": result_status,
                    **({"error": result_error} if result_error else {}),
                }
            )

        return Response({"status": True, "result": {"analyses": results}})

    def get(self, request):
        """Poll for analysis results."""
        saved_view_id = request.query_params.get(
            "saved_view_id"
        ) or request.query_params.get("savedViewId")
        trace_id = request.query_params.get("trace_id") or request.query_params.get(
            "traceId"
        )

        if not saved_view_id or not trace_id:
            return Response(
                {"status": False, "error": "saved_view_id and trace_id required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        analyses = ImagineAnalysis.objects.filter(
            saved_view_id=saved_view_id,
            trace_id=trace_id,
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
