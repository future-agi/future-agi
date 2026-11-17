"""
POST /tracer/observation-span/add_annotations/ answers which labels did not
save, and why.

The labels that pass are saved. A label whose value its settings refuse is
listed in ``failed_labels`` and its reason sits in ``failed_reasons`` under its
id. A label that cannot be saved because the span has no default annotation
queue is listed in ``failed_labels`` with no reason.
"""

import uuid
from unittest.mock import patch

import pytest
from rest_framework import status

from model_hub.models.choices import AnnotationTypeChoices
from model_hub.models.develop_annotations import AnnotationsLabels
from model_hub.models.score import Score

URL = "/tracer/observation-span/add_annotations/"

TOO_SHORT = "Text too short. Minimum length is 50 characters."


def _label(organization, workspace, name, label_type, settings=None):
    return AnnotationsLabels.objects.create(
        name=f"{name} {uuid.uuid4().hex[:8]}",
        type=label_type,
        settings=settings or {},
        organization=organization,
        workspace=workspace,
    )


def _text_label(organization, workspace, name, settings=None):
    return _label(
        organization, workspace, name, AnnotationTypeChoices.TEXT.value, settings
    )


def _star_label(organization, workspace, name):
    return _label(
        organization,
        workspace,
        name,
        AnnotationTypeChoices.STAR.value,
        {"no_of_stars": 5},
    )


def _send(client, span, values):
    return client.post(
        URL,
        {
            "observation_span_id": span.id,
            "annotation_values": {str(label.id): value for label, value in values},
        },
        format="json",
    )


def _result(response):
    data = response.json()
    return data.get("result", data)


def _saved(span, label):
    return Score._base_manager.filter(
        observation_span_id=span.id, label_id=label.id, deleted=False
    ).exists()


@pytest.mark.integration
@pytest.mark.api
class TestAddAnnotationsSaysWhichLabelsDidNotSave:
    def test_a_refused_label_is_named_with_its_reason_and_the_others_save(
        self, auth_client, organization, workspace, observation_span
    ):
        tone = _text_label(
            organization,
            workspace,
            "Tone",
            {"placeholder": "Describe the tone", "min_length": 50, "max_length": 1000},
        )
        clarity = _text_label(organization, workspace, "Clarity")
        rating = _star_label(organization, workspace, "Rating")

        response = _send(
            auth_client,
            observation_span,
            [(tone, "short note"), (clarity, "Clear and direct."), (rating, 4)],
        )

        assert response.status_code == status.HTTP_200_OK, response.content
        result = _result(response)
        assert result["failed_labels"] == [str(tone.id)]
        assert result.get("failed_reasons") == {str(tone.id): TOO_SHORT}
        assert sorted(result["success_labels"]) == sorted(
            [str(clarity.id), str(rating.id)]
        )
        assert _saved(observation_span, clarity)
        assert _saved(observation_span, rating)
        assert not _saved(observation_span, tone)

    def test_a_refused_star_label_says_what_to_choose(
        self, auth_client, organization, workspace, observation_span
    ):
        clarity = _text_label(organization, workspace, "Clarity")
        rating = _label(
            organization,
            workspace,
            "Rating",
            AnnotationTypeChoices.STAR.value,
            {"no_of_stars": 3},
        )

        response = _send(
            auth_client,
            observation_span,
            [(clarity, "Clear and direct."), (rating, 4)],
        )

        assert response.status_code == status.HTTP_200_OK, response.content
        result = _result(response)
        assert result["failed_labels"] == [str(rating.id)]
        assert result.get("failed_reasons") == {
            str(rating.id): "Choose from 1 to 3 stars."
        }
        assert result["success_labels"] == [str(clarity.id)]
        assert _saved(observation_span, clarity)
        assert not _saved(observation_span, rating)

    def test_a_single_choice_label_given_two_options_says_choose_one(
        self, auth_client, organization, workspace, observation_span
    ):
        clarity = _text_label(organization, workspace, "Clarity")
        verdict = _label(
            organization,
            workspace,
            "Verdict",
            AnnotationTypeChoices.CATEGORICAL.value,
            {
                "options": [{"label": "Good"}, {"label": "Bad"}],
                "multi_choice": False,
                "rule_prompt": "",
                "auto_annotate": False,
                "strategy": None,
            },
        )

        response = _send(
            auth_client,
            observation_span,
            [(clarity, "Clear and direct."), (verdict, ["Good", "Bad"])],
        )

        assert response.status_code == status.HTTP_200_OK, response.content
        result = _result(response)
        assert result["failed_labels"] == [str(verdict.id)]
        assert result.get("failed_reasons") == {
            str(verdict.id): "Choose one option for this label."
        }
        assert result["success_labels"] == [str(clarity.id)]
        assert _saved(observation_span, clarity)
        assert not _saved(observation_span, verdict)

    def test_every_label_saved_answers_no_failed_labels_and_no_reasons(
        self, auth_client, organization, workspace, observation_span
    ):
        clarity = _text_label(organization, workspace, "Clarity")
        rating = _star_label(organization, workspace, "Rating")

        response = _send(
            auth_client,
            observation_span,
            [(clarity, "Clear and direct."), (rating, 4)],
        )

        assert response.status_code == status.HTTP_200_OK, response.content
        result = _result(response)
        assert result["failed_labels"] == []
        assert result.get("failed_reasons") == {}
        assert sorted(result["success_labels"]) == sorted(
            [str(clarity.id), str(rating.id)]
        )
        assert _saved(observation_span, clarity)
        assert _saved(observation_span, rating)

    def test_a_label_with_no_default_queue_is_named_without_a_reason(
        self, auth_client, organization, workspace, observation_span
    ):
        clarity = _text_label(organization, workspace, "Clarity")

        with patch(
            "model_hub.utils.annotation_queue_helpers."
            "resolve_default_queue_item_for_source",
            return_value=None,
        ):
            response = _send(
                auth_client, observation_span, [(clarity, "Clear and direct.")]
            )

        assert response.status_code == status.HTTP_200_OK, response.content
        result = _result(response)
        assert result["failed_labels"] == [str(clarity.id)]
        assert result.get("failed_reasons") == {}
        assert result["success_labels"] == []
        assert not Score._base_manager.filter(
            observation_span_id=observation_span.id
        ).exists()
