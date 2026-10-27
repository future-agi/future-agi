from datetime import timedelta

import humanize
import structlog
from django.utils import timezone
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from accounts.models.user import User
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_serializers import ApiErrorResponseSerializer
from tfc.utils.general_methods import GeneralMethods
from tracer.models.project import Project
from tracer.models.trace_error_analysis_task import TraceErrorTaskStatus
from tracer.queries.error_analysis import TraceErrorAnalysisDB

logger = structlog.get_logger(__name__)

ERROR_RESPONSES = {
    400: ApiErrorResponseSerializer,
    403: ApiErrorResponseSerializer,
    404: ApiErrorResponseSerializer,
    500: ApiErrorResponseSerializer,
}


def parse_error_type_and_name(error_type_str: str) -> tuple:
    """
    Parse error type string to extract category and specific error name.
    Returns: (category, error_name)

    Examples:
    "Reflection Gaps  Lack of Self-Correction" -> ("Reflection Gaps", "Lack of Self-Correction")
    "Tool & System Failures > Setup Errors > Tool Missing" -> ("Tool & System Failures", "Tool Missing")
    """
    if not error_type_str:
        return ("Unknown", "Unknown")

    # Handle different separators
    if " > " in error_type_str:
        # Standard hierarchy separator
        parts = error_type_str.split(" > ")
        category = parts[0].strip()
        error_name = parts[-1].strip()
    elif "  " in error_type_str:
        # Double space separator (common in our data)
        parts = error_type_str.split("  ")
        category = parts[0].strip()
        error_name = parts[-1].strip() if len(parts) > 1 else parts[0].strip()
    else:
        # No recognized separator, try to be smart about it
        # Check if it matches a known category
        for cat in [
            "Thinking & Response Issues",
            "Safety & Security Risks",
            "Tool & System Failures",
            "Workflow & Task Gaps",
            "Reflection Gaps",
        ]:
            if error_type_str.startswith(cat):
                category = cat
                error_name = error_type_str[len(cat) :].strip()
                return (category, error_name)
        # No match, return as is
        return (error_type_str, error_type_str)

    return (category, error_name)


class TraceErrorAnalysisResultSerializer(serializers.Serializer):
    analysis_exists = serializers.BooleanField()
    trace_id = serializers.CharField()
    message = serializers.CharField(required=False)
    analysis_id = serializers.UUIDField(required=False)
    analysis_date = serializers.DateTimeField(required=False)
    agent_version = serializers.CharField(
        required=False, allow_null=True, allow_blank=True
    )
    memory_enhanced = serializers.BooleanField(required=False)
    summary = serializers.JSONField(required=False)
    errors = serializers.ListField(child=serializers.JSONField(), required=False)
    grouped_errors = serializers.ListField(child=serializers.JSONField(), required=False)
    scores = serializers.JSONField(required=False)
    memory_context = serializers.JSONField(required=False)


class TraceErrorAnalysisResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = TraceErrorAnalysisResultSerializer()


