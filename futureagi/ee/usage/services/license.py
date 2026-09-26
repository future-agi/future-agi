"""Compatibility adapter for the canonical EE license snapshot."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from tfc.licensing.types import LicenseSnapshot, LicenseState, MISSING_LICENSE


@dataclass(frozen=True)
class LicenseResult:
    valid: bool
    error: str = ""
    band: str = ""
    features: list[str] = field(default_factory=list)
    max_traces_monthly: int = 0
    max_gateway_monthly: int = 0
    max_users: int = 0
    issued_to: str = ""
    expires_at: Optional[datetime] = None


class LicenseValidator:
    """Legacy interface backed exclusively by ``ee.licensing`` state."""

    @staticmethod
    def validate(license_key: str) -> LicenseResult:
        from ee.licensing.validator import validate

        return _to_legacy_result(validate(license_key))

    @staticmethod
    def get_cached_license() -> Optional[LicenseResult]:
        from ee.licensing.state import get_snapshot

        snapshot = get_snapshot()
        if snapshot.state == LicenseState.MISSING:
            return None
        return _to_legacy_result(snapshot)

    @staticmethod
    def invalidate_cache() -> None:
        from ee.licensing.state import set_snapshot

        set_snapshot(MISSING_LICENSE)

    @staticmethod
    def hash_key(license_key: str) -> str:
        from ee.licensing.validator import hash_key

        return hash_key(license_key)


def _to_legacy_result(snapshot: LicenseSnapshot) -> LicenseResult:
    return LicenseResult(
        valid=snapshot.is_usable,
        error="" if snapshot.is_usable else _error_for_state(snapshot.state),
        band=snapshot.band or "",
        features=sorted(snapshot.features),
        max_traces_monthly=snapshot.limits.get("traces_monthly", 0),
        max_gateway_monthly=snapshot.limits.get("gateway_requests_monthly", 0),
        max_users=snapshot.limits.get("max_users", 0),
        issued_to=snapshot.issued_to or "",
        expires_at=snapshot.expires_at,
    )


def _error_for_state(state: LicenseState) -> str:
    errors = {
        LicenseState.MISSING: "No license key provided",
        LicenseState.INVALID: "Invalid license",
        LicenseState.EXPIRED: "License expired",
        LicenseState.TRIAL_EXPIRED: "Trial license expired",
    }
    return errors.get(state, f"License is not usable: {state.value}")
