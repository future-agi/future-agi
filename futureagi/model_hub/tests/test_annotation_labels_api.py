"""
Phase 1A – Annotation Labels API Tests.

Tests cover:
- List labels (with filters, search, ordering)
- Create labels (all 5 types + validation)
- Update labels
- Archive (soft delete) & Restore
"""

import uuid

import pytest
from rest_framework import status

BASE_URL = "/model-hub/annotations-labels/"


def detail_url(label_id):
    return f"{BASE_URL}{label_id}/"


def restore_url(label_id):
    return f"{BASE_URL}{label_id}/restore/"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_categorical_settings(**overrides):
    defaults = {
        "options": [{"label": "Good"}, {"label": "Bad"}],
        "multi_choice": False,
        "rule_prompt": "",
        "auto_annotate": False,
        "strategy": None,
    }
    defaults.update(overrides)
    return defaults


def make_numeric_settings(**overrides):
    defaults = {
        "min": 0,
        "max": 100,
        "step_size": 1,
        "display_type": "slider",
    }
    defaults.update(overrides)
    return defaults


def make_text_settings(**overrides):
    defaults = {
        "placeholder": "Enter text",
        "min_length": 0,
        "max_length": 1000,
    }
    defaults.update(overrides)
    return defaults


def make_star_settings(**overrides):
    defaults = {"no_of_stars": 5}
    defaults.update(overrides)
    return defaults


def create_label(auth_client, **overrides):
    """Helper to create a label via the API and return the response."""
    payload = {
        "name": overrides.pop("name", "Test Label"),
        "type": overrides.pop("type", "categorical"),
        "settings": overrides.pop("settings", make_categorical_settings()),
    }
    payload.update(overrides)
    return auth_client.post(BASE_URL, payload, format="json")


# ---------------------------------------------------------------------------
# 1.1 – List Labels
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestListLabels:
    """Tests for GET /model-hub/annotations-labels/"""

    def test_list_all_labels_empty(self, auth_client):
        """TC-1: List with no labels returns empty paginated result."""
        resp = auth_client.get(BASE_URL)
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["count"] == 0
        assert resp.data["results"] == []

    def test_list_all_labels(self, auth_client):
        """TC-1: List all labels returns paginated list."""
        create_label(auth_client, name="Label A")
        create_label(
            auth_client, name="Label B", type="text", settings=make_text_settings()
        )
        resp = auth_client.get(BASE_URL)
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["count"] == 2
        assert len(resp.data["results"]) == 2

    def test_filter_by_type(self, auth_client):
        """TC-2: Filter by type=categorical returns only categorical labels."""
        create_label(auth_client, name="Cat Label", type="categorical")
        create_label(
            auth_client, name="Text Label", type="text", settings=make_text_settings()
        )
        resp = auth_client.get(BASE_URL, {"type": "categorical"})
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["count"] == 1
        names = [r["name"] for r in resp.data["results"]]
        assert "Cat Label" in names
        assert "Text Label" not in names

    def test_search_by_name(self, auth_client):
        """TC-3: Search by name returns matching labels."""
        create_label(auth_client, name="Quality Check")
        create_label(auth_client, name="Other Label")
        resp = auth_client.get(BASE_URL, {"search": "quality"})
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["count"] == 1
        assert resp.data["results"][0]["name"] == "Quality Check"

    def test_include_usage_count(self, auth_client):
        """TC-4: include_usage_count=true adds annotation count fields."""
        create_label(auth_client, name="Usage Label")
        resp = auth_client.get(BASE_URL, {"include_usage_count": "true"})
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["count"] == 1
        result = resp.data["results"][0]
        assert "trace_annotations_count" in result
        assert "annotation_count" in result

    def test_combined_filters(self, auth_client):
        """TC-5: Combined type + search filters return intersection."""
        create_label(auth_client, name="Feedback Cat", type="categorical")
        create_label(
            auth_client,
            name="Feedback Text",
            type="text",
            settings=make_text_settings(),
        )
        create_label(auth_client, name="Other Cat", type="categorical")
        resp = auth_client.get(BASE_URL, {"type": "categorical", "search": "feedback"})
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["count"] == 1
        assert resp.data["results"][0]["name"] == "Feedback Cat"

    def test_empty_search_result(self, auth_client):
        """TC-6: Search for nonexistent returns 200 with empty results."""
        create_label(auth_client, name="Existing")
        resp = auth_client.get(BASE_URL, {"search": "nonexistent"})
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["count"] == 0

    def test_results_ordered_by_created_at_desc(self, auth_client):
        """TC-7: Results are ordered by created_at descending."""
        create_label(auth_client, name="First")
        create_label(
            auth_client, name="Second", type="text", settings=make_text_settings()
        )
        resp = auth_client.get(BASE_URL)
        assert resp.status_code == status.HTTP_200_OK
        results = resp.data["results"]
        assert len(results) == 2
        # Most recent first
        assert results[0]["name"] == "Second"
        assert results[1]["name"] == "First"


