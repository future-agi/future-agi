from __future__ import annotations

from rest_framework import status as drf_status
from temporalio.exceptions import ApplicationError

from tfc.ee_gating import FeatureUnavailable


class CapabilityDenied(FeatureUnavailable):
    """Raised when a capability check fails. HTTP 402.

    Inherits from FeatureUnavailable so existing exception handlers
    that catch FeatureUnavailable also catch this.
    """

    status_code = drf_status.HTTP_402_PAYMENT_REQUIRED
    default_detail = "This feature is not available on your current plan."
    default_code = "CAPABILITY_DENIED"

    def __init__(
        self,
        feature_id: str,
        reason_code: str,
        detail: str | None = None,
        upgrade_cta: dict | None = None,
        metadata: dict | None = None,
    ):
        self.feature_id = feature_id
        self.reason_code = reason_code
        self.upgrade_cta = upgrade_cta
        self.metadata = metadata or {}
        super().__init__(
            feature=feature_id,
            detail=detail or f"'{feature_id}' is not available. Upgrade your plan.",
            code=reason_code,
            upgrade_cta=upgrade_cta,
        )


def raise_capability_denied_for_temporal(
    feature_id: str,
    reason_code: str,
    detail: str | None = None,
) -> None:
    raise ApplicationError(
        detail or f"'{feature_id}' is not available.",
        type="CapabilityDenied",
        non_retryable=True,
    )
