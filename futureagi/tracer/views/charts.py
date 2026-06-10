import time

import structlog
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework import status
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

logger = structlog.get_logger(__name__)
from accounts.utils import get_request_organization
from tfc.utils.api_contracts import hide_swagger_schema_for_actions
from tfc.utils.general_methods import GeneralMethods
from tracer.models.observation_span import ObservationSpan
from tracer.models.project import Project
from tracer.serializers.monitor import (
    FetchGraphSerializer,
)
from tracer.utils.graphs_optimized import (
    get_all_system_metrics,
    get_eval_graph_data,
    get_system_metric_data,
)


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
        start_time = time.time()

        try:
            serializer = self.serializer_class(data=request.query_params)
            if not serializer.is_valid():
                return self._gm.bad_request(serializer.errors)

            validated_data = serializer.validated_data
            req_data_config = validated_data.get("req_data_config")
            interval = validated_data.get("interval")
            filters = validated_data.get("filters")
            property = validated_data.get("property")
            project_id = validated_data.get("project_id")

            if not project_id:
                return self._gm.bad_request("Project id is required")

            if not req_data_config:
                return self._gm.bad_request("Req data config property is required")

            data_type = req_data_config.get("type")
            if data_type not in ["EVAL", "SYSTEM_METRIC", "SYSTEM_METRICS"]:
                return self._gm.bad_request(
                    f"Filter property type '{data_type}' is not supported. "
                    f"Supported: EVAL, SYSTEM_METRIC (single), SYSTEM_METRICS (all three)"
                )

            try:
                project = Project.objects.get(
                    id=project_id,
                    organization=get_request_organization(request),
                    workspace=request.workspace,
                    deleted=False,
                )
            except Project.DoesNotExist:
                return self._gm.bad_request("Project does not exist")

            project_id = str(project.id)

            if data_type == "EVAL":
                metric_data = get_eval_graph_data(
                    interval=interval,
                    filters=filters,
                    property=property,
                    req_data_config=req_data_config,
                    eval_logger_filters={"project_id": project_id},
                    observe_type="charts",
                )

            elif data_type == "SYSTEM_METRICS":
                metric_data = get_all_system_metrics(
                    interval=interval,
                    filters=filters,
                    property=property,
                    system_metric_filters={"project_id": project_id},
                )

            elif data_type == "SYSTEM_METRIC":
                metric_data = get_system_metric_data(
                    interval=interval,
                    filters=filters,
                    property=property,
                    req_data_config=req_data_config,
                    system_metric_filters={"project_id": project_id},
                    observe_type="charts",
                )

            else:
                return self._gm.bad_request("Invalid data type")

            if not metric_data:
                return self._gm.bad_request("Metric data is not valid")

            elapsed_time = time.time() - start_time
            logger.info(
                f"fetch_graph_v2 completed in {elapsed_time:.3f}s for "
                f"type={data_type}, interval={interval}, project={project_id}"
            )

            return self._gm.success_response(metric_data)

        except Exception as e:
            elapsed_time = time.time() - start_time
            logger.error(
                f"fetch_graph_v2 failed after {elapsed_time:.3f}s: {str(e)}",
                exc_info=True,
            )
            return self._gm.bad_request(str(e))
