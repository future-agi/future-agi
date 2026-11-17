"""A label whose stored settings fail today's checks can still be archived.

A stored row can hold settings that today's checks refuse. Archiving and
restoring write only the archive fields, so such a label archives and
restores, and its settings stay as stored. A save that writes settings still
checks them.
"""

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone
from rest_framework import status

from model_hub.models.develop_annotations import AnnotationsLabels
from model_hub.tests.test_annotation_labels_api import (
    create_label,
    detail_url,
    make_categorical_settings,
    make_numeric_settings,
    restore_url,
)

# Settings a stored row can hold that today's checks refuse, each next to
# valid settings of the same label type.
STORED_SETTINGS_FAILING_TODAYS_CHECKS = [
    pytest.param(
        "numeric",
        make_numeric_settings(),
        make_numeric_settings(min=-5, max=10),
        id="numeric-negative-min",
    ),
    pytest.param(
        "numeric",
        make_numeric_settings(),
        make_numeric_settings(step_size=0),
        id="numeric-zero-step",
    ),
    pytest.param(
        "categorical",
        make_categorical_settings(),
        make_categorical_settings(options=[{"label": "Pass"}, {"label": "pass"}]),
        id="categorical-duplicate-options",
    ),
    pytest.param(
        "categorical",
        make_categorical_settings(),
        make_categorical_settings(options=[{"label": "Good"}, {"label": " "}]),
        id="categorical-empty-option",
    ),
]


def _stored(label_id):
    return AnnotationsLabels.all_objects.get(pk=label_id)


def _store_label(auth_client, label_type, valid_settings, stored_settings, **fields):
    resp = create_label(
        auth_client,
        name=f"Stored {label_type} label",
        type=label_type,
        settings=valid_settings,
    )
    assert resp.status_code == status.HTTP_200_OK, resp.data
    label_id = resp.data["result"]["id"]
    AnnotationsLabels.all_objects.filter(pk=label_id).update(
        settings=stored_settings, **fields
    )
    with pytest.raises(ValidationError):
        _stored(label_id).full_clean()
    return label_id


@pytest.mark.django_db
class TestArchiveOfStoredSettingsThatFailTodaysChecks:
    @pytest.mark.parametrize(
        ("label_type", "valid_settings", "stored_settings"),
        STORED_SETTINGS_FAILING_TODAYS_CHECKS,
    )
    def test_archive_answers_204_and_keeps_the_stored_settings(
        self, auth_client, label_type, valid_settings, stored_settings
    ):
        label_id = _store_label(
            auth_client, label_type, valid_settings, stored_settings
        )

        resp = auth_client.delete(detail_url(label_id))

        assert resp.status_code == status.HTTP_204_NO_CONTENT, resp.data
        stored = _stored(label_id)
        assert stored.deleted is True
        assert stored.deleted_at is not None
        assert stored.settings == stored_settings

    @pytest.mark.parametrize(
        ("label_type", "valid_settings", "stored_settings"),
        STORED_SETTINGS_FAILING_TODAYS_CHECKS,
    )
    def test_restore_answers_200_and_keeps_the_stored_settings(
        self, auth_client, label_type, valid_settings, stored_settings
    ):
        label_id = _store_label(
            auth_client,
            label_type,
            valid_settings,
            stored_settings,
            deleted=True,
            deleted_at=timezone.now(),
        )
        # An uncaught error answers a 500 page instead of raising in the test.
        auth_client.raise_request_exception = False

        resp = auth_client.post(restore_url(label_id))

        assert resp.status_code == status.HTTP_200_OK, resp.content
        assert resp.data["result"]["archived"] is False
        stored = _stored(label_id)
        assert stored.deleted is False
        assert stored.deleted_at is None
        assert stored.settings == stored_settings

    def test_an_ordinary_label_archives_and_restores(self, auth_client):
        created = create_label(auth_client, name="Ordinary Archive")
        assert created.status_code == status.HTTP_200_OK, created.data
        label_id = created.data["result"]["id"]

        archived = auth_client.delete(detail_url(label_id))

        assert archived.status_code == status.HTTP_204_NO_CONTENT
        assert _stored(label_id).deleted is True

        restored = auth_client.post(restore_url(label_id))

        assert restored.status_code == status.HTTP_200_OK, restored.data
        assert restored.data["result"]["archived"] is False
        assert _stored(label_id).deleted is False

    def test_a_save_that_writes_settings_still_checks_them(self, auth_client):
        created = create_label(
            auth_client,
            name="Checked Settings",
            type="numeric",
            settings=make_numeric_settings(),
        )
        assert created.status_code == status.HTTP_200_OK, created.data
        label = _stored(created.data["result"]["id"])
        label.settings = make_numeric_settings(min=-5, max=10)

        with pytest.raises(ValidationError, match="min cannot be negative"):
            label.save(update_fields=["settings", "deleted", "updated_at"])

        assert _stored(label.pk).settings == make_numeric_settings()
