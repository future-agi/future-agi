import uuid

import pytest
from django.utils import timezone
from rest_framework import status

from accounts.models.workspace import Workspace
from model_hub.models.ai_model import AIModel
from model_hub.models.choices import AnnotationTypeChoices
from model_hub.models.develop_annotations import AnnotationsLabels
from model_hub.models.score import Score
from tracer.models.observation_span import ObservationSpan
from tracer.models.project import Project
from tracer.models.trace import Trace


TRACE_ANNOTATION_URL = "/tracer/trace-annotation/"
BULK_ANNOTATION_URL = "/tracer/bulk-annotation/"


@pytest.fixture
def star_label(db, organization, workspace, project):
    return AnnotationsLabels.objects.create(
        name=f"Trace Quality {uuid.uuid4().hex[:8]}",
        type=AnnotationTypeChoices.STAR.value,
        settings={"no_of_stars": 5},
        organization=organization,
        workspace=workspace,
        project=project,
    )


def _make_project(organization, workspace, name_prefix="Trace Annotation Project"):
    return Project.objects.create(
        name=f"{name_prefix} {uuid.uuid4().hex[:8]}",
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="experiment",
        metadata={},
        config=[],
    )


def _make_span(project, name_prefix="Trace Annotation Span"):
    trace = Trace.objects.create(
        project=project,
        name=f"{name_prefix} Trace",
        metadata={},
        input={"prompt": "hello"},
        output={"response": "world"},
    )
    return ObservationSpan.objects.create(
        id=f"span_{uuid.uuid4().hex[:16]}",
        project=project,
        trace=trace,
        name=f"{name_prefix} {uuid.uuid4().hex[:8]}",
        observation_type="llm",
        start_time=timezone.now(),
        end_time=timezone.now(),
        status="OK",
    )


def _make_other_workspace_span(organization, user):
    other_workspace = Workspace.objects.create(
        name=f"Other Trace Annotation Workspace {uuid.uuid4().hex[:8]}",
        organization=organization,
        created_by=user,
    )
    other_project = _make_project(
        organization, other_workspace, name_prefix="Other Workspace Project"
    )
    other_span = _make_span(other_project, name_prefix="Other Workspace Span")
    return other_workspace, other_project, other_span


@pytest.mark.django_db
class TestTraceAnnotationAPI:
    def test_trace_annotation_crud_routes_return_405(self, auth_client):
        detail_url = f"{TRACE_ANNOTATION_URL}{uuid.uuid4()}/"

        responses = [
            auth_client.get(TRACE_ANNOTATION_URL),
            auth_client.post(TRACE_ANNOTATION_URL, {}, format="json"),
            auth_client.get(detail_url),
            auth_client.put(detail_url, {}, format="json"),
            auth_client.patch(detail_url, {}, format="json"),
            auth_client.delete(detail_url),
        ]

        for response in responses:
            assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED
            assert "bulk-annotation" in response.data["detail"]
            assert "get_annotation_values" in response.data["detail"]

    def test_bulk_annotation_sets_score_workspace_and_values_are_readable(
        self, auth_client, user, organization, workspace, observation_span, star_label
    ):
        response = auth_client.post(
            BULK_ANNOTATION_URL,
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
        assert response.data["result"]["annotations_created"] == 1

        score = Score.objects.get(
            observation_span=observation_span,
            label=star_label,
            annotator=user,
            deleted=False,
        )
        assert score.organization == organization
        assert score.workspace == workspace
        assert score.value == {"rating": 4.0}

        values_response = auth_client.get(
            f"{TRACE_ANNOTATION_URL}get_annotation_values/",
            {"observation_span_id": observation_span.id},
        )

        assert values_response.status_code == status.HTTP_200_OK, values_response.data
        annotations = values_response.data["result"]["annotations"]
        assert len(annotations) == 1
        assert annotations[0]["id"] == str(score.id)
        assert annotations[0]["annotation_label_id"] == str(star_label.id)
        assert annotations[0]["annotation_value"] == 4.0

    def test_get_annotation_values_excludes_other_workspace_scores(
        self, auth_client, user, organization
    ):
        other_workspace, other_project, other_span = _make_other_workspace_span(
            organization, user
        )
        other_label = AnnotationsLabels.objects.create(
            name=f"Other Workspace Quality {uuid.uuid4().hex[:8]}",
            type=AnnotationTypeChoices.STAR.value,
            settings={"no_of_stars": 5},
            organization=organization,
            workspace=other_workspace,
            project=other_project,
        )
        Score.objects.create(
            observation_span=other_span,
            label=other_label,
            annotator=user,
            source_type="observation_span",
            value={"rating": 5.0},
            score_source="human",
            organization=organization,
            workspace=other_workspace,
        )

        response = auth_client.get(
            f"{TRACE_ANNOTATION_URL}get_annotation_values/",
            {"observation_span_id": other_span.id},
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.data["result"]["annotations"] == []
        assert response.data["result"]["notes"] == []

    def test_bulk_annotation_rejects_same_org_other_workspace_span(
        self, auth_client, user, organization, star_label
    ):
        _, _, other_span = _make_other_workspace_span(organization, user)

        response = auth_client.post(
            BULK_ANNOTATION_URL,
            {
                "records": [
                    {
                        "observation_span_id": other_span.id,
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
        result = response.data["result"]
        assert result["annotations_created"] == 0
        assert result["errors_count"] == 1
        assert result["errors"][0]["error"] == "Span not found"
        assert not Score.objects.filter(observation_span=other_span).exists()
