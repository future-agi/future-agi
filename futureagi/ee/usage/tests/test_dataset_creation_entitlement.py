"""check_if_dataset_creation_is_allowed must consult Entitlements.can_create.

Its exception handler fails open, so a broken import inside the try (the
pre-relocation ``usage.services.entitlements`` path) silently allowed every
dataset creation instead of enforcing the plan limit.
"""

from unittest.mock import patch

import pytest

from accounts.models import Organization
from ee.usage.schemas.events import CheckResult
from ee.usage.utils.usage_entries import check_if_dataset_creation_is_allowed


@pytest.fixture
def organization(db):
    return Organization.objects.create(name="Dataset Limit Org")


@pytest.mark.django_db
def test_denied_entitlement_blocks_dataset_creation(organization):
    denied = CheckResult(allowed=False, error_code="ENTITLEMENT_LIMIT", limit=3)

    with patch(
        "ee.usage.services.entitlements.Entitlements.can_create",
        return_value=denied,
    ) as can_create:
        allowed, detail = check_if_dataset_creation_is_allowed(organization)

    can_create.assert_called_once_with(str(organization.id), "datasets", 0)
    assert allowed is False
    assert detail == {"resource_name": "dataset", "limit": 3}


@pytest.mark.django_db
def test_allowed_entitlement_permits_dataset_creation(organization):
    with patch(
        "ee.usage.services.entitlements.Entitlements.can_create",
        return_value=CheckResult(allowed=True),
    ) as can_create:
        assert check_if_dataset_creation_is_allowed(organization) == (True, {})

    can_create.assert_called_once()
