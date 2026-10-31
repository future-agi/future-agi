"""check_if_dataset_creation_is_allowed must consult Entitlements.can_create.

Its exception handler failed open, so a broken import inside the try (the
pre-relocation ``usage.services.entitlements`` path) silently allowed every
dataset creation instead of enforcing the plan limit. On cloud the dataset
quota is billing, so an error in the check now refuses the creation;
self-hosted deployments have no count limits and keep creating.
"""

from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
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
from model_hub.utils.dataset_limit import DatasetLimitCheck, DatasetLimitOutcome
from tfc.constants.api_calls import APICallStatusChoices, APICallTypeChoices
from tfc.utils.error_codes import get_error_message


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
        check = check_if_dataset_creation_is_allowed(organization)

    can_create.assert_called_once_with(str(organization.id), "datasets", 0)
    assert check == DatasetLimitCheck(DatasetLimitOutcome.LIMIT_REACHED, limit=3)


@pytest.mark.django_db
def test_allowed_entitlement_permits_dataset_creation(organization):
    with patch(
        "ee.usage.services.entitlements.Entitlements.can_create",
        return_value=CheckResult(allowed=True),
    ) as can_create:
        assert check_if_dataset_creation_is_allowed(organization) == (
            DatasetLimitCheck(DatasetLimitOutcome.ALLOWED)
        )

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
        check = check_if_dataset_creation_is_allowed(organization)

    assert check == DatasetLimitCheck(DatasetLimitOutcome.UNVERIFIED)


@pytest.mark.django_db
def test_entitlement_error_allows_dataset_creation_self_hosted(organization):
    with (
        patch("ee.usage.deployment.DeploymentMode.is_cloud", return_value=False),
        patch(
            "ee.usage.services.entitlements.Entitlements.can_create",
            side_effect=RedisError("entitlement cache unavailable"),
        ),
    ):
        assert check_if_dataset_creation_is_allowed(organization) == (
            DatasetLimitCheck(DatasetLimitOutcome.ALLOWED)
        )


@pytest.mark.django_db
def test_refusal_without_a_limit_value_reports_zero(organization):
    """A refusal that carries no limit value reports 0, as the pair did."""
    denied = CheckResult(allowed=False, error_code="ENTITLEMENT_LIMIT")

    with patch(
        "ee.usage.services.entitlements.Entitlements.can_create",
        return_value=denied,
    ):
        check = check_if_dataset_creation_is_allowed(organization)

    assert check == DatasetLimitCheck(DatasetLimitOutcome.LIMIT_REACHED, limit=0)


@pytest.fixture
def dataset_add_usage_rows(db):
    """Rows log_and_deduct_cost_for_resource_request needs to reach the check.

    post_migrate usually seeds them; without them the dispatcher returns None
    and every route refuses before the entitlement check runs.
    """
    SubscriptionTier.objects.get_or_create(
        name=SubscriptionTierChoices.FREE.value,
        defaults={"wallet_refill_amount": 0},
    )
    APICallType.objects.get_or_create(name=APICallTypeChoices.DATASET_ADD.value)


def _create_empty_dataset(auth_client, name):
    return auth_client.post(
        "/model-hub/develops/create-empty-dataset/",
        {"new_dataset_name": name, "model_type": "GenerativeLLM"},
        format="json",
    )


def _dataset_add_logs(organization):
    return APICallLog.objects.filter(
        organization=organization,
        api_call_type__name=APICallTypeChoices.DATASET_ADD.value,
    )


@pytest.mark.django_db
def test_entitlement_error_on_cloud_asks_to_retry_without_limit_alert(
    auth_client, organization, dataset_add_usage_rows
):
    """A failed check is not a reached limit: no upgrade alert, no limit row."""
    with (
        patch("ee.usage.deployment.DeploymentMode.is_cloud", return_value=True),
        patch(
            "ee.usage.services.entitlements.Entitlements.can_create",
            side_effect=RedisError("entitlement cache unavailable"),
        ),
        patch("ee.usage.utils.usage_entries.call_websocket") as websocket,
    ):
        response = _create_empty_dataset(auth_client, "Unverified Dataset")

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert response.json()["code"] == "dataset_limit_check_failed"
    assert response.json()["message"] == get_error_message("DATASET_LIMIT_CHECK_FAILED")
    websocket.assert_not_called()
    assert not _dataset_add_logs(organization).exists()
    assert not Dataset.no_workspace_objects.filter(
        name="Unverified Dataset", organization=organization
    ).exists()