class TraceErrorAnalysisView(APIView):
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.db = TraceErrorAnalysisDB()

    @validated_request(
        responses={200: TraceErrorAnalysisResponseSerializer, **ERROR_RESPONSES},
    )
    def get(self, request, trace_id):
        """
        Get error analysis for a specific trace
        GET /api/trace-error-analysis/<trace_id>/
        """
        try:
            workspace = getattr(request, "workspace", None)
            workspace_id = workspace.id if workspace else None

            analysis = self.db.get_analysis_by_trace_id(
                trace_id,
                (
                    getattr(request, "organization", None) or request.user.organization
                ).id,
                workspace_id=workspace_id,
            )

            if not analysis:
                return self._gm.success_response(
                    {
                        "analysis_exists": False,
                        "trace_id": trace_id,
                        "message": "No error analysis found for this trace",
                    }
                )

            error_details = list(self.db.get_error_details_for_analysis(analysis))
            error_groups = list(self.db.get_error_groups_for_analysis(analysis))

            response_data = {
                "analysis_exists": True,
                "trace_id": str(analysis.trace_id),
                "analysis_id": str(analysis.id),
                "analysis_date": analysis.analysis_date,
                "agent_version": analysis.agent_version,
                "memory_enhanced": analysis.memory_enhanced,
                "summary": {
                    "overall_score": analysis.overall_score,
                    "error_count": analysis.total_errors,
                    "insights": analysis.insights,
                    "recommended_priority": analysis.recommended_priority,
                },
                "errors": [
                    {
                        "error_id": error.error_id,
                        "cluster_id": error.cluster_id,
                        "category": error.category.split(" > ")[-1],
                        "full_category": error.category,
                        "location_spans": error.location_spans,
                        "evidence_snippets": error.evidence_snippets,
                        "description": error.description,
                        "impact": error.impact,
                        "urgency_to_fix": error.urgency_to_fix,
                        "root_causes": error.root_causes,
                        "recommendation": error.recommendation,
                        "immediate_fix": error.immediate_fix,
                        "trace_impact": error.trace_impact,
                        "trace_assessment": error.trace_assessment,
                        "memory_enhanced": error.memory_enhanced,
                    }
                    for error in error_details
                ],
                "grouped_errors": [
                    {
                        "cluster_id": group.cluster_id,
                        "error_type": group.error_type,
                        "error_ids": group.error_ids,
                        "affected_spans": (
                            list(
                                group.clusters.exclude(span=None)
                                .values_list("span_id", flat=True)
                                .distinct()
                            )
                            if hasattr(group, "clusters")
                            else []
                        ),
                        "combined_impact": group.combined_impact,
                        "combined_description": group.combined_description,
                        "error_count": group.error_count,
                        "trace_impact": group.trace_impact,
                    }
                    for group in error_groups
                ],
                "scores": {
                    "factual_grounding": {
                        "score": analysis.factual_grounding_score,
                        "reason": analysis.factual_grounding_reason,
                    },
                    "privacy_and_safety": {
                        "score": analysis.privacy_and_safety_score,
                        "reason": analysis.privacy_and_safety_reason,
                    },
                    "instruction_adherence": {
                        "score": analysis.instruction_adherence_score,
                        "reason": analysis.instruction_adherence_reason,
                    },
                    "optimal_plan_execution": {
                        "score": analysis.optimal_plan_execution_score,
                        "reason": analysis.optimal_plan_execution_reason,
                    },
                },
                "memory_context": {
                    "episodic_memory_used": (
                        analysis.memory_context.get("episodic_memory_used", False)
                        if analysis.memory_context
                        else False
                    ),
                    "semantic_memory_used": (
                        analysis.memory_context.get("semantic_memory_used", False)
                        if analysis.memory_context
                        else False
                    ),
                    "memory_enhanced_analysis": analysis.memory_enhanced,
                },
            }

            return self._gm.success_response(response_data)

        except Exception as e:
            return self._gm.bad_request(
                f"Error fetching trace error analysis: {str(e)}"
            )


