import pytest
from rest_framework import status

from model_hub.models.choices import AnnotationTypeChoices
from model_hub.models.develop_annotations import AnnotationsLabels
from model_hub.models.score import Score

URL = "/tracer/bulk-annotation/"


@pytest.fixture
def star_label(db, organization, workspace, project):
    return AnnotationsLabels.objects.create(
        name="Bulk Quality",
        type=AnnotationTypeChoices.STAR.value,
        settings={"no_of_stars": 5},
        organization=organization,
        workspace=workspace,
        project=project,
    )


@pytest.mark.django_db
class TestBulkAnnotationContracts:
    def test_accepts_canonical_payload(self, auth_client, observation_span, star_label):
        response = auth_client.post(
            URL,
            {
                "records": [
                    {
                        "observation_span_id": observation_span.id,
                        "annotations": [
                            {
                                "annotation_label_id": str(star_label.id),
                                "value_float": 4,
                            }
                        ],
                    }
                ]
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        assert Score.objects.filter(
            observation_span=observation_span,
            label=star_label,
            value={"rating": 4.0},
        ).exists()

    def test_rejects_legacy_nested_aliases(
        self, auth_client, observation_span, star_label
    ):
        response = auth_client.post(
            URL,
            {
                "records": [
                    {
                        "observation_span_id": observation_span.id,
                        "annotations": [
                            {
                                "annotationLabelId": str(star_label.id),
                                "value_float": 4,
                            }
                        ],
                    }
                ]
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "annotationLabelId" in str(response.data)

    def test_rejects_unknown_record_fields(
        self, auth_client, observation_span, star_label
    ):
        response = auth_client.post(
            URL,
            {
                "records": [
                    {
                        "observation_span_id": observation_span.id,
                        "spanId": observation_span.id,
                        "annotations": [
                            {
                                "annotation_label_id": str(star_label.id),
                                "value_float": 4,
                            }
                        ],
                    }
                ]
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "spanId" in str(response.data)

    def test_bulk_create_mirrors_scores_to_clickhouse(
        self,
        auth_client,
        observation_span,
        star_label,
        django_capture_on_commit_callbacks,
        monkeypatch,
    ):
        """Regression (TH-5642): the bulk endpoint writes Scores via
        ``bulk_create``, which emits no ``post_save`` — so the CH mirror signal
        never fires for it. CDC-off that makes the bulk-annotated rows invisible
        to the annotation filters. The endpoint must mirror the created ids
        explicitly. Reverting that call makes ``captured`` empty -> this fails.
        """
        import tracer.services.clickhouse.v2.score_writer as sw

        captured: list[str] = []
        monkeypatch.setattr(
            sw,
            "mirror_scores_to_clickhouse",
            lambda ids: captured.extend(str(i) for i in ids),
        )

        with django_capture_on_commit_callbacks(execute=True):
            response = auth_client.post(
                URL,
                {
                    "records": [
                        {
                            "observation_span_id": observation_span.id,
                            "annotations": [
                                {
                                    "annotation_label_id": str(star_label.id),
                                    "value_float": 4,
                                }
                            ],
                        }
                    ]
                },
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK, response.data
        score = Score.objects.get(observation_span=observation_span, label=star_label)
        assert str(score.id) in captured, (
            "bulk_create-d Score must be mirrored to CH explicitly "
            "(post_save does not fire for bulk_create)"
        )
