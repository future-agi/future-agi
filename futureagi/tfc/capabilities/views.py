from __future__ import annotations

import structlog

from drf_yasg.utils import swagger_auto_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from tfc.capabilities import edition, service
from tfc.capabilities.contracts import (
    CapabilitiesResponseSerializer,
    EditionEnvelopeSerializer,
)
from tfc.capabilities.registry import FEATURE_REGISTRY
from tfc.licensing.types import (
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

        flavor = service.get_deployment_flavor()
        location = service.get_deployment_location()
        license_state = self._get_license_state()
        display_mode = derive_display_mode(flavor, location, license_state)

        features = {}
        for feature_id, feature_def in FEATURE_REGISTRY.items():
            decision = service.check(feature_id, org_id=org_id)
            features[feature_id] = {
                "display_name": feature_def.display_name,
                "allowed": decision.allowed,
                "reason_code": decision.reason_code,
                "requires_network": decision.requires_network,
                "oss_baseline": feature_def.oss_baseline,
            }

        response_data = {
            "deployment_flavor": flavor.value,
            "display_mode": display_mode.value,
            "license_state": license_state.value,
            "features": features,
        }

        is_admin_view = (
            location == DeploymentLocation.SELF_HOSTED and self._is_admin(request)
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
        return Response(data)

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
            "license_type": snapshot.license_type.value if snapshot.license_type else None,
            "expires_at": snapshot.expires_at.isoformat() if snapshot.expires_at else None,
            "grace_ends_at": snapshot.grace_ends_at.isoformat() if snapshot.grace_ends_at else None,
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
        return _is_admin(request)


def _is_admin(request) -> bool:
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


class EditionView(APIView):
    """GET /api/edition/: the self-hosted edition, Community limits with current
    usage, and (admins only) the licence status, for Settings > Plan & License.

    Cloud answers ``{"edition": "cloud"}``. The raw licence key is never
    returned or logged: the licence id is masked and the key appears only as
    the first 8 hex digits of its SHA-256.
    """

    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(responses={200: EditionEnvelopeSerializer})
    def get(self, request):
        from tfc.utils.general_methods import GeneralMethods

        gm = GeneralMethods()
        if edition.is_cloud():
            return gm.success_response({"edition": "cloud"})

        organization = getattr(request, "organization", None) or getattr(
            request.user, "organization", None
        )
        data = edition.usage_summary(organization)
        data["activation"] = {"method": "env_restart"}
        if _is_admin(request):
            data["license"] = _edition_license_details()
        return gm.success_response(data)


def _edition_license_details() -> dict:
    snapshot = service.get_license_snapshot()
    details = {
        "state": edition.license_state(),
        "license_type": None,
        "issued_to": None,
        "expires_at": None,
        "grace_ends_at": None,
        "license_id_masked": None,
        "key_fingerprint": None,
    }
    if snapshot is None or not snapshot.license_id:
        return details
    details.update(
        license_type=snapshot.license_type.value if snapshot.license_type else None,
        issued_to=snapshot.issued_to,
        expires_at=snapshot.expires_at.isoformat() if snapshot.expires_at else None,
        grace_ends_at=(
            snapshot.grace_ends_at.isoformat() if snapshot.grace_ends_at else None
        ),
        license_id_masked=_mask(snapshot.license_id),
        key_fingerprint=_key_fingerprint(),
    )
    return details


def _mask(license_id: str) -> str:
    prefix = license_id.split("_", 1)[0] + "_" if "_" in license_id else ""
    return f"{prefix}****{license_id[-4:]}"


def _key_fingerprint() -> str | None:
    import hashlib

    from django.conf import settings

    key = getattr(settings, "EE_LICENSE_KEY", "") or ""
    if not key:
        return None
    return hashlib.sha256(key.encode()).hexdigest()[:8]