class ErrorClusterFeedView(APIView):
    """
    API for the error clusters feed view (the table showing all clusters)
    """

    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.db = TraceErrorAnalysisDB()

    def get(self, request):
        """
        Get error clusters for the feed view
        GET /tracer/trace-error-analysis/clusters/feed/

        Query params:
        - days: Number of days to look back (default: 7)
        - page_number: Page number (0-indexed, default: 0)
        - page_size: Number of items per page (default: 30)
        - project_id: Filter by specific project (optional)
        - trend_days: Number of days for trend data (can be fractional, e.g. 0.0417 for 1 hour)
        """
        try:
            # Get query parameters with validation caps
            days = max(1, min(int(request.query_params.get("days", 7)), 365))
            page_number = max(0, int(request.query_params.get("page_number", 0)))
            page_size = max(1, min(int(request.query_params.get("page_size", 10)), 100))
            project_id = request.query_params.get("project_id")
            try:
                trend_days = float(request.query_params.get("trend_days", 7))
            except (TypeError, ValueError):
                trend_days = 7.0

            offset = page_number * page_size
            limit = page_size

            # Get user's accessible projects
            org_id = (
                (getattr(request, "organization", None) or request.user.organization).id
                if hasattr(request.user, "organization")
                else None
            )
            workspace = getattr(request, "workspace", None)
            workspace_id = workspace.id if workspace else None
            if not org_id:
                return self._gm.forbidden_response(
                    "User not associated with an organization"
                )

            # Get project IDs user has access to using DB layer
            accessible_projects = self.db.get_user_accessible_projects(
                org_id, workspace_id
            )

            if project_id:
                # Filter to specific project if requested
                if project_id not in [str(p) for p in accessible_projects]:
                    return self._gm.forbidden_response("Access denied to this project")
                project_ids = [project_id]
            else:
                project_ids = accessible_projects

            # Get clusters from DB with pagination
            result = self.db.get_clusters_for_feed(
                project_ids=project_ids, days=days, limit=limit, offset=offset
            )

            clusters = result.get("clusters", [])
            total_count = result.get("total_count", 0)

            # Calculate pagination info
            total_pages = (
                (total_count + page_size - 1) // page_size if page_size > 0 else 1
            )
            current_page = page_number + 1  # Convert to 1-indexed for display

            # Format response with camelCase fields
            now = timezone.now()

            formatted_clusters = []
            for cluster in clusters:
                # Calculate age and format times
                age = (
                    now - cluster["first_seen"]
                    if cluster.get("first_seen")
                    else timedelta(0)
                )
                last_seen = (
                    now - cluster["last_seen"]
                    if cluster.get("last_seen")
                    else timedelta(0)
                )

                # Map impact to priority
                priority_map = {
                    "HIGH": "Urgent",
                    "MEDIUM": "High",
                    "LOW": "Medium",
                    "MINIMAL": "Low",
                }

                # Get assignee if available
                assignee_name = None
                if cluster.get("assignee"):
                    try:
                        assignee = User.objects.get(id=cluster["assignee"])
                        assignee_name = assignee.get_full_name() or assignee.email
                    except User.DoesNotExist:
                        assignee_name = None

                # Get trend data based on trend_days parameter
                # Use hourly data if less than 1 day, otherwise use daily data
                if trend_days < 1:
                    # Convert to hours (e.g. 0.25 days = 6 hours)
                    hours = int(trend_days * 24)
                    trends = self.db.get_cluster_trend_data_hourly(
                        cluster_id=cluster["cluster_id"], hours=hours
                    )
                else:
                    # Use daily data for 1 or more days
                    trends = self.db.get_cluster_trend_data(
                        cluster_id=cluster["cluster_id"], days=int(trend_days)
                    )

                category, error_name = parse_error_type_and_name(cluster["error_type"])

                cluster_data = {
                    "cluster_id": cluster["cluster_id"],
                    "error": {"name": error_name, "type": category},
                    "last_seen": humanize.naturaltime(last_seen),
                    "age": humanize.naturaltime(age),
                    "trends": trends if trends else [],
                    "events": cluster.get("total_events", 0),
                    "users": cluster.get(
                        "unique_users", 0
                    ),  # Unique end users affected
                    "priority": priority_map.get(
                        cluster.get("combined_impact", "LOW"), "Low"
                    ),
                    "assignee": assignee_name,
                    "project_name": cluster.get("project_name", "Unknown"),
                }

                formatted_clusters.append(cluster_data)

            response_data = {
                "clusters": formatted_clusters,
                "pagination": {
                    "total_count": total_count,
                    "page_size": page_size,
                    "page_number": page_number,
                    "current_page": current_page,  # 1-indexed for display
                    "total_pages": total_pages,
                    "has_next": (page_number + 1) * page_size < total_count,
                    "has_previous": page_number > 0,
                },
                "filters": {
                    "days": days,
                    "project_id": project_id,
                    "trend_days": trend_days,
                },
            }

            return self._gm.success_response(response_data)

        except Exception as e:
            logger.exception("feed_api_error")
            return self._gm.internal_server_error_response(
                f"Failed to fetch clusters: {str(e)}"
            )


