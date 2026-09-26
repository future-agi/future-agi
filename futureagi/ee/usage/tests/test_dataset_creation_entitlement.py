"""check_if_dataset_creation_is_allowed must consult Entitlements.can_create.

Its exception handler failed open, so a broken import inside the try (the
pre-relocation ``usage.services.entitlements`` path) silently allowed every
dataset creation instead of enforcing the plan limit. On cloud the dataset
quota is billing, so an error in the check now refuses the creation;
self-hosted deployments have no count limits and keep creating.
"""

from unittest.mock import patch

import pytest
from redis.exceptions import RedisError
from rest_framework import status

from accounts.models import Organization
from ee.usage.models.usage import (
    APICallLog,
    APICallType,
    SubscriptionTier,
    SubscriptionTierChoices,
)
from ee.usage.schemas.events import CheckResult
from ee.usage.utils.usage_entries import check_if_dataset_creation_is_allowed
from model_hub.models.develop_dataset import Dataset
from tfc.constants.api_calls import APICallStatusChoices, APICallTypeChoices


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


@pytest.mark.django_db
def test_entitlement_error_blocks_dataset_creation_on_cloud(organization):
    with (
        patch("ee.usage.deployment.DeploymentMode.is_cloud", return_value=True),
        patch(
            "ee.usage.services.entitlements.Entitlements.can_create",
            side_effect=RedisError("entitlement cache unavailable"),
        ),
    ):
        allowed, detail = check_if_dataset_creation_is_allowed(organization)

    assert allowed is False
    assert detail["resource_name"] == "dataset"
    assert "try again" in detail["reason"]


@pytest.mark.django_db
def test_entitlement_error_allows_dataset_creation_self_hosted(organization):
    with (
        patch("ee.usage.deployment.DeploymentMode.is_cloud", return_value=False),
        patch(
            "ee.usage.services.entitlements.Entitlements.can_create",
            side_effect=RedisError("entitlement cache unavailable"),
        ),
    ):
        assert check_if_dataset_creation_is_allowed(organization) == (True, {})


@pytest.fixture
def dataset_add_usage_rows(db):
    """Rows log_and_deduct_cost_for_resource_request needs to reach the check.

    post_migrate usually seeds them; without them the dispatcher returns None
    and every route answers 429 before the entitlement check runs.
    """
    SubscriptionTier.objects.get_or_create(
        name=SubscriptionTierChoices.FREE.value,
        defaults={"wallet_refill_amount": 0},
    )
    APICallType.objects.get_or_create(name=APICallTypeChoices.DATASET_ADD.value)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("is_cloud", "expected_status"),
    [(True, status.HTTP_429_TOO_MANY_REQUESTS), (False, status.HTTP_200_OK)],
)
def test_entitlement_error_on_create_empty_dataset_route(
    auth_client, organization, dataset_add_usage_rows, is_cloud, expected_status
):
    with (
        patch("ee.usage.deployment.DeploymentMode.is_cloud", return_value=is_cloud),
        patch(
            "ee.usage.services.entitlements.Entitlements.can_create",
            side_effect=RedisError("entitlement cache unavailable"),
        ),
        patch("ee.usage.utils.usage_entries.call_websocket"),
    ):
        response = auth_client.post(
            "/model-hub/develops/create-empty-dataset/",
            {"new_dataset_name": "Unverified Dataset", "model_type": "GenerativeLLM"},
            format="json",
        )

    assert response.status_code == expected_status
    created = Dataset.no_workspace_objects.filter(
        name="Unverified Dataset", organization=organization
    ).exists()
    assert created is not is_cloud
    call_log = APICallLog.objects.get(
        organization=organization,
        api_call_type__name=APICallTypeChoices.DATASET_ADD.value,
    )
    assert call_log.status == (
        APICallStatusChoices.RESOURCE_LIMIT.value
        if is_cloud
        else APICallStatusChoices.SUCCESS.value
    )
