from datetime import datetime, timedelta

import structlog
from django.db import models
from django.db.models import Count
from django.utils import timezone
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ModelViewSet

from accounts.utils import get_request_organization
from tfc.middleware.db_health_check import db_connection_required
from tfc.middleware.query_timeout import monitor_query_performance
from tfc.routers import uses_db
from tfc.utils.api_contracts import validated_request
from tfc.utils.base_viewset import BaseModelViewSetMixinWithUserOrg
from tfc.utils.error_codes import get_error_message
from tfc.utils.general_methods import GeneralMethods
from tracer.db_routing import DATABASE_FOR_PROJECT_LIST
from tracer.models.eval_task import EvalTask
from tracer.models.monitor import UserAlertMonitor
from tracer.models.observation_span import ObservationSpan
from tracer.models.project import Project
from tracer.models.project_version import ProjectVersion
from tracer.models.trace import Trace
from tracer.models.trace_scan import TraceScanConfig
from tracer.models.trace_session import TraceSession
from tracer.queries.projects import apply_project_list_filters
from tracer.serializers.project import (
    ProjectDetailResponseSerializer,
    ProjectGraphDataQuerySerializer,
    ProjectIdListResponseSerializer,
    ProjectNameUpdateSerializer,
    ProjectSerializer,
    ProjectUserGraphDataQuerySerializer,
    ProjectUserGraphDataRequestSerializer,
    ProjectUserMetricsRequestSerializer,
    ProjectUsersAggregateGraphDataRequestSerializer,
)
from tracer.services.clickhouse.graph_dispatch import (
    fetch_annotation_graph_ch,
    fetch_eval_graph_ch,
)
from tracer.services.clickhouse.query_builders import (
    ClickHouseFilterBuilder,
    TimeSeriesQueryBuilder,
    UserListQueryBuilder,
)
from tracer.services.clickhouse.query_service import AnalyticsQueryService
from tracer.services.clickhouse.v2.query_builders.user_list import (
    UserListQueryBuilderV2,
)
from tracer.utils.constants import (
    INSTALLATION_GUIDE,
    INSTRUMENTORS,
    OBSERVE_CODEBLOCK,
    ORG_KEYS,
    PROTOTYPE_CODEBLOCK,
)
from tracer.utils.graphs_optimized import get_all_system_metrics
from tracer.utils.helper import get_default_project_version_config, get_sort_query

logger = structlog.get_logger(__name__)


