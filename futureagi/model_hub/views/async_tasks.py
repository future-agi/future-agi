"""API views for the async task notification center data layer."""

import structlog
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.utils import get_request_organization
from model_hub.services.async_tasks import list_recent_async_tasks

logger = structlog.get_logger(__name__)


class AsyncTaskListView(APIView):
    """List recent async tasks for the requesting user's organization.

    Data-layer endpoint for the notification center: returns recent
    evaluations, run prompts, and experiments with a normalized status
    (queued, running, completed, failed) so the UI can surface live task
    state without a page refresh.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        organization = get_request_organization(request)
        try:
            limit = int(request.query_params.get("limit", 50))
        except (TypeError, ValueError):
            limit = 50
        tasks = list_recent_async_tasks(organization, limit=limit)
        return Response({"tasks": tasks})
