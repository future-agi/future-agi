import math
import traceback
from datetime import datetime, timedelta

import structlog
from django.db.models import Count, Exists, Max, OuterRef, Q
from django.utils import timezone
from drf_yasg.utils import swagger_auto_schema
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from tfc.utils.api_contracts import validated_request
from tfc.utils.api_serializers import (
    ApiErrorResponseSerializer,
)
from tfc.utils.base_viewset import (
    BaseModelViewSetMixin,
    BaseModelViewSetMixinWithUserOrg,
)
from tfc.utils.error_codes import get_error_message
from tfc.utils.general_methods import GeneralMethods
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.monitor import (
    MonitorMetricTypeChoices,
    UserAlertMonitor,
    UserAlertMonitorLog,
)
from tracer.models.project import Project
from tracer.serializers.monitor import (
    UserAlertMonitorBulkMuteRequestSerializer,
    UserAlertMonitorDetailSerializer,
    UserAlertMonitorDuplicateResponseSerializer,
    UserAlertMonitorDuplicateSerializer,
    UserAlertMonitorLogResolveRequestSerializer,
    UserAlertMonitorLogResolveResponseSerializer,
    UserAlertMonitorLogSerializer,
    UserAlertMonitorLogWriteRequestSerializer,
    UserAlertMonitorLogWriteResponseSerializer,
    UserAlertMonitorLogWriteSerializer,
    UserAlertMonitorMetricOptionsResponseSerializer,
    UserAlertMonitorPreviewGraphSerializer,
    UserAlertMonitorSerializer,
)
from tracer.utils.helper import get_sort_query
from tracer.utils.monitor_graphs import get_graph_data

logger = structlog.get_logger(__name__)


