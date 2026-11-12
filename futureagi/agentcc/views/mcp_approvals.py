"""Session-authenticated, tenant-scoped console approval bridge."""

from django.db import DatabaseError
from drf_yasg.utils import swagger_auto_schema
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from agentcc.permissions import require_gateway_admin
from agentcc.serializers.mcp_approvals import (
    MCPApprovalDecisionSerializer,
    MCPApprovalListResultSerializer,
    MCPApprovalMessageSerializer,
    MCPApprovalNotFoundSerializer,
    MCPApprovalResultSerializer,
    MCPApprovalSerializer,
)
from agentcc.services.gateway_client import GatewayClientError
from agentcc.services.mcp_approval_audit import project_approval, reconcile_organization
from agentcc.services.mcp_approval_client import MCPApprovalClient
from tfc.utils.api_serializers import ManagementAPIErrorResponseSerializer


class ApprovalView(APIView):
    permission_classes = [IsAuthenticated]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        # The inherited authentication layer can choose an org's first user
        # for a legacy system key. Console approval routes must never inherit
        # that attribution; require the person-authenticated bearer path.
        if getattr(request, "org_api_key", None) is not None:
            raise PermissionDenied("Sign in to the console to manage approvals.")
        organization = getattr(request, "organization", None)
        level = (
            request.user.get_membership_level(organization) if organization else None
        )
        if level is None or level < 1:
            raise PermissionDenied("Organization membership is required.")

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        return response

    @staticmethod
    def unavailable(exc):
        code = exc.status_code if isinstance(exc, GatewayClientError) else None
        if code == 404:
            return Response({"message": "Approval not found."}, status=404)
        if code == 409:
            return Response(
                {"message": "This approval is no longer pending. Refresh the list."},
                status=409,
            )
        return Response(
            {
                "message": "Approval state could not be confirmed. Refresh before retrying a decision."
            },
            status=503,
        )


class MCPApprovalListView(ApprovalView):
    @swagger_auto_schema(
        operation_id="agentcc_mcp_approvals_list",
        tags=["agentcc"],
        responses={
            200: MCPApprovalListResultSerializer(),
            400: MCPApprovalMessageSerializer,
            404: MCPApprovalMessageSerializer,
            409: MCPApprovalMessageSerializer,
            503: MCPApprovalMessageSerializer,
        },
    )
    def get(self, request):
        status = request.query_params.get("status", "")
        if status not in {"", "pending", "approved", "denied", "used", "expired"}:
            return Response({"message": "Invalid approval status."}, status=400)
        try:
            rows = reconcile_organization(request.organization, MCPApprovalClient())
        except (GatewayClientError, ValueError, DatabaseError) as exc:
            return self.unavailable(exc)
        rows = [row for row in rows if not status or row["status"] == status]
        rows.sort(
            key=lambda row: (row["requested_at"], row["approval_id"]), reverse=True
        )
        return Response({"result": MCPApprovalSerializer(rows, many=True).data})


class MCPApprovalDetailView(ApprovalView):
    @swagger_auto_schema(
        operation_id="agentcc_mcp_approvals_read",
        tags=["agentcc"],
        responses={
            200: MCPApprovalResultSerializer(),
            404: MCPApprovalNotFoundSerializer,
            409: MCPApprovalMessageSerializer,
            503: MCPApprovalMessageSerializer,
        },
    )
    def get(self, request, approval_id):
        if len(approval_id) > 4096:
            raise NotFound()
        try:
            row = MCPApprovalClient().get(request.organization, approval_id)
            if not isinstance(row, dict) or row.get("approval_id") != approval_id:
                raise ValueError("Invalid approval response")
            row = project_approval(request.organization, row)
        except (GatewayClientError, ValueError, DatabaseError) as exc:
            return self.unavailable(exc)
        return Response({"result": MCPApprovalSerializer(row).data})


class MCPApprovalDecisionView(ApprovalView):
    @swagger_auto_schema(
        operation_id="agentcc_mcp_approvals_decision_create",
        tags=["agentcc"],
        request_body=MCPApprovalDecisionSerializer,
        responses={
            200: MCPApprovalResultSerializer(),
            400: ManagementAPIErrorResponseSerializer,
            404: MCPApprovalNotFoundSerializer,
            409: MCPApprovalMessageSerializer,
            503: MCPApprovalMessageSerializer,
        },
    )
    def post(self, request, approval_id):
        require_gateway_admin(request)
        if len(approval_id) > 4096:
            raise NotFound()
        serializer = MCPApprovalDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            row = MCPApprovalClient().decide(
                request.organization,
                approval_id,
                serializer.validated_data["decision"],
                request.user,
                serializer.validated_data["note"],
            )
            if not isinstance(row, dict) or row.get("approval_id") != approval_id:
                raise ValueError("Invalid approval response")
            # Never replace a missing/mismatched server actor with the caller.
            if row.get("decided_by") != str(request.user.pk) or not row.get(
                "decided_at"
            ):
                raise ValueError("Invalid approval decision metadata")
            row = project_approval(request.organization, row)
        except (GatewayClientError, ValueError, DatabaseError) as exc:
            # Redis may have committed despite an audit/transport failure. A
            # second POST is not automatically retried; reconcile on refresh.
            return self.unavailable(exc)
        return Response({"result": MCPApprovalSerializer(row).data})
