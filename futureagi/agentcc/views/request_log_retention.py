import structlog
from django.db import IntegrityError, transaction
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from accounts.utils import get_request_organization
from agentcc.models import AgentccRequestLogRetention
from agentcc.serializers.contracts import (
    AgentccErrorResponseSerializer,
    RequestLogRetentionQuerySerializer,
    RequestLogRetentionResponseSerializer,
    RequestLogRetentionUpdateSerializer,
)
from agentcc.services.request_log_retention import (
    FOREVER,
    budget_retention_floor,
    effective_cutoff,
    estimate_rows,
    longest_period,
    next_run_at,
    org_budgets,
)
from tfc.permissions.rbac import IsOrganizationAdmin
from tfc.utils.api_contracts import validated_request
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)


def _retention(org):
    return AgentccRequestLogRetention.no_workspace_objects.filter(
        organization=org
    ).first()


def _state(org, row, retention_days):
    """The panel's view of the retention ``retention_days`` would give."""
    budgets = org_budgets(org.id)
    floor = budget_retention_floor(budgets)
    cutoff = effective_cutoff(retention_days, budgets)
    estimate = estimate_rows(org.id, cutoff)
    return {
        "retention_days": row.retention_days if row else None,
        "min_retention_days": floor if isinstance(floor, int) else None,
        "keep_forever_required": floor == FOREVER,
        "effective_cutoff": cutoff,
        "next_run_at": next_run_at(),
        "estimated_rows": estimate["count"],
        "estimate_capped": estimate["capped"],
        "last_run": {
            "started_at": row.last_run_started_at if row else None,
            "finished_at": row.last_run_finished_at if row else None,
            "deleted_rows": row.last_run_deleted_rows if row else 0,
            "error": row.last_run_error if row else "",
        },
    }


class RequestLogRetentionView(APIView):
    """How long the organization keeps gateway Request Logs.

    GET for any member; ``?retention_days=N`` previews the next run for N.
    PUT for organization admins: ``{"retention_days": N}`` or null to keep
    forever, never shorter than the longest budget period in use.
    """

    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    def get_permissions(self):
        if self.request.method == "PUT":
            return [IsAuthenticated(), IsOrganizationAdmin()]
        return super().get_permissions()

    @validated_request(
        query_serializer=RequestLogRetentionQuerySerializer,
        responses={
            200: RequestLogRetentionResponseSerializer,
            400: AgentccErrorResponseSerializer,
        },
        reject_unknown_fields=True,
    )
    def get(self, request):
        org = get_request_organization(request)
        if org is None:
            return self._gm.bad_request("Organization context is required")
        row = _retention(org)
        preview = request.validated_query_data.get("retention_days")
        days = preview if preview is not None else (row.retention_days if row else None)
        return self._gm.success_response(_state(org, row, days))

    @validated_request(
        request_serializer=RequestLogRetentionUpdateSerializer,
        responses={
            200: RequestLogRetentionResponseSerializer,
            400: AgentccErrorResponseSerializer,
            403: AgentccErrorResponseSerializer,
        },
        reject_unknown_fields=True,
    )
    def put(self, request):
        org = get_request_organization(request)
        if org is None:
            return self._gm.bad_request("Organization context is required")
        days = request.validated_data["retention_days"]
        budgets = org_budgets(org.id)
        floor = budget_retention_floor(budgets)
        if days is not None and floor == FOREVER:
            return self._gm.bad_request(
                "Request Logs must be kept forever because a total budget is configured"
            )
        if days is not None and isinstance(floor, int) and days < floor:
            return self._gm.bad_request(
                f"Retention must be at least {floor} days because a "
                f"{longest_period(budgets)} budget is configured"
            )
        row = _retention(org)
        if row is None:
            try:
                with transaction.atomic():
                    row = AgentccRequestLogRetention.no_workspace_objects.create(
                        organization=org, retention_days=days, updated_by=request.user
                    )
            except IntegrityError:
                # Another admin's first save won the race: update that row.
                row = AgentccRequestLogRetention.no_workspace_objects.get(
                    organization=org
                )
        row.retention_days = days
        row.updated_by = request.user
        row.save(update_fields=["retention_days", "updated_by", "updated_at"])
        logger.info(
            "request_log_retention_updated",
            organization_id=str(org.id),
            retention_days=days,
        )
        return self._gm.success_response(_state(org, row, days))