class UserAlertMonitorView(BaseModelViewSetMixinWithUserOrg, ModelViewSet):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]
    serializer_class = UserAlertMonitorSerializer

    def _current_organization(self):
        # Returns None for unauthenticated requests (e.g. drf-yasg's fake view
        # during OpenAPI generation) instead of raising on AnonymousUser, which
        # would otherwise silently drop request bodies from the generated schema.
        org = getattr(self.request, "organization", None)
        if org is not None:
            return org
        user = getattr(self.request, "user", None)
        if user is None or not user.is_authenticated:
            return None
        return getattr(user, "organization", None)

    def _workspace_scope_q(self, field_name="workspace"):
        workspace = getattr(self.request, "workspace", None)
        if not workspace:
            return Q()
        if getattr(workspace, "is_default", False):
            return (
                Q(**{field_name: workspace})
                | Q(
                    **{
                        f"{field_name}__is_default": True,
                        f"{field_name}__organization": workspace.organization,
                    }
                )
                | Q(**{f"{field_name}__isnull": True})
            )
        return Q(**{field_name: workspace})

    def _visible_observe_projects(self):
        organization = self._current_organization()
        if organization is None:
            return Project.no_workspace_objects.none()
        return Project.no_workspace_objects.filter(
            self._workspace_scope_q("workspace"),
            organization=organization,
            trace_type="observe",
            deleted=False,
        )

    def _base_monitor_queryset(self):
        unresolved_logs = UserAlertMonitorLog.objects.filter(
            alert=OuterRef("pk"), resolved=False
        )
        return (
            super()
            .get_queryset()
            .select_related("organization", "created_by", "project")
            .annotate(
                no_of_alerts=Count("useralertmonitorlog"),
                last_triggered=Max("useralertmonitorlog__created_at"),
                has_unresolved_logs=Exists(unresolved_logs),
            )
        )

    def get_serializer(self, *args, **kwargs):
        serializer = super().get_serializer(*args, **kwargs)
        serializer_obj = getattr(serializer, "child", serializer)
        fields = getattr(serializer_obj, "fields", None)
        if fields and "project" in fields:
            fields["project"].queryset = self._visible_observe_projects()
        return serializer

    def get_queryset(self):
        user_alert_id = self.kwargs.get("pk")
        query_Set = self._base_monitor_queryset()

        if user_alert_id:
            return query_Set.filter(id=user_alert_id)

        search_text = self.request.query_params.get("search_text")
        page_number = self.request.query_params.get("page_number", 0)
        page_size = self.request.query_params.get("page_size", 30)
        project_ids = self.request.query_params.getlist("project_id")
        status_filters = self.request.query_params.getlist("status")
        metric_type_filters = self.request.query_params.getlist("metric_type")

        if search_text:
            query_Set = query_Set.filter(Q(name__icontains=search_text))

        if project_ids:
            query_Set = query_Set.filter(project_id__in=project_ids)

        if status_filters:
            if "triggered" in status_filters and "healthy" not in status_filters:
                query_Set = query_Set.filter(has_unresolved_logs=True)
            elif "healthy" in status_filters and "triggered" not in status_filters:
                query_Set = query_Set.filter(has_unresolved_logs=False)

        if metric_type_filters:
            query_Set = query_Set.filter(metric_type__in=metric_type_filters)

        total_count = query_Set.count()

        sort_by = self.request.query_params.get("sort_by", "created_at")
        sort_direction = self.request.query_params.get("sort_direction", "desc")
        sort_query = get_sort_query(sort_by, sort_direction)

        start = int(page_number) * int(page_size)
        end = start + int(page_size)

        return query_Set.order_by(sort_query)[start:end], total_count

    def list(self, request, *args, **kwargs):
        """Return the paginated root monitor list.

        ``get_queryset`` returns ``(page_queryset, total_count)`` for list
        requests because ``list_monitors`` also needs the total count. DRF's
        default ``list`` expects only a queryset, so keep the root endpoint
        explicit instead of letting DRF serialize the tuple incorrectly.
        """
        try:
            page_number = int(request.query_params.get("page_number", 0))
            page_size = int(request.query_params.get("page_size", 30))
            queryset, total_records = self.get_queryset()
            serializer = self.get_serializer(queryset, many=True)

            return self._gm.success_response(
                {
                    "results": serializer.data,
                    "metadata": {
                        "total_rows": total_records,
                        "page_number": page_number,
                        "page_size": page_size,
                        "total_pages": (
                            math.ceil(total_records / page_size) if page_size > 0 else 0
                        ),
                    },
                }
            )
        except ValueError:
            return self._gm.bad_request(
                {"pagination": "page_number and page_size must be integers."}
            )

    @action(detail=True, methods=["get"], url_path="details")
    def monitor_details(self, request, *args, **kwargs):
        try:
            user_alert_id = kwargs.get("pk")
            user_alert_object = self._base_monitor_queryset().get(id=user_alert_id)
            serializer = UserAlertMonitorDetailSerializer(
                user_alert_object, context={"request": request}
            )

            data = serializer.data

            try:
                page_number = int(request.query_params.get("page_number", 0))
                page_size = int(request.query_params.get("page_size", 30))
            except (TypeError, ValueError):
                page_number = 0
                page_size = 10

            # Get and filter logs
            log_types = request.query_params.getlist("type")
            logs_queryset = (
                user_alert_object.useralertmonitorlog_set.select_related("resolved_by")
                .all()
                .order_by("-created_at")
            )

            latest_log = logs_queryset.first()
            if latest_log:
                data["last_triggered_at"] = latest_log.created_at
            else:
                data["last_triggered_at"] = None

            if log_types:
                logs_queryset = logs_queryset.filter(type__in=log_types)

            # Get total count before slicing
            total_logs = logs_queryset.count()

            # Slice for pagination
            start_index = page_number * page_size
            end_index = start_index + page_size
            paginated_logs_qs = logs_queryset[start_index:end_index]

            log_serializer = UserAlertMonitorLogSerializer(paginated_logs_qs, many=True)

            total_pages = math.ceil(total_logs / page_size) if page_size > 0 else 0

            data["logs"] = {
                "results": log_serializer.data,
                "metadata": {
                    "total_rows": total_logs,
                    "page_number": page_number,
                    "page_size": page_size,
                    "total_pages": total_pages,
                },
            }

            return self._gm.success_response(data)
        except UserAlertMonitor.DoesNotExist:
            return self._gm.not_found(get_error_message("MONITOR_NOT_FOUND"))
        except Exception as e:
            logger.error(f"Failed to get monitor details: {e}", exc_info=True)
            return self._gm.bad_request(get_error_message("FAILED_TO_GET_MONITOR"))

    def delete(self, request, *args, **kwargs):
        try:
            select_all = request.data.get("select_all", False)
            exclude_ids = request.data.get("exclude_ids", [])
            ids = request.data.get("ids", [])

            if select_all and ids:
                return self._gm.bad_request(
                    "Cannot provide both 'select_all' and 'ids'."
                )

            if not select_all and not ids:
                return self._gm.bad_request(
                    "A list of IDs or select_all flag is required for deletion"
                )

            user_alert_objects = self._base_monitor_queryset()

            if select_all:
                if exclude_ids:
                    user_alert_objects = user_alert_objects.exclude(id__in=exclude_ids)
            else:
                user_alert_objects = user_alert_objects.filter(id__in=ids)

            if not user_alert_objects.exists():
                return self._gm.bad_request(
                    "No User Alerts found for the provided criteria"
                )

            deleted_count = user_alert_objects.update(
                deleted=True, deleted_at=timezone.now()
            )

            return self._gm.success_response(
                {"message": f"{deleted_count} User Alerts deleted successfully"}
            )
        except Exception as e:
            return self._gm.internal_server_error_response(
                f"Error occurred while deleting User Alerts: {str(e)}"
            )

    def _scope_safe_update_data(self, request, instance, *, partial):
        data = request.data.copy()
        data.pop("organization", None)
        data.pop("workspace", None)
        data.pop("created_by", None)
        if not partial:
            data["organization"] = str(instance.organization_id)
            if instance.workspace_id:
                data["workspace"] = str(instance.workspace_id)
            if instance.created_by_id:
                data["created_by"] = str(instance.created_by_id)
        return data

    def _update_monitor(self, request, *, partial):
        try:
            instance = self.get_object()
            data = self._scope_safe_update_data(request, instance, partial=partial)

            serializer = self.get_serializer(instance, data=data, partial=partial)
            if serializer.is_valid():
                updated_instance = serializer.save()
                updated_instance.logs.append(
                    {
                        "timestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                        "message": f"Monitor {updated_instance.name} has been updated",
                        "type": "INFO",
                    }
                )
                updated_instance.save(update_fields=["logs"])
                return self._gm.success_response(serializer.data)
            else:
                return self._gm.bad_request(serializer.errors)
        except Exception as e:
            traceback.print_exc()
            logger.info(f"Error occurred while updating Alert Monitor: {str(e)}")
            return self._gm.bad_request(get_error_message("FAILED_TO_UPDATE_ALERT"))

    def _get_trend_data(self, monitor_obj, step=timedelta(days=1)):
        """
        Calculates trend data for a monitor using a dynamic time step.

        This function fetches all relevant log timestamps and performs bucketing
        in Python. This approach is conceptually simple and flexible.

        Args:
            monitor_obj: The UserAlertMonitor instance.
            step: A timedelta object for the interval (e.g., timedelta(days=2)).
        """
        end_date = timezone.now()
        start_date = end_date - timedelta(days=7)

        timestamps = monitor_obj.useralertmonitorlog_set.filter(
            created_at__gte=start_date,
            deleted=False,
        ).values_list("created_at", flat=True)

        num_buckets = int(math.ceil((end_date - start_date) / step))

        if num_buckets <= 0:
            return []

        buckets = [0] * num_buckets

        for ts in timestamps:
            if start_date <= ts < end_date:
                time_since_start = ts - start_date
                bucket_index = int(
                    time_since_start.total_seconds() // step.total_seconds()
                )
                if 0 <= bucket_index < num_buckets:
                    buckets[bucket_index] += 1

        trend_data = []
        for i in range(num_buckets):
            bucket_start_time = start_date + (i * step)
            midpoint = bucket_start_time + (step / 2)
            trend_data.append({"timestamp": midpoint.isoformat(), "count": buckets[i]})

        return trend_data

    @validated_request(request_serializer=UserAlertMonitorBulkMuteRequestSerializer)
    @action(detail=False, methods=["post"], url_path="bulk-mute")
    def bulk_mute(self, request, *args, **kwargs):
        try:
            ids = request.data.get("ids", [])
            is_mute = request.data.get("is_mute", True)
            select_all = request.data.get("select_all", False)
            exclude_ids = request.data.get("exclude_ids", [])

            if select_all and ids:
                return self._gm.bad_request(
                    "Cannot provide both 'select_all' and 'ids'."
                )

            if not select_all and not ids:
                return self._gm.bad_request(
                    "A list of alert IDs or select_all flag is required."
                )

            user_alert_objects = self._base_monitor_queryset()

            if select_all:
                if exclude_ids:
                    user_alert_objects = user_alert_objects.exclude(id__in=exclude_ids)
            else:
                user_alert_objects = user_alert_objects.filter(id__in=ids)

            if not user_alert_objects.exists():
                return self._gm.bad_request(
                    "No User Alerts found for the provided criteria."
                )

            updated_count = user_alert_objects.update(is_mute=is_mute)

            action_str = "muted" if is_mute else "unmuted"
            return self._gm.success_response(
                {
                    "message": f"{updated_count} User Alerts have been {action_str} successfully."
                }
            )
        except Exception as e:
            return self._gm.internal_server_error_response(
                f"Error occurred while updating User Alerts: {str(e)}"
            )

    @action(detail=False, methods=["get"])
    def list_monitors(self, request, *args, **kwargs):
        try:
            page_size = self.request.query_params.get("page_size", 30)
            queryset, total_records = self.get_queryset()
            queryset = queryset.prefetch_related("useralertmonitorlog_set")
            serializer = self.get_serializer(queryset, many=True)
            monitors = serializer.data
            response = {}
            column_config = [
                {"id": "name", "name": "Alert Name", "is_visible": True},
                {"id": "trends", "name": "Trends", "is_visible": False},
                {"id": "created_at", "name": "Created At", "is_visible": True},
                {"id": "updated_at", "name": "Updated At", "is_visible": True},
                {"id": "metric_type", "name": "Metric Type", "is_visible": True},
                {"id": "filters", "name": "Filters", "is_visible": False},
                {"id": "status", "name": "Status", "is_visible": True},
                {"id": "no_of_alerts", "name": "No. of Alerts", "is_visible": True},
                {
                    "id": "last_triggered",
                    "name": "Last Triggered",
                    "is_visible": True,
                },
            ]
            response["column_config"] = column_config
            table_data = []

            for monitor_obj, monitor_dict in zip(queryset, monitors, strict=False):
                trend_data = self._get_trend_data(monitor_obj)
                result = {
                    "id": monitor_dict["id"],
                    "name": monitor_dict["name"],
                    "created_at": monitor_dict["created_at"],
                    "updated_at": monitor_dict["updated_at"],
                    "metric_type": monitor_dict["metric_name"],
                    "filters": monitor_dict.get("filters"),
                    "status": (
                        "triggered" if monitor_obj.has_unresolved_logs else "healthy"
                    ),
                    "no_of_alerts": monitor_obj.no_of_alerts,
                    "last_triggered": monitor_obj.last_triggered,
                    "is_mute": monitor_obj.is_mute,
                    "trends": trend_data,
                }
                table_data.append(result)

            response["table"] = table_data

            response["metadata"] = {
                "total_rows": total_records,
                "total_pages": math.ceil(total_records / int(page_size)),
            }

            return self._gm.success_response(response)

        except Exception as e:
            traceback.print_exc()
            logger.info(f"Error occurred while fetching monitors list: {str(e)}")
            return self._gm.bad_request(f"error fetching the monitors list {str(e)}")

    @validated_request(
        request_serializer=UserAlertMonitorSerializer,
        strict_request_validation=False,
    )
    def create(self, request, *args, **kwargs):
        from tfc.ee_gating import EEResource, check_ee_can_create
        from tracer.models.monitor import UserAlertMonitor

        org = getattr(request, "organization", None) or request.user.organization
        current_count = UserAlertMonitor.objects.filter(
            organization=org, deleted=False
        ).count()
        check_ee_can_create(
            EEResource.MONITORS,
            org_id=str(org.id),
            current_count=current_count,
        )

        try:
            data = request.data.copy()
            data["organization"] = (
                getattr(request, "organization", None) or request.user.organization
            ).id
            if getattr(request, "workspace", None):
                data["workspace"] = request.workspace.id
            data["created_by"] = request.user.id
            data["logs"] = [
                {
                    "timestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                    "message": f"Monitor {data.get('name')} has been created",
                    "type": "INFO",
                }
            ]

            serializer = self.get_serializer(data=data)
            if serializer.is_valid():
                user_alert = serializer.save()

                return self._gm.success_response(
                    f"{user_alert.name} alert created successfully"
                )
            else:
                return self._gm.bad_request(serializer.errors)
        except Exception as e:
            return self._gm.internal_server_error_response(str(e))

    @validated_request(
        request_serializer=UserAlertMonitorSerializer,
        strict_request_validation=False,
    )
    def update(self, request, *args, **kwargs):
        return self._update_monitor(request, partial=False)

    @validated_request(
        request_serializer=UserAlertMonitorSerializer,
        partial_request_validation=True,
        strict_request_validation=False,
    )
    def partial_update(self, request, *args, **kwargs):
        return self._update_monitor(request, partial=True)

    @validated_request(
        request_serializer=UserAlertMonitorDuplicateSerializer,
        responses={
            200: UserAlertMonitorDuplicateResponseSerializer,
            400: ApiErrorResponseSerializer,
            404: ApiErrorResponseSerializer,
            500: ApiErrorResponseSerializer,
        },
        reject_unknown_fields=True,
    )
    @action(detail=False, methods=["post"], url_path="duplicate")
    def duplicate(self, request, *args, **kwargs):
        data = request.validated_data
        org = getattr(request, "organization", None) or request.user.organization
        try:
            monitor = self._base_monitor_queryset().get(
                id=data["id"],
            )
        except UserAlertMonitor.DoesNotExist:
            return self._gm.not_found(get_error_message("MONITOR_NOT_FOUND"))

        new_name = data["name"]
        if (
            self._base_monitor_queryset()
            .filter(
                project=monitor.project,
                name=new_name,
            )
            .exists()
        ):
            return self._gm.bad_request(
                {"name": f"An alert with the name '{new_name}' already exists."}
            )

        duplicated_monitor = UserAlertMonitor.objects.create(
            organization=org,
            workspace=monitor.workspace,
            project=monitor.project,
            created_by=request.user,
            name=new_name,
            metric_type=monitor.metric_type,
            metric=monitor.metric,
            threshold_operator=monitor.threshold_operator,
            threshold_type=monitor.threshold_type,
            threshold_metric_value=monitor.threshold_metric_value,
            critical_threshold_value=monitor.critical_threshold_value,
            warning_threshold_value=monitor.warning_threshold_value,
            alert_frequency=monitor.alert_frequency,
            auto_threshold_time_window=monitor.auto_threshold_time_window,
            notification_emails=monitor.notification_emails,
            slack_webhook_url=monitor.slack_webhook_url,
            slack_notes=monitor.slack_notes,
            is_mute=False,
            filters=monitor.filters,
            logs=[
                {
                    "timestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                    "message": f"Monitor {new_name} has been duplicated from {monitor.name}",
                    "type": "INFO",
                }
            ],
        )
        return self._gm.success_response(
            {
                "id": str(duplicated_monitor.id),
                "message": f"{duplicated_monitor.name} duplicated successfully",
            }
        )

    @swagger_auto_schema(
        responses={
            200: UserAlertMonitorMetricOptionsResponseSerializer,
            400: ApiErrorResponseSerializer,
            404: ApiErrorResponseSerializer,
            500: ApiErrorResponseSerializer,
        },
    )
    @action(detail=False, methods=["get"], url_path="metric-options")
    def metric_options(self, request, *args, **kwargs):
        try:
            org = getattr(request, "organization", None) or request.user.organization
            project_id = request.query_params.get("project_id")
            if not project_id:
                return self._gm.bad_request({"project_id": "This field is required."})
            project = self._visible_observe_projects().filter(id=project_id).first()
            if not project:
                return self._gm.not_found("Project not found")

            system_options = [
                {
                    "id": value,
                    "name": label,
                    "metric_type": value,
                    "output_type": "system_metric",
                }
                for value, label in MonitorMetricTypeChoices.choices
                if value != MonitorMetricTypeChoices.EVALUATION_METRICS.value
            ]

            eval_options = [
                {
                    "id": str(eval_config.id),
                    "name": eval_config.name or str(eval_config.id),
                    "metric_type": MonitorMetricTypeChoices.EVALUATION_METRICS.value,
                    "output_type": (eval_config.eval_template.config or {}).get(
                        "output", ""
                    ),
                }
                for eval_config in CustomEvalConfig.objects.select_related(
                    "eval_template"
                ).filter(
                    project=project,
                    project__organization=org,
                    deleted=False,
                    eval_template__deleted=False,
                )
            ]

            return self._gm.success_response(system_options + eval_options)
        except Exception as e:
            logger.error(f"Failed to get monitor metric options: {e}", exc_info=True)
            return self._gm.bad_request(get_error_message("FAILED_TO_GET_MONITOR"))

    @validated_request(
        request_serializer=UserAlertMonitorPreviewGraphSerializer,
        strict_request_validation=False,
    )
    @action(detail=False, methods=["post"], url_path="preview-graph")
    def preview_graph(self, request, *args, **kwargs):
        """
        Returns time-series data for a temporary monitor's metric, suitable for graphing a preview.
        Accepts monitor configuration in the request body.
        """
        try:
            data = request.data.copy()
            data["organization"] = (
                getattr(request, "organization", None) or request.user.organization
            ).id
            if getattr(request, "workspace", None):
                data["workspace"] = request.workspace.id

            # Remove the name , we don't need to validate it for preview
            if "name" in data:
                del data["name"]

            serializer = self.get_serializer(data=data)
            serializer.fields["name"].required = False

            if serializer.is_valid():
                validated_data = serializer.validated_data
            else:
                return self._gm.bad_request(serializer.errors)

            # Create a non-persistent monitor instance
            monitor = UserAlertMonitor(**validated_data)

            end_time_str = request.query_params.get("end_date")
            start_time_str = request.query_params.get("start_date")

            start_time = (
                datetime.fromisoformat(start_time_str) if start_time_str else None
            )
            end_time = datetime.fromisoformat(end_time_str) if end_time_str else None

            graph_data = get_graph_data(
                monitor=monitor,
                time_window_start=start_time,
                time_window_end=end_time,
            )

            return self._gm.success_response(graph_data)

        except Exception as e:
            logger.error(
                f"Failed to get monitor preview graph data: {e}", exc_info=True
            )
            return self._gm.bad_request(
                get_error_message("FAILED_TO_GET_MONITOR_PREVIEW", str(e))
            )

    @action(detail=True, methods=["get"], url_path="graph")
    def graph_data(self, request, *args, **kwargs):
        """
        Returns time-series data for a monitor's metric, suitable for graphing.

        Accepts `start_date` and `end_date` query parameters (ISO 8601 format).
        If not provided, it defaults to the last 7 days.
        """
        try:
            monitor = self.get_object()

            # Get the time window from query params, with sane defaults.
            end_time_str = request.query_params.get("end_date")
            start_time_str = request.query_params.get("start_date")

            start_time = (
                datetime.fromisoformat(start_time_str) if start_time_str else None
            )
            end_time = datetime.fromisoformat(end_time_str) if end_time_str else None

            # Call the graphing utility function to get the bucketed data.
            graph_data = get_graph_data(
                monitor=monitor,
                time_window_start=start_time,
                time_window_end=end_time,
            )

            return self._gm.success_response(graph_data)

        except UserAlertMonitor.DoesNotExist:
            return self._gm.not_found(get_error_message("MONITOR_NOT_FOUND"))
        except Exception as e:
            logger.error(f"Failed to get monitor graph data: {e}", exc_info=True)
            return self._gm.bad_request(get_error_message("FAILED_TO_GET_MONITOR"))


