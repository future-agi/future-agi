"""Tests for the post_migrate auto-seed of default prompt labels (TH-7261).

Fresh databases had no Production/Staging/Development system labels because
nothing ever called PromptLabel.create_default_system_labels() — the Add Tags
modal showed "No labels found". The seed now runs after every migrate via a
post_migrate hook in ModelHubConfig.
"""

import pytest
from rest_framework import status as http_status

from model_hub.apps import _seed_prompt_labels_after_migrate
from model_hub.models.prompt_label import LabelTypeChoices, PromptLabel

DEFAULT_LABEL_NAMES = {"Production", "Staging", "Development"}


def _global_system_labels():
    return PromptLabel.no_workspace_objects.filter(
        organization__isnull=True, type=LabelTypeChoices.SYSTEM.value
    )


@pytest.mark.unit
class TestSeedDefaultPromptLabels:
    def test_post_migrate_creates_default_labels(self, db):
        _seed_prompt_labels_after_migrate(sender=None)
        assert (
            set(_global_system_labels().values_list("name", flat=True))
            == DEFAULT_LABEL_NAMES
        )

    def test_post_migrate_is_idempotent(self, db):
        _seed_prompt_labels_after_migrate(sender=None)
        _seed_prompt_labels_after_migrate(sender=None)
        assert _global_system_labels().count() == len(DEFAULT_LABEL_NAMES)

    def test_hook_is_connected_to_post_migrate(self):
        from django.db.models.signals import post_migrate

        # receivers entries are (lookup_key, receiver, ...) where
        # lookup_key is (dispatch_uid_or_id, sender_id)
        receiver_ids = {entry[0][0] for entry in post_migrate.receivers}
        assert "model_hub_seed_default_prompt_labels" in receiver_ids


@pytest.mark.integration
@pytest.mark.api
class TestSeededLabelsVisibleInListEndpoint:
    def test_seeded_labels_returned_by_prompt_labels_list(self, auth_client, db):
        """GET /model-hub/prompt-labels/ feeds the Add Tags modal — seeded
        system labels must show up for every org/workspace."""
        _seed_prompt_labels_after_migrate(sender=None)

        response = auth_client.get("/model-hub/prompt-labels/")
        assert response.status_code == http_status.HTTP_200_OK

        payload = response.json()
        rows = payload.get("results") or payload.get("result") or payload
        names = {row["name"] for row in rows}
        assert DEFAULT_LABEL_NAMES <= names
