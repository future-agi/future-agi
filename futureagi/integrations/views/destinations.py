"""``GET /integrations/destinations/``: the one destination registry (webhooks DESIGN.md section 11.1).

Read only. The list comes from ``integrations.services.destinations``; webhook endpoints are filtered by the webhooks
RBAC (section 9.1), and ``webhooks`` says whether the Webhooks section exists on this install and what the caller may
do in it, so the page never shows a control that cannot work.
"""

from __future__ import annotations

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.utils import get_request_organization
from integrations.serializers.contracts import INTEGRATION_ERROR_RESPONSES
from integrations.serializers.destinations import (
    DestinationsQuery,
    DestinationsResponse,
)
from integrations.services.destinations import list_destinations, webhooks_availability
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_errors import build_error_envelope


def _validation_error(errors):
    return Response(
        build_error_envelope(errors, status_code=status.HTTP_400_BAD_REQUEST),
        status=status.HTTP_400_BAD_REQUEST,
    )


def _webhooks_caller(request):
    from webhooks.api_errors import WebhookApiError
    from webhooks.permissions import caller_of

    try:
        return caller_of(request)
    except WebhookApiError:
        return None


class DestinationsView(APIView):
    permission_classes = [IsAuthenticated]

    @validated_request(
        query_serializer=DestinationsQuery,
        responses={200: DestinationsResponse, **INTEGRATION_ERROR_RESPONSES},
        validation_error_response=_validation_error,
        reject_unknown_fields=True,
        tags=["integrations"],
        operation_id="integrations_destinations_list",
    )
    def get(self, request):
        from webhooks.permissions import ADMIN, MANAGE, VIEW

        org = get_request_organization(request)
        if org is None:
            return Response(
                build_error_envelope("Organization not found", status_code=404),
                status=status.HTTP_404_NOT_FOUND,
            )
        ws = getattr(request, "workspace", None)
        ws_id = None if ws is None else str(ws.id)
        caller = _webhooks_caller(request)
        avail = webhooks_availability()

        def can(action, workspace_id) -> bool:
            return caller is not None and caller.can(action, workspace_id)

        def can_view(workspace_id) -> bool:
            return avail["available"] and can(VIEW, workspace_id)

        kind = request.validated_query_data.get("kind")
        rows = list_destinations(
            org.id,
            ws_id,
            can_view_endpoint=can_view,
            kinds=None if not kind else (kind,),
        )
        ws_scope = caller.request_workspace_id if caller is not None else ws_id
        webhooks = {
            "available": avail["available"],
            "reasons": avail["reasons"],
            "can_view": bool(ws_scope) and can(VIEW, ws_scope),
            "can_manage": bool(ws_scope) and can(MANAGE, ws_scope),
            "can_admin": bool(ws_scope) and can(ADMIN, ws_scope),
            "org_admin": can(ADMIN, None),
            "workspace_id": ws_scope,
        }
        return Response(
            {"status": True, "result": {"destinations": rows, "webhooks": webhooks}}
        )
