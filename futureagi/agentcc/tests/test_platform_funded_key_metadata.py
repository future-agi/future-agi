"""billing is reserved key metadata: only the platform sets it.

A key the platform pays for itself carries billing: platform_funded, which the
gateway reads to leave the key's spend out of the organization's gateway
budgets and managed credit. A tenant's key metadata with it is refused, and
the key sync never sends a stored value; the platform's own mint adds it when
it sends the key.
"""

import pytest
from rest_framework import serializers

from agentcc.serializers.api_key import KeyMetadataValidationMixin
from agentcc.services.auth_bridge import bound_key_metadata
from agentcc.validators import RESERVED_KEY_METADATA, reserved_key_metadata


def test_billing_is_reserved():
    assert "billing" in RESERVED_KEY_METADATA
    assert reserved_key_metadata({"billing": "platform_funded", "env": "prod"}) == [
        "billing"
    ]


def test_a_tenants_key_metadata_with_billing_is_refused():
    class TenantMetadata(KeyMetadataValidationMixin):
        context = {"key_organization_id": "org-1"}

    with pytest.raises(serializers.ValidationError):
        TenantMetadata().validate_metadata({"billing": "platform_funded"})


def test_key_sync_never_sends_a_stored_billing():
    bound = bound_key_metadata({"billing": "platform_funded", "env": "prod"}, "org-1")

    assert bound == {"env": "prod", "org_id": "org-1"}