class ErrorClusterDetailView(APIView):
    """
    API endpoint to get detailed information about a specific error cluster
    """

    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.db = TraceErrorAnalysisDB()

    def get(self, request, cluster_id):
        """
        Get detailed cluster data including trace navigation
        GET /api/trace-error-analysis/clusters/<cluster_id>/

        Query params:
        - current_trace_id: Current trace being viewed (optional, for navigation)
        - trend_days: Number of days for trend data (default: 7, can be fractional)
        """
        try:
            # Get query parameters
            current_trace_id = request.query_params.get("current_trace_id")
            trend_days = float(request.query_params.get("trend_days", 7))

            # Get cluster with access check from DB layer
            org_id = (
                (getattr(request, "organization", None) or request.user.organization).id
                if hasattr(request.user, "organization")
                else None
            )
            cluster = self.db.get_cluster_detail_data(
                cluster_id=cluster_id, organization_id=org_id
            )

            if not cluster:
                return self._gm.not_found("Cluster not found or access denied")

            # Get trace navigation data
            trace_navigation = self.db.get_cluster_trace_navigation(
                cluster_id=cluster_id, current_trace_id=current_trace_id
            )

            # Get trend data
            if trend_days < 1:
                hours = int(trend_days * 24)
                trends = self.db.get_cluster_trend_data_hourly(
                    cluster_id=cluster_id, hours=hours
                )
            else:
                trends = self.db.get_cluster_trend_data(
                    cluster_id=cluster_id, days=int(trend_days)
                )

            # Format timestamps
            now = timezone.now()
            last_seen_human = (
                humanize.naturaltime(now - cluster.last_seen)
                if cluster.last_seen
                else "Never"
            )
            first_seen_human = (
                humanize.naturaltime(now - cluster.first_seen)
                if cluster.first_seen
                else "Never"
            )

            category, error_name = parse_error_type_and_name(cluster.error_type)
            # Build response
            response_data = {
                "cluster_id": cluster_id,
                "error_type": category,
                "error_name": error_name,
                "description": cluster.combined_description,
                "impact": cluster.combined_impact,
                "trace_navigation": trace_navigation,
                "last_seen": (
                    cluster.last_seen.isoformat() if cluster.last_seen else None
                ),
                "last_seen_human": last_seen_human,
                "first_seen": (
                    cluster.first_seen.isoformat() if cluster.first_seen else None
                ),
                "first_seen_human": first_seen_human,
                "events": cluster.total_events or 0,
                "users": cluster.unique_users or 0,
                "unique_traces": cluster.unique_traces or 0,
                "trends": trends,
                "project_id": str(cluster.project_id),
            }

            return self._gm.success_response(response_data)

        except ValueError:
            return self._gm.bad_request("Invalid trend_days parameter")
        except Exception as e:
            logger.exception(f"Error fetching cluster data: {str(e)}")
            return self._gm.internal_server_error_response(
                f"Failed to fetch cluster data: {str(e)}"
            )


class TraceErrorTaskResponseResultSerializer(serializers.Serializer):
    project_id = serializers.UUIDField()
    project_name = serializers.CharField()
    sampling_rate = serializers.FloatField()
    status = serializers.ChoiceField(choices=TraceErrorTaskStatus.CHOICES)
    is_active = serializers.BooleanField(required=False)
    total_traces_analyzed = serializers.IntegerField(required=False)
    total_errors_found = serializers.IntegerField(required=False)
    failed_analyses = serializers.IntegerField(required=False)
    last_run_at = serializers.DateTimeField(required=False, allow_null=True)
    created = serializers.BooleanField(required=False)


class TraceErrorTaskResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = TraceErrorTaskResponseResultSerializer()


