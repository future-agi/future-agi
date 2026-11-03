"""Startup wiring for the capability service.

Detects deployment flavor and location, then wires the appropriate
resolvers into the capability service. Called from CapabilitiesConfig.ready().
"""

from __future__ import annotations

import structlog

from tfc.ee_loader import has_ee
from tfc.licensing.types import DeploymentFlavor, DeploymentLocation

logger = structlog.get_logger(__name__)


def _detect_flavor() -> DeploymentFlavor:
    if not has_ee("ee.usage"):
        return DeploymentFlavor.OSS
    try:
        from ee.usage.deployment import DeploymentMode

        if DeploymentMode.is_cloud():
            return DeploymentFlavor.CLOUD
    except ImportError:
        return DeploymentFlavor.OSS
    return DeploymentFlavor.SELF_HOSTED_EE


def _detect_location() -> DeploymentLocation:
    if not has_ee("ee.usage"):
        return DeploymentLocation.SELF_HOSTED
    try:
        from ee.usage.deployment import DeploymentMode

        if DeploymentMode.is_cloud():
            return DeploymentLocation.CLOUD
    except ImportError:
        pass
    return DeploymentLocation.SELF_HOSTED


def wire_resolvers() -> None:
    from tfc.capabilities import service

    flavor = _detect_flavor()
    location = _detect_location()

    license_resolver = None
    cloud_plan_resolver = None

    if flavor == DeploymentFlavor.SELF_HOSTED_EE:
        license_resolver = _try_load_license_resolver()

    if location == DeploymentLocation.CLOUD:
        cloud_plan_resolver = _try_load_cloud_resolver()

    service.configure(
        flavor=flavor,
        location=location,
        license_resolver=license_resolver,
        cloud_plan_resolver=cloud_plan_resolver,
    )

    logger.info(
        "capabilities_configured",
        extra={"flavor": flavor.value, "location": location.value},
    )


def _try_load_license_resolver():
    try:
        from ee.licensing.state import get_resolver

        return get_resolver()
    except ImportError:
        logger.debug("ee.licensing.state not available; license resolver disabled")
        return None
    except Exception:
        logger.exception("Failed to load license resolver")
        return None


def _try_load_cloud_resolver():
    try:
        from ee.usage.services.entitlements import get_cloud_plan_resolver

        return get_cloud_plan_resolver()
    except ImportError:
        logger.debug("Cloud plan resolver not available")
        return None
    except Exception:
        logger.exception("Failed to load cloud plan resolver")
        return None
