from __future__ import annotations

import structlog

from django.conf import settings
from drf_yasg.utils import swagger_auto_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from model_hub.services.experiment_source_capabilities import get_experiment_run_sources
from tfc.capabilities import service
from tfc.capabilities.contracts import CapabilitiesResponseSerializer
from tfc.capabilities.registry import FEATURE_REGISTRY
from tfc.licensing.types import (
    DenialReason,
    DeploymentFlavor,
    DeploymentLocation,
    LicenseState,
    derive_display_mode,
)

logger = structlog.get_logger(__name__)


class CapabilitiesView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(responses={200: CapabilitiesResponseSerializer})
    def get(self, request):
        org = getattr(request, "organization", None)
        org_id = str(org.id) if org else None
        workspace = getattr(request, "workspace", None)
        workspace_id = str(workspace.id) if workspace else None
        rollout = self._rollout_admissions(org_id, workspace_id)

        flavor = service.get_deployment_flavor()
        location = service.get_deployment_location()
        license_state = self._get_license_state()
        display_mode = derive_display_mode(flavor, location, license_state)

        features = {}
        for feature_id, feature_def in FEATURE_REGISTRY.items():
            if feature_def.rollout_product is not None:
                decision = service.check_rollout(
                    feature_def,
                    org_id=org_id,
                    workspace_id=workspace_id,
                    admission=(
                        rollout.get(feature_def.rollout_product)
                        if rollout is not None
                        else service.ROLLOUT_UNREADABLE
                    ),
                )
            else:
                decision = service.check(feature_id, org_id=org_id)
            features[feature_id] = {
                "display_name": feature_def.display_name,
                "allowed": decision.allowed,
                "reason_code": decision.reason_code,
                "requires_network": decision.requires_network,
                "oss_baseline": feature_def.oss_baseline,
            }

        # Personal stars use a deployment switch, not a license entitlement.
        # Match DatasetStarView.initial exactly; do not enable writes here.
        stars_enabled = getattr(settings, "DSX_DATASET_STARS_ENABLED", False) is True
        features["dataset_stars"] = {
            "display_name": "Star datasets",
            "allowed": stars_enabled,
            "reason_code": None if stars_enabled else DenialReason.SERVICE_UNAVAILABLE.value,
            "requires_network": False,
            "oss_baseline": True,
        }

        response_data = {
            "deployment_flavor": flavor.value,
            "display_mode": display_mode.value,
            "license_state": license_state.value,
            "features": features,
            "experiment_run_sources": get_experiment_run_sources(request),
        }

        is_admin_view = location == DeploymentLocation.SELF_HOSTED and self._is_admin(
            request
        )
        if is_admin_view:
            response_data["license"] = self._get_license_details()
            response_data["instance_id"] = self._get_instance_id()

        serializer = CapabilitiesResponseSerializer(data=response_data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.data)
        if not is_admin_view:
            # Non-admins never see license/instance metadata, even as
            # null placeholders — the DRF serializer would otherwise
            # render both fields as null because they are declared
            # optional.
            data.pop("license", None)
            data.pop("instance_id", None)
        return Response(data, headers={"Cache-Control": "private, no-store"})

    def _rollout_admissions(self, org_id, workspace_id):
        """One read of the rollout store for all five products; None when it cannot be read, so
        each product then answers RESOLVER_UNAVAILABLE (fail closed)."""
        from omega_rollout import service as rollout
        from omega_rollout.store import RolloutUnavailable

        try:
            return rollout.admissions(org_id, workspace_id)
        except RolloutUnavailable:
            logger.warning("capabilities_view_rollout_unavailable")
            return None

    def _get_license_state(self) -> LicenseState:
        snapshot = service.get_license_snapshot()
        if snapshot is None:
            if service.get_deployment_flavor() == DeploymentFlavor.CLOUD:
                return LicenseState.NOT_APPLICABLE
            return LicenseState.MISSING
        return snapshot.state

    def _get_license_details(self) -> dict | None:
        snapshot = service.get_license_snapshot()
        if snapshot is None:
            return None
        if snapshot.state in (LicenseState.MISSING, LicenseState.NOT_APPLICABLE):
            return None
        return {
            "issued_to": snapshot.issued_to,
            "band": snapshot.band,
            "license_type": snapshot.license_type.value
            if snapshot.license_type
            else None,
            "expires_at": snapshot.expires_at.isoformat()
            if snapshot.expires_at
            else None,
            "grace_ends_at": snapshot.grace_ends_at.isoformat()
            if snapshot.grace_ends_at
            else None,
            "features_count": len(snapshot.features),
            "state": snapshot.state.value,
        }

    def _get_instance_id(self) -> str | None:
        try:
            from tfc.deployment_telemetry.state import get_or_create_telemetry_state

            state = get_or_create_telemetry_state()
            return str(state.instance_id)
        except Exception:
            logger.debug("capabilities_view_instance_id_unavailable", exc_info=True)
            return None

    def _is_admin(self, request) -> bool:
        user = request.user
        if not user or not user.is_authenticated:
            return False
        org = getattr(request, "organization", None)
        if org is None:
            return user.is_staff
        try:
            from tfc.constants.roles import OrganizationRoles

            admin_roles = (
                OrganizationRoles.OWNER,
                OrganizationRoles.ADMIN,
            )
            membership = user.organization_memberships.filter(organization=org).first()
            if membership and membership.role in admin_roles:
                return True
        except Exception:
            logger.debug("capabilities_view_membership_check_failed", exc_info=True)
        return user.is_staff