# ---------------------------------------------------------------------------
# 1.2 – Create Label
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCreateLabel:
    """Tests for POST /model-hub/annotations-labels/"""

    def test_create_categorical_label(self, auth_client):
        """TC-8: Create categorical label with options."""
        resp = create_label(
            auth_client,
            name="Sentiment",
            type="categorical",
            settings=make_categorical_settings(
                options=[
                    {"label": "Positive"},
                    {"label": "Negative"},
                    {"label": "Neutral"},
                ],
                multi_choice=True,
            ),
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["status"] is True

    def test_create_returns_created_label_object(self, auth_client):
        """Create responds with the serialized label under ``result``."""
        resp = create_label(
            auth_client,
            name="Echoed Label",
            type="categorical",
            settings=make_categorical_settings(),
        )

        assert resp.status_code == status.HTTP_200_OK
        result = resp.data["result"]
        assert result["name"] == "Echoed Label"
        assert result["type"] == "categorical"
        assert result["id"]

    def test_create_numeric_label(self, auth_client):
        """TC-9: Create numeric label with min/max/step."""
        resp = create_label(
            auth_client,
            name="Score",
            type="numeric",
            settings=make_numeric_settings(min=0, max=100, step_size=5),
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["status"] is True

    @pytest.mark.parametrize(
        ("settings", "message"),
        [
            (make_numeric_settings(min=-1), "min cannot be negative"),
            (make_numeric_settings(max=-1), "max cannot be negative"),
            (make_numeric_settings(step_size=0), "step_size must be greater than 0"),
        ],
    )
    def test_create_numeric_label_rejects_negative_bounds_and_zero_step(
        self,
        auth_client,
        settings,
        message,
    ):
        resp = create_label(
            auth_client,
            name=f"Invalid Numeric {uuid.uuid4()}",
            type="numeric",
            settings=settings,
        )

        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert message in str(resp.data)

    def test_create_text_label(self, auth_client):
        """TC-10: Create text label with placeholder."""
        resp = create_label(
            auth_client,
            name="Comment",
            type="text",
            settings=make_text_settings(placeholder="Write your comment"),
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["status"] is True

    def test_create_text_label_rejects_invalid_length_range(self, auth_client):
        resp = create_label(
            auth_client,
            name=f"Invalid Text {uuid.uuid4()}",
            type="text",
            settings=make_text_settings(min_length=10, max_length=10),
        )

        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "min_length must be less than max_length" in str(resp.data)

    def test_create_categorical_label_rejects_duplicate_option_names(
        self,
        auth_client,
    ):
        resp = create_label(
            auth_client,
            name=f"Duplicate Options {uuid.uuid4()}",
            type="categorical",
            settings=make_categorical_settings(
                options=[
                    {"label": "Pass"},
                    {"label": "pass"},
                ],
            ),
        )

        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "Categorical option labels must be unique" in str(resp.data)

    def test_create_star_label(self, auth_client):
        """TC-11: Create star label."""
        resp = create_label(
            auth_client,
            name="Rating",
            type="star",
            settings=make_star_settings(no_of_stars=5),
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["status"] is True

    def test_create_thumbs_up_down_label(self, auth_client):
        """TC-12: Create thumbs_up_down label with empty settings."""
        resp = create_label(
            auth_client,
            name="Thumbs",
            type="thumbs_up_down",
            settings={},
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["status"] is True

    def test_create_missing_name(self, auth_client):
        """TC-13: Missing name returns 400."""
        resp = auth_client.post(
            BASE_URL,
            {"type": "categorical", "settings": make_categorical_settings()},
            format="json",
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_invalid_type(self, auth_client):
        """TC-14: Invalid type returns 400."""
        resp = create_label(auth_client, name="Bad Type", type="invalid_type")
        assert resp.status_code == status.HTTP_400_BAD_REQUEST


# ---------------------------------------------------------------------------
# 1.3 – Update Label
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUpdateLabel:
    """Tests for PATCH /model-hub/annotations-labels/{id}/"""

    def _create_and_get_id(self, auth_client):
        """Create a label and return its ID."""
        create_label(auth_client, name="Editable")
        resp = auth_client.get(BASE_URL)
        return resp.data["results"][0]["id"]

    def test_update_label_name(self, auth_client):
        """TC-15: Update label name."""
        label_id = self._create_and_get_id(auth_client)
        resp = auth_client.patch(
            detail_url(label_id), {"name": "Updated Name"}, format="json"
        )
        assert resp.status_code == status.HTTP_200_OK
        # Verify the change
        get_resp = auth_client.get(detail_url(label_id))
        assert get_resp.status_code == status.HTTP_200_OK
        assert get_resp.data["name"] == "Updated Name"

    def test_update_label_settings(self, auth_client):
        """TC-16: Update label settings."""
        label_id = self._create_and_get_id(auth_client)
        new_settings = make_categorical_settings(
            options=[{"label": "A"}, {"label": "B"}, {"label": "C"}],
        )
        resp = auth_client.patch(
            detail_url(label_id), {"settings": new_settings}, format="json"
        )
        assert resp.status_code == status.HTTP_200_OK

    def test_update_nonexistent_label(self, auth_client):
        """TC-17: Update non-existent label returns 404."""
        fake_id = uuid.uuid4()
        resp = auth_client.patch(detail_url(fake_id), {"name": "Nope"}, format="json")
        assert resp.status_code == status.HTTP_404_NOT_FOUND


# ---------------------------------------------------------------------------
# 1.4 – Archive (Soft Delete) & Restore
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestArchiveAndRestore:
    """Tests for DELETE and POST /restore/ on annotation labels."""

    def _create_and_get_id(self, auth_client):
        create_label(auth_client, name="Archivable")
        resp = auth_client.get(BASE_URL)
        return resp.data["results"][0]["id"]

    def test_archive_label(self, auth_client):
        """TC-18: Archive (soft delete) a label."""
        label_id = self._create_and_get_id(auth_client)
        resp = auth_client.delete(detail_url(label_id))
        assert resp.status_code in (status.HTTP_200_OK, status.HTTP_204_NO_CONTENT)

    def test_archived_label_hidden_from_list(self, auth_client):
        """TC-19: Archived label is not returned in list."""
        label_id = self._create_and_get_id(auth_client)
        auth_client.delete(detail_url(label_id))
        resp = auth_client.get(BASE_URL)
        assert resp.status_code == status.HTTP_200_OK
        ids = [r["id"] for r in resp.data["results"]]
        assert str(label_id) not in [str(i) for i in ids]

    def test_archived_filter_lists_only_archived_labels(self, auth_client):
        """Archived labels are discoverable for restore without mixing active labels."""
        label_id = self._create_and_get_id(auth_client)
        auth_client.delete(detail_url(label_id))
        create_label(auth_client, name="Still Active")
        active_list_resp = auth_client.get(BASE_URL, {"search": "Still Active"})
        active_id = active_list_resp.data["results"][0]["id"]

        resp = auth_client.get(BASE_URL, {"archived": "true"})

        assert resp.status_code == status.HTTP_200_OK
        results = resp.data["results"]
        ids = [str(r["id"]) for r in results]
        assert str(label_id) in ids
        assert str(active_id) not in ids
        archived_label = next(r for r in results if str(r["id"]) == str(label_id))
        assert archived_label["archived"] is True

    def test_restore_archived_label(self, auth_client):
        """TC-20: Restore a soft-deleted label."""
        label_id = self._create_and_get_id(auth_client)
        auth_client.delete(detail_url(label_id))
        # Restore
        resp = auth_client.post(restore_url(label_id))
        assert resp.status_code == status.HTTP_200_OK
        # Verify it's back in the list
        list_resp = auth_client.get(BASE_URL)
        ids = [str(r["id"]) for r in list_resp.data["results"]]
        assert str(label_id) in ids

    def test_restore_nonexistent_label(self, auth_client):
        """TC-21: Restore non-existent label returns 404."""
        fake_id = uuid.uuid4()
        resp = auth_client.post(restore_url(fake_id))
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    def test_restore_non_archived_label(self, auth_client):
        """TC-22: Restore a label that is not archived returns 404."""
        label_id = self._create_and_get_id(auth_client)
        # Don't archive — try to restore directly
        resp = auth_client.post(restore_url(label_id))
        assert resp.status_code == status.HTTP_404_NOT_FOUND


# ---------------------------------------------------------------------------
# Additional edge cases
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestLabelEdgeCases:
    """Extra edge-case coverage."""

    def test_duplicate_name_type_rejected(self, auth_client):
        """Creating a label with the same name+type should fail."""
        create_label(auth_client, name="Dup", type="categorical")
        resp = create_label(auth_client, name="Dup", type="categorical")
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_same_name_different_type_ok(self, auth_client):
        """Same name but different type should succeed."""
        create_label(auth_client, name="Same", type="categorical")
        resp = create_label(
            auth_client, name="Same", type="text", settings=make_text_settings()
        )
        assert resp.status_code == status.HTTP_200_OK

    def test_unauthenticated_access(self, api_client):
        """Unauthenticated access should be rejected."""
        resp = api_client.get(BASE_URL)
        assert resp.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )
