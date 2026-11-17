"""API for external metrics: dataset metrics whose scores the caller computes."""

from uuid import UUID

from django.core.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from model_hub.serializers.contracts import MODEL_HUB_ERROR_RESPONSES
from model_hub.serializers.external_metrics import (
    ExternalMetricCreateRequestSerializer,
    ExternalMetricCreateResponseSerializer,
    ExternalMetricCreateResultSerializer,
    ExternalMetricListQuerySerializer,
    ExternalMetricListResponseSerializer,
    ExternalMetricListResultSerializer,
)
from model_hub.services.external_metrics import (
    ExternalMetricConflict,
    ExternalMetricDatasetNotFound,
    ExternalMetricDefinition,
    ExternalMetricInvalid,
    create_or_reuse_external_metric,
    listable_external_metrics,
    metric_view,
    resolved_version,
)
from model_hub.utils.json_parser import BoundedJSONParser
from model_hub.utils.workspace_scope import scoped_dataset_queryset
from tfc.routers import primary_required
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_errors import build_error_envelope
from tfc.utils.general_methods import GeneralMethods

_gm = GeneralMethods()


class ExternalMetricsJSONParser(BoundedJSONParser):
    max_body_bytes = 16384


class ExternalMetricsView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [ExternalMetricsJSONParser]

    @validated_request(
        request_serializer=ExternalMetricCreateRequestSerializer,
        responses={
            200: ExternalMetricCreateResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        },
        reject_unknown_fields=True,
        operation_id="dataset_external_metrics_create",
        operation_description="Create, or reuse, a dataset metric whose scores the caller computes (an external metric) and return its metric_id and the evaluator_version_id to pin when creating an external run; score_refs on that run's results then name this pair. The same dataset, name, source_hash, output_type, higher_is_better and pass_threshold always return the same metric (created false); any change returns a new metric, so a run's pinned binding never moves. Only the sha256 of the evaluator source is accepted and stored, never the source. The server never executes an external metric: the metric model refuses any executing status for it and the evaluation engine refuses its template. A non-external metric with the same name on the dataset is a 409; an invalid definition is a 400; a dataset outside the caller's organization or workspace, or deleted, is a 404. Requires workspace write access; body at most 16384 bytes.",
    )
    @primary_required
    def post(self, request: Request, dataset_id: UUID) -> Response:
        if getattr(request, "organization", None) is None:
            return _gm.not_found("Dataset not found")
        workspace = getattr(request, "workspace", None)
        if workspace is None or not request.user.can_write_to_workspace(workspace):
            return Response(
                build_error_envelope(
                    "Workspace write access required", status_code=403
                ),
                status=403,
            )
        dataset = scoped_dataset_queryset(request).filter(id=dataset_id).first()
        if dataset is None:
            return _gm.not_found("Dataset not found")
        data = request.validated_data
        try:
            binding = create_or_reuse_external_metric(
                dataset=dataset,
                definition=ExternalMetricDefinition(
                    name=data["name"],
                    output_type=data["output_type"],
                    source_hash=data["source_hash"],
                    higher_is_better=data["higher_is_better"],
                    pass_threshold=data["pass_threshold"],
                ),
                user=request.user,
            )
        except ExternalMetricInvalid as exc:
            return _gm.bad_request(str(exc))
        except ExternalMetricDatasetNotFound:
            return _gm.not_found("Dataset not found")
        except ExternalMetricConflict as exc:
            return Response(build_error_envelope(str(exc), status_code=409), status=409)
        except ValidationError as exc:
            return _gm.bad_request("; ".join(exc.messages))
        return _gm.success_response(
            ExternalMetricCreateResultSerializer(
                {
                    **metric_view(binding.metric, binding.version),
                    "created": binding.created,
                }
            ).data
        )

    @validated_request(
        query_serializer=ExternalMetricListQuerySerializer,
        responses={
            200: ExternalMetricListResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        },
        reject_unknown_fields=True,
        operation_id="dataset_external_metrics_list",
        operation_description="List the dataset's external metrics, oldest first, with the evaluator version each one resolves to now. name filters by exact name. limit is 1-500 (default 100). Performs no writes.",
    )
    @primary_required
    def get(self, request: Request, dataset_id: UUID) -> Response:
        if getattr(request, "organization", None) is None:
            return _gm.not_found("Dataset not found")
        dataset = scoped_dataset_queryset(request).filter(id=dataset_id).first()
        if dataset is None:
            return _gm.not_found("Dataset not found")
        query = request.validated_query_data
        metrics = listable_external_metrics(dataset)
        if query.get("name"):
            metrics = metrics.filter(name=query["name"])
        limit, offset = query["limit"], query["offset"]
        page = list(metrics[offset : offset + limit + 1])
        rows = []
        for metric in page[:limit]:
            version = resolved_version(metric)
            if version is not None:
                rows.append(metric_view(metric, version))
        return _gm.success_response(
            ExternalMetricListResultSerializer(
                {
                    "dataset_id": dataset.id,
                    "metrics": rows,
                    "limit": limit,
                    "offset": offset,
                    "has_more": len(page) > limit,
                }
            ).data
        )