class ProjectView(BaseModelViewSetMixinWithUserOrg, ModelViewSet):
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()
    serializer_class = ProjectSerializer

    def _request_organization(self):
        return get_request_organization(self.request) or self.request.user.organization

    def _workspace_scope_q(self):
        workspace = getattr(self.request, "workspace", None)
        if not workspace:
            return models.Q()

        if getattr(workspace, "is_default", False):
            return (
                models.Q(workspace=workspace)
                | models.Q(
                    workspace__is_default=True,
                    workspace__organization=workspace.organization,
                )
                | models.Q(workspace__isnull=True)
            )

        return models.Q(workspace=workspace)

    def _project_scope_queryset(self):
        return Project.no_workspace_objects.filter(
            self._workspace_scope_q(),
            organization=self._request_organization(),
        )

    def _get_project_in_scope(self, project_id):
        if not project_id:
            return None
        return self._project_scope_queryset().filter(id=project_id).first()

    def _soft_delete_projects(self, projects, project_type):
        now = timezone.now()
        if project_type == "experiment":
            ProjectVersion.objects.filter(project__in=projects).update(
                deleted=True, deleted_at=now
            )
        else:
            TraceSession.objects.filter(project__in=projects).update(
                deleted=True, deleted_at=now
            )
        Trace.objects.filter(project__in=projects).update(deleted=True, deleted_at=now)
        ObservationSpan.objects.filter(project__in=projects).update(
            deleted=True, deleted_at=now
        )
        UserAlertMonitor.objects.filter(project__in=projects).update(
            deleted=True, deleted_at=now
        )
        EvalTask.objects.filter(project__in=projects).update(
            deleted=True, deleted_at=now
        )
        projects.update(deleted=True, deleted_at=now)

    def get_queryset(self):
        # Get base queryset with automatic filtering from mixin
        queryset = super().get_queryset()

        project_id = self.kwargs.get("pk")

        if project_id:
            return queryset.filter(id=project_id)

        # Apply filters
        search_name = self.request.query_params.get("name")
        project_type = self.request.query_params.get("project_type")

        if search_name:
            queryset = queryset.filter(name__icontains=search_name)

        if project_type:
            queryset = queryset.filter(trace_type=project_type)

        # Apply sorting
        sort_by = self.request.query_params.get("sort_by", "created_at")
        sort_direction = self.request.query_params.get("sort_direction", "desc")
        sort_query = get_sort_query(sort_by, sort_direction)
        return queryset.order_by(sort_query)

    def perform_update(self, serializer):
        """Override to invalidate PII cache when project metadata changes."""
        instance = serializer.save()
        try:
            from tracer.utils.pii_settings import invalidate_pii_cache

            invalidate_pii_cache(str(instance.organization_id), instance.name)
        except Exception:
            logger.warning("pii_cache_invalidation_failed", exc_info=True)

    def list(self, request, *args, **kwargs):
        """
        Get a paginated list of all projects for the organization.
        """
        try:
            # Get base queryset
            queryset = self.get_queryset()

            # Get total count before pagination
            total_count = queryset.count()

            # Apply pagination
            page_number = int(self.request.query_params.get("page_number", 0))
            page_size = int(self.request.query_params.get("page_size", 20))
            start = page_number * page_size
            end = start + page_size

            # Get paginated queryset with trace counts and run counts
            # Use distinct=True to avoid cartesian join between traces and versions
            from tracer.models.project_version import ProjectVersion

            paginated_queryset = queryset[start:end].annotate(
                trace_count=Count(
                    "traces", filter=models.Q(traces__deleted=False), distinct=True
                ),
                run_count=models.Subquery(
                    ProjectVersion.objects.filter(
                        project_id=models.OuterRef("id"), deleted=False
                    )
                    .values("project_id")
                    .annotate(c=Count("id"))
                    .values("c"),
                    output_field=models.IntegerField(),
                ),
            )

            # Serialize data
            serializer = self.get_serializer(paginated_queryset, many=True)

            # Add trace_count and run_count to serialized data
            for data, project in zip(serializer.data, paginated_queryset, strict=False):
                data["trace_count"] = project.trace_count
                data["run_count"] = project.run_count or 0

            return self._gm.success_response(
                {"projects": serializer.data, "total_count": total_count}
            )

        except Exception as e:
            logger.exception(f"Error in fetching the project list: {str(e)}")

            return self._gm.bad_request(
                f"error fetching the projects list {get_error_message('ERROR_FETCHING_PROJECT_LISTS')}"
            )

    def create(self, request, *args, **kwargs):
        """
        Create a new project.
        """
        try:
            serializer = self.get_serializer(data=request.data)

            if serializer.is_valid():
                serializer.save(
                    organization=getattr(self.request, "organization", None)
                    or self.request.user.organization,
                    workspace=getattr(self.request, "workspace", None),
                    config=get_default_project_version_config(),
                )

                return self._gm.success_response(
                    {
                        "project_id": str(serializer.instance.id),
                        "name": serializer.instance.name,
                    }
                )
            return self._gm.bad_request(serializer.errors)

        except Exception as e:
            logger.exception(f"Error in creating the project: {str(e)}")
            return self._gm.bad_request(get_error_message("FAILED_TO_CREATE_PROJECT"))

    @validated_request(responses={200: ProjectDetailResponseSerializer})
    def retrieve(self, request, *args, **kwargs):
        """
        Get a single project by ID with sampling rate.
        """
        try:
            instance = self.get_object()
            serializer = self.get_serializer(instance)
            data = serializer.data
            if instance.trace_type == "experiment" and not data.get("config"):
                data["config"] = get_default_project_version_config()

            try:
                scan_config = TraceScanConfig.objects.get(project=instance)
                data["sampling_rate"] = scan_config.sampling_rate
            except TraceScanConfig.DoesNotExist:
                data["sampling_rate"] = 0

            return self._gm.success_response(data)

        except Exception as e:
            logger.exception(f"Error in retrieving the project: {str(e)}")
            return self._gm.bad_request(get_error_message("PROJECT_NOT_FOUND"))

    def delete(self, request, *args, **kwargs):
        """
        Delete projects.
        """
        try:
            project_ids = request.data.get("project_ids", [])
            project_type = request.data.get("project_type", "experiment")
            if not project_ids:
                return self._gm.bad_request(get_error_message("PROJECT_ID_REQUIRED"))
            projects = self._project_scope_queryset().filter(id__in=project_ids)
            if projects.exists():
                self._soft_delete_projects(projects, project_type)

                return self._gm.success_response(
                    "Successfully deleted the selected projects"
                )

            else:
                return self._gm.bad_request(get_error_message("PROJECT_NOT_FOUND"))

        except Exception as e:
            logger.exception(f"Error in deleting the project: {str(e)}")

            return self._gm.bad_request(get_error_message("FAILED_TO_DELETE_PROJECT"))

    def destroy(self, request, *args, **kwargs):
        try:
            project = self.get_object()
            self._soft_delete_projects(
                self._project_scope_queryset().filter(id=project.id),
                project.trace_type,
            )
            return self._gm.success_response("Successfully deleted the project")
        except Exception as e:
            logger.exception(f"Error in deleting the project: {str(e)}")
            return self._gm.bad_request(get_error_message("FAILED_TO_DELETE_PROJECT"))

    @action(detail=False, methods=["post"])
    def update_project_config(self, request, *args, **kwargs):
        try:
            project_id = self.request.data.get("project_id")
            visibility = self.request.data.get("visibility", {})
            project = self._get_project_in_scope(project_id)
            if not project:
                return self._gm.bad_request("Project not found")
            config = project.config

            for key, value in visibility.items():
                config_entry = next(
                    (item for item in config if item.get("id") == key), None
                )
                if config_entry:
                    config_entry["is_visible"] = value

            project.config = config
            project.save()

            return self._gm.success_response({"project_id": project.id})
        except Exception as e:
            logger.exception(f"Error in updating the project config: {str(e)}")

            return self._gm.bad_request(
                f"Error updating project config: {get_error_message('FAILED_TO_UPDATE_PROJECT_CONFIG')}"
            )

    @action(detail=False, methods=["post"])
    def update_project_name(self, request, *args, **kwargs):
        try:
            serializer = ProjectNameUpdateSerializer(data=request.data)
            if serializer.is_valid():
                validated_data = serializer.data
                project_id = validated_data["project_id"]
                new_name = validated_data["name"]
                sampling_rate = validated_data.get("sampling_rate")

                project = self._get_project_in_scope(project_id)

                if not project:
                    return self._gm.bad_request(get_error_message("PROJECT_NOT_FOUND"))

                # Update project name
                project.name = new_name
                project.save(update_fields=["name"])

                response_message = "Project name updated successfully"
                response_data = {
                    "message": response_message,
                    "project_id": str(project_id),
                    "project_name": new_name,
                }

                # Update sampling rate if provided
                if sampling_rate is not None:
                    scan_config, _ = TraceScanConfig.objects.get_or_create(
                        project=project,
                        defaults={"sampling_rate": sampling_rate},
                    )
                    old_rate = scan_config.sampling_rate
                    if not _:
                        scan_config.sampling_rate = sampling_rate
                        scan_config.save(update_fields=["sampling_rate"])

                    response_data["sampling_rate"] = {
                        "old_rate": old_rate,
                        "new_rate": sampling_rate,
                        "message": "Sampling rate updated successfully",
                    }
                    response_message = (
                        "Project name and sampling rate updated successfully"
                    )
                    response_data["message"] = response_message

                return self._gm.success_response(response_data)
            else:
                return self._gm.bad_request(serializer.errors)

        except Exception as e:
            logger.exception(f"Error in updating the project: {str(e)}")

            return self._gm.bad_request(
                get_error_message("FAILED_TO_UPDATE_PROJECT_NAME")
            )

    @action(detail=False, methods=["post"])
    def update_project_session_config(self, request, *args, **kwargs):
        try:
            project_id = self.request.data.get("project_id")
            visibility = self.request.data.get("visibility", {})
            project = self._get_project_in_scope(project_id)
            if not project:
                return self._gm.bad_request("Project not found")

            config = project.session_config or []

            for key, value in visibility.items():
                config_entry = next(
                    (item for item in config if item.get("id") == key), None
                )
                if config_entry:
                    config_entry["is_visible"] = value

            project.session_config = config
            project.save()

            return self._gm.success_response({"project_id": project.id})
        except Exception as e:
            logger.exception(f"Error in updating the project session config: {str(e)}")

            return self._gm.bad_request(
                get_error_message("FAILED_TO_UPDATE_PROJECT_CONFIG")
            )

    @action(detail=False, methods=["get"])
    @db_connection_required
    @monitor_query_performance
    @uses_db(DATABASE_FOR_PROJECT_LIST, feature_key="feature:project_list")
    def list_projects(self, request, *args, **kwargs):
        """
        List projects filtered by organization ID.

        Volume counts come from ClickHouse (fast) instead of a PG
        JOIN on observation_spans (was 12+ seconds).

        Routing: this is the single highest-impact PG list endpoint by
        weekly time (see Sentry data, ~4,032s/wk PG time, p95 ~1s, 28k
        calls/wk). Both PG queries below (the Project list and the
        ProjectVersion count aggregate) route to DATABASE_FOR_PROJECT_LIST
        so they land on the same alias.
        """
        try:
            # Get base queryset — lightweight PG query, no annotation JOINs.
            # Routes to replica when "feature:project_list" is opted in.
            queryset = (
                self.get_queryset()
                .using(DATABASE_FOR_PROJECT_LIST)
                .only("id", "name", "created_at", "updated_at", "tags")
            )

            # Tag filtering (legacy flat param: ?tags=a,b -> exact-tag AND)
            tags_param = self.request.query_params.get("tags")
            if tags_param:
                for tag in tags_param.split(","):
                    tag = tag.strip()
                    if tag:
                        queryset = queryset.filter(tags__contains=[tag])

            # Operator-based name/tag filters (equals/contains/not_*) from the
            # `filters` JSON array — the trace/span list convention.
            queryset = apply_project_list_filters(
                queryset, self.request.query_params.get("filters")
            )

            ALLOWED_SORT_FIELDS = {"name", "created_at", "updated_at"}
            raw_sort = self.request.query_params.get("sort_by", "created_at")
            # CH-only fields can't be sorted in PG — fall back to created_at
            sort_by = raw_sort if raw_sort in ALLOWED_SORT_FIELDS else "created_at"
            sort_direction = self.request.query_params.get("sort_direction", "desc")
            sort_query = f"-{sort_by}" if sort_direction == "desc" else sort_by
            queryset = queryset.order_by(sort_query)

            total_count = queryset.count()

            page_number = int(self.request.query_params.get("page_number", 0))
            page_size = int(self.request.query_params.get("page_size", 20))
            start = page_number * page_size
            end = start + page_size

            paginated_queryset = queryset[start:end]

            projects_data = list(
                paginated_queryset.values(
                    "id", "name", "created_at", "updated_at", "tags"
                )
            )

            # Get 30-day volume from ClickHouse for just this page of projects
            volume_map = {}
            daily_volume_map = {}
            project_ids = [str(p["id"]) for p in projects_data]
            if project_ids:
                try:
                    from tracer.services.clickhouse.client import get_clickhouse_client
                    from tracer.services.clickhouse.query_service import (
                        is_clickhouse_enabled,
                    )

                    if is_clickhouse_enabled():
                        ch = get_clickhouse_client()
                        thirty_days_ago = (
                            datetime.now() - timedelta(days=30)
                        ).strftime("%Y-%m-%d")
                        vol_result = ch.execute_read(
                            "SELECT project_id, count() AS vol "
                            "FROM spans "
                            "WHERE project_id IN %(pids)s "
                            "AND is_deleted = 0 "
                            "AND (parent_span_id IS NULL OR parent_span_id = %(e)s) "
                            "AND start_time >= %(since)s "
                            "AND created_at >= %(since)s "
                            "GROUP BY project_id",
                            {"pids": project_ids, "e": "", "since": thirty_days_ago},
                            timeout_ms=5000,
                        )
                        raw = (
                            vol_result[0]
                            if isinstance(vol_result, tuple)
                            else vol_result
                        )
                        volume_map = {str(r[0]): r[1] for r in raw}

                        # Daily volume for sparkline charts
                        daily_result = ch.execute_read(
                            "SELECT project_id, toDate(start_time) AS day, count() AS vol "
                            "FROM spans "
                            "WHERE project_id IN %(pids)s "
                            "AND is_deleted = 0 "
                            "AND (parent_span_id IS NULL OR parent_span_id = %(e)s) "
                            "AND start_time >= %(since)s "
                            "AND created_at >= %(since)s "
                            "GROUP BY project_id, day "
                            "ORDER BY project_id, day",
                            {"pids": project_ids, "e": "", "since": thirty_days_ago},
                            timeout_ms=5000,
                        )
                        daily_raw = (
                            daily_result[0]
                            if isinstance(daily_result, tuple)
                            else daily_result
                        )
                        # Build { project_id: [vol_day1, vol_day2, ...vol_day30] }
                        from collections import defaultdict

                        daily_map_raw = defaultdict(dict)
                        for r in daily_raw:
                            pid = str(r[0])
                            day = r[1]  # date object
                            vol = r[2]
                            daily_map_raw[pid][str(day)] = vol

                        # Fill in missing days with 0
                        daily_volume_map = {}
                        for pid in project_ids:
                            pid_str = str(pid)
                            days_data = []
                            for i in range(30):
                                day = (
                                    datetime.now() - timedelta(days=29 - i)
                                ).strftime("%Y-%m-%d")
                                days_data.append(
                                    daily_map_raw.get(pid_str, {}).get(day, 0)
                                )
                            daily_volume_map[pid_str] = days_data

                        # Last active — most recent span ingested per project
                        last_active_result = ch.execute_read(
                            "SELECT project_id, max(start_time) AS last_active "
                            "FROM spans "
                            "WHERE project_id IN %(pids)s "
                            "AND is_deleted = 0 "
                            "GROUP BY project_id",
                            {"pids": project_ids},
                            timeout_ms=5000,
                        )
                        la_raw = (
                            last_active_result[0]
                            if isinstance(last_active_result, tuple)
                            else last_active_result
                        )
                        last_active_map = {
                            str(r[0]): r[1].isoformat() if r[1] else None
                            for r in la_raw
                        }
                except Exception as e:
                    logger.warning(f"CH volume query failed, falling back to 0: {e}")

            last_active_map = locals().get("last_active_map", {})

            # Run counts — count ProjectVersions per project
            run_count_map = {}
            if project_ids:
                try:
                    from django.db.models import Count

                    from tracer.models.project_version import ProjectVersion

                    counts = (
                        ProjectVersion.objects.db_manager(DATABASE_FOR_PROJECT_LIST)
                        .filter(project_id__in=project_ids, deleted=False)
                        .values("project_id")
                        .annotate(count=Count("id"))
                    )
                    run_count_map = {str(c["project_id"]): c["count"] for c in counts}
                except Exception as e:
                    logger.warning(f"Run count query failed: {e}")

            # Alert counts — number of alert monitors configured per project
            # (drives the "Alerts" column). Same shape/scoping as run_count.
            alert_count_map = {}
            if project_ids:
                try:
                    alert_counts = (
                        UserAlertMonitor.objects.db_manager(DATABASE_FOR_PROJECT_LIST)
                        .filter(project_id__in=project_ids, deleted=False)
                        .values("project_id")
                        .annotate(count=Count("id"))
                    )
                    alert_count_map = {
                        str(c["project_id"]): c["count"] for c in alert_counts
                    }
                except Exception as e:
                    logger.warning(f"Alert count query failed: {e}")

            result = [
                {
                    "name": project["name"],
                    "last_30_days_vol": volume_map.get(str(project["id"]), 0),
                    "daily_volume": daily_volume_map.get(str(project["id"]), []),
                    "created_at": project["created_at"],
                    "updated_at": project["updated_at"],
                    "last_active": last_active_map.get(str(project["id"])),
                    "run_count": run_count_map.get(str(project["id"]), 0),
                    "issues": alert_count_map.get(str(project["id"]), 0),
                    "tags": project.get("tags") or [],
                    "id": project["id"],
                }
                for project in projects_data
            ]

            response = {
                "metadata": {
                    "total_rows": total_count,
                    "page_number": page_number,
                    "page_size": page_size,
                    "total_pages": (total_count + page_size - 1) // page_size,
                },
                "table": result,
            }

            return self._gm.success_response(response)

        except Exception as e:
            logger.exception(f"Error in fetching the project list: {str(e)}")

            return self._gm.bad_request(
                get_error_message("ERROR_FETCHING_PROJECT_LISTS")
            )

    @action(detail=True, methods=["patch"], url_path="tags")
    def update_tags(self, request, *args, **kwargs):
        """Update tags for a project."""
        try:
            project = self.get_object()
            tags = request.data.get("tags")
            if tags is None:
                return self._gm.bad_request("tags field is required")
            if not isinstance(tags, list):
                return self._gm.bad_request("tags must be a list")
            project.tags = tags
            project.save(update_fields=["tags", "updated_at"])
            return self._gm.success_response(
                {"id": str(project.id), "tags": project.tags}
            )
        except Exception as e:
            logger.exception(f"Error updating project tags: {e}")
            return self._gm.bad_request("Error updating tags")

    @validated_request(query_serializer=ProjectGraphDataQuerySerializer)
    @action(detail=False, methods=["get"])
    def get_graph_data(self, request, *args, **kwargs):
        query_params = request.validated_query_data
        project_id = str(query_params["project_id"])

        try:
            if not self._get_project_in_scope(project_id):
                return self._gm.bad_request("Project not found.")
            response_data = get_all_system_metrics(
                interval=query_params["interval"],
                filters=query_params["filters"],
                property="average",
                system_metric_filters={"project_id": project_id},
            )
            graph_data = {
                "system_metrics": response_data,
                "evaluations": {},
            }
            return self._gm.success_response(graph_data)

        except Project.DoesNotExist:
            return self._gm.bad_request("Project not found.")
        except Exception as e:
            logger.exception(f"Error in get_graph_data: {str(e)}")
            return self._gm.bad_request("Error fetching graph data")

    @validated_request(request_serializer=ProjectUserMetricsRequestSerializer)
    @action(detail=False, methods=["post"])
    def get_user_metrics(self, request, *args, **kwargs):
        try:
            body = request.validated_data
            end_user_id = str(body["end_user_id"])
            project_id = str(body["project_id"])
            filters = body["filters"]

            if not self._get_project_in_scope(project_id):
                return self._gm.bad_request("Project not found.")

            _org = get_request_organization(request) or request.user.organization
            _org_id = str(_org.id)
            analytics = AnalyticsQueryService()
            builder = UserListQueryBuilderV2(
                organization_id=_org_id,
                workspace_id=str(request.workspace.id),
                project_id=project_id,
                filters=filters,
                end_user_id=end_user_id,
                include_null_workspace=bool(
                    getattr(request.workspace, "is_default", False)
                ),
            )
            query, params = builder.build()
            result = analytics.execute_ch_query(query, params, timeout_ms=30000)
            output = []
            for row in builder.format_rows(result.data)["table"]:
                output.append(
                    {
                        "user_id": row.get("user_id"),
                        "user_id_type": row.get("user_id_type"),
                        "user_id_hash": row.get("user_id_hash"),
                        "active_days": row.get("num_active_days", 0),
                        "last_active": row.get("last_active"),
                        "total_cost": row.get("total_cost", 0),
                        "total_tokens": row.get("total_tokens", 0),
                        "avg_session_duration": row.get("avg_session_duration", 0),
                        "avg_trace_latency": row.get("avg_trace_latency", 0),
                        "num_llm_calls": row.get("num_llm_calls", 0),
                        "num_guardrails_triggered": row.get(
                            "num_guardrails_triggered", 0
                        ),
                        "num_traces_with_errors": row.get("num_traces_with_errors", 0),
                        "num_sessions": row.get("num_sessions", 0),
                    }
                )

            return self._gm.success_response(output)
        except Exception as e:
            logger.exception(f"ERROR IN RETRIEVING USER METRICS: {e}")
            return self._gm.internal_server_error_response()

    @validated_request(
        request_serializer=ProjectUsersAggregateGraphDataRequestSerializer
    )
    @action(detail=False, methods=["post"])
    def get_users_aggregate_graph_data(self, request, *args, **kwargs):
        """
        Fetch time-series aggregate user metrics for the observe graph.

        Supports SYSTEM_METRIC, EVAL, and ANNOTATION types.
        All metrics are aggregated at the user level.
        """
        try:
            body = request.validated_data
            project_id = str(body["project_id"])
            filters = body["filters"]
            interval = body["interval"]
            req_data_config = body["req_data_config"]
            metric_type = req_data_config.get("type", "SYSTEM_METRIC")
            metric_id = req_data_config.get("id", "active_users")

            if not self._get_project_in_scope(project_id):
                return self._gm.bad_request("Project not found.")

            analytics = AnalyticsQueryService()

            if metric_type == "SYSTEM_METRIC":
                try:
                    from tracer.services.clickhouse.query_builders.user_time_series import (
                        UserTimeSeriesQueryBuilder,
                    )

                    builder = UserTimeSeriesQueryBuilder(
                        project_id=str(project_id),
                        filters=filters,
                        interval=interval,
                    )
                    query, params = builder.build()
                    result = analytics.execute_ch_query(query, params, timeout_ms=10000)
                    ch_data = builder.format_result(result.data, result.columns or [])

                    metric_key = metric_id if metric_id in ch_data else "active_users"
                    metric_points = ch_data.get(metric_key, [])
                    traffic_points = ch_data.get("traffic", [])
                    traffic_by_ts = {
                        t.get("timestamp"): t.get("traffic", 0) for t in traffic_points
                    }
                    graph_data = {
                        "metric_name": metric_id,
                        "data": [
                            {
                                "timestamp": p.get("timestamp"),
                                "value": p.get("value", 0),
                                "primary_traffic": traffic_by_ts.get(
                                    p.get("timestamp"), 0
                                ),
                            }
                            for p in metric_points
                        ],
                    }
                    return self._gm.success_response(graph_data)
                except Exception as e:
                    logger.warning("CH user time-series failed", error=str(e))
                    return self._gm.bad_request("ClickHouse user graph failed")

            elif metric_type in ("EVAL", "ANNOTATION"):
                user_filters = [
                    *filters,
                    {
                        "column_id": "end_user_id",
                        "filter_config": {
                            "col_type": "SYSTEM_METRIC",
                            "filter_type": "text",
                            "filter_op": "is_not_null",
                            "filter_value": None,
                        },
                    },
                ]
                if metric_type == "EVAL":
                    try:
                        return self._gm.success_response(
                            fetch_eval_graph_ch(
                                analytics=analytics,
                                project_id=project_id,
                                filters=user_filters,
                                interval=interval,
                                req_data_config=req_data_config,
                            )
                        )
                    except Exception as e:
                        logger.exception(
                            "ClickHouse user eval graph failed",
                            error=str(e),
                        )
                        return self._gm.bad_request("ClickHouse user graph failed")

                if metric_type == "ANNOTATION":
                    try:
                        return self._gm.success_response(
                            fetch_annotation_graph_ch(
                                analytics=analytics,
                                project_id=project_id,
                                filters=user_filters,
                                interval=interval,
                                req_data_config=req_data_config,
                                observe_type="trace",
                            )
                        )
                    except Exception as e:
                        logger.exception(
                            "ClickHouse user annotation graph failed",
                            error=str(e),
                        )
                        return self._gm.bad_request("ClickHouse user graph failed")

                from tracer.models.trace import Trace
                from tracer.utils.graphs_optimized import (
                    get_annotation_graph_data,
                    get_eval_graph_data,
                )

                # All traces that have a user
                user_trace_qs = Trace.objects.filter(
                    project_id=project_id,
                ).filter(
                    id__in=ObservationSpan.objects.filter(
                        project_id=project_id,
                        end_user__isnull=False,
                    ).values("trace_id"),
                )

                if metric_type == "EVAL":
                    graph_data = get_eval_graph_data(
                        interval=interval,
                        filters=filters,
                        property=body["property"],
                        observe_type="trace",
                        req_data_config=req_data_config,
                        eval_logger_filters={"trace_ids_queryset": user_trace_qs},
                    )
                else:
                    graph_data = get_annotation_graph_data(
                        interval=interval,
                        filters=filters,
                        property=body["property"],
                        observe_type="trace",
                        req_data_config=req_data_config,
                        annotation_logger_filters={"trace_ids_queryset": user_trace_qs},
                    )
                return self._gm.success_response(
                    graph_data or {"metric_name": metric_id, "data": []}
                )

            # Fallback: empty
            return self._gm.success_response({"metric_name": metric_id, "data": []})
        except Exception as e:
            logger.exception(f"Error in get_users_aggregate_graph_data: {str(e)}")
            return self._gm.bad_request(f"Error fetching user graph data: {str(e)}")

    @validated_request(
        query_serializer=ProjectUserGraphDataQuerySerializer,
        request_serializer=ProjectUserGraphDataRequestSerializer,
    )
    @action(detail=False, methods=["post"])
    def get_user_graph_data(self, request, *args, **kwargs):
        try:
            query_params = request.validated_query_data
            body = request.validated_data
            project_id = str(query_params["project_id"])
            end_user_id = str(query_params["end_user_id"])
            if not self._get_project_in_scope(project_id):
                return self._gm.bad_request("Project not found.")

            try:
                interval = body["interval"]
                filters = body["filters"]
                analytics = AnalyticsQueryService()
                builder = TimeSeriesQueryBuilder(
                    project_id=project_id,
                    filters=filters,
                    interval=interval,
                )
                _org = get_request_organization(request) or request.user.organization
                start_date, end_date = builder.parse_time_range(filters)
                bucket_fn = builder.time_bucket_expr(interval)
                fb = ClickHouseFilterBuilder(
                    table="spans",
                    project_id=project_id,
                    query_mode=ClickHouseFilterBuilder.QUERY_MODE_SPAN,
                )
                extra_where, extra_params = fb.translate(filters)
                extra_clause = f"AND {extra_where}" if extra_where else ""
                params = {
                    "project_id": project_id,
                    "end_user_id": end_user_id,
                    "org_id": str(_org.id),
                    "start_date": start_date,
                    "end_date": end_date,
                    **extra_params,
                }
                if getattr(request, "workspace", None):
                    params["workspace_id"] = str(request.workspace.id)

                # CH25 EndUser cutover (DESIGN §4.3): curated source is the v2
                # `end_users` RMT. It has no `workspace_id` (schema 017), so the
                # workspace clause is dropped here — `project_id` is validated
                # upstream via `_get_project_in_scope`, and the subquery already
                # pins to a single enduser by (id, organization_id, project_id),
                # which fully constrains the row without the workspace guard.
                #
                # P3b step1.5 (DESIGN §3 / id_remap_sql): resolve each span's
                # `end_user_id` new→old through `end_user_id_remap` BEFORE the
                # `IN (end_users …)` membership check, so a cross-cutover
                # straddler's NEW (deterministic-id) spans match the SAME curated
                # `end_user_id` the `end_users` subquery returns (the OLD id, still
                # primary) and roll into this per-user detail graph instead of
                # being dropped. The raw `spans` scan keeps the committed
                # project/time/soft-delete predicates and `{extra_clause}` on the
                # bare columns; the remap join is a thin outer layer and
                # `resolved_id_expr` is the zero-uuid-guarded new→old map (NOT a
                # COALESCE — an unmatched LEFT JOIN fills `old_id` with the
                # zero-uuid, not NULL; see id_remap_sql). Pre-flip NO span matches
                # a `new_id`, so the resolved id == the span's own id and this is a
                # byte-identical no-op (acceptance gate B).
                from tracer.services.clickhouse.v2.id_remap_sql import (
                    remap_left_join,
                    resolved_id_expr,
                )

                # P3b step1.5 — DUAL remap (DESIGN §3 / id_remap_sql): this per-user
                # graph filters by the OLD curated end_user_id AND reports
                # `uniqExactIf(trace_session_id)`. A cross-cutover straddler splits
                # on BOTH axes, so resolve BOTH columns new→old. The two joins hang
                # off the SAME inner scan `rs` and so MUST carry DISTINCT aliases
                # (the default `id_remap` would collide) — `eu_remap` / `ts_remap`.
                # Resolving the session id makes `uniqExactIf` count a straddler's
                # old+new session ids as ONE session (else session_count inflates).
                # Pre-flip NO span matches either `new_id`, so both resolved ids ==
                # own id → byte-identical no-op (gate B).
                eu_remap_join = remap_left_join(
                    "rs.end_user_id", "end_user_id_remap", "eu_remap"
                )
                ts_remap_join = remap_left_join(
                    "rs.trace_session_id", "trace_session_id_remap", "ts_remap"
                )
                eu_resolved = resolved_id_expr("rs.end_user_id", "eu_remap")
                ts_resolved = resolved_id_expr("rs.trace_session_id", "ts_remap")
                query = f"""
                SELECT
                    {bucket_fn}(created_at) AS time_bucket,
                    uniqExactIf(toString(trace_session_id), isNotNull(trace_session_id)) AS session_count,
                    uniqExact(trace_id) AS trace_count,
                    sum(ifNull(cost, 0)) AS cost,
                    sum(ifNull(prompt_tokens, 0)) AS input_tokens,
                    sum(ifNull(completion_tokens, 0)) AS output_tokens
                FROM (
                    SELECT
                        {eu_resolved} AS end_user_id,
                        rs.trace_id AS trace_id,
                        {ts_resolved} AS trace_session_id,
                        rs.created_at AS created_at,
                        rs.cost AS cost,
                        rs.prompt_tokens AS prompt_tokens,
                        rs.completion_tokens AS completion_tokens
                    FROM spans AS rs
                    {eu_remap_join}
                    {ts_remap_join}
                    WHERE rs.project_id = %(project_id)s
                      AND rs.is_deleted = 0
                      AND rs.created_at >= %(start_date)s
                      AND rs.created_at < %(end_date)s
                      {extra_clause}
                )
                WHERE end_user_id IN (
                    SELECT end_user_id
                    FROM end_users FINAL
                    WHERE end_user_id = toUUID(%(end_user_id)s)
                      AND organization_id = toUUID(%(org_id)s)
                      AND project_id = toUUID(%(project_id)s)
                      AND is_deleted = 0
                  )
                GROUP BY time_bucket
                ORDER BY time_bucket
                """
                result = analytics.execute_ch_query(query, params, timeout_ms=10000)
                rows = result.data or []

                def _series(source_key, output_key):
                    series_rows = [
                        (
                            row.get("time_bucket"),
                            row.get(source_key, 0),
                        )
                        for row in rows
                    ]
                    return builder.format_time_series(
                        rows=series_rows,
                        columns=["time_bucket", output_key],
                        interval=interval,
                        start_date=start_date,
                        end_date=end_date,
                        value_keys=[output_key],
                    )

                return self._gm.success_response(
                    {
                        "session": _series("session_count", "session"),
                        "trace": _series("trace_count", "trace"),
                        "cost": _series("cost", "cost"),
                        "input_tokens": _series("input_tokens", "input_tokens"),
                        "output_tokens": _series("output_tokens", "output_tokens"),
                    }
                )
            except Project.DoesNotExist:
                return self._gm.bad_request("Project not found.")
            except Exception as e:
                logger.exception(f"Error in get_graph_data: {str(e)}")
                return self._gm.internal_server_error_response(str(e))
        except Exception as e:
            logger.exception(f"ERROR IN RETRIEVING USER DATA GRAPH: {e}")
            return self._gm.internal_server_error_response()

    @validated_request(responses={200: ProjectIdListResponseSerializer})
    @action(detail=False, methods=["get"])
    def list_project_ids(self, request, *args, **kwargs):
        """
        List project ids for a given project.
        """
        try:
            projects = self.get_queryset().values("id", "name", "trace_type")
            return self._gm.success_response({"projects": list(projects)})
        except Exception as e:
            logger.exception(f"Error in listing projects: {str(e)}")

            return self._gm.bad_request(
                get_error_message("ERROR_FETCHING_PROJECT_LISTS")
            )

    @action(detail=False, methods=["get"])
    def project_sdk_code(self, request, *args, **kwargs):
        project_type = self.request.query_params.get("project_type", "experiment")

        if project_type == "experiment":
            sdk_code = PROTOTYPE_CODEBLOCK
        elif project_type == "observe":
            sdk_code = OBSERVE_CODEBLOCK
        else:
            return self._gm.bad_request("Invalid project type")

        response = {
            "installation_guide": INSTALLATION_GUIDE,
            "project_add_code": sdk_code,
            "keys": {
                lang: code.format("YOUR_FI_API_KEY", "YOUR_FI_SECRET_KEY")
                for lang, code in ORG_KEYS.items()
            },
            "instruments": INSTRUMENTORS,
        }
        return self._gm.success_response(response)

    @action(detail=False, methods=["get"])
    def fetch_system_metrics(self, request, *args, **kwargs):
        try:
            metrics = ["latency", "cost", "tokens"]
            return self._gm.success_response(metrics)
        except Exception as e:
            logger.exception(f"Error in fetching system metrics: {str(e)}")
            return self._gm.bad_request("Error fetching system metrics")
