import json
from datetime import datetime, timedelta

import structlog
from django.db import models
from django.db.models import Count
from django.db.models.functions import Coalesce
from django.utils import timezone
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ModelViewSet

from accounts.models.user import OrgApiKey
from accounts.utils import get_request_organization

logger = structlog.get_logger(__name__)
from analytics.utils import mixpanel_slack_notfy, track_mixpanel_event
from model_hub.utils.SQL_queries import SQLQueryHandler
from tfc.middleware.db_health_check import db_connection_required
from tfc.middleware.query_timeout import monitor_query_performance
from tfc.routers import uses_db
from tfc.utils.base_viewset import BaseModelViewSetMixinWithUserOrg
from tfc.utils.error_codes import get_error_message
from tfc.utils.general_methods import GeneralMethods
from tracer.db_routing import DATABASE_FOR_PROJECT_LIST
from tracer.models.eval_task import EvalTask
from tracer.models.monitor import UserAlertMonitor, UserAlertMonitorLog
from tracer.models.observation_span import EndUser, ObservationSpan
from tracer.models.project import Project
from tracer.models.project_version import ProjectVersion
from tracer.models.trace import Trace
from tracer.models.trace_scan import TraceScanConfig
from tracer.models.trace_session import TraceSession
from tracer.queries.error_analysis import TraceErrorAnalysisDB
from tracer.serializers.project import ProjectNameUpdateSerializer, ProjectSerializer
from tracer.utils.constants import (
    INSTALLATION_GUIDE,
    INSTRUMENTORS,
    OBSERVE_CODEBLOCK,
    ORG_KEYS,
    PROTOTYPE_CODEBLOCK,
)
from tracer.utils.filters import FilterEngine
from tracer.utils.graphs import GraphEngine
from tracer.utils.graphs_optimized import get_all_system_metrics
from tracer.utils.helper import get_default_project_version_config, get_sort_query


