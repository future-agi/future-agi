"""
Feed list + stats endpoints.

- GET /tracer/feed/issues/       → FeedListView
- GET /tracer/feed/issues/stats/ → FeedStatsView
"""

import structlog
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from tfc.utils.api_contracts import validated_request
from tfc.utils.api_serializers import ApiErrorResponseSerializer
from tfc.utils.general_methods import GeneralMethods
from tracer.serializers.feed import (
    FeedListApiResponseSerializer,
    FeedListQuerySerializer,
    FeedListResponseSerializer,
    FeedStatsApiResponseSerializer,
    FeedStatsQuerySerializer,
    FeedStatsSerializer,
)
from tracer.utils import feed as feed_service
from tracer.views.feed._permissions import resolve_requested_project_ids

logger = structlog.get_logger(__name__)

ERROR_RESPONSES = {
    400: ApiErrorResponseSerializer,
    403: ApiErrorResponseSerializer,
    404: ApiErrorResponseSerializer,
    500: ApiErrorResponseSerializer,
}


class FeedListView(APIView):
    """GET /tracer/feed/issues/ — paginated cluster list with filters/sort."""

    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @validated_request(
        query_serializer=FeedListQuerySerializer,
        responses={200: FeedListApiResponseSerializer, **ERROR_RESPONSES},
    )
    def get(self, request):
        params = request.validated_query_data
        requested_project_id = params.get("project_id")
        project_ids = resolve_requested_project_ids(
            request, str(requested_project_id) if requested_project_id else None
        )
        if project_ids is None:
            return self._gm.forbidden_response("Access denied to this project")
        if not project_ids:
            return self._gm.forbidden_response(
                "User not associated with an organization"
            )

        try:
            result = feed_service.list_feed_issues(
                project_ids=project_ids,
                search=params.get("search") or None,
                status=params.get("status"),
                severity=params.get("severity"),
                fix_layer=params.get("fix_layer") or None,
                source=params.get("source"),
                issue_group=params.get("issue_group") or None,
                time_range_days=params.get("time_range_days"),
                sort_by=params.get("sort_by", "last_seen"),
                sort_dir=params.get("sort_dir", "desc"),
                limit=params.get("limit", 25),
                offset=params.get("offset", 0),
            )
        except Exception:
            logger.exception("feed_list_failed")
            return self._gm.bad_request("Failed to fetch feed issues")

        data = FeedListResponseSerializer(result).data
        return self._gm.success_response(data)


class FeedStatsView(APIView):
    """GET /tracer/feed/issues/stats/ — top stats bar totals."""

    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @validated_request(
        query_serializer=FeedStatsQuerySerializer,
        responses={200: FeedStatsApiResponseSerializer, **ERROR_RESPONSES},
    )
    def get(self, request):
        params = request.validated_query_data
        requested_project_id = params.get("project_id")
        project_ids = resolve_requested_project_ids(
            request, str(requested_project_id) if requested_project_id else None
        )
        if project_ids is None:
            return self._gm.forbidden_response("Access denied to this project")
        if not project_ids:
            return self._gm.forbidden_response(
                "User not associated with an organization"
            )

        try:
            result = feed_service.get_feed_stats(
                project_ids=project_ids,
                time_range_days=params.get("time_range_days"),
            )
        except Exception:
            logger.exception("feed_stats_failed")
            return self._gm.bad_request("Failed to fetch feed stats")

        data = FeedStatsSerializer(result).data
        return self._gm.success_response(data)
