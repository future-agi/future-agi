from decimal import Decimal

import structlog
from django.db.models import Avg, Count, Max, Min, Sum
from django.db.models import Q
from django.utils import timezone
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ModelViewSet

from agentcc.models import AgentccRequestLog
from agentcc.models.session import AgentccSession
from agentcc.serializers.session import AgentccSessionSerializer
from tfc.utils.base_viewset import BaseModelViewSetMixinWithUserOrg
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)


class AgentccSessionViewSet(BaseModelViewSetMixinWithUserOrg, ModelViewSet):
    """Session management — create, list, retrieve sessions with stats."""

    permission_classes = [IsAuthenticated]
    serializer_class = AgentccSessionSerializer
    queryset = AgentccSession.no_workspace_objects.all()
    _gm = GeneralMethods()
    # TH-4667 (ai_tools bridge): get_queryset honors `search` (session_id /
    # name icontains below) — declared because the custom list handler hides
    # it from auto-detection. page/page_size auto-detect from the paginator.
    mcp_list_params = {"search": "search"}

    def get_queryset(self):
        qs = super().get_queryset()
        status = self.request.query_params.get("status")
        if status:
            qs = qs.filter(status=status)
        search = self.request.query_params.get("search")
        if search:
            qs = qs.filter(Q(session_id__icontains=search) | Q(name__icontains=search))
        return qs

    def list(self, request, *args, **kwargs):
        try:
            queryset = self.get_queryset()
            page = self.paginate_queryset(queryset)
            if page is not None:
                serializer = AgentccSessionSerializer(page, many=True)
                data = serializer.data
                for row, session in zip(data, page):
                    row["stats"] = self._get_session_stats(session)
                return self.get_paginated_response(data)
            serializer = AgentccSessionSerializer(queryset, many=True)
            data = serializer.data
            for row, session in zip(data, queryset):
                row["stats"] = self._get_session_stats(session)
            return self._gm.success_response(data)
        except Exception as e:
            logger.exception("session_list_error", error=str(e))
            return self._gm.bad_request(str(e))

    @staticmethod
    def _empty_stats():
        return {
            "request_count": 0,
            "total_tokens": 0,
            "total_cost": Decimal("0"),
            "avg_latency_ms": 0,
        }

    def _session_logs(self, session):
        if not session or not session.session_id:
            return AgentccRequestLog.no_workspace_objects.none()
        logs = AgentccRequestLog.no_workspace_objects.filter(
            organization=session.organization,
            session_id=session.session_id,
            deleted=False,
        )
        if session.workspace_id:
            logs = logs.filter(workspace_id=session.workspace_id)
        return logs

    def _get_session_stats(self, session):
        if not session or not session.session_id:
            return self._empty_stats()
        logs = self._session_logs(session)
        aggregates = logs.aggregate(
            request_count=Count("id"),
            total_tokens=Sum("total_tokens"),
            total_cost=Sum("cost"),
            avg_latency_ms=Avg("latency_ms"),
        )
        return {
            "request_count": aggregates["request_count"] or 0,
            "total_tokens": aggregates["total_tokens"] or 0,
            "total_cost": aggregates["total_cost"] or Decimal("0"),
            "avg_latency_ms": float(aggregates["avg_latency_ms"] or 0),
        }

    def create(self, request, *args, **kwargs):
        try:
            serializer = AgentccSessionSerializer(data=request.data)
            if not serializer.is_valid():
                return self._gm.bad_request(serializer.errors)
            org = getattr(request, "organization", None)
            if org is None:
                return self._gm.bad_request("Organization context is required")
            session = serializer.save(
                organization=org,
                workspace=getattr(request, "workspace", None),
            )
            return self._gm.success_response(AgentccSessionSerializer(session).data)
        except Exception as e:
            logger.exception("session_create_error", error=str(e))
            return self._gm.bad_request(str(e))

    def retrieve(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            data = AgentccSessionSerializer(instance).data
            data["stats"] = self._get_session_stats(instance)
            return self._gm.success_response(data)
        except Exception as e:
            logger.exception("session_retrieve_error", error=str(e))
            return self._gm.bad_request(str(e))

    def update(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            serializer = AgentccSessionSerializer(
                instance, data=request.data, partial=kwargs.get("partial", False)
            )
            if not serializer.is_valid():
                return self._gm.bad_request(serializer.errors)
            session = serializer.save()
            return self._gm.success_response(AgentccSessionSerializer(session).data)
        except Exception as e:
            logger.exception("session_update_error", error=str(e))
            return self._gm.bad_request(str(e))

    def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return self.update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            instance.deleted = True
            instance.deleted_at = timezone.now()
            instance.save(update_fields=["deleted", "deleted_at", "updated_at"])
            return self._gm.success_response({"deleted": True})
        except Exception as e:
            logger.exception("session_delete_error", error=str(e))
            return self._gm.bad_request(str(e))

    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        """Close a session."""
        try:
            instance = self.get_object()
            instance.status = AgentccSession.CLOSED
            instance.save(update_fields=["status", "updated_at"])
            return self._gm.success_response(AgentccSessionSerializer(instance).data)
        except Exception as e:
            logger.exception("session_close_error", error=str(e))
            return self._gm.bad_request(str(e))

    @action(detail=True, methods=["get"])
    def requests(self, request, pk=None):
        """List all request logs for this session."""
        try:
            instance = self.get_object()
            from agentcc.serializers.request_log import AgentccRequestLogSerializer

            logs = self._session_logs(instance).order_by("-started_at")[:100]

            serializer = AgentccRequestLogSerializer(logs, many=True)
            return self._gm.success_response(serializer.data)
        except Exception as e:
            logger.exception("session_requests_error", error=str(e))
            return self._gm.bad_request(str(e))
