"""
Feed detail endpoint + status updates.

- GET   /tracer/feed/issues/{cluster_id}/ → FeedDetailView.get
- PATCH /tracer/feed/issues/{cluster_id}/ → FeedDetailView.patch
"""

import structlog
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from tfc.utils.api_contracts import validated_request
from tfc.utils.api_serializers import ApiErrorResponseSerializer
from tfc.utils.general_methods import GeneralMethods
from tracer.serializers.feed import (
    FeedDetailApiResponseSerializer,
    FeedDetailCoreSerializer,
    FeedDetailQuerySerializer,
    FeedUpdateBodySerializer,
)
from tracer.types.feed_types import FeedUpdatePayload
from tracer.utils import feed as feed_service
from tracer.views.feed._permissions import resolve_requested_project_ids

logger = structlog.get_logger(__name__)

ERROR_RESPONSES = {
    400: ApiErrorResponseSerializer,
    403: ApiErrorResponseSerializer,
    404: ApiErrorResponseSerializer,
    500: ApiErrorResponseSerializer,
}


class FeedDetailView(APIView):
    """GET + PATCH /tracer/feed/issues/{cluster_id}/"""

    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @validated_request(
        query_serializer=FeedDetailQuerySerializer,
        responses={200: FeedDetailApiResponseSerializer, **ERROR_RESPONSES},
    )
    def get(self, request, cluster_id: str):
        requested_project_id = request.validated_query_data.get("project_id")
        project_ids = resolve_requested_project_ids(
            request,
            str(requested_project_id) if requested_project_id else None,
        )
        if project_ids is None:
            return self._gm.forbidden_response("Access denied to this project")
        if not project_ids:
            return self._gm.forbidden_response(
                "User not associated with an organization"
            )

        try:
            detail = feed_service.get_feed_detail(cluster_id, project_ids)
        except Exception:
            logger.exception("feed_detail_failed", cluster_id=cluster_id)
            return self._gm.bad_request("Failed to fetch feed detail")

        if detail is None:
            return self._gm.not_found(f"Cluster {cluster_id} not found")

        return self._gm.success_response(FeedDetailCoreSerializer(detail).data)

    @validated_request(
        request_serializer=FeedUpdateBodySerializer,
        responses={200: FeedDetailApiResponseSerializer, **ERROR_RESPONSES},
    )
    def patch(self, request, cluster_id: str):
        requested_project_id = request.validated_data.get("project_id")
        project_ids = resolve_requested_project_ids(
            request,
            str(requested_project_id) if requested_project_id else None,
        )
        if project_ids is None:
            return self._gm.forbidden_response("Access denied to this project")
        if not project_ids:
            return self._gm.forbidden_response(
                "User not associated with an organization"
            )

        payload = FeedUpdatePayload(
            status=request.validated_data.get("status"),
            severity=request.validated_data.get("severity"),
            assignee=request.validated_data.get("assignee"),
            assignee_provided="assignee" in request.validated_data,
        )

        try:
            detail = feed_service.update_feed_issue(cluster_id, project_ids, payload)
        except Exception:
            logger.exception("feed_update_failed", cluster_id=cluster_id)
            return self._gm.bad_request("Failed to update feed issue")

        if detail is None:
            return self._gm.not_found(f"Cluster {cluster_id} not found")

        return self._gm.success_response(FeedDetailCoreSerializer(detail).data)
