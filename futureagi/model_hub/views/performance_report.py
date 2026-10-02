from django.db.models import Q
from drf_yasg.utils import swagger_auto_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

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
from model_hub.utils.workspace_scope import (
    request_workspace,
    request_workspace_filter,
    scoped_ai_model_queryset,
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
        model = scoped_ai_model_queryset(request).filter(id=model_id).first()
        if model is None:
            return self._gm.not_found(get_error_message("AI_MODEL_NOT_FOUND"))

        queryset = self.model.no_workspace_objects.filter(
            request_workspace_filter(request),
            model=model,
            organization=model.organization,
        ).order_by("-created_at")

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
        model = scoped_ai_model_queryset(request).filter(id=model_id).first()
        if model is None:
            return self._gm.not_found(get_error_message("AI_MODEL_NOT_FOUND"))

        serializer = request.validated_serializer
        serializer.save(
            model=model,
            organization=model.organization,
            workspace=request_workspace(request),
        )
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
        model = scoped_ai_model_queryset(request).filter(id=model_id).first()
        if model is None:
            return self._gm.not_found(get_error_message("AI_MODEL_NOT_FOUND"))

        instance = self.model.no_workspace_objects.filter(
            request_workspace_filter(request),
            id=report_id,
            model=model,
            organization=model.organization,
        ).first()
        if instance is None:
            return self._gm.not_found(get_error_message("PERFORMANCE_REPORT_NOT_FOUND"))

        instance.delete()
        return self._gm.success_response("Performance report deleted successfully")
