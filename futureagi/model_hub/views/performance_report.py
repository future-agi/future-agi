from django.db.models import Q
from drf_yasg.utils import swagger_auto_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from model_hub.models.ai_model import AIModel
from model_hub.models.performance_report import PerformanceReport
from model_hub.serializers.contracts import (
    MODEL_HUB_ERROR_RESPONSES,
    ModelHubStringResultResponseSerializer,
    PerformanceReportCreateResponseSerializer,
    PerformanceReportPaginatedResponseSerializer,
)
from model_hub.serializers.performance_report import (
    PerformanceReportCreateSerializer,
    PerformanceReportSerializer,
)
from tfc.utils.api_contracts import validated_request
from tfc.utils.error_codes import get_error_message
from tfc.utils.general_methods import GeneralMethods
from tfc.utils.pagination import ExtendedPageNumberPagination


class PerformanceReportApiView(APIView):
    permission_classes = [IsAuthenticated]
    model = PerformanceReport
    serializer_class = PerformanceReportSerializer
    create_serializer_class = PerformanceReportCreateSerializer
    _gm = GeneralMethods()

    @swagger_auto_schema(
        responses={
            200: PerformanceReportPaginatedResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        }
    )
    def get(self, request, model_id):
        queryset = self.model.objects.filter(model_id=model_id).order_by("-created_at")

        search_query = request.query_params.get("search_query", "")
        if search_query:
            queryset = queryset.filter(Q(name__icontains=search_query))

        paginator = ExtendedPageNumberPagination()
        result_page = paginator.paginate_queryset(queryset, request)
        result_page = self.serializer_class(result_page, many=True).data

        return paginator.get_paginated_response(result_page)

    @validated_request(
        request_serializer=PerformanceReportCreateSerializer,
        responses={
            201: PerformanceReportCreateResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        },
        reject_unknown_fields=True,
    )
    def post(self, request, model_id):
        try:
            model = AIModel.objects.get(id=model_id)
        except AIModel.DoesNotExist:
            return self._gm.not_found(get_error_message("AI_MODEL_NOT_FOUND"))

        serializer = request.validated_serializer
        serializer.save(model=model, organization=model.organization)
        return self._gm.create_response(self.serializer_class(serializer.instance).data)


class PerformanceReportDetailApiView(APIView):
    permission_classes = [IsAuthenticated]
    model = PerformanceReport
    _gm = GeneralMethods()

    @swagger_auto_schema(
        responses={
            200: ModelHubStringResultResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        }
    )
    def delete(self, request, model_id, report_id):
        try:
            instance = self.model.objects.get(id=report_id, model_id=model_id)
        except self.model.DoesNotExist as e:
            raise self._gm.not_found(
                get_error_message("PERFORMANCE_REPORT_NOT_FOUND")
            ) from e

        instance.delete()
        return self._gm.success_response("Performance report deleted successfully")