class ProjectView(BaseModelViewSetMixinWithUserOrg, ModelViewSet):
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()
    serializer_class = ProjectSerializer

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

    def retrieve(self, request, *args, **kwargs):
        """
        Get a single project by ID with sampling rate.
        """
        try:
            instance = self.get_object()
            serializer = self.get_serializer(instance)
            data = serializer.data

            try:
                scan_config = TraceScanConfig.objects.get(project=instance)
                data["sampling_rate"] = scan_config.sampling_rate
            except TraceScanConfig.DoesNotExist:
                data["sampling_rate"] = 0.1

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
            projects = Project.objects.filter(
                id__in=project_ids,
                organization=getattr(request, "organization", None)
                or request.user.organization,
            )
            if projects.exists():
                if project_type == "experiment":
                    ProjectVersion.objects.filter(project__in=projects).update(
                        deleted=True, deleted_at=timezone.now()
                    )
                else:
                    TraceSession.objects.filter(project__in=projects).update(
                        deleted=True, deleted_at=timezone.now()
                    )
                Trace.objects.filter(project__in=projects).update(
                    deleted=True, deleted_at=timezone.now()
                )
                ObservationSpan.objects.filter(project__in=projects).update(
                    deleted=True, deleted_at=timezone.now()
                )
                UserAlertMonitor.objects.filter(project__in=projects).update(
                    deleted=True, deleted_at=timezone.now()
                )

                EvalTask.objects.filter(project__in=projects).update(
                    deleted=True, deleted_at=timezone.now()
                )

                projects.update(deleted=True, deleted_at=timezone.now())

                return self._gm.success_response(
                    "Successfully deleted the selected projects"
                )

            else:
                return self._gm.bad_request(get_error_message("PROJECT_NOT_FOUND"))

        except Exception as e:
            logger.exception(f"Error in deleting the project: {str(e)}")

            return self._gm.bad_request(get_error_message("FAILED_TO_DELETE_PROJECT"))

    @action(detail=False, methods=["post"])
    def update_project_config(self, request, *args, **kwargs):
        try:
            project_id = self.request.data.get("project_id")
            visibility = self.request.data.get("visibility", {})
            try:
                project = Project.objects.get(
                    id=project_id,
                    organization=getattr(self.request, "organization", None)
                    or self.request.user.organization,
                )
            except Project.DoesNotExist:
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

                project = Project.objects.filter(
                    id=project_id,
                    organization=getattr(request, "organization", None)
                    or request.user.organization,
                ).first()

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
            try:
                project = Project.objects.get(
                    id=project_id,
                    organization=getattr(self.request, "organization", None)
                    or self.request.user.organization,
                )
            except Project.DoesNotExist:
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

            # Tag filtering
            tags_param = self.request.query_params.get("tags")
            if tags_param:
                for tag in tags_param.split(","):
                    tag = tag.strip()
                    if tag:
                        queryset = queryset.filter(tags__contains=[tag])

            ALLOWED_SORT_FIELDS = {"name", "created_at", "updated_at", "issues"}
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
                            "AND _peerdb_is_deleted = 0 "
                            "AND (parent_span_id IS NULL OR parent_span_id = %(e)s) "
                            "AND start_time >= %(since)s "
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
                            "AND _peerdb_is_deleted = 0 "
                            "AND (parent_span_id IS NULL OR parent_span_id = %(e)s) "
                            "AND start_time >= %(since)s "
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
                            "AND _peerdb_is_deleted = 0 "
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

            result = [
                {
                    "name": project["name"],
                    "last_30_days_vol": volume_map.get(str(project["id"]), 0),
                    "daily_volume": daily_volume_map.get(str(project["id"]), []),
                    "created_at": project["created_at"],
                    "updated_at": project["updated_at"],
                    "last_active": last_active_map.get(str(project["id"])),
                    "run_count": run_count_map.get(str(project["id"]), 0),
                    "issues": 0,
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

    @action(detail=False, methods=["get"])
    def get_graph_data(self, request, *args, **kwargs):
        project_id = self.request.query_params.get(
            "project_id"
        ) or self.request.query_params.get("projectId")
        if not project_id:
            return self._gm.bad_request("Project id is required.")

        try:
            # Get interval and filters from request
            interval = self.request.query_params.get("interval", "hour")
            filters = self.request.query_params.get("filters", [])
            if filters:
                filters = json.loads(filters)

            response_data = get_all_system_metrics(
                interval=interval,
                filters=filters,
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

    @action(detail=False, methods=["post"])
    def get_user_metrics(self, request, *args, **kwargs):
        try:
            end_user_id = self.request.data.get("end_user_id")
            filters = request.data.get("filters", [])
            project_id = request.data.get("project_id")
            if not project_id:
                return self._gm.bad_request("Project id is required.")
            if not end_user_id:
                return self._gm.bad_request("End User id is required.")
            try:
                end_user = EndUser.objects.get(
                    id=end_user_id,
                    organization=getattr(request, "organization", None)
                    or request.user.organization,
                    project_id=project_id,
                )
            except EndUser.DoesNotExist:
                return self._gm.bad_request("User not found for the given end_user_id")

            _org = get_request_organization(request)
            _org_id = str(_org.id) if _org else None
            query_params = {
                "org_id": _org_id,
                "filters": filters,
                "end_user_id": str(end_user.id),
                "project_id": project_id,
                "workspace_id": request.workspace.id,
            }

            query_params = {k: v for k, v in query_params.items() if v is not None}

            default_metrics = SQLQueryHandler.get_user_default_details(
                org_id=_org_id,
                end_user_id=end_user_id,
                project_id=project_id,
            )
            results = SQLQueryHandler.get_spans_by_end_users(**query_params)

            output = []
            for i, result in enumerate(results):
                output.append(
                    {
                        "user_id": result[0],
                        "user_id_type": result[19],
                        "user_id_hash": result[20],
                        "active_days": default_metrics[i][1],
                        "last_active": default_metrics[i][2],
                        "total_cost": result[1],
                        "total_tokens": result[2],
                        "avg_session_duration": result[7],
                        "avg_trace_latency": result[8],
                        "num_llm_calls": result[9],
                        "num_guardrails_triggered": result[10],
                        "num_traces_with_errors": result[14],
                        "num_sessions": result[6],
                    }
                )
            if len(output) == 0 and len(default_metrics) > 0:
                for metric in default_metrics:
                    output.append(
                        {
                            "user_id": metric[0],
                            "user_id_type": None,
                            "user_id_hash": None,
                            "active_days": 0,
                            "last_active": metric[2],
                            "total_cost": 0,
                            "total_tokens": 0,
                            "avg_session_duration": 0,
                            "avg_trace_latency": 0,
                            "num_llm_calls": 0,
                            "num_guardrails_triggered": 0,
                            "num_traces_with_errors": 0,
                            "num_sessions": 0,
                        }
                    )

            return self._gm.success_response(output)
        except Exception as e:
            logger.exception(f"ERROR IN RETRIEVING USER METRICS: {e}")
            return self._gm.internal_server_error_response()

    @action(detail=False, methods=["post"])
    def get_users_aggregate_graph_data(self, request, *args, **kwargs):
        """
        Fetch time-series aggregate user metrics for the observe graph.

        Supports SYSTEM_METRIC, EVAL, and ANNOTATION types.
        All metrics are aggregated at the user level.
        """
        try:
            project_id = request.data.get("project_id")
            if not project_id:
                return self._gm.bad_request("project_id is required")

            filters = request.data.get("filters", [])
            interval = request.data.get("interval", "day")
            req_data_config = request.data.get("req_data_config", {})
            metric_type = req_data_config.get("type", "SYSTEM_METRIC")
            metric_id = req_data_config.get("id", "active_users")

            from tracer.services.clickhouse.query_service import (
                AnalyticsQueryService,
                QueryType,
            )

            analytics = AnalyticsQueryService()

            if metric_type == "SYSTEM_METRIC":
                if analytics.should_use_clickhouse(QueryType.TIME_SERIES):
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
                        result = analytics.execute_ch_query(
                            query, params, timeout_ms=10000
                        )
                        ch_data = builder.format_result(
                            result.data, result.columns or []
                        )

                        metric_key = (
                            metric_id if metric_id in ch_data else "active_users"
                        )
                        metric_points = ch_data.get(metric_key, [])
                        traffic_points = ch_data.get("traffic", [])
                        traffic_by_ts = {
                            t.get("timestamp"): t.get("traffic", 0)
                            for t in traffic_points
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

            elif metric_type in ("EVAL", "ANNOTATION"):
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
                        property=request.data.get("property", "average"),
                        observe_type="trace",
                        req_data_config=req_data_config,
                        eval_logger_filters={"trace_ids_queryset": user_trace_qs},
                    )
                else:
                    graph_data = get_annotation_graph_data(
                        interval=interval,
                        filters=filters,
                        property=request.data.get("property", "average"),
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

    @action(detail=False, methods=["post"])
    def get_user_graph_data(self, request, *args, **kwargs):
        try:
            project_id = request.query_params.get("project_id")
            if not project_id:
                return self._gm.bad_request("Project id is required.")
            end_user_id = request.query_params.get("end_user_id")
            if not end_user_id:
                return self._gm.bad_request("End User id is required.")
            try:
                end_user_id = str(
                    EndUser.objects.get(
                        id=end_user_id,
                        organization=getattr(request, "organization", None)
                        or request.user.organization,
                        project_id=project_id,
                    ).id
                )
            except EndUser.DoesNotExist:
                return self._gm.bad_request("User not found for the given end_user_id")

            try:
                # Get interval and filters from request
                interval = request.data.get("interval", "hour")
                filters = request.data.get("filters", [])
                # Get spans with a single efficient query
                spans = (
                    ObservationSpan.objects.filter(
                        project_id=project_id, end_user_id=end_user_id
                    )
                    .select_related("trace")
                    .order_by("-created_at")
                )
                # Format spans for filter engine
                formatted_spans = [
                    {
                        "id": span.id,
                        "created_at": span.created_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                        # Add other necessary fields here
                    }
                    for span in spans
                ]

                # Apply filters
                filter_engine = FilterEngine(formatted_spans)
                filtered_spans = filter_engine.apply_filters(filters)
                span_ids = [span["id"] for span in filtered_spans]
                spans = ObservationSpan.objects.filter(id__in=span_ids)

                # Create graph engine instance with model objects
                graph_engine = GraphEngine(
                    objects=spans,
                    interval=interval,
                    filters=filters,
                )

                # Generate graph data
                graph_data = graph_engine.generate_graph(metric="users_graph")
                return self._gm.success_response(graph_data)
            except Project.DoesNotExist:
                return self._gm.bad_request("Project not found.")
            except Exception as e:
                logger.exception(f"Error in get_graph_data: {str(e)}")
                return self._gm.internal_server_error_response(str(e))
        except Exception as e:
            logger.exception(f"ERROR IN RETRIEVING USER DATA GRAPH: {e}")
            return self._gm.internal_server_error_response()

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

        org = getattr(request, "organization", None) or request.user.organization
        if project_type == "experiment":
            sdk_code = PROTOTYPE_CODEBLOCK
        elif project_type == "observe":
            sdk_code = OBSERVE_CODEBLOCK
        else:
            return self._gm.bad_request("Invalid project type")

        apiKeys = OrgApiKey.objects.filter(
            organization=org, type="user", enabled=True, user=request.user
        )
        if len(apiKeys) == 0:
            apiKeys = OrgApiKey.objects.create(
                organization=org, type="user", enabled=True, user=request.user
            )

        else:
            apiKeys = OrgApiKey.objects.filter(
                organization=org, type="user", enabled=True, user=request.user
            ).first()
        response = {
            "installation_guide": INSTALLATION_GUIDE,
            "project_add_code": sdk_code,
            "keys": {
                lang: code.format(apiKeys.api_key, apiKeys.secret_key)
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
