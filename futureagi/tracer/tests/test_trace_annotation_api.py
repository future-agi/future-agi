import uuid

import pytest
from django.utils import timezone
from rest_framework import status

from accounts.models.organization import Organization
from accounts.models.user import User
from accounts.models.workspace import Workspace
from model_hub.models.ai_model import AIModel
from model_hub.models.choices import AnnotationTypeChoices
from model_hub.models.develop_annotations import AnnotationsLabels
from model_hub.models.score import Score
from tfc.constants.roles import OrganizationRoles
from tracer.models.observation_span import ObservationSpan
from tracer.models.project import Project
from tracer.models.trace import Trace


TRACE_ANNOTATION_URL = "/tracer/trace-annotation/"
BULK_ANNOTATION_URL = "/tracer/bulk-annotation/"
GET_ANNOTATION_LABELS_URL = "/tracer/get-annotation-labels/"


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


def _response_result(response):
    return response.json().get("result", response.json())


def _create_label(
    *,
    name,
    organization,
    workspace,
    project=None,
    label_type=AnnotationTypeChoices.STAR.value,
    settings=None,
    deleted=False,
):
    label = AnnotationsLabels.no_workspace_objects.create(
        name=name,
        type=label_type,
        settings=settings or {"no_of_stars": 5},
        organization=organization,
        workspace=workspace,
        project=project,
    )
    if deleted:
        label.delete()
    return label