class TraceErrorTaskUpdateRequestSerializer(serializers.Serializer):
    sampling_rate = serializers.FloatField(min_value=0, max_value=1)
    status = serializers.ChoiceField(
        choices=(TraceErrorTaskStatus.WAITING, TraceErrorTaskStatus.PAUSED),
        required=False,
    )


class TraceErrorTaskUpdateResultSerializer(serializers.Serializer):
    message = serializers.CharField()
    project_id = serializers.UUIDField()
    project_name = serializers.CharField()
    sampling_rate = serializers.FloatField()
    status = serializers.ChoiceField(choices=TraceErrorTaskStatus.CHOICES)
    action = serializers.CharField()
    old_rate = serializers.FloatField()
    new_rate = serializers.FloatField()


class TraceErrorTaskUpdateResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = TraceErrorTaskUpdateResultSerializer()


class TraceErrorTaskView(APIView):
    """
    API for managing trace error analysis tasks (sampling rate)
    """

    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.db = TraceErrorAnalysisDB()

    @validated_request(
        responses={200: TraceErrorTaskResponseSerializer, **ERROR_RESPONSES},
    )
    def get(self, request, project_id):
        """
        Get current task configuration for a project
        GET /api/trace-error-task/<project_id>/
        """
        try:

            try:
                project = Project.objects.get(
                    id=project_id,
                    organization=getattr(request, "organization", None)
                    or request.user.organization,
                )
            except Project.DoesNotExist:
                return self._gm.not_found("Project not found")

            task, created = self.db.get_or_create_task_for_project(project)

            response_data = {
                "project_id": str(project.id),
                "project_name": project.name,
                "sampling_rate": task.sampling_rate,
                "status": task.status,
                "is_active": task.status != TraceErrorTaskStatus.PAUSED,
                "total_traces_analyzed": task.total_traces_analyzed,
                "total_errors_found": task.total_errors_found,
                "failed_analyses": task.failed_analyses,
                "last_run_at": task.last_run_at,
                "created": created,
            }

            return self._gm.success_response(response_data)

        except Exception as e:
            return self._gm.internal_server_error_response(str(e))

    @validated_request(
        request_serializer=TraceErrorTaskUpdateRequestSerializer,
        responses={200: TraceErrorTaskUpdateResponseSerializer, **ERROR_RESPONSES},
    )
    def post(self, request, project_id):
        """
        Update task configuration for a project
        POST /api/trace-error-task/<project_id>/

        Request body:
        {
            "sampling_rate": 0.2,  // Required: 0-1
            "status": "waiting"    // Optional: "waiting" or "paused"
        }
        """
        try:
            data = request.validated_data
            sampling_rate = data.get("sampling_rate")
            status = data.get("status")
            try:
                project = Project.objects.get(
                    id=project_id,
                    organization=getattr(request, "organization", None)
                    or request.user.organization,
                )
            except Project.DoesNotExist:
                return self._gm.not_found("Project not found")

            task, _created = self.db.get_or_create_task_for_project(project)

            old_rate = task.sampling_rate
            task.sampling_rate = sampling_rate
            update_fields = ["sampling_rate"]

            new_status = status
            if new_status:
                task.status = new_status
                update_fields.append("status")

            task.save(update_fields=update_fields)

            # Determine what will happen
            action_message = ""
            if sampling_rate > old_rate:
                action_message = "Will process additional traces on next run"
            elif sampling_rate < old_rate:
                action_message = "Will maintain existing analyses, no reprocessing"
            else:
                action_message = "Sampling rate unchanged"

            response_data = {
                "message": f"Task updated for project {project.name}",
                "project_id": str(project.id),
                "project_name": project.name,
                "sampling_rate": task.sampling_rate,
                "status": task.status,
                "action": action_message,
                "old_rate": old_rate,
                "new_rate": sampling_rate,
            }

            return self._gm.success_response(response_data)

        except Exception as e:
            return self._gm.internal_server_error_response(str(e))
