"""HTTP contract of the Enterprise gate (TH-8084).

Refusals from the Community edition rule are HTTP 402 with the stable code
ENTERPRISE_FEATURE_REQUIRED and an ``enterprise_gate`` block. Self-hosted
``oss_locked`` product denials keep their reason code and gain the same block.
Cloud denials are unchanged.
"""

from __future__ import annotations

import pytest

from accounts.authentication import custom_exception_handler
from tfc.capabilities import edition, service
from tfc.capabilities.edition import EditionDecision, EditionResource
from tfc.capabilities.errors import CapabilityDenied, EnterpriseFeatureRequired
from tfc.licensing.types import (
    MISSING_LICENSE,
    DenialReason,
    DeploymentFlavor,
    DeploymentLocation,
    LicenseState,
)

pytestmark = pytest.mark.edition_rule

GATE_KEYS = {
    "feature",
    "edition",
    "limit",
    "current",
    "requested",
    "license_state",
    "contact",
    "activation_route",
}


class _Resolver:
    def __init__(self, snapshot):
        self._snapshot = snapshot

    def get_snapshot(self):
        return self._snapshot


@pytest.fixture
def restore_service():
    saved = (
        service._deployment_flavor,
        service._deployment_location,
        service._license_resolver,
        service._cloud_plan_resolver,
        service._configured,
    )
    yield
    (
        service._deployment_flavor,
        service._deployment_location,
        service._license_resolver,
        service._cloud_plan_resolver,
        service._configured,
    ) = saved


def _member_refusal() -> EnterpriseFeatureRequired:
    return EnterpriseFeatureRequired.from_decision(
        EditionDecision(
            allowed=False,
            resource=EditionResource.MEMBER,
            limit=3,
            current=3,
            requested=1,
            license_state="missing",
        )
    )


class TestEditionGateEnvelope:
    def test_status_code_and_stable_code(self):
        """AC-08 / C8: 402 ENTERPRISE_FEATURE_REQUIRED with an enterprise_gate block."""
        response = custom_exception_handler(_member_refusal(), context={})

        assert response.status_code == 402
        body = response.data
        assert body["status"] is False
        assert body["type"] == "entitlement_error"
        assert body["code"] == "ENTERPRISE_FEATURE_REQUIRED"
        assert body["error"]["code"] == "ENTERPRISE_FEATURE_REQUIRED"
        assert body["upgrade_required"] is True
        assert body["details"]["feature"] == ["members"]
        assert set(body["enterprise_gate"]) == GATE_KEYS
        assert body["enterprise_gate"] == {
            "feature": "members",
            "edition": "community",
            "limit": 3,
            "current": 3,
            "requested": 1,
            "license_state": "missing",
            "contact": "sales@futureagi.com",
            "activation_route": "/dashboard/settings/ee-licenses",
        }

    def test_not_retryable_and_not_a_quota_error(self):
        """AC-08: no Retry-After, never 429, never 'usage limit' copy."""
        response = custom_exception_handler(_member_refusal(), context={})

        assert response.status_code != 429
        assert "Retry-After" not in response
        message = response.data["message"].lower()
        assert "enterprise" in message
        for banned in ("usage limit", "quota", "rate limit", "upgrade your plan"):
            assert banned not in message

    @pytest.mark.parametrize(
        ("resource", "expected"),
        [
            (EditionResource.MEMBER, "up to 3 organization members"),
            (EditionResource.ORGANIZATION, "one organization"),
            (EditionResource.WORKSPACE, "one workspace"),
        ],
    )
    def test_copy_names_the_community_allowance(self, resource, expected):
        """AC-20: copy describes the Community allowance and names only sales@futureagi.com."""
        exc = EnterpriseFeatureRequired.from_decision(
            EditionDecision(
                allowed=False,
                resource=resource,
                limit=edition.COMMUNITY_LIMITS[resource],
                current=edition.COMMUNITY_LIMITS[resource],
                requested=1,
                license_state="missing",
            )
        )
        message = str(exc.detail)
        assert expected in message
        assert "Enterprise" in message
        assert "@" not in message.replace("sales@futureagi.com", "")

    def test_is_a_capability_denial(self):
        """AC-08: existing FeatureUnavailable handlers keep catching the gate."""
        from tfc.ee_gating import FeatureUnavailable

        exc = _member_refusal()
        assert isinstance(exc, CapabilityDenied)
        assert isinstance(exc, FeatureUnavailable)

    def test_denial_reason_is_stable(self):
        """C8: the reason code is part of the stable DenialReason contract."""
        assert (
            DenialReason.ENTERPRISE_FEATURE_REQUIRED.value
            == "ENTERPRISE_FEATURE_REQUIRED"
        )


class TestProductGates:
    @pytest.mark.parametrize(
        "feature_id", ["falcon_ai", "turing_models", "protect", "error_feed"]
    )
    def test_oss_locked_denial_carries_gate_with_original_reason(
        self, restore_service, feature_id
    ):
        """AC-14: product gates keep their reason code and gain enterprise_gate."""
        service.configure(
            flavor=DeploymentFlavor.SELF_HOSTED_EE,
            location=DeploymentLocation.SELF_HOSTED,
            license_resolver=_Resolver(MISSING_LICENSE),
        )
        with pytest.raises(CapabilityDenied) as exc_info:
            service.check_or_raise(feature_id)

        response = custom_exception_handler(exc_info.value, context={})
        assert response.status_code == 402
        assert response.data["code"] == DenialReason.LICENSE_MISSING.value
        gate = response.data["enterprise_gate"]
        assert set(gate) == GATE_KEYS
        assert gate["feature"] == feature_id
        assert gate["license_state"] == LicenseState.MISSING.value
        assert gate["contact"] == "sales@futureagi.com"
        assert (gate["limit"], gate["current"], gate["requested"]) == (None, None, None)

    def test_oss_image_denial_carries_gate(self, restore_service):
        """AC-14: OSS-image product denials get the same block."""
        service.configure(
            flavor=DeploymentFlavor.OSS, location=DeploymentLocation.SELF_HOSTED
        )
        with pytest.raises(CapabilityDenied) as exc_info:
            service.check_or_raise("falcon_ai")
        response = custom_exception_handler(exc_info.value, context={})
        assert response.data["code"] == DenialReason.EE_CODE_UNAVAILABLE.value
        assert response.data["enterprise_gate"]["feature"] == "falcon_ai"

    def test_cloud_denial_is_unchanged(self, restore_service):
        """AC-13: Cloud plan denials carry no enterprise_gate."""

        class _Cloud:
            def has_feature(self, org_id, feature_id):
                return False

            def get_upgrade_cta(self, org_id, feature_id):
                return {"text": "Upgrade", "plan": "payg"}

        service.configure(
            flavor=DeploymentFlavor.CLOUD,
            location=DeploymentLocation.CLOUD,
            cloud_plan_resolver=_Cloud(),
        )
        with pytest.raises(CapabilityDenied) as exc_info:
            service.check_or_raise("falcon_ai", org_id="org-1")
        response = custom_exception_handler(exc_info.value, context={})
        assert response.data["code"] == DenialReason.PLAN_FEATURE_MISSING.value
        assert "enterprise_gate" not in response.data
        assert response.data["upgrade_cta"] == {"text": "Upgrade", "plan": "payg"}

    def test_plain_feature_unavailable_is_unchanged(self):
        """AC-13: other 402s keep their existing shape."""
        from tfc.ee_gating import FeatureUnavailable

        response = custom_exception_handler(
            FeatureUnavailable("audit_logs"), context={}
        )
        assert response.status_code == 402
        assert "enterprise_gate" not in response.data