class UserAlertMonitorLogView(BaseModelViewSetMixin, ModelViewSet):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]
    serializer_class = UserAlertMonitorLogSerializer

    def _current_organization(self):
        return (
            getattr(self.request, "organization", None)
            or self.request.user.organization
        )

    def _workspace_scope_q(self):
        workspace = getattr(self.request, "workspace", None)
        if not workspace:
            return Q()
        if getattr(workspace, "is_default", False):
            return (
                Q(alert__workspace=workspace)
                | Q(
                    alert__workspace__is_default=True,
                    alert__workspace__organization=workspace.organization,
                )
                | Q(alert__workspace__isnull=True)
            )
        return Q(alert__workspace=workspace)

    def _alert_workspace_scope_q(self):
        workspace = getattr(self.request, "workspace", None)
        if not workspace:
            return Q()
        if getattr(workspace, "is_default", False):
            return (
                Q(workspace=workspace)
                | Q(
                    workspace__is_default=True,
                    workspace__organization=workspace.organization,
                )
                | Q(workspace__isnull=True)
            )
        return Q(workspace=workspace)

    def _visible_alert_queryset(self):
        return UserAlertMonitor.objects.filter(
            self._alert_workspace_scope_q(),
            organization=self._current_organization(),
            deleted=False,
        )

    def get_serializer_class(self):
        if self.action in {"create", "update", "partial_update"}:
            return UserAlertMonitorLogWriteSerializer
        return super().get_serializer_class()

    def get_serializer(self, *args, **kwargs):
        serializer = super().get_serializer(*args, **kwargs)
        serializer_obj = getattr(serializer, "child", serializer)
        fields = getattr(serializer_obj, "fields", None)
        if fields and "alert" in fields:
            fields["alert"].queryset = self._visible_alert_queryset()
        return serializer

    def get_queryset(self):
        queryset = (
            super()
            .get_queryset()
            .select_related("resolved_by", "alert")
            .filter(
                self._workspace_scope_q(),
                alert__organization=self._current_organization(),
            )
        )
        return queryset

    @validated_request(
        request_serializer=UserAlertMonitorLogWriteRequestSerializer,
        responses={
            201: UserAlertMonitorLogWriteResponseSerializer,
            400: ApiErrorResponseSerializer,
            404: ApiErrorResponseSerializer,
        },
    )
    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)

    @validated_request(
        request_serializer=UserAlertMonitorLogWriteRequestSerializer,
        responses={
            200: UserAlertMonitorLogWriteResponseSerializer,
            400: ApiErrorResponseSerializer,
            404: ApiErrorResponseSerializer,
        },
    )
    def update(self, request, *args, **kwargs):
        return super().update(request, *args, **kwargs)

    def _update_log(self, request, *args, partial=False, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(
            instance,
            data=request.data,
            partial=partial,
        )
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)

        if getattr(instance, "_prefetched_objects_cache", None):
            instance._prefetched_objects_cache = {}

        return Response(serializer.data)

    @validated_request(
        request_serializer=UserAlertMonitorLogWriteRequestSerializer,
        responses={
            200: UserAlertMonitorLogWriteResponseSerializer,
            400: ApiErrorResponseSerializer,
            404: ApiErrorResponseSerializer,
        },
        partial_request_validation=True,
    )
    def partial_update(self, request, *args, **kwargs):
        return self._update_log(request, *args, partial=True, **kwargs)

    @action(detail=False, methods=["get"], url_path="all")
    def list_all(self, request, *args, **kwargs):
        try:
            queryset = self.get_queryset()
            serializer = self.get_serializer(queryset, many=True)
            return self._gm.success_response(serializer.data)
        except Exception as e:
            return self._gm.bad_request(f"Error listing all logs: {str(e)}")

    @action(detail=True, methods=["get"], url_path="list")
    def list_for_alert(self, request, pk=None):
        try:
            queryset = self.get_queryset().filter(alert_id=pk)
            serializer = self.get_serializer(queryset, many=True)
            return self._gm.success_response(serializer.data)
        except Exception as e:
            return self._gm.bad_request(f"Error listing logs for alert: {str(e)}")

    @validated_request(
        request_serializer=UserAlertMonitorLogResolveRequestSerializer,
        responses={
            200: UserAlertMonitorLogResolveResponseSerializer,
            400: ApiErrorResponseSerializer,
        },
    )
    @action(detail=False, methods=["post"], url_path="resolve")
    def mark_as_resolved(self, request, *args, **kwargs):
        try:
            validated_data = getattr(request, "validated_data", {})
            log_ids = validated_data.get("log_ids", [])
            select_all = validated_data.get("select_all", False)
            exclude_ids = validated_data.get("exclude_ids", [])

            if select_all and log_ids:
                return self._gm.bad_request(
                    "Cannot provide both 'select_all' and 'log_ids'."
                )

            if not select_all and not log_ids:
                return self._gm.bad_request(
                    "A list of log IDs or select_all flag is required for resolution"
                )

            log_entries = self.get_queryset()

            if select_all:
                if exclude_ids:
                    log_entries = log_entries.exclude(id__in=exclude_ids)
            else:
                log_entries = log_entries.filter(id__in=log_ids)

            if not log_entries.exists():
                return self._gm.bad_request(
                    "No log entries found for the provided criteria"
                )

            updated_count = log_entries.update(
                resolved=True, resolved_at=datetime.now(), resolved_by=request.user
            )

            return self._gm.success_response(
                f"{updated_count} log entries marked as resolved successfully"
            )
        except Exception as e:
            return self._gm.bad_request(f"Error resolving logs: {str(e)}")
