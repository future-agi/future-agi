"""
Per-tab endpoints for the Error Feed detail view.

- GET  /tracer/feed/issues/{cluster_id}/overview/      → FeedOverviewView
- GET  /tracer/feed/issues/{cluster_id}/traces/        → FeedTracesView
- GET  /tracer/feed/issues/{cluster_id}/trends/        → FeedTrendsView
- GET  /tracer/feed/issues/{cluster_id}/sidebar/       → FeedSidebarView
- GET  /tracer/feed/issues/{cluster_id}/root-cause/    → FeedRootCauseView
- POST /tracer/feed/issues/{cluster_id}/deep-analysis/ → FeedDeepAnalysisView
"""

import structlog
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from tfc.utils.api_contracts import validated_request
from tfc.utils.api_serializers import ApiErrorResponseSerializer
from tfc.utils.general_methods import GeneralMethods
from tracer.serializers.feed import (
    DeepAnalysisApiResponseSerializer,
    DeepAnalysisBodySerializer,
    DeepAnalysisDispatchApiResponseSerializer,
    DeepAnalysisDispatchResponseSerializer,
    DeepAnalysisQuerySerializer,
    DeepAnalysisResponseSerializer,
    FeedSidebarApiResponseSerializer,
    FeedSidebarQuerySerializer,
    FeedSidebarSerializer,
    OverviewApiResponseSerializer,
    OverviewQuerySerializer,
    OverviewResponseSerializer,
    TracesTabApiResponseSerializer,
    TracesTabQuerySerializer,
    TracesTabResponseSerializer,
    TrendsTabApiResponseSerializer,
    TrendsTabQuerySerializer,
    TrendsTabResponseSerializer,
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


def _accessible_project_ids_or_response(request, gm):
    project_ids = resolve_requested_project_ids(request, None)
    if project_ids is None:
        return None, gm.forbidden_response("Access denied to this project")
    if not project_ids:
        return None, gm.forbidden_response("User not associated with an organization")
    return project_ids, None


class FeedOverviewView(APIView):
    """GET /tracer/feed/issues/{cluster_id}/overview/"""

    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @validated_request(
        query_serializer=OverviewQuerySerializer,
        responses={200: OverviewApiResponseSerializer, **ERROR_RESPONSES},
    )
    def get(self, request, cluster_id: str):
        params = request.validated_query_data
        project_ids, response = _accessible_project_ids_or_response(request, self._gm)
        if response is not None:
            return response

        try:
            result = feed_service.get_overview_tab(
                cluster_id,
                project_ids,
                rep_limit=params.get("rep_limit", 20),
            )
        except Exception:
            logger.exception("feed_overview_failed", cluster_id=cluster_id)
            return self._gm.bad_request("Failed to fetch overview")

        if result is None:
            return self._gm.not_found(f"Cluster {cluster_id} not found")

        return self._gm.success_response(OverviewResponseSerializer(result).data)


class FeedTracesView(APIView):
    """GET /tracer/feed/issues/{cluster_id}/traces/"""

    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @validated_request(
        query_serializer=TracesTabQuerySerializer,
        responses={200: TracesTabApiResponseSerializer, **ERROR_RESPONSES},
    )
    def get(self, request, cluster_id: str):
        params = request.validated_query_data
        project_ids, response = _accessible_project_ids_or_response(request, self._gm)
        if response is not None:
            return response

        try:
            result = feed_service.get_traces_tab(
                cluster_id,
                project_ids,
                limit=params.get("limit", 50),
                offset=params.get("offset", 0),
            )
        except Exception:
            logger.exception("feed_traces_failed", cluster_id=cluster_id)
            return self._gm.bad_request("Failed to fetch traces")

        if result is None:
            return self._gm.not_found(f"Cluster {cluster_id} not found")

        return self._gm.success_response(TracesTabResponseSerializer(result).data)


class FeedTrendsView(APIView):
    """GET /tracer/feed/issues/{cluster_id}/trends/"""

    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @validated_request(
        query_serializer=TrendsTabQuerySerializer,
        responses={200: TrendsTabApiResponseSerializer, **ERROR_RESPONSES},
    )
    def get(self, request, cluster_id: str):
        days = request.validated_query_data.get("days", 14)
        project_ids, response = _accessible_project_ids_or_response(request, self._gm)
        if response is not None:
            return response

        try:
            result = feed_service.get_trends_tab(cluster_id, project_ids, days=days)
        except Exception:
            logger.exception("feed_trends_failed", cluster_id=cluster_id)
            return self._gm.bad_request("Failed to fetch trends")

        if result is None:
            return self._gm.not_found(f"Cluster {cluster_id} not found")

        return self._gm.success_response(TrendsTabResponseSerializer(result).data)


class FeedSidebarView(APIView):
    """GET /tracer/feed/issues/{cluster_id}/sidebar/

    Accepts an optional ``?trace_id=`` query param. When present, the
    trace-level sections (AI Metadata + Evaluations) are computed for
    that trace instead of the cluster's latest, keeping the sidebar in
    sync with the Overview tab's trace selection.
    """

    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @validated_request(
        query_serializer=FeedSidebarQuerySerializer,
        responses={200: FeedSidebarApiResponseSerializer, **ERROR_RESPONSES},
    )
    def get(self, request, cluster_id: str):
        trace_id = request.validated_query_data.get("trace_id") or None
        project_ids, response = _accessible_project_ids_or_response(request, self._gm)
        if response is not None:
            return response

        try:
            result = feed_service.get_sidebar(
                cluster_id, project_ids, trace_id=trace_id
            )
        except Exception:
            logger.exception("feed_sidebar_failed", cluster_id=cluster_id)
            return self._gm.bad_request("Failed to fetch sidebar")

        if result is None:
            return self._gm.not_found(f"Cluster {cluster_id} not found")

        return self._gm.success_response(FeedSidebarSerializer(result).data)


class FeedRootCauseView(APIView):
    """GET /tracer/feed/issues/{cluster_id}/root-cause/?trace_id=X

    Read cached deep-analysis results for a single trace within the
    cluster. The frontend hits this on mount (to show existing results)
    and polls it after a POST to /deep-analysis/ until ``status`` flips
    from ``running`` to ``done`` or ``failed``.
    """

    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @validated_request(
        query_serializer=DeepAnalysisQuerySerializer,
        responses={200: DeepAnalysisApiResponseSerializer, **ERROR_RESPONSES},
    )
    def get(self, request, cluster_id: str):
        trace_id = request.validated_query_data["trace_id"]
        project_ids, response = _accessible_project_ids_or_response(request, self._gm)
        if response is not None:
            return response

        try:
            result = feed_service.get_deep_analysis(
                cluster_id, project_ids, trace_id=trace_id
            )
        except Exception:
            logger.exception(
                "feed_root_cause_failed",
                cluster_id=cluster_id,
                trace_id=trace_id,
            )
            return self._gm.bad_request("Failed to fetch deep analysis")

        if result is None:
            return self._gm.not_found(
                f"Trace {trace_id} is not part of cluster {cluster_id}"
            )

        return self._gm.success_response(DeepAnalysisResponseSerializer(result).data)


class FeedDeepAnalysisView(APIView):
    """POST /tracer/feed/issues/{cluster_id}/deep-analysis/

    Body: ``{trace_id, force?}``. On first click (``force=False``),
    returns the cached result if one exists without re-running; on an
    explicit Re-run click (``force=True``), deletes the cached
    analysis and dispatches a fresh Temporal run. Always returns 202
    with a status the frontend can switch on.
    """

    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @validated_request(
        request_serializer=DeepAnalysisBodySerializer,
        responses={200: DeepAnalysisDispatchApiResponseSerializer, **ERROR_RESPONSES},
    )
    def post(self, request, cluster_id: str):
        trace_id = request.validated_data["trace_id"]
        force = request.validated_data.get("force", False)
        project_ids, response = _accessible_project_ids_or_response(request, self._gm)
        if response is not None:
            return response

        try:
            result = feed_service.dispatch_deep_analysis(
                cluster_id, project_ids, trace_id=trace_id, force=force
            )
        except Exception:
            logger.exception(
                "feed_deep_analysis_dispatch_failed",
                cluster_id=cluster_id,
                trace_id=trace_id,
            )
            return self._gm.bad_request("Failed to dispatch deep analysis")

        if result is None:
            return self._gm.not_found(
                f"Trace {trace_id} is not part of cluster {cluster_id}"
            )

        return self._gm.success_response(
            DeepAnalysisDispatchResponseSerializer(result).data
        )
