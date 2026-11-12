"""Creating a queue without labels says which field is missing."""

from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from conftest import create_categorical_label
from rest_framework import status

QUEUE_URL = "/model-hub/annotation-queues/"
MESSAGE = "At least one label is required. Add a label to this queue (API field: label_ids)."


@pytest.fixture(autouse=True)
def _bypass_entitlements():
    """Bypass EE feature and quota gates so the tests focus on validation."""
    with ExitStack() as stack:
        stack.enter_context(patch("tfc.ee_gating.check_ee_can_create"))
        stack.enter_context(patch("tfc.ee_gating.check_ee_feature"))
        stack.enter_context(
            patch(
                "ee.usage.services.entitlements.Entitlements.check_feature",
                return_value=SimpleNamespace(allowed=True, reason=None),
            )
        )
        stack.enter_context(
            patch(
                "ee.usage.services.entitlements.Entitlements.can_create",
                return_value=SimpleNamespace(allowed=True, reason=None),
            )
        )
        yield


@pytest.mark.django_db
class TestQueueLabelRequirementMessage:
    def test_missing_label_ids_names_the_field(self, auth_client):
        resp = auth_client.post(QUEUE_URL, {"name": "No labels"}, format="json")
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert MESSAGE in str(resp.data)

    def test_empty_label_ids_names_the_field(self, auth_client):
        resp = auth_client.post(
            QUEUE_URL, {"name": "Empty labels", "label_ids": []}, format="json"
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert MESSAGE in str(resp.data)

    def test_queue_with_a_label_is_still_created(self, auth_client):
        label_id = str(create_categorical_label(auth_client))
        resp = auth_client.post(
            QUEUE_URL,
            {"name": "With a label", "label_ids": [label_id]},
            format="json",
        )
        assert resp.status_code == status.HTTP_201_CREATED, resp.data
