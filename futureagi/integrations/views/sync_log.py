import math

import structlog
from django.utils.decorators import method_decorator
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ReadOnlyModelViewSet

from integrations.models import SyncLog
from integrations.serializers.contracts import (
    INTEGRATION_ERROR_RESPONSES,
    SyncLogListQuerySerializer,
    SyncLogListResponseSerializer,
)
from integrations.serializers.sync_log import SyncLogSerializer
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_errors import build_error_envelope
from tfc.utils.base_viewset import BaseModelViewSetMixin

logger = structlog.get_logger(__name__)

integration_errors = swagger_auto_schema(responses=INTEGRATION_ERROR_RESPONSES)


def _validation_error_response(errors):
    return Response(
        build_error_envelope(errors, status_code=status.HTTP_400_BAD_REQUEST),
        status=status.HTTP_400_BAD_REQUEST,
    )


@method_decorator(name="retrieve", decorator=integration_errors)
class SyncLogViewSet(BaseModelViewSetMixin, ReadOnlyModelViewSet):
    """Read-only viewset for sync logs."""

    serializer_class = SyncLogSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        # Filter to only show sync logs for connections belonging to the user's org
        queryset = SyncLog.objects.filter(
            connection__organization=getattr(self.request, "organization", None)
            or self.request.user.organization,
            connection__deleted=False,
        ).order_by("-started_at")
        workspace = getattr(self.request, "workspace", None)
        if workspace:
            queryset = queryset.filter(connection__workspace=workspace)

        query_data = getattr(self.request, "validated_query_data", {})
        connection_id = query_data.get(
            "connection_id"
        ) or self.request.query_params.get("connection_id")
        if connection_id:
            queryset = queryset.filter(connection_id=connection_id)
        return queryset

    @validated_request(
        query_serializer=SyncLogListQuerySerializer,
        responses={
            200: SyncLogListResponseSerializer,
            **INTEGRATION_ERROR_RESPONSES,
        },
        validation_error_response=_validation_error_response,
        reject_unknown_fields=True,
    )
    def list(self, request, *args, **kwargs):
        try:
            queryset = self.get_queryset()
            total_count = queryset.count()

            page_number = request.validated_query_data.get("page_number", 0)
            page_size = request.validated_query_data.get("page_size", 20)

            start = page_number * page_size
            end = start + page_size

            total_pages = math.ceil(total_count / page_size) if page_size > 0 else 0
            next_page_number = (
                page_number + 1 if (page_number + 1) < total_pages else None
            )

            paginated_queryset = queryset[start:end]
            serializer = self.get_serializer(paginated_queryset, many=True)

            return Response(
                {
                    "status": True,
                    "result": {
                        "metadata": {
                            "total_count": total_count,
                            "current_page": page_number,
                            "page_size": page_size,
                            "total_pages": total_pages,
                            "next_page": next_page_number,
                        },
                        "sync_logs": serializer.data,
                    },
                }
            )
        except Exception as e:
            logger.exception("Error listing sync logs", error=str(e))
            return Response(
                build_error_envelope(
                    "Failed to list sync logs.",
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                ),
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
