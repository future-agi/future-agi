"""Live Console admission for EE's stored trigger owner. Internal read-only channel.

This endpoint accepts neither a browser bearer nor an API key. The existing host-events
transport secret signs a domain-separated request naming a server-resolved host identity.
It returns no credentials and never creates identities, triggers or runs.
"""

import hashlib
import hmac
import json
import re
import time
from uuid import UUID

from django.db import DatabaseError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.host_events.config import READY, load_config
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from accounts.omega_session import delegation_role
from omega_rollout.service import claim_set
from omega_rollout.store import RolloutUnavailable
from tfc.routers import force_primary

DOMAIN = b"omega-trigger-admission/v1\n"
FIELDS = {
    "issuer",
    "subject",
    "organizationId",
    "workspaceId",
    "product",
    "nonce",
    "timestamp",
}


class OmegaTriggerAdmissionView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = []
    # This machine-to-machine control route is not a customer SDK operation.
    swagger_schema = None

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        return response

    def post(self, request):
        channel = load_config()
        if channel.state != READY:
            return Response({"error": "admission_unavailable"}, status=503)
        # Read at most 4 KiB even when no Content-Length was supplied. No parser has
        # buffered request.data, and unauthenticated input never reaches the database.
        stream = request.stream
        body = stream.read(4097) if stream is not None else b""
        signature = request.headers.get("X-Omega-Admission-Signature", "")
        if (
            len(body) > 4096
            or request.headers.get("X-Omega-Admission-Key-Id") != channel.key_id
            or not re.fullmatch(r"[0-9a-f]{64}", signature)
            or not hmac.compare_digest(
                hmac.new(channel.secret, DOMAIN + body, hashlib.sha256).hexdigest(),
                signature,
            )
        ):
            return Response({"error": "admission_refused"}, status=401)
        try:
            value = json.loads(body)
            if (
                not isinstance(value, dict)
                or set(value) != FIELDS
                or value["issuer"] != channel.issuer
                or value["product"] != "console"
                or type(value["timestamp"]) is not int
                or abs(time.time() - value["timestamp"]) > 30
            ):
                raise ValueError("invalid admission request")
            for field in ("subject", "organizationId", "workspaceId", "nonce"):
                UUID(value[field])
        except (ValueError, TypeError, KeyError, AttributeError):
            return Response({"error": "admission_refused"}, status=401)
        try:
            # Membership, workspace and rollout all come from primary, not a stale replica.
            with force_primary():
                reason = self.owner_refusal(value)
                products = (
                    {}
                    if reason
                    else claim_set(
                        value["organizationId"], value["workspaceId"]
                    ).products
                )
            return Response(
                {
                    "nonce": value["nonce"],
                    "products": products,
                    **({"reason": reason} if reason else {}),
                }
            )
        except (DatabaseError, RolloutUnavailable):
            return Response({"error": "admission_unavailable"}, status=503)

    @staticmethod
    def owner_refusal(value):
        membership = OrganizationMembership.no_workspace_objects.filter(
            user_id=value["subject"],
            user__is_active=True,
            organization_id=value["organizationId"],
            is_active=True,
        ).first()
        workspace = Workspace.no_workspace_objects.filter(
            pk=value["workspaceId"],
            organization_id=value["organizationId"],
            is_active=True,
        ).first()
        if membership is None or workspace is None:
            return "owner_removed"
        workspace_member = WorkspaceMembership.no_workspace_objects.filter(
            user_id=value["subject"],
            workspace=workspace,
            is_active=True,
        ).first()
        try:
            role = delegation_role(
                membership.level, workspace_member.level if workspace_member else None
            )
        except ValueError:
            return "owner_role_denied"
        return None if role in ("owner", "admin") else "owner_role_denied"
