"""Canonical runtime types for deployment flavor and license state.

These types separate deployment identity from license authorization. A
self-hosted installation remains self-hosted whether its license is missing,
invalid, active, in grace, or expired. The license determines entitlements,
not deployment identity.

This module MUST NOT import from ee.* or django.* — it is a pure contract
that can be imported anywhere without side effects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum


class DeploymentFlavor(str, Enum):
    """Physical image flavor — what code is available on disk."""

    OSS = "oss_image"
    SELF_HOSTED_EE = "self_hosted_ee_image"
    CLOUD = "cloud_image"


class DeploymentLocation(str, Enum):
    """Where this instance runs."""

    SELF_HOSTED = "self_hosted"
    CLOUD = "cloud"


class LicenseState(str, Enum):
    """License validation states for self-hosted EE deployments.

    Cloud deployments use NOT_APPLICABLE. OSS deployments without the EE
    package use MISSING (the package itself is absent, not just the key).
    """

    NOT_APPLICABLE = "not_applicable"
    MISSING = "missing"
    INVALID = "invalid"
    ACTIVE = "active"
    GRACE = "grace"
    EXPIRED = "expired"
    TRIAL_ACTIVE = "trial_active"
    TRIAL_EXPIRED = "trial_expired"


class LicenseType(str, Enum):
    """License issuance type."""

    PRODUCTION = "production"
    TRIAL = "trial"


class DisplayMode(str, Enum):
    """Externally displayed deployment mode (derived from flavor + license)."""

    OSS = "oss"
    OSS_LOCKED = "oss_locked"
    ENTERPRISE = "enterprise"
    CLOUD = "cloud"


class DenialReason(str, Enum):
    """Stable reason codes for capability denials.

    These are the canonical codes returned in CapabilityDecision and surfaced
    to the frontend. They MUST remain stable across versions.
    """

    # Feature registry errors
    FEATURE_UNKNOWN = "FEATURE_UNKNOWN"

    # License state denials
    LICENSE_MISSING = "LICENSE_MISSING"
    LICENSE_INVALID = "LICENSE_INVALID"
    LICENSE_EXPIRED = "LICENSE_EXPIRED"
    LICENSE_TRIAL_EXPIRED = "LICENSE_TRIAL_EXPIRED"

    # Feature-level denials
    LICENSE_FEATURE_MISSING = "LICENSE_FEATURE_MISSING"
    FEATURE_NOT_IN_GRACE = "FEATURE_NOT_IN_GRACE"

    # Code availability
    EE_CODE_UNAVAILABLE = "EE_CODE_UNAVAILABLE"

    # Resolver/infrastructure failures
    RESOLVER_UNAVAILABLE = "RESOLVER_UNAVAILABLE"

    # Quota denials
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"

    # Managed service denials
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    NETWORK_REQUIRED = "NETWORK_REQUIRED"
    USAGE_LIMIT_REACHED = "USAGE_LIMIT_REACHED"

    # Cloud-specific
    PLAN_FEATURE_MISSING = "PLAN_FEATURE_MISSING"

    # Runtime compatibility
    LICENSE_VERSION_UNSUPPORTED = "LICENSE_VERSION_UNSUPPORTED"


@dataclass(frozen=True)
class LicenseSnapshot:
    """Immutable point-in-time view of the validated license.

    Persisted instance-wide (not per-organization). Published once at
    startup and shared across web and Temporal worker processes.

    Does NOT contain the raw license key.
    """

    state: LicenseState
    license_type: LicenseType | None = None
    license_id: str | None = None
    customer_id: str | None = None
    issued_to: str | None = None
    band: str | None = None
    features: frozenset[str] = field(default_factory=frozenset)
    limits: dict[str, int] = field(default_factory=dict)
    max_instances: int | None = None
    min_version: str | None = None
    issued_at: datetime | None = None
    expires_at: datetime | None = None
    grace_ends_at: datetime | None = None
    validated_at: datetime | None = None
    denial_reason: DenialReason | None = None

    def live_state(self, now: datetime | None = None) -> LicenseState:
        """Recompute the effective state against the current clock.

        The snapshot is frozen at process start, so a long-lived worker
        would otherwise keep serving paid features past expires_at until
        it restarts. Recheck expires_at / grace_ends_at on every call and
        return the transitioned state.
        """
        base = self.state
        if base in (
            LicenseState.MISSING,
            LicenseState.INVALID,
            LicenseState.NOT_APPLICABLE,
            LicenseState.EXPIRED,
            LicenseState.TRIAL_EXPIRED,
        ):
            return base

        current = now or datetime.now(UTC)

        if base == LicenseState.TRIAL_ACTIVE:
            if self.expires_at and current >= self.expires_at:
                return LicenseState.TRIAL_EXPIRED
            return base

        # ACTIVE or GRACE — check active window then grace window.
        if base == LicenseState.ACTIVE and self.expires_at and current >= self.expires_at:
            if self.grace_ends_at and current < self.grace_ends_at:
                return LicenseState.GRACE
            return LicenseState.EXPIRED

        if base == LicenseState.GRACE:
            if self.grace_ends_at and current >= self.grace_ends_at:
                return LicenseState.EXPIRED
            return base

        return base

    @property
    def is_active(self) -> bool:
        return self.live_state() in (LicenseState.ACTIVE, LicenseState.TRIAL_ACTIVE)

    @property
    def is_grace(self) -> bool:
        return self.live_state() == LicenseState.GRACE

    @property
    def is_expired(self) -> bool:
        return self.live_state() in (LicenseState.EXPIRED, LicenseState.TRIAL_EXPIRED)

    @property
    def is_usable(self) -> bool:
        """True if the license allows any paid feature access (active or grace)."""
        return self.live_state() in (
            LicenseState.ACTIVE,
            LicenseState.GRACE,
            LicenseState.TRIAL_ACTIVE,
        )


# Sentinel snapshots for common states.
MISSING_LICENSE = LicenseSnapshot(
    state=LicenseState.MISSING,
    denial_reason=DenialReason.LICENSE_MISSING,
)

INVALID_LICENSE = LicenseSnapshot(
    state=LicenseState.INVALID,
    denial_reason=DenialReason.LICENSE_INVALID,
)


def derive_display_mode(
    flavor: DeploymentFlavor,
    location: DeploymentLocation,
    license_state: LicenseState,
) -> DisplayMode:
    """Derive the externally-visible mode from independent state axes."""
    if location == DeploymentLocation.CLOUD or flavor == DeploymentFlavor.CLOUD:
        return DisplayMode.CLOUD

    if flavor == DeploymentFlavor.SELF_HOSTED_EE:
        if license_state in (
            LicenseState.ACTIVE,
            LicenseState.GRACE,
            LicenseState.TRIAL_ACTIVE,
        ):
            return DisplayMode.ENTERPRISE
        return DisplayMode.OSS_LOCKED

    return DisplayMode.OSS
