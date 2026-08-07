"""EE Feature Middleware — gate EE-only features based on deployment mode + license.

On self-hosted: checks EE_LICENSE_KEY → license.features[].
On cloud: checks PlanEntitlement (handled by existing entitlement service).
With no EE license and not cloud: blocks EE features entirely.

Add to MIDDLEWARE in settings to enable.
"""

from __future__ import annotations

import structlog
from django.http import JsonResponse

from ee.usage.deployment import DeploymentMode
from tfc.utils.api_errors import build_error_envelope

logger = structlog.get_logger(__name__)

# URL path prefixes that require specific EE features.
# This is the middleware layer of a belt-and-suspenders gate — decorators
# on views/tools/activities are the primary defense (see tfc/ee_gating.py).
# Add an entry here when an entire URL subtree gates on a single feature.
EE_FEATURE_PATHS: dict[str, str] = {
    "/api/scim/": "scim",
    "/api/audit-logs/": "audit_logs",
    "/falcon-ai/": "falcon_ai",
}


class EEFeatureMiddleware:
    """Middleware to gate EE-only features based on deployment mode and license."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Check if this path requires an EE feature. The unified resolver
        # handles self-hosted licenses and cloud plan entitlements.
        for path_prefix, feature in EE_FEATURE_PATHS.items():
            if request.path.startswith(path_prefix):
                from ee.usage.services.entitlements import Entitlements

                org = getattr(request, "organization", None)
                org_id = str(org.id) if org else ""

                if not Entitlements.has_feature_unified(org_id, feature):
                    code = (
                        "LICENSE_FEATURE_DENIED"
                        if DeploymentMode.is_ee()
                        else "ENTITLEMENT_DENIED"
                    )
                    message = "This feature requires " + (
                        "an EE license with this feature enabled"
                        if DeploymentMode.is_ee()
                        else "an EE license key"
                    )
                    logger.info(
                        "ee_feature_blocked",
                        path=request.path,
                        feature=feature,
                        mode=DeploymentMode.get_mode(),
                    )
                    return JsonResponse(
                        build_error_envelope(
                            message,
                            status_code=402,
                            error_type="entitlement_error",
                            code=code,
                            details={"feature": [feature]},
                            extra={
                                "upgrade_required": True,
                            },
                        ),
                        status=402,
                    )

        return self.get_response(request)
