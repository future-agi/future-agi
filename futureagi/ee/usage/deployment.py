"""Deployment mode detection — oss / ee / cloud.

Detected once at startup from environment variables. Cached for process lifetime.

Usage:
    from ee.usage.deployment import DeploymentMode

    if DeploymentMode.is_cloud():
        # Cloud: use PlanEntitlement for feature gating
    elif DeploymentMode.is_ee():
        # EE: use license.features[] for feature gating
    else:
        # OSS: core features only
"""

from __future__ import annotations

import hashlib
import hmac
from functools import lru_cache
from typing import Literal

import structlog

from tfc.capabilities.registry import PAID_FEATURES

logger = structlog.get_logger(__name__)

DeploymentModeType = Literal["cloud", "ee", "oss"]

_EXPECTED_SECRET_HASH = (
    "1daf0c55c8157b4f8345c968a73e095c4b18afc635aa5fdbebaacc4eb83e68a2"
)


def _validate_cloud_secret(secret: str) -> bool:
    """Validate CLOUD_DEPLOYMENT_SECRET against expected SHA-256 hash.

    Uses constant-time comparison to prevent timing attacks.
    """
    if not secret:
        return False
    computed = hashlib.sha256(secret.encode()).hexdigest()
    return hmac.compare_digest(computed, _EXPECTED_SECRET_HASH)


@lru_cache(maxsize=1)
def _detect_mode() -> DeploymentModeType:
    """Detect deployment mode from environment variables. Cached forever."""
    from django.conf import settings

    cloud_deployment = getattr(settings, "CLOUD_DEPLOYMENT", "")
    if cloud_deployment in ("US", "EU", "DEV"):
        cloud_secret = getattr(settings, "CLOUD_DEPLOYMENT_SECRET", "")
        if _validate_cloud_secret(cloud_secret):
            return "cloud"
        logger.warning(
            "cloud_secret_invalid",
            cloud_deployment=cloud_deployment,
            msg="CLOUD_DEPLOYMENT is set but CLOUD_DEPLOYMENT_SECRET is "
            "missing or invalid — falling back to OSS/EE detection",
        )

    ee_license_key = getattr(settings, "EE_LICENSE_KEY", "")
    if ee_license_key:
        return "ee"

    return "oss"


class DeploymentMode:
    """Deployment mode detection. Detected once, cached for process lifetime."""

    @staticmethod
    def get_mode() -> DeploymentModeType:
        return _detect_mode()

    @staticmethod
    def is_cloud() -> bool:
        return _detect_mode() == "cloud"

    @staticmethod
    def is_ee() -> bool:
        return _detect_mode() == "ee"

    @staticmethod
    def is_oss() -> bool:
        return _detect_mode() == "oss"


# Compatibility alias for the canonical paid capability registry.
EE_FEATURES = PAID_FEATURES
