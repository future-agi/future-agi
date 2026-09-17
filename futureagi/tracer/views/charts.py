import time

import structlog
from django.db import DatabaseError
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from accounts.utils import get_request_organization
from tfc.utils.api_contracts import hide_swagger_schema_for_actions, validated_request
from tfc.utils.api_serializers import ApiErrorResponseSerializer
from tfc.utils.general_methods import GeneralMethods
from tracer.models.project import Project
from tracer.serializers.monitor import (
    FetchGraphResponseSerializer,
    FetchGraphSerializer,
)
from tracer.services.clickhouse.graph_action_deadline import (
    GraphActionUnavailable,
    bounded_graph_action_request,
    finish_graph_action_response,
    graph_action_postgres_budget,
    start_graph_action_deadline,
)
from tracer.services.clickhouse.graph_dispatch import graph_payload_is_publishable
from tracer.services.filter_principal_context import (
    FilterPrincipalContextError,
    bind_request_my_annotations_principal,
)
from tracer.utils.graphs_optimized import (
    EvalGraphConfigurationError,
    EvalGraphReadError,
    SystemMetricGraphReadError,
    get_all_system_metrics,
    get_eval_graph_data,
    get_system_metric_data,
)

logger = structlog.get_logger(__name__)


@hide_swagger_schema_for_actions(
    "list",
    "create",
    "retrieve",
    "update",
    "partial_update",
    "destroy",
)
class ChartsView(GenericViewSet):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]
    serializer_class = FetchGraphSerializer
    pagination_class = None

    def _unsupported_crud_response(self):
        return Response(
            {
                "status": False,
                "detail": "Charts CRUD is not supported. Use /tracer/charts/fetch_graph/.",
            },
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    def list(self, request, *args, **kwargs):
        return self._unsupported_crud_response()

    def create(self, request, *args, **kwargs):
        return self._unsupported_crud_response()

    def retrieve(self, request, *args, **kwargs):
        return self._unsupported_crud_response()

    def update(self, request, *args, **kwargs):
        return self._unsupported_crud_response()

    def partial_update(self, request, *args, **kwargs):
        return self._unsupported_crud_response()

    def destroy(self, request, *args, **kwargs):
        return self._unsupported_crud_response()

    @bounded_graph_action_request(resource="charts_fetch_graph")
    @validated_request(
        query_serializer=FetchGraphSerializer,
        responses={
            200: FetchGraphResponseSerializer,
            400: ApiErrorResponseSerializer,
            500: ApiErrorResponseSerializer,
            503: ApiErrorResponseSerializer,
        },
    )
    @action(detail=False, methods=["get"])
    def fetch_graph(self, request, *args, **kwargs):
        """
        Optimized version of fetch_graph using database-level aggregation.

        Handles 1M+ datapoints efficiently with:
        - Database-level time bucketing and aggregation
        - Subquery-based filtering (no large IN clauses)
        - Query result caching
        - Minimal memory footprint

        Performance targets:
        - 20-30k datapoints: <100ms
        - 100k datapoints: <500ms
        - 1M datapoints: 2-3s

        Query parameters same as fetch_graph.
        """
        deadline = kwargs.pop("_graph_action_deadline", None)
        deadline_injected = deadline is not None
        if deadline is None:
            deadline = start_graph_action_deadline()
        start_time = time.time()

        def finish(response):
            if deadline_injected:
                return response
            return finish_graph_action_response(deadline, response)

        try:
            validated_data = getattr(request, "validated_query_data", None)
            if validated_data is None:
                # Preserve the established direct/unwrapped unit boundary while
                # the real routed call is validated by ``validated_request``.
                serializer = self.serializer_class(data=request.query_params)
                if not serializer.is_valid():
                    return finish(self._gm.bad_request(serializer.errors))
                validated_data = serializer.validated_data
            req_data_config = validated_data.get("req_data_config")
            interval = validated_data.get("interval")
            filters = bind_request_my_annotations_principal(
                request,
                validated_data.get("filters"),
            )
            property = validated_data.get("property")
            project_id = validated_data.get("project_id")
            refresh = validated_data.get("refresh", False)

            if not project_id:
                return finish(self._gm.bad_request("Project id is required"))

            if not req_data_config:
                return finish(
                    self._gm.bad_request("Req data config property is required")
                )

            data_type = req_data_config.get("type")
            if data_type not in ["EVAL", "SYSTEM_METRIC", "SYSTEM_METRICS"]:
                return finish(
                    self._gm.bad_request(
                        f"Filter property type '{data_type}' is not supported. "
                        "Supported: EVAL, SYSTEM_METRIC (single), "
                        "SYSTEM_METRICS (all three)"
                    )
                )

            try:
                with graph_action_postgres_budget(deadline):
                    project = Project.objects.get(
                        id=project_id,
                        organization=get_request_organization(request),
                        workspace=request.workspace,
                        deleted=False,
                    )
            except Project.DoesNotExist:
                return finish(self._gm.bad_request("Project does not exist"))

            project_id = str(project.id)
            organization_id = str(project.organization_id)
            workspace_id = str(request.workspace.id)

            if data_type == "EVAL":
                with graph_action_postgres_budget(deadline):
                    metric_data = get_eval_graph_data(
                        interval=interval,
                        filters=filters,
                        property=property,
                        req_data_config=req_data_config,
                        eval_logger_filters={"project_id": project_id},
                        observe_type="charts",
                        refresh=refresh,
                        organization_id=organization_id,
                        workspace_id=workspace_id,
                    )

            elif data_type == "SYSTEM_METRICS":
                metric_data = get_all_system_metrics(
                    interval=interval,
                    filters=filters,
                    property=property,
                    system_metric_filters={"project_id": project_id},
                    refresh=refresh,
                    organization_id=organization_id,
                    workspace_id=workspace_id,
                )

            elif data_type == "SYSTEM_METRIC":
                metric_data = get_system_metric_data(
                    interval=interval,
                    filters=filters,
                    property=property,
                    req_data_config=req_data_config,
                    system_metric_filters={"project_id": project_id},
                    observe_type="charts",
                    refresh=refresh,
                    organization_id=organization_id,
                    workspace_id=workspace_id,
                )

            else:
                return finish(self._gm.bad_request("Invalid data type"))

            if not metric_data:
                return finish(self._gm.bad_request("Metric data is not valid"))
            if not graph_payload_is_publishable(
                metric_data,
                allow_sampled=False,
            ):
                return finish(
                    self._gm.custom_error_response(
                        status.HTTP_503_SERVICE_UNAVAILABLE,
                        "Graph data is temporarily unavailable. Please retry.",
                        code="service_unavailable",
                    )
                )

            elapsed_time = time.time() - start_time
            logger.info(
                f"fetch_graph_v2 completed in {elapsed_time:.3f}s for "
                f"type={data_type}, interval={interval}, project={project_id}"
            )

            return finish(self._gm.success_response(metric_data))

        except EvalGraphConfigurationError as exc:
            return finish(self._gm.bad_request(str(exc)))
        except FilterPrincipalContextError as exc:
            return finish(self._gm.bad_request(str(exc)))
        except (GraphActionUnavailable, DatabaseError):
            elapsed_time = time.time() - start_time
            logger.warning(
                "fetch_graph_request_deadline_exceeded",
                elapsed_seconds=round(elapsed_time, 3),
            )
            return self._gm.custom_error_response(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Graph data is temporarily unavailable. Please retry.",
                code="service_unavailable",
            )
        except (EvalGraphReadError, SystemMetricGraphReadError):
            elapsed_time = time.time() - start_time
            logger.exception(
                "fetch_graph_clickhouse_read_failed",
                elapsed_seconds=round(elapsed_time, 3),
            )
            return self._gm.custom_error_response(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Graph data is temporarily unavailable. Please retry.",
                code="service_unavailable",
            )
        except Exception as exc:
            elapsed_time = time.time() - start_time
            logger.exception(
                "fetch_graph_v2_failed",
                elapsed_seconds=round(elapsed_time, 3),
                error_type=type(exc).__name__,
            )
            return self._gm.custom_error_response(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "Unable to fetch graph data. Please retry.",
                code="internal_error",
            )
