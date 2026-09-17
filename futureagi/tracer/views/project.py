from datetime import timedelta
from functools import wraps

import structlog
from django.db import models
from django.db.models import Count
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ModelViewSet

from accounts.utils import get_request_organization
from tfc.middleware.db_health_check import db_connection_required
from tfc.middleware.query_timeout import monitor_query_performance
from tfc.routers import uses_db
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_serializers import ApiErrorResponseSerializer
from tfc.utils.base_viewset import BaseModelViewSetMixinWithUserOrg
from tfc.utils.error_codes import get_error_message
from tfc.utils.general_methods import GeneralMethods
from tracer.db_routing import DATABASE_FOR_PROJECT_LIST
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.monitor import UserAlertMonitor
from tracer.models.project import Project
from tracer.models.trace_scan import TraceScanConfig
from tracer.queries.projects import apply_project_list_filters
from tracer.serializers.filters import (
    ObserveGraphDataQuerySerializer,
    ObserveGraphDataResponseSerializer,
)
from tracer.serializers.project import (
    ProjectDetailResponseSerializer,
    ProjectGraphDataQuerySerializer,
    ProjectGraphDataResponseSerializer,
    ProjectIdListResponseSerializer,
    ProjectListQuerySerializer,
    ProjectListResponseSerializer,
    ProjectNameUpdateSerializer,
    ProjectSerializer,
    ProjectUserGraphDataQuerySerializer,
    ProjectUserGraphDataRequestSerializer,
    ProjectUserGraphDataResponseSerializer,
    ProjectUserMetricsRequestSerializer,
    ProjectUsersAggregateGraphDataRequestSerializer,
)
from tracer.services.clickhouse.graph_action_deadline import (
    GraphActionUnavailable,
    finish_graph_action_response,
    graph_action_postgres_budget,
    graph_action_remaining_ms,
    start_graph_action_deadline,
)
from tracer.services.clickhouse.graph_dispatch import (
    enforce_exact_graph_data_contract,
    fetch_annotation_graph_ch,
    fetch_eval_graph_ch,
    fetch_user_system_metric_graph_ch,
    graph_payload_is_publishable,
)
from tracer.services.clickhouse.query_builders.latest_filter_predicates import (
    UnsupportedFilterShapeError,
)
from tracer.services.clickhouse.read_budget import (
    ReadDeadline,
    is_clickhouse_api_read_unavailable_error,
)
from tracer.services.clickhouse.v2.query_builders.user_list import (
    UserListQueryBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.user_time_series import (
    UserDetailTimeSeriesQueryBuilderV2,
)
from tracer.services.clickhouse.v2.query_service import V2AnalyticsQueryService
from tracer.services.filter_principal_context import (
    FilterPrincipalContextError,
    bind_request_my_annotations_principal,
)
from tracer.services.project_deletion import soft_delete_projects
from tracer.utils.constants import (
    INSTALLATION_GUIDE,
    INSTRUMENTORS,
    OBSERVE_CODEBLOCK,
    ORG_KEYS,
    PROTOTYPE_CODEBLOCK,
)
from tracer.utils.graphs_optimized import (
    SystemMetricGraphReadError,
    get_all_system_metrics,
)
from tracer.utils.helper import (
    get_default_project_session_config,
    get_default_project_version_config,
    get_sort_query,
)
from tracer.utils.property_registry import validate_property_graph_namespace

logger = structlog.get_logger(__name__)

# The Observe landing page is a latency-critical navigation path. Never replay
# raw span versions here: the dedicated rollup has one aggregate state per
# project/hour, so its cost is bounded by the requested time window rather than
# tenant span volume. The rollup is an insert-time materialization and therefore
# deliberately advertised as non-exact (a later tombstone cannot retract an
# earlier state). Exact correction belongs in a background snapshot, not in the
# interactive project list.
_PROJECT_ACTIVITY_TIMEOUT_MS = 9_500
_PROJECT_ACTIVITY_PROVENANCE = "trace_count_rollup"
_PROJECT_ACTIVITY_READ_SETTINGS = {
    "max_threads": 4,
    "max_block_size": 8_192,
    "read_overflow_mode": "throw",
    "max_bytes_to_read": 36 * 1024 * 1024 * 1024,
    "max_memory_usage": 36 * 1024 * 1024 * 1024,
    "max_bytes_before_external_group_by": 64 * 1024 * 1024,
    "optimize_aggregation_in_order": 1,
    "max_result_rows": 1_000,
    "max_result_bytes": 16 * 1024 * 1024,
    "result_overflow_mode": "throw",
    "timeout_overflow_mode": "throw",
}

# The legacy per-user metrics panel is still a production Observe read even
# though the project-level user graph has moved to exact snapshots. Keep this
# one statement under the shared graph-action wall: source-row volume is not an
# error condition, while bytes, memory, result size, threads, and wall time are
# finite and fail closed.
_PROJECT_USER_GRAPH_READ_SETTINGS = {
    "max_threads": 1,
    "max_block_size": 8_192,
    "max_bytes_to_read": 36 * 1024 * 1024 * 1024,
    "max_memory_usage": 36 * 1024 * 1024 * 1024,
    "max_bytes_before_external_group_by": 64 * 1024 * 1024,
    "max_result_rows": 10_000,
    "max_result_bytes": 32 * 1024 * 1024,
    "read_overflow_mode": "throw",
    "result_overflow_mode": "throw",
    "timeout_overflow_mode": "throw",
}


def _bounded_project_user_action(*, log_event: str, unavailable_message: str):
    """Start a project user-action wall before runtime request validation."""

    def decorate(view_method):
        @wraps(view_method)
        def wrapped(view, request, *args, **kwargs):
            deadline = start_graph_action_deadline()
            kwargs.pop("_graph_action_deadline", None)
            try:
                response = view_method(
                    view,
                    request,
                    *args,
                    _graph_action_deadline=deadline,
                    **kwargs,
                )
                return finish_graph_action_response(deadline, response)
            except GraphActionUnavailable:
                logger.warning(log_event)
                return view._gm.custom_error_response(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    unavailable_message,
                    code="service_unavailable",
                )

        # Keep one-level ``.__wrapped__`` and ``inspect.unwrap`` callers on the
        # original action. Runtime calls still execute ``view_method`` above,
        # including its request-validation wrapper and copied DRF metadata.
        wrapped.__wrapped__ = getattr(view_method, "__wrapped__", view_method)
        return wrapped

    return decorate


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
        soft_delete_projects(projects, project_type)

    def get_queryset(self):
        # Request scope is authoritative here.  ``Project.objects`` also applies
        # the ambient ContextVar workspace, so starting through the generic
        # mixin can intersect two different workspace scopes and hide a valid
        # project.  The explicit no-workspace manager keeps authorization bound
        # to the authenticated request while avoiding inherited worker/request
        # context from changing the result.
        queryset = self._project_scope_queryset()

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

            # Merge in default columns missing from the stored config (empty
            # config, or one seeded before newer columns existed) so their
            # visibility can be persisted instead of silently dropped.
            defaults = get_default_project_session_config()
            config = project.session_config or []
            existing_ids = {item.get("id") for item in config}
            config = config + [d for d in defaults if d["id"] not in existing_ids]

            for key, value in visibility.items():
                config_entry = next(
                    (item for item in config if item.get("id") == key), None
                )
                if config_entry:
                    config_entry["is_visible"] = value

            project.session_config = config
            project.save(update_fields=["session_config"])

            return self._gm.success_response({"project_id": project.id})
        except Exception as e:
            logger.exception(f"Error in updating the project session config: {str(e)}")

            return self._gm.bad_request(
                get_error_message("FAILED_TO_UPDATE_PROJECT_CONFIG")
            )

    @validated_request(
        query_serializer=ProjectListQuerySerializer,
        responses={200: ProjectListResponseSerializer},
    )
    @action(detail=False, methods=["get"], pagination_class=None)
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
            if sort_direction not in {"asc", "desc"}:
                return self._gm.bad_request("sort_direction must be asc or desc")
            sort_query = f"-{sort_by}" if sort_direction == "desc" else sort_by
            # A complete picker may follow multiple numbered pages. Every
            # supported sort field can tie (especially created_at during bulk
            # imports), so keep the ordering total and stable across requests;
            # otherwise rows can move between pages and be skipped/duplicated.
            id_sort = "-id" if sort_direction == "desc" else "id"
            queryset = queryset.order_by(sort_query, id_sort)

            try:
                page_number = int(self.request.query_params.get("page_number", 0))
                page_size = int(self.request.query_params.get("page_size", 20))
            except (TypeError, ValueError):
                return self._gm.bad_request(
                    "page_number and page_size must be integers"
                )
            if page_number < 0 or not 1 <= page_size <= 100:
                return self._gm.bad_request(
                    "page_number must be non-negative and page_size must be 1 to 100"
                )
            total_count = queryset.count()
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
            last_active_map = {}
            project_ids = [str(p["id"]) for p in projects_data]
            activity_query_complete = not project_ids
            activity_error_code = None
            if project_ids:
                try:
                    service = V2AnalyticsQueryService()
                except Exception as e:
                    activity_error_code = "project_activity_unavailable"
                    logger.warning(f"CH project activity client unavailable: {e}")
                else:
                    try:
                        # A readonly=1 / locked ClickHouse profile strips every
                        # query-local row, byte, memory, and execution-time cap.
                        # This high-volume optional read is safe only when the
                        # server accepts the finite settings below; otherwise
                        # fail closed without issuing it.
                        if not service.supports_per_query_read_settings:
                            raise RuntimeError(
                                "exact project activity read requires enforced "
                                "per-query limits"
                            )
                        activity_today = timezone.now().date()
                        volume_window_start = activity_today - timedelta(days=29)
                        activity_window_start = activity_today - timedelta(days=89)
                        activity_window_end = activity_today + timedelta(days=1)
                        activity_deadline = ReadDeadline.start(
                            _PROJECT_ACTIVITY_TIMEOUT_MS
                        )
                        activity_query = """
                                WITH daily AS (
                                    SELECT
                                        project_id,
                                        toDate(hour) AS day,
                                        uniqExactMerge(uniq_traces_state)
                                            AS day_volume,
                                        max(hour) AS day_last_active
                                    FROM trace_count_rollup
                                    PREWHERE project_id IN %(pids)s
                                      AND hour >= toDateTime(
                                          %(activity_start)s, 'UTC'
                                      )
                                      AND hour < toDateTime(
                                          %(activity_end)s, 'UTC'
                                      )
                                    GROUP BY project_id, day
                                )
                                SELECT
                                    toString(project_id) AS project_id_text,
                                    sumIf(
                                        day_volume,
                                        day >= toDate(%(volume_start)s)
                                    ) AS volume,
                                    max(day_last_active) AS last_active,
                                    arraySort(
                                        item -> item.1,
                                        groupArrayIf(
                                            tuple(toString(day), day_volume),
                                            day >= toDate(%(volume_start)s)
                                        )
                                    ) AS daily_volume
                                FROM daily
                                GROUP BY project_id
                            """

                        # Publish all three activity fields atomically only
                        # after the single bounded rollup read parses cleanly.
                        pending_volume_map = dict.fromkeys(project_ids, 0)
                        pending_daily_map_raw = {pid: {} for pid in project_ids}
                        pending_last_active_values = dict.fromkeys(project_ids)
                        activity_result = service.execute_ch_query(
                            activity_query,
                            {
                                "pids": project_ids,
                                "activity_start": activity_window_start.strftime(
                                    "%Y-%m-%d"
                                ),
                                "activity_end": activity_window_end.strftime(
                                    "%Y-%m-%d"
                                ),
                                "volume_start": volume_window_start.strftime(
                                    "%Y-%m-%d"
                                ),
                            },
                            timeout_ms=activity_deadline.remaining_ms(),
                            settings=_PROJECT_ACTIVITY_READ_SETTINGS,
                        )
                        for row in activity_result.data:
                            required_columns = {
                                "project_id_text",
                                "volume",
                                "last_active",
                                "daily_volume",
                            }
                            if not isinstance(
                                row, dict
                            ) or not required_columns.issubset(row):
                                raise ValueError(
                                    "project activity rollup returned an invalid schema"
                                )
                            pid = str(row["project_id_text"])
                            if pid not in pending_volume_map:
                                continue
                            pending_volume_map[pid] = int(row.get("volume") or 0)
                            for day, day_volume in row.get("daily_volume") or []:
                                pending_daily_map_raw[pid][str(day)] = int(day_volume)
                            pending_last_active_values[pid] = row.get("last_active")

                        pending_daily_volume_map = {
                            pid: [
                                pending_daily_map_raw[pid].get(
                                    (
                                        volume_window_start + timedelta(days=offset)
                                    ).strftime("%Y-%m-%d"),
                                    0,
                                )
                                for offset in range(30)
                            ]
                            for pid in project_ids
                        }
                        pending_last_active_map = {
                            pid: (last_active.isoformat() if last_active else None)
                            for pid, last_active in pending_last_active_values.items()
                        }
                        volume_map = pending_volume_map
                        daily_volume_map = pending_daily_volume_map
                        last_active_map = pending_last_active_map
                        activity_query_complete = True
                    except Exception as e:
                        volume_map = {}
                        daily_volume_map = {}
                        last_active_map = {}
                        activity_error_code = "project_activity_unavailable"
                        logger.warning(f"CH exact project activity query failed: {e}")

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
                    "last_30_days_vol": (
                        volume_map.get(str(project["id"]), 0)
                        if activity_query_complete
                        else None
                    ),
                    "daily_volume": (
                        daily_volume_map.get(str(project["id"]), [])
                        if activity_query_complete
                        else None
                    ),
                    "created_at": project["created_at"],
                    "updated_at": project["updated_at"],
                    "last_active": (
                        last_active_map.get(str(project["id"]))
                        if activity_query_complete
                        else None
                    ),
                    "activity_query_complete": activity_query_complete,
                    "activity_error_code": activity_error_code,
                    "activity_query_exact": False,
                    "activity_query_provenance": _PROJECT_ACTIVITY_PROVENANCE,
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

    @validated_request(
        query_serializer=ProjectGraphDataQuerySerializer,
        responses={
            200: ProjectGraphDataResponseSerializer,
            400: ApiErrorResponseSerializer,
            500: ApiErrorResponseSerializer,
            503: ApiErrorResponseSerializer,
        },
    )
    @action(detail=False, methods=["get"])
    def get_graph_data(self, request, *args, **kwargs):
        query_params = request.validated_query_data
        project_id = str(query_params["project_id"])
        refresh = query_params.get("refresh", False)

        try:
            project = self._get_project_in_scope(project_id)
            if not project:
                return self._gm.bad_request("Project not found.")
            workspace_id = getattr(getattr(request, "workspace", None), "id", None)
            response_data = get_all_system_metrics(
                interval=query_params["interval"],
                filters=bind_request_my_annotations_principal(
                    request,
                    query_params["filters"],
                ),
                property="average",
                system_metric_filters={"project_id": project_id},
                refresh=refresh,
                organization_id=str(project.organization_id),
                workspace_id=str(workspace_id) if workspace_id else None,
            )
            if not graph_payload_is_publishable(
                response_data,
                allow_sampled=False,
            ):
                return self._gm.custom_error_response(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    "Graph data is temporarily unavailable. Please retry.",
                    code="service_unavailable",
                )
            graph_data = {
                "system_metrics": response_data,
                "evaluations": {},
            }
            return self._gm.success_response(graph_data)

        except Project.DoesNotExist:
            return self._gm.bad_request("Project not found.")
        except (UnsupportedFilterShapeError, FilterPrincipalContextError):
            return self._gm.bad_request("Graph filter configuration is invalid")
        except Exception as exc:
            if isinstance(
                exc, SystemMetricGraphReadError
            ) or is_clickhouse_api_read_unavailable_error(exc):
                logger.warning(
                    "project_graph_data_unavailable",
                    project_id=project_id,
                    error_type=type(exc).__name__,
                )
                return self._gm.custom_error_response(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    "Graph data is temporarily unavailable. Please retry.",
                    code="service_unavailable",
                )
            logger.exception(
                "project_graph_data_failed",
                project_id=project_id,
                error_type=type(exc).__name__,
            )
            return self._gm.custom_error_response(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "Graph data could not be loaded",
                code="server_error",
            )

    @_bounded_project_user_action(
        log_event="project_user_metrics_action_deadline_exceeded",
        unavailable_message="User metrics are temporarily unavailable. Please retry.",
    )
    @validated_request(request_serializer=ProjectUserMetricsRequestSerializer)
    @action(detail=False, methods=["post"])
    def get_user_metrics(self, request, *args, **kwargs):
        deadline = kwargs.pop("_graph_action_deadline", None)
        deadline_injected = deadline is not None
        if deadline is None:
            deadline = start_graph_action_deadline()

        def finish(response):
            if deadline_injected:
                return response
            return finish_graph_action_response(deadline, response)

        try:
            body = request.validated_data
            end_user_id = str(body["end_user_id"])
            project_id = str(body["project_id"])
            filters = bind_request_my_annotations_principal(
                request,
                body["filters"],
            )

            with graph_action_postgres_budget(deadline):
                project = self._get_project_in_scope(project_id)
            if not project:
                return finish(self._gm.bad_request("Project not found."))

            _org = get_request_organization(request) or request.user.organization
            _org_id = str(_org.id)
            analytics = V2AnalyticsQueryService()
            if not analytics.supports_per_query_read_settings:
                logger.warning("project_user_metrics_requires_enforced_read_limits")
                return finish(
                    self._gm.custom_error_response(
                        status.HTTP_503_SERVICE_UNAVAILABLE,
                        "User metrics are temporarily unavailable. Please retry.",
                        code="service_unavailable",
                    )
                )
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
            result = analytics.execute_ch_query(
                query,
                params,
                timeout_ms=graph_action_remaining_ms(deadline),
                settings=_PROJECT_USER_GRAPH_READ_SETTINGS,
            )
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

            return finish(self._gm.success_response(output))
        except GraphActionUnavailable:
            if deadline_injected:
                raise
            logger.warning("project_user_metrics_action_deadline_exceeded")
            return self._gm.custom_error_response(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "User metrics are temporarily unavailable. Please retry.",
                code="service_unavailable",
            )
        except Exception as exc:
            if is_clickhouse_api_read_unavailable_error(exc):
                logger.warning(
                    "project_user_metrics_unavailable",
                    error_type=type(exc).__name__,
                )
                return self._gm.custom_error_response(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    "User metrics are temporarily unavailable. Please retry.",
                    code="service_unavailable",
                )
            logger.exception(
                "project_user_metrics_failed",
                error_type=type(exc).__name__,
            )
            return self._gm.internal_server_error_response(
                "User metrics could not be loaded"
            )

    @_bounded_project_user_action(
        log_event="project_users_graph_action_deadline_exceeded",
        unavailable_message="User graph data is temporarily unavailable. Please retry.",
    )
    @validated_request(
        query_serializer=ObserveGraphDataQuerySerializer,
        request_serializer=ProjectUsersAggregateGraphDataRequestSerializer,
        responses={
            200: ObserveGraphDataResponseSerializer,
            400: ApiErrorResponseSerializer,
            500: ApiErrorResponseSerializer,
            503: ApiErrorResponseSerializer,
        },
    )
    @action(detail=False, methods=["post"])
    def get_users_aggregate_graph_data(self, request, *args, **kwargs):
        """
        Fetch time-series aggregate user metrics for the observe graph.

        Supports SYSTEM_METRIC, EVAL, and ANNOTATION types.
        All metrics are aggregated at the user level.
        """
        deadline = kwargs.pop("_graph_action_deadline", None)
        deadline_injected = deadline is not None
        if deadline is None:
            deadline = start_graph_action_deadline()

        def finish(response):
            if deadline_injected:
                return response
            return finish_graph_action_response(deadline, response)

        try:
            body = request.validated_data
            refresh = request.validated_query_data.get("refresh", False)
            project_id = str(body["project_id"])
            filters = bind_request_my_annotations_principal(
                request,
                body["filters"],
            )
            interval = body["interval"]
            req_data_config = body["req_data_config"]
            try:
                validate_property_graph_namespace(
                    req_data_config.get("property_id"),
                    expected_definition_source="users",
                )
            except ValueError:
                return finish(
                    self._gm.bad_request(
                        "property_id is not valid for this graph endpoint"
                    )
                )
            metric_type = req_data_config.get("type", "SYSTEM_METRIC")
            metric_id = req_data_config.get("id", "active_users")

            with graph_action_postgres_budget(deadline):
                project = self._get_project_in_scope(project_id)
            if not project:
                return finish(self._gm.bad_request("Project not found."))
            workspace_id = getattr(getattr(request, "workspace", None), "id", None)

            if metric_type == "EVAL":
                with graph_action_postgres_budget(deadline):
                    eval_config_available = CustomEvalConfig.objects.filter(
                        id=metric_id,
                        project_id=project_id,
                        deleted=False,
                    ).exists()
                if not eval_config_available:
                    return finish(
                        self._gm.bad_request(
                            "Evaluation config is not available for this project."
                        )
                    )

            analytics = V2AnalyticsQueryService()

            if metric_type == "SYSTEM_METRIC":
                try:
                    graph_data = fetch_user_system_metric_graph_ch(
                        analytics=analytics,
                        project_id=project_id,
                        filters=filters,
                        interval=interval,
                        metric_id=metric_id,
                        timeout_ms=graph_action_remaining_ms(deadline),
                        refresh=refresh,
                        organization_id=str(project.organization_id),
                        workspace_id=str(workspace_id) if workspace_id else None,
                    )
                    graph_data = enforce_exact_graph_data_contract(graph_data)
                    if not graph_payload_is_publishable(
                        graph_data,
                        allow_sampled=False,
                    ):
                        return finish(
                            self._gm.custom_error_response(
                                status.HTTP_503_SERVICE_UNAVAILABLE,
                                "User graph data is temporarily unavailable. Please retry.",
                                code="service_unavailable",
                            )
                        )
                    return finish(self._gm.success_response(graph_data))
                except Exception as e:
                    logger.warning("CH user time-series failed", error=str(e))
                    raise

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
                        graph_data = fetch_eval_graph_ch(
                            analytics=analytics,
                            project_id=project_id,
                            filters=user_filters,
                            interval=interval,
                            req_data_config=req_data_config,
                            timeout_ms=graph_action_remaining_ms(deadline),
                            refresh=refresh,
                            aggregation_context="user",
                            organization_id=str(project.organization_id),
                            workspace_id=str(workspace_id) if workspace_id else None,
                        )
                    except Exception as e:
                        logger.exception(
                            "ClickHouse user eval graph failed",
                            error=str(e),
                        )
                        raise

                elif metric_type == "ANNOTATION":
                    try:
                        graph_data = fetch_annotation_graph_ch(
                            analytics=analytics,
                            project_id=project_id,
                            filters=user_filters,
                            interval=interval,
                            req_data_config=req_data_config,
                            observe_type="trace",
                            timeout_ms=graph_action_remaining_ms(deadline),
                            refresh=refresh,
                            aggregation_context="user",
                            organization_id=str(project.organization_id),
                            workspace_id=str(workspace_id) if workspace_id else None,
                        )
                    except Exception as e:
                        logger.exception(
                            "ClickHouse user annotation graph failed",
                            error=str(e),
                        )
                        raise

                graph_data = enforce_exact_graph_data_contract(graph_data)
                if not graph_payload_is_publishable(
                    graph_data,
                    allow_sampled=False,
                ):
                    return finish(
                        self._gm.custom_error_response(
                            status.HTTP_503_SERVICE_UNAVAILABLE,
                            "User graph data is temporarily unavailable. Please retry.",
                            code="service_unavailable",
                        )
                    )
                return finish(self._gm.success_response(graph_data))

            # Fallback: empty
            return finish(
                self._gm.success_response({"metric_name": metric_id, "data": []})
            )
        except GraphActionUnavailable:
            if deadline_injected:
                raise
            logger.warning("project_users_graph_action_deadline_exceeded")
            return self._gm.custom_error_response(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "User graph data is temporarily unavailable. Please retry.",
                code="service_unavailable",
            )
        except Exception as exc:
            if is_clickhouse_api_read_unavailable_error(exc):
                logger.warning(
                    "project_users_graph_unavailable",
                    error_type=type(exc).__name__,
                )
                return self._gm.custom_error_response(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    "User graph data is temporarily unavailable. Please retry.",
                    code="service_unavailable",
                )
            logger.exception(
                "project_users_graph_failed",
                error_type=type(exc).__name__,
            )
            return self._gm.internal_server_error_response(
                "User graph data could not be loaded"
            )

    @_bounded_project_user_action(
        log_event="project_user_graph_action_deadline_exceeded",
        unavailable_message="User graph data is temporarily unavailable. Please retry.",
    )
    @validated_request(
        query_serializer=ProjectUserGraphDataQuerySerializer,
        request_serializer=ProjectUserGraphDataRequestSerializer,
        responses={
            200: ProjectUserGraphDataResponseSerializer,
            400: ApiErrorResponseSerializer,
            500: ApiErrorResponseSerializer,
            503: ApiErrorResponseSerializer,
        },
    )
    @action(detail=False, methods=["post"])
    def get_user_graph_data(self, request, *args, **kwargs):
        deadline = kwargs.pop("_graph_action_deadline", None)
        deadline_injected = deadline is not None
        if deadline is None:
            deadline = start_graph_action_deadline()

        def finish(response):
            if deadline_injected:
                return response
            return finish_graph_action_response(deadline, response)

        try:
            query_params = request.validated_query_data
            body = request.validated_data
            project_id = str(query_params["project_id"])
            end_user_id = str(query_params["end_user_id"])
            with graph_action_postgres_budget(deadline):
                project = self._get_project_in_scope(project_id)
            if not project:
                return finish(self._gm.bad_request("Project not found."))

            try:
                interval = body["interval"]
                filters = bind_request_my_annotations_principal(
                    request,
                    body["filters"],
                )
                analytics = V2AnalyticsQueryService()
                if not analytics.supports_per_query_read_settings:
                    logger.warning("project_user_graph_requires_enforced_read_limits")
                    return finish(
                        self._gm.custom_error_response(
                            status.HTTP_503_SERVICE_UNAVAILABLE,
                            "User graph data is temporarily unavailable. Please retry.",
                            code="service_unavailable",
                        )
                    )
                _org = get_request_organization(request) or request.user.organization
                builder = UserDetailTimeSeriesQueryBuilderV2(
                    project_id=project_id,
                    organization_id=str(_org.id),
                    end_user_id=end_user_id,
                    filters=filters,
                    interval=interval,
                )
                query, params = builder.build()
                start_date = builder.start_date
                end_date = builder.end_date
                assert start_date is not None and end_date is not None
                result = analytics.execute_ch_query(
                    query,
                    params,
                    timeout_ms=graph_action_remaining_ms(deadline),
                    settings=_PROJECT_USER_GRAPH_READ_SETTINGS,
                )
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

                return finish(
                    self._gm.success_response(
                        {
                            "session": _series("session_count", "session"),
                            "trace": _series("trace_count", "trace"),
                            "cost": _series("cost", "cost"),
                            "input_tokens": _series("input_tokens", "input_tokens"),
                            "output_tokens": _series("output_tokens", "output_tokens"),
                        }
                    )
                )
            except Project.DoesNotExist:
                return self._gm.bad_request("Project not found.")
            except GraphActionUnavailable:
                raise
            except Exception as exc:
                if is_clickhouse_api_read_unavailable_error(exc):
                    logger.warning(
                        "project_user_graph_unavailable",
                        error_type=type(exc).__name__,
                    )
                    return self._gm.custom_error_response(
                        status.HTTP_503_SERVICE_UNAVAILABLE,
                        "User graph data is temporarily unavailable. Please retry.",
                        code="service_unavailable",
                    )
                logger.exception(
                    "project_user_graph_failed",
                    error_type=type(exc).__name__,
                )
                return self._gm.internal_server_error_response(
                    "User graph data could not be loaded"
                )
        except GraphActionUnavailable:
            if deadline_injected:
                raise
            logger.warning("project_user_graph_action_deadline_exceeded")
            return self._gm.custom_error_response(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "User graph data is temporarily unavailable. Please retry.",
                code="service_unavailable",
            )
        except Exception as exc:
            logger.exception(
                "project_user_graph_request_failed",
                error_type=type(exc).__name__,
            )
            return self._gm.internal_server_error_response(
                "User graph data could not be loaded"
            )

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
