"""
A label with stored answers keeps its limits on the label API.

Once a text or numeric label has answers, PUT/PATCH on
/model-hub/annotations-labels/{id}/ refuses a higher minimum or a lower
maximum, and says why. Resending the stored settings (a rename) and loosening
the limits still save, and a label without answers stays freely editable.
"""

import pytest
from rest_framework import status

from model_hub.models.choices import DataTypeChoices, SourceChoices
from model_hub.models.develop_annotations import Annotations, AnnotationsLabels
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.score import Score

BASE_URL = "/model-hub/annotations-labels/"

TEXT_LIMITS_MESSAGE = (
    "This label has stored answers, so its minimum length can't be raised "
    "and its maximum length can't be lowered."
)
NUMERIC_LIMITS_MESSAGE = (
    "This label has stored answers, so its minimum can't be raised "
    "and its maximum can't be lowered."
)


def detail_url(label_id):
    return f"{BASE_URL}{label_id}/"


def make_text_settings(**overrides):
    settings = {"placeholder": "Enter text", "min_length": 5, "max_length": 200}
    settings.update(overrides)
    return settings


def make_numeric_settings(**overrides):
    settings = {"min": 2, "max": 10, "step_size": 1, "display_type": "slider"}
    settings.update(overrides)
    return settings


def make_categorical_settings(**overrides):
    settings = {
        "options": [{"label": "Good"}, {"label": "Bad"}],
        "multi_choice": False,
        "rule_prompt": "",
        "auto_annotate": False,
        "strategy": None,
    }
    settings.update(overrides)
    return settings


def create_label(auth_client, name, label_type, settings):
    resp = auth_client.post(
        BASE_URL,
        {"name": name, "type": label_type, "settings": settings},
        format="json",
    )
    assert resp.status_code == status.HTTP_200_OK, resp.data
    return resp.data["result"]["id"]


def store_answer(label_id, organization, workspace, user, value, deleted=False):
    """Store one Score for the label on a dataset row."""
    dataset = Dataset.objects.create(
        name="Answers", organization=organization, workspace=workspace
    )
    row = Row.objects.create(dataset=dataset, order=1, metadata={})
    return Score.objects.create(
        source_type="dataset_row",
        dataset_row=row,
        label_id=label_id,
        annotator=user,
        value=value,
        score_source="human",
        organization=organization,
        deleted=deleted,
    )


def store_dataset_cell_answer(label_id, organization, workspace, user, value):
    """Store one answer the way the dataset annotation view does: in a Cell."""
    label = AnnotationsLabels.all_objects.get(pk=label_id)
    dataset = Dataset.objects.create(
        name="Annotated", organization=organization, workspace=workspace
    )
    annotation = Annotations.objects.create(
        name="Dataset annotation",
        organization=organization,
        workspace=workspace,
        dataset=dataset,
        responses=1,
    )
    annotation.assigned_users.add(user)
    annotation.labels.add(label)
    column = Column.objects.create(
        name="Answer column",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.ANNOTATION_LABEL.value,
        source_id=f"{annotation.id}-sourceid-{label.id}",
    )
    annotation.columns.add(column)
    row = Row.objects.create(dataset=dataset, order=0)
    return Cell.objects.create(
        dataset=dataset,
        column=column,
        row=row,
        value=value,
        feedback_info={
            "description": "",
            "annotation": {
                "user_id": str(user.id),
                "auto_annotate": False,
                "verified": False,
                "label_id": str(label.id),
                "annotation_id": str(annotation.id),
                "time_taken": None,
            },
        },
    )


def assert_refused(resp, message):
    assert resp.status_code == status.HTTP_400_BAD_REQUEST, resp.data
    assert resp.data["message"] == message
    assert resp.data["detail"] == message


def assert_unchanged(label_id, name, settings):
    stored = AnnotationsLabels.all_objects.get(pk=label_id)
    assert stored.name == name
    assert stored.settings == settings


