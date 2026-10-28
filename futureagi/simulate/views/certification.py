from __future__ import annotations

from django.conf import settings
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from simulate.serializers.certification import (
    CertificationCampaignCreateSerializer,
    CertificationCampaignReadSerializer,
)
from simulate.services.certification import (
    CertificationError,
    create_campaign,
    request_campaign_cancel,
)
from simulate.services.harness_credentials import request_scope
from tfc.utils.api_contracts import validated_request


def _allowlisted(organization) -> bool:
    """Org allowlist gate — the plan requires campaigns be restricted to
    approved organizations until the capability is fully rolled out.

    ``PRODUCTION_CERTIFICATION_ORG_ALLOWLIST`` is a comma-separated list of
    organization UUIDs. Empty/unset = nobody can create campaigns (safe
    default — the feature is inert until explicitly configured).
    """
    raw = str(
        getattr(settings, "PRODUCTION_CERTIFICATION_ORG_ALLOWLIST", "") or ""
    ).strip()
    if not raw:
        return False
    allowed = {item.strip() for item in raw.split(",") if item.strip()}
    return organization is not None and str(organization.id) in allowed


class CertificationCampaignViewSet(viewsets.ViewSet):
    """Production certification campaign admission/read/cancel.

    A campaign orchestrates ordinary hosted harness jobs against deployed
    production agents; creation is idempotent on
    (organization, deployment_id, suite_digest) and returns the existing
    campaign on retries.
    """

    permission_classes = [IsAuthenticated]

    @validated_request(
        request_serializer=CertificationCampaignCreateSerializer,
        responses={201: CertificationCampaignReadSerializer},
        reject_unknown_fields=True,
    )
    def create(self, request):
        organization, workspace = request_scope(request)
        if organization is None:
            return Response(
                {"detail": "an organization is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not _allowlisted(organization):
            return Response(
                {
                    "detail": "certification campaigns are not enabled for "
                    "this organization"
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        # Idempotency-Key header is required for admission; the semantic
        # dedupe key (deployment+suite) makes the operation exactly-once.
        idem_header = request.headers.get("Idempotency-Key", "").strip()
        if not idem_header or len(idem_header) > 255:
            return Response(
                {"detail": "a valid Idempotency-Key header is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        data = request.validated_data
        try:
            campaign, created = create_campaign(
                organization,
                workspace,
                deployment_id=data["deployment_id"],
                deployment_identity=data.get("deployment_identity") or {},
                suite_key=data.get("suite_key") or "production_agents_v1",
                requested_by=str(getattr(request.user, "email", "") or ""),
            )
        except CertificationError as exc:
            return Response(exc.as_dict(), status=exc.status_code)

        return self._campaign_response(
            campaign, status.HTTP_201_CREATED if created else status.HTTP_200_OK
        )

    def list(self, request):
        organization, _workspace = request_scope(request)
        if organization is None:
            return Response(
                {"detail": "an organization is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        from simulate.models import CertificationCampaign

        campaigns = (
            CertificationCampaign.no_workspace_objects.filter(organization=organization)
            .prefetch_related("cases")
            .order_by("-created_at")[:50]
        )
        return Response(CertificationCampaignReadSerializer(campaigns, many=True).data)

    def retrieve(self, request, pk=None):
        campaign = self._get_scoped(request, pk)
        if isinstance(campaign, Response):
            return campaign
        return self._campaign_response(campaign, status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="cancel")
    def cancel(self, request, pk=None):
        campaign = self._get_scoped(request, pk)
        if isinstance(campaign, Response):
            return campaign
        from simulate.models import CertificationCampaign as _CC

        if campaign.state in (
            _CC.State.COMPLETED,
            _CC.State.COMPLETED_WITH_FAILURES,
            _CC.State.CANCELED,
            _CC.State.FAILED,
        ):
            return Response(
                {"detail": f"campaign is already {campaign.state}"},
                status=status.HTTP_409_CONFLICT,
            )
        request_campaign_cancel(campaign)
        campaign.refresh_from_db()
        return self._campaign_response(campaign, status.HTTP_200_OK)

    # ------------------------------------------------------------------

    def _get_scoped(self, request, pk):
        from simulate.models import CertificationCampaign

        organization, _workspace = request_scope(request)
        if organization is None:
            return Response(
                {"detail": "an organization is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        campaign = (
            CertificationCampaign.no_workspace_objects.filter(
                organization=organization, id=pk
            )
            .prefetch_related("cases")
            .first()
        )
        if campaign is None:
            return Response(
                {"detail": "campaign not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        return campaign

    def _campaign_response(self, campaign, http_status):
        return Response(
            CertificationCampaignReadSerializer(campaign).data,
            status=http_status,
        )
