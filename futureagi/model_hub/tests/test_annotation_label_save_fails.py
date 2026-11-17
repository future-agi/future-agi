"""A label save or delete that fails reaches the error answer.

The stored label stays as it was, and the answer says the change did not
happen. Refusals keep their 400 sentence, and a database outage keeps the
503 answer.
"""

from unittest.mock import patch

import pytest
from django.db import OperationalError
from rest_framework import status

from model_hub.models.develop_annotations import AnnotationsLabels
from model_hub.tests.test_annotation_labels_api import (
    create_label,
    detail_url,
    make_numeric_settings,
)

UPDATE_FAILED = "Unable to update annotation label. Please try again."
DELETE_FAILED = "Unable to delete annotation label. Please try again."
DATABASE_UNAVAILABLE = "Database temporarily unavailable."
DUPLICATE_NAME = "already exists"


def _create_label_id(auth_client, name, **overrides):
    resp = create_label(auth_client, name=name, **overrides)
    assert resp.status_code == status.HTTP_200_OK, resp.data
    return resp.data["result"]["id"]


def _stored(label_id):
    return AnnotationsLabels.all_objects.get(pk=label_id)


@pytest.mark.django_db
class TestLabelUpdateThatFails:
    def test_a_save_that_fails_answers_the_update_error_and_keeps_the_stored_name(
        self, auth_client
    ):
        label_id = _create_label_id(auth_client, "Before Rename")

        with patch.object(
            AnnotationsLabels, "full_clean", side_effect=RuntimeError("boom")
        ):
            resp = auth_client.patch(
                detail_url(label_id), {"name": "After Rename"}, format="json"
            )

        assert resp.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR, resp.data
        assert resp.data["status"] is False
        assert resp.data["code"] == "server_error"
        assert resp.data["message"] == UPDATE_FAILED
        assert "updated successfully" not in str(resp.data)
        assert "After Rename" not in str(resp.data)
        assert _stored(label_id).name == "Before Rename"

    def test_a_database_outage_during_a_save_answers_503(self, auth_client):
        label_id = _create_label_id(auth_client, "Before Outage")

        with patch.object(
            AnnotationsLabels,
            "save",
            side_effect=OperationalError("server closed the connection"),
        ):
            resp = auth_client.patch(
                detail_url(label_id), {"name": "After Outage"}, format="json"
            )

        assert resp.status_code == status.HTTP_503_SERVICE_UNAVAILABLE, resp.data
        assert resp.data["code"] == "service_unavailable"
        assert resp.data["message"] == DATABASE_UNAVAILABLE
        assert "server closed the connection" not in str(resp.data)
        assert "After Outage" not in str(resp.data)
        assert _stored(label_id).name == "Before Outage"

    def test_an_ordinary_rename_answers_200_with_the_new_name(self, auth_client):
        label_id = _create_label_id(auth_client, "Plain Before")

        resp = auth_client.patch(
            detail_url(label_id), {"name": "Plain After"}, format="json"
        )

        assert resp.status_code == status.HTTP_200_OK, resp.data
        assert resp.data["name"] == "Plain After"
        assert _stored(label_id).name == "Plain After"

    def test_a_duplicate_name_answers_400_with_its_sentence(self, auth_client):
        _create_label_id(auth_client, "Taken Name")
        label_id = _create_label_id(auth_client, "Free Name")

        resp = auth_client.patch(
            detail_url(label_id), {"name": "Taken Name"}, format="json"
        )

        assert resp.status_code == status.HTTP_400_BAD_REQUEST, resp.data
        assert DUPLICATE_NAME in resp.data["message"]
        assert UPDATE_FAILED not in str(resp.data)
        assert _stored(label_id).name == "Free Name"

    def test_settings_the_label_refuses_answer_400_with_their_sentence(
        self, auth_client
    ):
        label_id = _create_label_id(
            auth_client,
            "Numeric Bounds",
            type="numeric",
            settings=make_numeric_settings(),
        )

        resp = auth_client.patch(
            detail_url(label_id),
            {"settings": make_numeric_settings(min=-1)},
            format="json",
        )

        assert resp.status_code == status.HTTP_400_BAD_REQUEST, resp.data
        assert "min cannot be negative" in str(resp.data)
        assert UPDATE_FAILED not in str(resp.data)
        assert _stored(label_id).settings["min"] == 0


@pytest.mark.django_db
class TestLabelDeleteThatFails:
    def test_a_delete_that_fails_answers_the_delete_error_and_keeps_the_label(
        self, auth_client
    ):
        label_id = _create_label_id(auth_client, "Kept Label")

        with patch.object(
            AnnotationsLabels, "delete", side_effect=RuntimeError("boom")
        ):
            resp = auth_client.delete(detail_url(label_id))

        assert resp.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR, resp.data
        assert resp.data["status"] is False
        assert resp.data["code"] == "server_error"
        assert resp.data["message"] == DELETE_FAILED
        assert _stored(label_id).deleted is False

    def test_a_database_outage_during_a_delete_answers_503(self, auth_client):
        label_id = _create_label_id(auth_client, "Kept Through Outage")

        with patch.object(
            AnnotationsLabels,
            "delete",
            side_effect=OperationalError("server closed the connection"),
        ):
            resp = auth_client.delete(detail_url(label_id))

        assert resp.status_code == status.HTTP_503_SERVICE_UNAVAILABLE, resp.data
        assert resp.data["code"] == "service_unavailable"
        assert resp.data["message"] == DATABASE_UNAVAILABLE
        assert _stored(label_id).deleted is False

    def test_an_ordinary_delete_answers_204_and_archives_the_label(self, auth_client):
        label_id = _create_label_id(auth_client, "Plain Delete")

        resp = auth_client.delete(detail_url(label_id))

        assert resp.status_code == status.HTTP_204_NO_CONTENT
        stored = _stored(label_id)
        assert stored.deleted is True
        assert stored.deleted_at is not None
