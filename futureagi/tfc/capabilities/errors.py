from __future__ import annotations

from typing import TYPE_CHECKING

from rest_framework import status as drf_status
from temporalio.exceptions import ApplicationError

from tfc.ee_gating import FeatureUnavailable
from tfc.licensing.types import DenialReason

if TYPE_CHECKING:
    from tfc.capabilities.edition import EditionDecision


class CapabilityDenied(FeatureUnavailable):
    """Raised when a capability check fails. HTTP 402.

    Inherits from FeatureUnavailable so existing exception handlers
    that catch FeatureUnavailable also catch this.

    ``enterprise_gate`` is set for self-hosted Enterprise gates (see
    tfc.capabilities.edition) and rendered as a top-level response block.
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
        enterprise_gate: dict | None = None,
    ):
        self.feature_id = feature_id
        self.reason_code = reason_code
        self.upgrade_cta = upgrade_cta
        self.metadata = metadata or {}
        self.enterprise_gate = enterprise_gate
        super().__init__(
            feature=feature_id,
            detail=detail or f"'{feature_id}' is not available. Upgrade your plan.",
            code=reason_code,
            upgrade_cta=upgrade_cta,
        )


class EnterpriseFeatureRequired(CapabilityDenied):
    """Community edition refusal: one more organization, workspace or member
    needs an Enterprise licence. HTTP 402 ENTERPRISE_FEATURE_REQUIRED; not
    retryable, and never presented as a quota or usage limit."""

    default_code = DenialReason.ENTERPRISE_FEATURE_REQUIRED.value

    def __init__(self, feature: str, *, detail: str, enterprise_gate: dict):
        super().__init__(
            feature_id=feature,
            reason_code=DenialReason.ENTERPRISE_FEATURE_REQUIRED.value,
            detail=detail,
            enterprise_gate=enterprise_gate,
        )

    @classmethod
    def from_decision(cls, decision: EditionDecision) -> EnterpriseFeatureRequired:
        from tfc.capabilities import edition

        return cls(
            decision.resource.value,
            detail=edition.refusal_message(decision.resource),
            enterprise_gate=edition.gate_payload(decision),
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