# ---------------------------------------------------------------------------
# Text labels
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTextLabelWithStoredAnswers:
    @pytest.fixture
    def answered_label(self, auth_client, organization, workspace, user):
        label_id = create_label(auth_client, "Comment", "text", make_text_settings())
        store_answer(label_id, organization, workspace, user, {"text": "Fine reply"})
        return label_id

    @pytest.mark.parametrize("method", ["put", "patch"])
    def test_raising_the_minimum_is_refused(self, auth_client, answered_label, method):
        resp = getattr(auth_client, method)(
            detail_url(answered_label),
            {
                "name": "Comment renamed",
                "type": "text",
                "settings": make_text_settings(min_length=10),
            },
            format="json",
        )

        assert_refused(resp, TEXT_LIMITS_MESSAGE)
        assert_unchanged(answered_label, "Comment", make_text_settings())

    def test_lowering_the_maximum_is_refused(self, auth_client, answered_label):
        resp = auth_client.put(
            detail_url(answered_label),
            {
                "name": "Comment renamed",
                "type": "text",
                "settings": make_text_settings(max_length=100),
            },
            format="json",
        )

        assert_refused(resp, TEXT_LIMITS_MESSAGE)
        assert_unchanged(answered_label, "Comment", make_text_settings())

    def test_renaming_with_the_same_settings_saves(self, auth_client, answered_label):
        resp = auth_client.put(
            detail_url(answered_label),
            {
                "name": "Reviewer comment",
                "type": "text",
                "settings": make_text_settings(),
            },
            format="json",
        )

        assert resp.status_code == status.HTTP_200_OK, resp.data
        assert_unchanged(answered_label, "Reviewer comment", make_text_settings())

    @pytest.mark.parametrize(
        "settings",
        [make_text_settings(min_length=2), make_text_settings(max_length=500)],
        ids=["minimum-lowered", "maximum-raised"],
    )
    def test_loosening_the_limits_saves(self, auth_client, answered_label, settings):
        resp = auth_client.put(
            detail_url(answered_label),
            {"name": "Comment", "type": "text", "settings": settings},
            format="json",
        )

        assert resp.status_code == status.HTTP_200_OK, resp.data
        assert_unchanged(answered_label, "Comment", settings)

    def test_an_answer_in_a_dataset_cell_counts(
        self, auth_client, organization, workspace, user
    ):
        label_id = create_label(
            auth_client, "Cell comment", "text", make_text_settings()
        )
        store_dataset_cell_answer(label_id, organization, workspace, user, "Fine reply")

        resp = auth_client.put(
            detail_url(label_id),
            {
                "name": "Cell comment",
                "type": "text",
                "settings": make_text_settings(min_length=10),
            },
            format="json",
        )

        assert_refused(resp, TEXT_LIMITS_MESSAGE)
        assert_unchanged(label_id, "Cell comment", make_text_settings())

    def test_a_cleared_dataset_cell_does_not_count(
        self, auth_client, organization, workspace, user
    ):
        label_id = create_label(
            auth_client, "Cleared comment", "text", make_text_settings()
        )
        store_dataset_cell_answer(label_id, organization, workspace, user, None)

        resp = auth_client.put(
            detail_url(label_id),
            {
                "name": "Cleared comment",
                "type": "text",
                "settings": make_text_settings(min_length=10),
            },
            format="json",
        )

        assert resp.status_code == status.HTTP_200_OK, resp.data
        assert_unchanged(label_id, "Cleared comment", make_text_settings(min_length=10))

    def test_a_minimum_stored_below_zero_can_be_raised_to_zero(
        self, auth_client, organization, workspace, user
    ):
        stored_settings = make_text_settings(min_length=-5)
        label_id = create_label(auth_client, "Old comment", "text", stored_settings)
        store_answer(label_id, organization, workspace, user, {"text": "Fine reply"})

        resp = auth_client.put(
            detail_url(label_id),
            {
                "name": "Old comment renamed",
                "type": "text",
                "settings": make_text_settings(min_length=0),
            },
            format="json",
        )

        assert resp.status_code == status.HTTP_200_OK, resp.data
        assert_unchanged(
            label_id, "Old comment renamed", make_text_settings(min_length=0)
        )

    def test_a_maximum_sent_as_true_is_refused(
        self, auth_client, organization, workspace, user
    ):
        stored_settings = make_text_settings(min_length=0)
        label_id = create_label(auth_client, "Short comment", "text", stored_settings)
        store_answer(label_id, organization, workspace, user, {"text": "Fine reply"})

        resp = auth_client.put(
            detail_url(label_id),
            {
                "name": "Short comment",
                "type": "text",
                "settings": make_text_settings(min_length=0, max_length=True),
            },
            format="json",
        )

        assert_refused(resp, TEXT_LIMITS_MESSAGE)
        assert_unchanged(label_id, "Short comment", stored_settings)

    def test_limits_added_to_a_label_stored_without_them_are_refused(
        self, auth_client, organization, workspace, user
    ):
        label_id = create_label(auth_client, "Bare comment", "text", {})
        store_answer(label_id, organization, workspace, user, {"text": "Fine reply"})

        resp = auth_client.put(
            detail_url(label_id),
            {
                "name": "Bare comment",
                "type": "text",
                "settings": make_text_settings(min_length=5, max_length=100),
            },
            format="json",
        )

        assert_refused(resp, TEXT_LIMITS_MESSAGE)
        assert_unchanged(label_id, "Bare comment", {})


