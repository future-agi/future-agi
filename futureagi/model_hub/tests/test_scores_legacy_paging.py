"""Source-score reads remain unpaginated for released client paging arguments."""

from uuid import uuid4

import pytest
from django.http import QueryDict

from model_hub.models.develop_annotations import AnnotationsLabels
from model_hub.models.develop_dataset import Dataset, Row
from model_hub.models.score import Score
from model_hub.serializers.scores import ScoreForSourceQuerySerializer

pytestmark = pytest.mark.django_db


@pytest.fixture(scope="module", autouse=True)
def load_unscoped_urlconf():
    from django.urls import get_resolver
    from tfc.middleware.workspace_context import clear_workspace_context

    clear_workspace_context()
    get_resolver().url_patterns


@pytest.fixture
def scored_row(organization, workspace, user):
    dataset = Dataset.objects.create(
        name="Scored data", organization=organization, workspace=workspace,
    )
    row = Row.objects.create(dataset=dataset, order=0)
    for index in range(3):
        label = AnnotationsLabels.objects.create(
            name=f"Label {index}", type="text", settings={},
            organization=organization, workspace=workspace,
        )
        Score.objects.create(
            source_type="dataset_row", dataset_row=row, label=label,
            value=f"Value {index}", annotator=user,
            organization=organization, workspace=workspace,
        )
    return row


def test_legacy_paging_keys_do_not_change_validated_source():
    source_id = str(uuid4())
    query = QueryDict(f"source_type=dataset_row&source_id={source_id}&page=2&limit=1")
    serializer = ScoreForSourceQuerySerializer(data=query)
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["source_id"] == source_id
    assert "page" not in serializer.validated_data
    assert "limit" not in serializer.validated_data
    assert query["page"] == "2"
    assert query["limit"] == "1"


@pytest.mark.parametrize("paging", [{}, {"page": 1}, {"limit": 1}, {"page": 2, "limit": 1}])
def test_source_scores_return_every_score_with_or_without_paging(
    auth_client, scored_row, paging,
):
    response = auth_client.get(
        "/model-hub/scores/for-source/",
        {"source_type": "dataset_row", "source_id": str(scored_row.id), **paging},
    )
    assert response.status_code == 200, response.data
    result = response.data["result"]
    assert isinstance(result, list)
    assert len(result) == 3
    assert [score["label_name"] for score in result] == ["Label 0", "Label 1", "Label 2"]
    assert {score["source_id"] for score in result} == {str(scored_row.id)}


def test_source_score_paging_keeps_other_query_keys_strict(auth_client, scored_row):
    response = auth_client.get(
        "/model-hub/scores/for-source/",
        {"source_type": "dataset_row", "source_id": str(scored_row.id),
         "page": 1, "limit": 1, "unexpected": "value"},
    )
    assert response.status_code == 400, response.data
    assert "unexpected" in str(response.data)
    assert "Unknown field." in str(response.data)