@pytest.mark.django_db
def test_entitlement_error_self_hosted_still_creates(
    auth_client, organization, dataset_add_usage_rows
):
    with (
        patch("ee.usage.deployment.DeploymentMode.is_cloud", return_value=False),
        patch(
            "ee.usage.services.entitlements.Entitlements.can_create",
            side_effect=RedisError("entitlement cache unavailable"),
        ),
        patch("ee.usage.utils.usage_entries.call_websocket"),
    ):
        response = _create_empty_dataset(auth_client, "Self Hosted Dataset")

    assert response.status_code == status.HTTP_200_OK
    assert Dataset.no_workspace_objects.filter(
        name="Self Hosted Dataset", organization=organization
    ).exists()
    assert _dataset_add_logs(organization).get().status == (
        APICallStatusChoices.SUCCESS.value
    )


@pytest.mark.django_db
def test_reached_limit_keeps_limit_response_and_upgrade_alert(
    auth_client, organization, dataset_add_usage_rows
):
    denied = CheckResult(allowed=False, error_code="ENTITLEMENT_LIMIT", limit=3)

    with (
        patch("ee.usage.deployment.DeploymentMode.is_cloud", return_value=True),
        patch(
            "ee.usage.services.entitlements.Entitlements.can_create",
            return_value=denied,
        ),
        patch("ee.usage.utils.usage_entries.call_websocket") as websocket,
    ):
        response = _create_empty_dataset(auth_client, "Over Limit Dataset")

    assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    assert response.json()["message"] == get_error_message(
        "DATASET_CREATE_LIMIT_REACHED"
    )
    websocket.assert_called_once()
    alert = websocket.call_args.kwargs["message"]["alert_description"]
    assert "supports only 3 dataset" in alert
    assert _dataset_add_logs(organization).get().status == (
        APICallStatusChoices.RESOURCE_LIMIT.value
    )


@pytest.mark.django_db
def test_entitlement_error_on_cloud_lets_sdk_file_upload_through(
    auth_client, organization, dataset_add_usage_rows, monkeypatch
):
    """SDK uploads are not held to the dataset limit, so an unverifiable one
    does not stop them either (as before the check failed closed)."""
    from ee.usage.utils.usage_entries import log_and_deduct_cost_for_resource_request

    class _RowAddAllowed:
        status = APICallStatusChoices.PROCESSING.value

        def save(self):
            return None

    def dataset_add_only(organization, api_call_type, **kwargs):
        if api_call_type == APICallTypeChoices.ROW_ADD.value:
            return _RowAddAllowed()
        return log_and_deduct_cost_for_resource_request(
            organization, api_call_type, **kwargs
        )

    module = "model_hub.views.datasets.create.file_upload"
    monkeypatch.setattr(
        f"{module}.log_and_deduct_cost_for_resource_request", dataset_add_only
    )
    monkeypatch.setattr(
        f"{module}.upload_file_to_minio", lambda *args, **kwargs: "minio://sdk.csv"
    )
    monkeypatch.setattr(
        f"{module}.process_dataset_from_file.delay", lambda *args, **kwargs: None
    )

    with (
        patch("ee.usage.deployment.DeploymentMode.is_cloud", return_value=True),
        patch(
            "ee.usage.services.entitlements.Entitlements.can_create",
            side_effect=RedisError("entitlement cache unavailable"),
        ),
    ):
        response = auth_client.post(
            "/model-hub/develops/create-dataset-from-local-file/",
            {
                "new_dataset_name": "Unverified SDK Dataset",
                "model_type": "GenerativeLLM",
                "source": "sdk",
                "file": SimpleUploadedFile(
                    "sdk.csv", b"input,output\nhello,world\n", "text/csv"
                ),
            },
            format="multipart",
        )

    assert response.status_code == status.HTTP_200_OK, response.content
    assert Dataset.no_workspace_objects.filter(
        name="Unverified SDK Dataset", organization=organization
    ).exists()


