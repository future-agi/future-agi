"""Settings -> Early access (READINESS 3.13, RC-05): an org sees the Omega products it may ask for and
asks; staff see the request in the admin API and invite it.

A product still at S0 (internal) is not listed to an org that is not on its staff list: a dark
product does not reveal itself here either. Owners and admins ask for their org, from a browser
session: an org API key pair is refused, since a `system` key would let any member ask as the org's
first user (omega_rollout/authority.py).
"""

from __future__ import annotations

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models.organization_membership import OrganizationMembership
from omega_rollout import ops, service
from omega_rollout import products as p
from omega_rollout import serializers as s
from omega_rollout.authority import SessionOnly
from omega_rollout.enforce import unavailable
from omega_rollout.models import WaitlistRequest
from omega_rollout.store import RolloutUnavailable
from tfc.constants.levels import Level
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_errors import build_error_envelope
from tfc.utils.api_serializers import EmptyRequestSerializer


def _can_request(request) -> bool:
    membership = OrganizationMembership.no_workspace_objects.filter(
        user=request.user, organization=request.organization, is_active=True
    ).first()
    return membership is not None and membership.level_or_legacy >= Level.ADMIN


def _listing(request) -> dict:
    organization = request.organization
    workspace = getattr(request, "workspace", None)
    overview = service.org_overview(organization.pk, getattr(workspace, "pk", None))
    requests = {
        row.product: row
        for row in WaitlistRequest.objects.filter(organization=organization)
    }
    products = []
    for product in p.HOSTED_PRODUCTS:
        status_row = overview[product]
        if not (status_row["admitted"] or status_row["accepting_requests"]):
            continue
        asked = requests.get(product)
        products.append(
            {
                "product": product,
                "label": p.PRODUCT_LABELS[product],
                "admitted": status_row["admitted"],
                "state": status_row["state"],
                "request_status": asked.status if asked else None,
                "requested_at": asked.created_at if asked else None,
            }
        )
    return {"can_request": _can_request(request), "products": products}


class EarlyAccessView(APIView):
    permission_classes = [SessionOnly, IsAuthenticated]

    def _org_required(self, request):
        if getattr(request, "organization", None) is None:
            return Response(
                build_error_envelope("Select an organization first.", status_code=403),
                status=status.HTTP_403_FORBIDDEN,
            )
        return None

    @validated_request(
        query_serializer=EmptyRequestSerializer,
        responses={200: s.EarlyAccessSerializer, **s.ERROR_RESPONSES},
    )
    def get(self, request):
        missing = self._org_required(request)
        if missing is not None:
            return missing
        try:
            return Response(_listing(request))
        except RolloutUnavailable as exc:
            raise unavailable(p.CONSOLE) from exc

    @validated_request(
        request_serializer=s.EarlyAccessRequestSerializer,
        reject_unknown_fields=True,
        responses={200: s.EarlyAccessSerializer, **s.ERROR_RESPONSES},
    )
    def post(self, request):
        missing = self._org_required(request)
        if missing is not None:
            return missing
        if not _can_request(request):
            return Response(
                build_error_envelope(
                    "Only organization owners and admins can ask for early access.",
                    status_code=403,
                ),
                status=status.HTTP_403_FORBIDDEN,
            )
        product = request.validated_data["product"]
        try:
            overview = service.org_overview(request.organization.pk, None)
        except RolloutUnavailable as exc:
            raise unavailable(product) from exc
        if (
            not overview[product]["accepting_requests"]
            and not overview[product]["admitted"]
        ):
            return Response(
                build_error_envelope(
                    "Not found.", status_code=404, code=p.REASON_NOT_ADMITTED
                ),
                status=404,
            )
        ops.request_access(
            product,
            request.organization,
            user=request.user,
            note=request.validated_data.get("note", ""),
        )
        return Response(_listing(request))