@pytest.mark.django_db
class TestTextLabelWithoutStoredAnswers:
    @pytest.mark.parametrize("answers", ["none", "deleted-only"])
    def test_raising_the_minimum_saves(
        self, auth_client, organization, workspace, user, answers
    ):
        label_id = create_label(auth_client, "Comment", "text", make_text_settings())
        if answers == "deleted-only":
            store_answer(
                label_id,
                organization,
                workspace,
                user,
                {"text": "Fine reply"},
                deleted=True,
            )

        resp = auth_client.put(
            detail_url(label_id),
            {
                "name": "Comment",
                "type": "text",
                "settings": make_text_settings(min_length=10),
            },
            format="json",
        )

        assert resp.status_code == status.HTTP_200_OK, resp.data
        assert_unchanged(label_id, "Comment", make_text_settings(min_length=10))


# ---------------------------------------------------------------------------
# Numeric labels
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestNumericLabelWithStoredAnswers:
    @pytest.fixture
    def answered_label(self, auth_client, organization, workspace, user):
        label_id = create_label(
            auth_client, "Score", "numeric", make_numeric_settings()
        )
        store_answer(label_id, organization, workspace, user, {"value": 5})
        return label_id

    def test_raising_the_minimum_is_refused(self, auth_client, answered_label):
        resp = auth_client.put(
            detail_url(answered_label),
            {
                "name": "Score renamed",
                "type": "numeric",
                "settings": make_numeric_settings(min=3),
            },
            format="json",
        )

        assert_refused(resp, NUMERIC_LIMITS_MESSAGE)
        assert_unchanged(answered_label, "Score", make_numeric_settings())

    def test_lowering_the_maximum_is_refused(self, auth_client, answered_label):
        resp = auth_client.put(
            detail_url(answered_label),
            {
                "name": "Score renamed",
                "type": "numeric",
                "settings": make_numeric_settings(max=8),
            },
            format="json",
        )

        assert_refused(resp, NUMERIC_LIMITS_MESSAGE)
        assert_unchanged(answered_label, "Score", make_numeric_settings())

    def test_renaming_with_the_same_settings_saves(self, auth_client, answered_label):
        resp = auth_client.put(
            detail_url(answered_label),
            {
                "name": "Quality score",
                "type": "numeric",
                "settings": make_numeric_settings(),
            },
            format="json",
        )

        assert resp.status_code == status.HTTP_200_OK, resp.data
        assert_unchanged(answered_label, "Quality score", make_numeric_settings())

    @pytest.mark.parametrize(
        "settings",
        [make_numeric_settings(min=1), make_numeric_settings(max=20)],
        ids=["minimum-lowered", "maximum-raised"],
    )
    def test_loosening_the_limits_saves(self, auth_client, answered_label, settings):
        resp = auth_client.put(
            detail_url(answered_label),
            {"name": "Score", "type": "numeric", "settings": settings},
            format="json",
        )

        assert resp.status_code == status.HTTP_200_OK, resp.data
        assert_unchanged(answered_label, "Score", settings)


@pytest.mark.django_db
class TestNumericLabelWithoutStoredAnswers:
    @pytest.mark.parametrize("answers", ["none", "deleted-only"])
    def test_raising_the_minimum_saves(
        self, auth_client, organization, workspace, user, answers
    ):
        label_id = create_label(
            auth_client, "Score", "numeric", make_numeric_settings()
        )
        if answers == "deleted-only":
            store_answer(
                label_id, organization, workspace, user, {"value": 5}, deleted=True
            )

        resp = auth_client.put(
            detail_url(label_id),
            {
                "name": "Score",
                "type": "numeric",
                "settings": make_numeric_settings(min=3),
            },
            format="json",
        )

        assert resp.status_code == status.HTTP_200_OK, resp.data
        assert_unchanged(label_id, "Score", make_numeric_settings(min=3))


# ---------------------------------------------------------------------------
# Other label types
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCategoricalLabelWithStoredAnswers:
    def test_changing_the_settings_saves(
        self, auth_client, organization, workspace, user
    ):
        label_id = create_label(
            auth_client, "Verdict", "categorical", make_categorical_settings()
        )
        store_answer(label_id, organization, workspace, user, {"selected": ["Good"]})
        new_settings = make_categorical_settings(
            options=[{"label": "Good"}, {"label": "Bad"}, {"label": "Unsure"}]
        )

        resp = auth_client.put(
            detail_url(label_id),
            {"name": "Verdict", "type": "categorical", "settings": new_settings},
            format="json",
        )

        assert resp.status_code == status.HTTP_200_OK, resp.data
        assert_unchanged(label_id, "Verdict", new_settings)