def _upload_sdk_file(auth_client, monkeypatch, name):
    """POST an SDK file upload; only the DATASET_ADD entry runs for real."""
    from ee.usage.utils.usage_entries import log_and_deduct_cost_for_resource_request

    class _RowAddAllowed:
        status = APICallStatusChoices.PROCESSING.value

        def save(self):
            return None

    def dataset_add_only(organization, api_call_type, **kwargs):
        if api_call_type == APICallTypeChoices.ROW_ADD.value:
            return _RowAddAllowed()
        return log_and_deduct_cost_for_resource_request(
            organization, api_call_type, **kwargs
        )

    module = "model_hub.views.datasets.create.file_upload"
    monkeypatch.setattr(
        f"{module}.log_and_deduct_cost_for_resource_request", dataset_add_only
    )
    monkeypatch.setattr(
        f"{module}.upload_file_to_minio", lambda *args, **kwargs: "minio://sdk.csv"
    )
    monkeypatch.setattr(
        f"{module}.process_dataset_from_file.delay", lambda *args, **kwargs: None
    )
    return auth_client.post(
        "/model-hub/develops/create-dataset-from-local-file/",
        {
            "new_dataset_name": name,
            "model_type": "GenerativeLLM",
            "source": "sdk",
            "file": SimpleUploadedFile(
                "sdk.csv", b"input,output\nhello,world\n", "text/csv"
            ),
        },
        format="multipart",
    )


@pytest.mark.django_db
def test_reached_limit_lets_sdk_file_upload_through_without_alert(
    auth_client, organization, dataset_add_usage_rows, monkeypatch
):
    """SDK uploads are not held to a reached limit either.

    The usage entry still records the limit hit (then marked SUCCESS by the
    route, as before) and no upgrade alert is pushed to the SDK caller's org.
    """
    denied = CheckResult(allowed=False, error_code="ENTITLEMENT_LIMIT", limit=3)

    with (
        patch("ee.usage.deployment.DeploymentMode.is_cloud", return_value=True),
        patch(
            "ee.usage.services.entitlements.Entitlements.can_create",
            return_value=denied,
        ),
        patch("ee.usage.utils.usage_entries.call_websocket") as websocket,
    ):
        response = _upload_sdk_file(auth_client, monkeypatch, "Over Limit SDK Dataset")

    assert response.status_code == status.HTTP_200_OK, response.content
    websocket.assert_not_called()
    row = _dataset_add_logs(organization).get()
    assert row.status == APICallStatusChoices.SUCCESS.value
    assert '"limit": 3' in row.config


@pytest.mark.django_db
def test_sdk_exemption_is_decided_by_one_policy(
    auth_client, organization, dataset_add_usage_rows, monkeypatch
):
    """Both SDK paths (unverified and reached) consult the same policy.

    Flip the policy so the limit binds SDK uploads too: the dispatcher then
    refuses an unverified SDK upload (503) and the refusal helper refuses a
    reached one (429). Before, each path carried its own copy of the rule.
    """
    monkeypatch.setattr(
        "model_hub.utils.dataset_limit.dataset_limit_binds",
        lambda sdk_source: True,
    )

    with (
        patch("ee.usage.deployment.DeploymentMode.is_cloud", return_value=True),
        patch(
            "ee.usage.services.entitlements.Entitlements.can_create",
            side_effect=RedisError("entitlement cache unavailable"),
        ),
    ):
        unverified = _upload_sdk_file(auth_client, monkeypatch, "Unverified SDK")

    with (
        patch("ee.usage.deployment.DeploymentMode.is_cloud", return_value=True),
        patch(
            "ee.usage.services.entitlements.Entitlements.can_create",
            return_value=CheckResult(
                allowed=False, error_code="ENTITLEMENT_LIMIT", limit=3
            ),
        ),
        patch("ee.usage.utils.usage_entries.call_websocket"),
    ):
        reached = _upload_sdk_file(auth_client, monkeypatch, "Reached SDK")

    assert unverified.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert reached.status_code == status.HTTP_429_TOO_MANY_REQUESTS