@pytest.mark.django_db
class TestGetAnnotationLabelsAPI:
    def test_lists_current_workspace_labels_and_legacy_null_workspace_labels(
        self, auth_client, organization, workspace, user, project
    ):
        current_project_label = _create_label(
            name=f"Current Project Label {uuid.uuid4().hex[:8]}",
            organization=organization,
            workspace=workspace,
            project=project,
        )
        legacy_null_workspace_label = _create_label(
            name=f"Legacy Null Workspace Label {uuid.uuid4().hex[:8]}",
            organization=organization,
            workspace=None,
        )
        deleted_label = _create_label(
            name=f"Deleted Label {uuid.uuid4().hex[:8]}",
            organization=organization,
            workspace=workspace,
            project=project,
            deleted=True,
        )

        other_workspace = Workspace.no_workspace_objects.create(
            name=f"Other Label Workspace {uuid.uuid4().hex[:8]}",
            organization=organization,
            is_default=False,
            is_active=True,
            created_by=user,
        )
        other_workspace_project = _make_project(
            organization,
            other_workspace,
            name_prefix="Other Label Workspace Project",
        )
        same_org_other_workspace_label = _create_label(
            name=f"Other Workspace Label {uuid.uuid4().hex[:8]}",
            organization=organization,
            workspace=other_workspace,
            project=other_workspace_project,
        )

        other_organization = Organization.objects.create(
            name=f"Other Label Org {uuid.uuid4().hex[:8]}"
        )
        other_user = User.objects.create_user(
            email=f"other-label-user-{uuid.uuid4().hex[:8]}@futureagi.com",
            password="testpassword123",
            name="Other Label User",
            organization=other_organization,
            organization_role=OrganizationRoles.OWNER,
        )
        other_org_workspace = Workspace.no_workspace_objects.create(
            name=f"Other Org Label Workspace {uuid.uuid4().hex[:8]}",
            organization=other_organization,
            is_default=True,
            is_active=True,
            created_by=other_user,
        )
        other_org_project = Project.no_workspace_objects.create(
            name=f"Other Org Label Project {uuid.uuid4().hex[:8]}",
            organization=other_organization,
            workspace=other_org_workspace,
            model_type=AIModel.ModelTypes.GENERATIVE_LLM,
            trace_type="experiment",
            metadata={},
            config=[],
        )
        other_org_label = _create_label(
            name=f"Other Org Label {uuid.uuid4().hex[:8]}",
            organization=other_organization,
            workspace=other_org_workspace,
            project=other_org_project,
        )

        response = auth_client.get(GET_ANNOTATION_LABELS_URL)

        assert response.status_code == status.HTTP_200_OK
        label_ids = {str(label["id"]) for label in _response_result(response)}
        assert str(current_project_label.id) in label_ids
        assert str(legacy_null_workspace_label.id) in label_ids
        assert str(deleted_label.id) not in label_ids
        assert str(same_org_other_workspace_label.id) not in label_ids
        assert str(other_org_label.id) not in label_ids

    def test_project_filter_uses_request_scoped_project(
        self, auth_client, organization, workspace, user, project
    ):
        project_label = _create_label(
            name=f"Scoped Project Label {uuid.uuid4().hex[:8]}",
            organization=organization,
            workspace=workspace,
            project=project,
        )
        global_label = _create_label(
            name=f"Scoped Global Label {uuid.uuid4().hex[:8]}",
            organization=organization,
            workspace=workspace,
        )

        other_workspace = Workspace.no_workspace_objects.create(
            name=f"Other Project Filter Workspace {uuid.uuid4().hex[:8]}",
            organization=organization,
            is_default=False,
            is_active=True,
            created_by=user,
        )
        other_workspace_project = _make_project(
            organization,
            other_workspace,
            name_prefix="Other Project Filter Project",
        )
        other_workspace_label = _create_label(
            name=f"Other Project Filter Label {uuid.uuid4().hex[:8]}",
            organization=organization,
            workspace=other_workspace,
            project=other_workspace_project,
        )

        response = auth_client.get(
            GET_ANNOTATION_LABELS_URL,
            data={"project_id": str(project.id)},
        )

        assert response.status_code == status.HTTP_200_OK
        label_ids = {str(label["id"]) for label in _response_result(response)}
        assert str(project_label.id) in label_ids
        assert str(global_label.id) not in label_ids
        assert str(other_workspace_label.id) not in label_ids

        out_of_scope_response = auth_client.get(
            GET_ANNOTATION_LABELS_URL,
            data={"project_id": str(other_workspace_project.id)},
        )
        assert out_of_scope_response.status_code == status.HTTP_404_NOT_FOUND
        assert (
            other_workspace_project.name.encode() not in out_of_scope_response.content
        )

    def test_non_default_workspace_excludes_default_and_null_workspace_labels(
        self, api_client, organization, workspace, user, project
    ):
        default_label = _create_label(
            name=f"Default Workspace Label {uuid.uuid4().hex[:8]}",
            organization=organization,
            workspace=workspace,
            project=project,
        )
        null_workspace_label = _create_label(
            name=f"Null Workspace Label {uuid.uuid4().hex[:8]}",
            organization=organization,
            workspace=None,
        )
        non_default_workspace = Workspace.no_workspace_objects.create(
            name=f"Non Default Label Workspace {uuid.uuid4().hex[:8]}",
            organization=organization,
            is_default=False,
            is_active=True,
            created_by=user,
        )
        non_default_project = _make_project(
            organization,
            non_default_workspace,
            name_prefix="Non Default Label Project",
        )
        non_default_label = _create_label(
            name=f"Non Default Workspace Label {uuid.uuid4().hex[:8]}",
            organization=organization,
            workspace=non_default_workspace,
            project=non_default_project,
        )

        api_client.force_authenticate(user=user)
        api_client.set_workspace(non_default_workspace)
        response = api_client.get(GET_ANNOTATION_LABELS_URL)

        assert response.status_code == status.HTTP_200_OK
        label_ids = {str(label["id"]) for label in _response_result(response)}
        assert str(non_default_label.id) in label_ids
        assert str(default_label.id) not in label_ids
        assert str(null_workspace_label.id) not in label_ids

    def test_rejects_legacy_project_id_alias(self, auth_client, project):
        response = auth_client.get(
            GET_ANNOTATION_LABELS_URL,
            data={"projectId": str(project.id)},
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "projectId" in str(response.data)


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
        from tracer.tests._ch_seed import seed_ch_score

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

        seed_ch_score(score)

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
