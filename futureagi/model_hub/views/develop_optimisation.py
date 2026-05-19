import structlog
from django.shortcuts import get_object_or_404
from django_filters.rest_framework import DjangoFilterBackend
from drf_yasg.utils import swagger_auto_schema

# views.py
from rest_framework import filters, generics
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.utils import get_request_organization
from model_hub.models.develop_optimisation import OptimizationDataset
from model_hub.models.evals_metric import UserEvalMetric
from model_hub.serializers.contracts import (
    MODEL_HUB_ERROR_RESPONSES,
    MetricsByColumnResponseSerializer,
    ModelHubStringResultResponseSerializer,
)
from model_hub.serializers.develop_optimisation import (
    OptimizationDatasetGetSerializer,
    OptimizationDatasetSerializer,
)
from model_hub.utils.eval_list import build_user_eval_list_items
from model_hub.views.develop_optimiser import DevelopOptimizer
from tfc.utils.api_contracts import validated_request
from tfc.utils.error_codes import get_error_message
from tfc.utils.general_methods import GeneralMethods
from tfc.utils.pagination import ExtendedPageNumberPagination

logger = structlog.get_logger(__name__)


class OptimisationCreateView(APIView):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    @validated_request(
        request_serializer=OptimizationDatasetSerializer,
        responses={
            200: ModelHubStringResultResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        },
        reject_unknown_fields=True,
    )
    def post(self, request):
        try:
            validated_data = request.validated_data
            # Extract nested data. `validated_request` owns request-shape errors;
            # the view should only handle domain validation from here on.
            dataset = validated_data.get("dataset")
            column = validated_data.get("column") or None
            messages = validated_data.get("messages") or []
            user_eval_template_ids = (
                validated_data.get("user_eval_template_ids") or []
            )
            model_config = validated_data.get("model_config")

            if OptimizationDataset.objects.filter(
                name=validated_data["name"], dataset=dataset, deleted=False
            ).exists():
                return self._gm.bad_request(
                    get_error_message("OPTIMIZATION_NAME_EXISTS")
                )

            optimiser = OptimizationDataset.objects.create(
                name=validated_data["name"],
                optimize_type=validated_data["optimize_type"],
                dataset=dataset,
                prompt_name=validated_data.get("prompt_name"),
                model_config=model_config,
                messages=messages,
                column=column,
                user_eval_template_mapping=validated_data.get(
                    "user_eval_template_mapping"
                ),
            )
            optimiser.user_eval_template_ids.set(user_eval_template_ids)
            optimizer = DevelopOptimizer(optim_obj_id=optimiser.id, avoid_cost=True)
            optimizer.create_column()

            return self._gm.success_response("success.")
        except Exception as e:
            logger.exception(f"Error in creating optimize dataset: {str(e)}")
            return self._gm.bad_request(
                get_error_message("FAILED_TO_CREATE_OPTIMIZE_DATASET")
            )

    @validated_request(
        request_serializer=OptimizationDatasetSerializer,
        responses={
            200: ModelHubStringResultResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        },
        partial_request_validation=True,
        reject_unknown_fields=True,
    )
    def put(self, request, pk):
        try:
            optimization_dataset = get_object_or_404(OptimizationDataset, pk=pk)
            validated_data = request.validated_data
            dataset = validated_data.get("dataset", optimization_dataset.dataset)
            column = (
                validated_data["column"]
                if "column" in validated_data
                else optimization_dataset.column
            )
            messages = validated_data.get(
                "messages", optimization_dataset.messages or []
            )
            model_config = validated_data.get(
                "model_config", optimization_dataset.model_config
            )
            name = validated_data.get("name", optimization_dataset.name)
            optimize_type = validated_data.get(
                "optimize_type", optimization_dataset.optimize_type
            )
            prompt_name = validated_data.get(
                "prompt_name", optimization_dataset.prompt_name
            )
            user_eval_template_mapping = validated_data.get(
                "user_eval_template_mapping",
                optimization_dataset.user_eval_template_mapping,
            )

            if (
                OptimizationDataset.objects.filter(
                    name=name, dataset=dataset, deleted=False
                )
                .exclude(id=pk)
                .exists()
            ):
                return self._gm.bad_request(
                    get_error_message("OPTIMIZATION_NAME_EXISTS")
                )

            OptimizationDataset.objects.filter(id=pk).update(
                name=name,
                optimize_type=optimize_type,
                dataset=dataset,
                prompt_name=prompt_name,
                model_config=model_config,
                messages=messages,
                column=column,
                user_eval_template_mapping=user_eval_template_mapping,
            )
            if "user_eval_template_ids" in validated_data:
                optimization_dataset.user_eval_template_ids.set(
                    validated_data["user_eval_template_ids"]
                )

            return self._gm.success_response("success.")
        except Exception as e:
            logger.exception(f"Error in updating optimize dataset: {str(e)}")
            return self._gm.bad_request(
                get_error_message("FAILED_TO_UPDATE_OPTIMIZE_DATASET")
            )


class OptimizationDatasetListView(generics.ListAPIView):
    queryset = OptimizationDataset.objects.all()
    serializer_class = OptimizationDatasetSerializer
    pagination_class = ExtendedPageNumberPagination
    filter_backends = [
        DjangoFilterBackend,
        filters.SearchFilter,
        filters.OrderingFilter,
    ]
    filterset_fields = ["optimize_type", "status"]
    search_fields = ["name", "dataset__name"]
    ordering_fields = ["created_at", "name"]
    ordering = ["-created_at"]

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        queryset = queryset.filter(
            dataset__organization=getattr(request, "organization", None)
            or request.user.organization
        )

        dataset_id = request.query_params.get("dataset_id")
        if dataset_id:
            queryset = queryset.filter(dataset_id=dataset_id)

        # Get total queries if needed (you can modify this based on your requirements)

        page = self.paginate_queryset(queryset)

        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(queryset, many=True)
        return self._gm.success_response(serializer.data)

    def get_paginated_response(self, data):
        assert self.paginator is not None
        return self.paginator.get_paginated_response(data)


class OptimizationDatasetDetailView(generics.RetrieveAPIView):
    queryset = OptimizationDataset.objects.all()
    serializer_class = OptimizationDatasetGetSerializer

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()

        serializer = self.get_serializer(instance)
        return Response(serializer.data)


@swagger_auto_schema(
    method="get",
    responses={200: MetricsByColumnResponseSerializer, **MODEL_HUB_ERROR_RESPONSES},
)
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_metrics_by_column(request):
    _gm = GeneralMethods()
    """
    Get all UserEvalMetrics that use a specific column in their config mapping.
    """
    column_id = request.query_params.get("column_id")

    if not column_id:
        return _gm.bad_request(get_error_message("MISSING_COLUMN_ID"))

    try:
        # Get organization from the authenticated user
        _org = get_request_organization(request)
        organization_id = _org.id if _org else None

        metrics = UserEvalMetric.get_metrics_using_column(
            organization_id=organization_id, column_id=column_id
        )

        return _gm.success_response(build_user_eval_list_items(metrics))

    except Exception as e:
        logger.exception(f"Error in fetching metrics by columns: {str(e)}")
        return _gm.bad_request(get_error_message("FAILED_TO_GET_METRICS_BY_COLUMN"))
