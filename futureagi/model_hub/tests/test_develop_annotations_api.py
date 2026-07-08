"""
Test cases for model_hub/views/develop_annotations.py

Tests cover:
- AnnotationsLabelsViewSet (CRUD for annotation labels)
- AnnotationsViewSet (CRUD for annotations)
- UserViewSet (List users in organization)
- AnnotationSummaryView (Get annotation statistics)
"""

import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from rest_framework import status

# TestAnnotationSummaryView tests don't mock the EE entitlement, so they get
# 403 in non-EE test environments. See PLAN.md. The legacy ``Annotations``
# model is still exposed through generated frontend contracts, so these tests
# lock its supported fallback behavior while unified ``Score`` remains the
# canonical annotation store.
from rest_framework.test import APIClient

from accounts.models import Organization, User
from accounts.models.workspace import Workspace
from model_hub.models import AIModel, AnnotationTask
from model_hub.models.choices import (
    AnnotationTypeChoices,
    DatasetSourceChoices,
    DataTypeChoices,
    SourceChoices,
)
from model_hub.models.develop_annotations import Annotations, AnnotationsLabels
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from tfc.middleware.workspace_context import set_workspace_context


@pytest.fixture
def organization(db):
    return Organization.objects.create(name="Test Organization")


@pytest.fixture
def user(db, organization):
    return User.objects.create_user(
        email="test@example.com",
        password="testpassword123",
        name="Test User",
        organization=organization,
    )


@pytest.fixture
def workspace(db, organization, user):
    from accounts.models.organization_membership import OrganizationMembership
    from accounts.models.workspace import WorkspaceMembership
    from tfc.constants.levels import Level
    from tfc.constants.roles import OrganizationRoles

    ws = Workspace.objects.create(
        name="Default Workspace",
        organization=organization,
        is_default=True,
        created_by=user,
    )
    org_mem, _ = OrganizationMembership.no_workspace_objects.get_or_create(
        user=user,
        organization=organization,
        defaults={
            "role": OrganizationRoles.OWNER,
            "level": Level.OWNER,
            "is_active": True,
        },
    )
    WorkspaceMembership.no_workspace_objects.get_or_create(
        user=user,
        workspace=ws,
        defaults={
            "role": "Workspace Owner",
            "level": Level.OWNER,
            "is_active": True,
            "organization_membership": org_mem,
        },
    )
    return ws


@pytest.fixture
def other_user(db, organization):
    return User.objects.create_user(
        email="other@example.com",
        password="testpassword123",
        name="Other User",
        organization=organization,
    )


@pytest.fixture
def other_organization(db):
    return Organization.objects.create(name="Other Organization")


@pytest.fixture
def other_org_user(db, other_organization):
    return User.objects.create_user(
        email="otherorg@example.com",
        password="testpassword123",
        name="Other Org User",
        organization=other_organization,
    )


@pytest.fixture
def auth_client(user, workspace):
    client = APIClient()
    client.force_authenticate(user=user)
    set_workspace_context(workspace=workspace, organization=user.organization)
    return client


def _create_annotation_task(organization, workspace, user, name="Task"):
    ai_model = AIModel.objects.create(
        user_model_id=f"annotation-task-model-{uuid.uuid4()}",
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        organization=organization,
        workspace=workspace,
    )
    task = AnnotationTask.objects.create(
        task_name=name,
        ai_model=ai_model,
        organization=organization,
        workspace=workspace,
    )
    task.assigned_users.add(user)
    return task


@pytest.fixture
def dataset(db, organization, workspace):
    return Dataset.objects.create(
        name="Test Dataset",
        organization=organization,
        workspace=workspace,
        source=DatasetSourceChoices.BUILD.value,
    )


@pytest.fixture
def column(db, dataset):
    return Column.objects.create(
        name="Test Column",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )


@pytest.fixture
def row(db, dataset):
    return Row.objects.create(dataset=dataset, order=0)


@pytest.fixture
def cell(db, dataset, column, row):
    return Cell.objects.create(
        dataset=dataset,
        column=column,
        row=row,
        value="Test value",
    )


@pytest.fixture
def numeric_label_settings():
    return {
        "min": 0,
        "max": 10,
        "step_size": 1,
        "display_type": "slider",
    }


@pytest.fixture
def text_label_settings():
    return {
        "placeholder": "Enter text",
        "max_length": 500,
        "min_length": 1,
    }


@pytest.fixture
def categorical_label_settings():
    return {
        "rule_prompt": "Select the appropriate category",
        "multi_choice": False,
        "options": [
            {"label": "Option A"},
            {"label": "Option B"},
            {"label": "Option C"},
        ],
        "auto_annotate": False,
        "strategy": None,
    }


@pytest.fixture
def annotation_label(db, organization, workspace, numeric_label_settings):
    return AnnotationsLabels.objects.create(
        name="Test Label",
        type=AnnotationTypeChoices.NUMERIC.value,
        organization=organization,
        workspace=workspace,
        settings=numeric_label_settings,
    )


@pytest.fixture
def annotation(db, organization, workspace, dataset, user, annotation_label):
    annotation = Annotations.objects.create(
        name="Test Annotation",
        organization=organization,
        workspace=workspace,
        dataset=dataset,
        responses=1,
    )
    annotation.assigned_users.add(user)
    annotation.labels.add(annotation_label)
    return annotation


# ==================== AnnotationsLabelsViewSet Tests ====================


@pytest.mark.django_db
class TestAnnotationsLabelsViewSet:
    """Tests for AnnotationsLabelsViewSet CRUD operations."""

    def test_list_annotation_labels(self, auth_client, annotation_label):
        """Test listing annotation labels."""
        response = auth_client.get("/model-hub/annotations-labels/")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "results" in data
        assert len(data["results"]) >= 1

    def test_list_annotation_labels_rejects_legacy_query_aliases(self, auth_client):
        """Label list accepts canonical snake_case query params only."""
        response = auth_client.get(
            f"/model-hub/annotations-labels/?projectId={uuid.uuid4()}"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_list_annotation_labels_rejects_invalid_boolean_query(self, auth_client):
        """Boolean query params should be validated instead of silently coerced."""
        response = auth_client.get(
            "/model-hub/annotations-labels/?include_usage_count=maybe"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_numeric_label(self, auth_client, numeric_label_settings):
        """Test creating a numeric annotation label."""
        payload = {
            "name": "Numeric Label",
            "type": AnnotationTypeChoices.NUMERIC.value,
            "settings": numeric_label_settings,
        }
        response = auth_client.post(
            "/model-hub/annotations-labels/", payload, format="json"
        )
        assert response.status_code == status.HTTP_200_OK
        assert AnnotationsLabels.objects.filter(name="Numeric Label").exists()

    def test_create_text_label(self, auth_client, text_label_settings):
        """Test creating a text annotation label."""
        payload = {
            "name": "Text Label",
            "type": AnnotationTypeChoices.TEXT.value,
            "settings": text_label_settings,
        }
        response = auth_client.post(
            "/model-hub/annotations-labels/", payload, format="json"
        )
        assert response.status_code == status.HTTP_200_OK
        assert AnnotationsLabels.objects.filter(name="Text Label").exists()

    def test_create_categorical_label(self, auth_client, categorical_label_settings):
        """Test creating a categorical annotation label."""
        payload = {
            "name": "Categorical Label",
            "type": AnnotationTypeChoices.CATEGORICAL.value,
            "settings": categorical_label_settings,
        }
        response = auth_client.post(
            "/model-hub/annotations-labels/", payload, format="json"
        )
        assert response.status_code == status.HTTP_200_OK
        assert AnnotationsLabels.objects.filter(name="Categorical Label").exists()

    def test_create_label_missing_required_settings(self, auth_client):
        """Test creating a label with missing required settings."""
        from django.core.exceptions import ValidationError as DjangoValidationError

        payload = {
            "name": "Invalid Label",
            "type": AnnotationTypeChoices.NUMERIC.value,
            "settings": {"min": 0},  # Missing max, step_size, display_type
        }
        try:
            response = auth_client.post(
                "/model-hub/annotations-labels/", payload, format="json"
            )
            # If we get a response, check status code
            assert response.status_code in [
                status.HTTP_400_BAD_REQUEST,
                status.HTTP_500_INTERNAL_SERVER_ERROR,
            ]
        except DjangoValidationError:
            # Validation error raised at model level - this is expected behavior
            pass

    def test_create_label_invalid_numeric_range(self, auth_client):
        """Test creating a numeric label with min >= max."""
        from django.core.exceptions import ValidationError as DjangoValidationError

        payload = {
            "name": "Invalid Range Label",
            "type": AnnotationTypeChoices.NUMERIC.value,
            "settings": {
                "min": 10,
                "max": 5,  # Invalid: min >= max
                "step_size": 1,
                "display_type": "slider",
            },
        }
        try:
            response = auth_client.post(
                "/model-hub/annotations-labels/", payload, format="json"
            )
            # If we get a response, check status code
            assert response.status_code in [
                status.HTTP_400_BAD_REQUEST,
                status.HTTP_500_INTERNAL_SERVER_ERROR,
            ]
        except DjangoValidationError:
            # Validation error raised at model level - this is expected behavior
            pass

    def test_create_categorical_label_insufficient_options(self, auth_client):
        """Test creating a categorical label with less than 2 options."""
        from django.core.exceptions import ValidationError as DjangoValidationError

        payload = {
            "name": "Invalid Categorical",
            "type": AnnotationTypeChoices.CATEGORICAL.value,
            "settings": {
                "rule_prompt": "Test",
                "multi_choice": False,
                "options": [{"label": "Only One"}],  # Need at least 2
                "auto_annotate": False,
                "strategy": None,
            },
        }
        try:
            response = auth_client.post(
                "/model-hub/annotations-labels/", payload, format="json"
            )
            # If we get a response, check status code
            assert response.status_code in [
                status.HTTP_400_BAD_REQUEST,
                status.HTTP_500_INTERNAL_SERVER_ERROR,
            ]
        except DjangoValidationError:
            # Validation error raised at model level - this is expected behavior
            pass

    def test_create_duplicate_label_name(
        self, auth_client, annotation_label, numeric_label_settings
    ):
        """Test creating a label with duplicate name in same org/project."""
        payload = {
            "name": annotation_label.name,  # Same name
            "type": annotation_label.type,  # Same type
            "settings": numeric_label_settings,
        }
        response = auth_client.post(
            "/model-hub/annotations-labels/", payload, format="json"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_retrieve_annotation_label(self, auth_client, annotation_label):
        """Test retrieving a specific annotation label."""
        response = auth_client.get(
            f"/model-hub/annotations-labels/{annotation_label.id}/"
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["name"] == annotation_label.name

    def test_update_annotation_label(
        self, auth_client, annotation_label, numeric_label_settings
    ):
        """Test updating an annotation label."""
        payload = {
            "name": "Updated Label Name",
            "type": AnnotationTypeChoices.NUMERIC.value,
            "settings": numeric_label_settings,
        }
        response = auth_client.put(
            f"/model-hub/annotations-labels/{annotation_label.id}/",
            payload,
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        annotation_label.refresh_from_db()
        assert annotation_label.name == "Updated Label Name"

    def test_delete_annotation_label(self, auth_client, annotation_label):
        """Test deleting an annotation label."""
        response = auth_client.delete(
            f"/model-hub/annotations-labels/{annotation_label.id}/"
        )
        assert response.status_code == status.HTTP_204_NO_CONTENT
        # Should be soft deleted
        annotation_label.refresh_from_db()
        assert annotation_label.deleted is True

    def test_unauthenticated_access(self, annotation_label):
        """Test that unauthenticated users cannot access annotation labels."""
        client = APIClient()
        response = client.get("/model-hub/annotations-labels/")
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]


# ==================== AnnotationsViewSet Tests ====================


@pytest.mark.django_db
class TestAnnotationsViewSet:
    """Tests for AnnotationsViewSet CRUD operations."""

    def test_list_annotations(self, auth_client, annotation):
        """Test listing annotations."""
        response = auth_client.get("/model-hub/annotations/")
        assert response.status_code == status.HTTP_200_OK

    def test_list_annotations_by_dataset(self, auth_client, annotation, dataset):
        """Test listing annotations filtered by dataset."""
        response = auth_client.get(f"/model-hub/annotations/?dataset={dataset.id}")
        assert response.status_code == status.HTTP_200_OK

    def test_create_annotation(
        self, auth_client, dataset, user, workspace, annotation_label, column
    ):
        """Test creating an annotation."""
        payload = {
            "name": "New Annotation",
            "dataset": str(dataset.id),
            "assigned_users": [str(user.id)],
            "labels": [{"id": str(annotation_label.id), "required": False}],
            "responses": 1,
            "static_fields": [
                {
                    "column_id": str(column.id),
                    "type": "plain_text",
                    "view": "default_open",
                }
            ],
        }
        response = auth_client.post("/model-hub/annotations/", payload, format="json")
        assert response.status_code == status.HTTP_200_OK
        created = Annotations.objects.get(name="New Annotation")
        assert created.workspace == workspace
        assert created.labels.filter(id=annotation_label.id).exists()

    def test_create_annotation_responses_exceeds_users(
        self, auth_client, dataset, user, annotation_label
    ):
        """Test that responses cannot exceed number of assigned users."""
        payload = {
            "name": "Invalid Annotation",
            "dataset": str(dataset.id),
            "assigned_users": [str(user.id)],  # Only 1 user
            "labels": [{"id": str(annotation_label.id), "required": False}],
            "responses": 5,  # More than users
        }
        response = auth_client.post("/model-hub/annotations/", payload, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not Annotations.objects.filter(name="Invalid Annotation").exists()

    def test_create_annotation_required_label_requires_entitlement(
        self, auth_client, dataset, user, annotation_label
    ):
        """Required labels are plan-gated and should fail cleanly."""
        payload = {
            "name": "Required Label Annotation",
            "dataset": str(dataset.id),
            "assigned_users": [str(user.id)],
            "labels": [{"id": str(annotation_label.id), "required": True}],
            "responses": 1,
        }
        response = auth_client.post("/model-hub/annotations/", payload, format="json")
        assert response.status_code == status.HTTP_402_PAYMENT_REQUIRED

    def test_retrieve_annotation(self, auth_client, annotation):
        """Test retrieving a specific annotation."""
        response = auth_client.get(f"/model-hub/annotations/{annotation.id}/")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["name"] == annotation.name

    def test_update_annotation(self, auth_client, annotation):
        """Test updating an annotation."""
        payload = {
            "name": "Updated Annotation",
            "dataset": str(annotation.dataset.id),
            "labels": [
                {"id": str(label.id), "required": False}
                for label in annotation.labels.all()
            ],
            "responses": 1,
        }
        response = auth_client.put(
            f"/model-hub/annotations/{annotation.id}/",
            payload,
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        annotation.refresh_from_db()
        assert annotation.name == "Updated Annotation"
        assert annotation.labels.exists()

    def test_update_annotation_required_label_requires_entitlement(
        self, auth_client, annotation
    ):
        """Update should propagate required-label entitlement denial as 402."""
        payload = {
            "name": "Required Updated Annotation",
            "dataset": str(annotation.dataset.id),
            "labels": [
                {"id": str(label.id), "required": True}
                for label in annotation.labels.all()
            ],
            "responses": 1,
        }
        response = auth_client.put(
            f"/model-hub/annotations/{annotation.id}/",
            payload,
            format="json",
        )
        assert response.status_code == status.HTTP_402_PAYMENT_REQUIRED

    def test_partial_update_preserves_labels_and_assignees(
        self, auth_client, annotation
    ):
        """PATCH name-only updates must not clear legacy M2M relationships."""
        label_ids = set(annotation.labels.values_list("id", flat=True))
        user_ids = set(annotation.assigned_users.values_list("id", flat=True))

        response = auth_client.patch(
            f"/model-hub/annotations/{annotation.id}/",
            {"name": "Patched Annotation"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        annotation.refresh_from_db()
        assert annotation.name == "Patched Annotation"
        assert set(annotation.labels.values_list("id", flat=True)) == label_ids
        assert set(annotation.assigned_users.values_list("id", flat=True)) == user_ids

    def test_delete_annotation(self, auth_client, annotation):
        """Test deleting an annotation."""
        response = auth_client.delete(f"/model-hub/annotations/{annotation.id}/")
        assert response.status_code == status.HTTP_200_OK

    def test_delete_annotation_soft_deletes_generated_cells(
        self, auth_client, annotation, annotation_label, row
    ):
        """Deleting an annotation should soft-delete generated columns and cells."""
        generated_column = Column.objects.create(
            name="Generated Annotation Column",
            dataset=annotation.dataset,
            data_type=DataTypeChoices.FLOAT.value,
            source=SourceChoices.ANNOTATION_LABEL.value,
            source_id=f"{annotation.id}-sourceid-{annotation_label.id}",
        )
        annotation.columns.add(generated_column)
        generated_cell = Cell.objects.create(
            dataset=annotation.dataset,
            row=row,
            column=generated_column,
            value=None,
            feedback_info={
                "description": "reset note",
                "annotation": {
                    "user_id": None,
                    "label_id": str(annotation_label.id),
                    "annotation_id": str(annotation.id),
                },
            },
        )

        response = auth_client.delete(f"/model-hub/annotations/{annotation.id}/")

        assert response.status_code == status.HTTP_200_OK
        generated_column.refresh_from_db()
        generated_cell.refresh_from_db()
        assert generated_column.deleted is True
        assert generated_column.deleted_at is not None
        assert generated_cell.deleted is True
        assert generated_cell.deleted_at is not None

        response = auth_client.delete(f"/model-hub/annotations/{annotation.id}/")
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_bulk_destroy_annotations(self, auth_client, annotation):
        """Test bulk deleting annotations."""
        payload = {"annotation_ids": [str(annotation.id)]}
        response = auth_client.post(
            "/model-hub/annotations/bulk_destroy/",
            payload,
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        result = data.get("result", data.get("data", data))
        deleted_count = result.get("deleted_count") or result.get("data", {}).get(
            "deleted_count"
        )
        assert deleted_count == 1

    def test_bulk_destroy_empty_ids(self, auth_client):
        """Test bulk destroy with empty ids list."""
        payload = {"annotation_ids": []}
        response = auth_client.post(
            "/model-hub/annotations/bulk_destroy/",
            payload,
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
class TestAnnotationsViewSetActions:
    """Tests for AnnotationsViewSet custom actions."""

    def test_annotation_tasks_reject_legacy_predictive_journey_alias(self, auth_client):
        """Annotation task list uses canonical predictive_journey query params."""
        response = auth_client.get(
            f"/model-hub/annotation-tasks/?predictiveJourney={uuid.uuid4()}"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_annotation_tasks_list_and_detail_return_seeded_task(
        self, api_client, organization, workspace, user
    ):
        """AnnotationTask list/detail expose the read-only legacy task contract."""
        task = _create_annotation_task(
            organization=organization,
            workspace=workspace,
            user=user,
            name="Legacy annotation task contract",
        )
        api_client.force_authenticate(user=user)
        api_client.set_workspace(workspace)

        response = api_client.get(
            "/model-hub/annotation-tasks/",
            {
                "page": 1,
                "limit": 10,
                "predictive_journey": str(task.ai_model_id),
            },
        )

        assert response.status_code == status.HTTP_200_OK
        results = response.json()["results"]
        assert [row["id"] for row in results] == [str(task.id)]
        assert results[0]["task_name"] == task.task_name
        assert results[0]["ai_model"]["id"] == str(task.ai_model_id)
        assert [assigned["id"] for assigned in results[0]["assigned_users"]] == [
            str(user.id)
        ]

        detail = api_client.get(f"/model-hub/annotation-tasks/{task.id}/")

        assert detail.status_code == status.HTTP_200_OK
        detail_payload = detail.json()
        assert detail_payload["id"] == str(task.id)
        assert detail_payload["task_name"] == task.task_name
        assert detail_payload["ai_model"]["id"] == str(task.ai_model_id)

    def test_annotation_tasks_reject_same_org_other_workspace_task(
        self, api_client, organization, workspace, user
    ):
        """AnnotationTask read routes must stay scoped to request.workspace."""
        other_workspace = Workspace.objects.create(
            name="Other annotation task workspace",
            organization=organization,
            is_default=False,
            created_by=user,
        )
        task = _create_annotation_task(
            organization=organization,
            workspace=other_workspace,
            user=user,
            name="Other workspace annotation task",
        )
        api_client.force_authenticate(user=user)
        api_client.set_workspace(workspace)

        list_response = api_client.get(
            "/model-hub/annotation-tasks/",
            {
                "page": 1,
                "limit": 10,
                "predictive_journey": str(task.ai_model_id),
            },
        )
        detail_response = api_client.get(f"/model-hub/annotation-tasks/{task.id}/")

        assert list_response.status_code == status.HTTP_200_OK
        assert list_response.json()["results"] == []
        assert detail_response.status_code == status.HTTP_404_NOT_FOUND

    def test_bulk_destroy_rejects_legacy_annotation_ids_alias(
        self, auth_client, annotation
    ):
        """bulk_destroy accepts annotation_ids only, not annotationIds."""
        response = auth_client.post(
            "/model-hub/annotations/bulk_destroy/",
            {"annotationIds": [str(annotation.id)]},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_annotate_row(self, auth_client, annotation, row):
        """Test annotating a specific row."""
        response = auth_client.get(
            f"/model-hub/annotations/{annotation.id}/annotate_row/?row_order={row.order}"
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        # Response can be {"data": ...} or {"result": {"data": ...}}
        result = data.get("result", data)
        assert "data" in result or "label" in result.get("data", result)

    def test_annotate_row_rejects_legacy_row_order_alias(self, auth_client, annotation):
        """annotate_row accepts row_order only, not rowOrder."""
        response = auth_client.get(
            f"/model-hub/annotations/{annotation.id}/annotate_row/?rowOrder=0"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_annotate_row_missing_row_order(self, auth_client, annotation):
        """Test annotating without row_order parameter."""
        response = auth_client.get(
            f"/model-hub/annotations/{annotation.id}/annotate_row/"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_annotate_row_invalid_row_order(self, auth_client, annotation):
        """Test annotating with non-existent row_order."""
        response = auth_client.get(
            f"/model-hub/annotations/{annotation.id}/annotate_row/?row_order=99999"
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_update_cells_not_assigned_user(
        self, auth_client, annotation, row, other_user
    ):
        """Test that non-assigned users cannot update cells."""
        # Remove user from assigned users
        annotation.assigned_users.clear()
        annotation.assigned_users.add(other_user)
        annotation.save()

        payload = {
            "label_values": [
                {
                    "row_id": str(row.id),
                    "label_id": str(annotation.labels.first().id),
                    "column_id": str(uuid.uuid4()),
                    "value": 5,
                }
            ]
        }
        response = auth_client.post(
            f"/model-hub/annotations/{annotation.id}/update_cells/",
            payload,
            format="json",
        )
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    def test_update_cells_rejects_legacy_label_values_alias(
        self, auth_client, annotation, row
    ):
        """update_cells accepts label_values only, not labelValues."""
        payload = {
            "labelValues": [
                {
                    "row_id": str(row.id),
                    "label_id": str(annotation.labels.first().id),
                    "column_id": str(uuid.uuid4()),
                    "value": 5,
                }
            ]
        }
        response = auth_client.post(
            f"/model-hub/annotations/{annotation.id}/update_cells/",
            payload,
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_update_cells_accepts_zero_label_value(
        self, auth_client, annotation, row, column
    ):
        """Falsy-but-valid annotation values should survive request validation."""
        payload = {
            "label_values": [
                {
                    "row_id": str(row.id),
                    "label_id": str(annotation.labels.first().id),
                    "column_id": str(column.id),
                    "value": 0,
                }
            ]
        }
        response = auth_client.post(
            f"/model-hub/annotations/{annotation.id}/update_cells/",
            payload,
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK

    def test_update_cells_missing_data(self, auth_client, annotation):
        """Test update_cells with missing label_values and response_field_values."""
        payload = {}
        response = auth_client.post(
            f"/model-hub/annotations/{annotation.id}/update_cells/",
            payload,
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_reset_annotations(self, auth_client, annotation, row):
        """Test resetting annotations for a row."""
        payload = {"row_id": str(row.id)}
        response = auth_client.post(
            f"/model-hub/annotations/{annotation.id}/reset_annotations/",
            payload,
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

    def test_reset_annotations_missing_row_id(self, auth_client, annotation):
        """Test reset_annotations without row_id."""
        payload = {}
        response = auth_client.post(
            f"/model-hub/annotations/{annotation.id}/reset_annotations/",
            payload,
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_reset_annotations_rejects_legacy_row_id_alias(
        self, auth_client, annotation
    ):
        """reset_annotations accepts row_id only, not rowId."""
        response = auth_client.post(
            f"/model-hub/annotations/{annotation.id}/reset_annotations/",
            {"rowId": str(uuid.uuid4())},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_preview_annotations(self, auth_client, dataset, column, row, cell):
        """Test previewing annotations."""
        payload = {
            "dataset_id": str(dataset.id),
            "static_column": [str(column.id)],
        }
        response = auth_client.post(
            "/model-hub/annotations/preview_annotations/",
            payload,
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        result = data.get("result", data)
        assert "preview_data" in result.get("data", result)

    def test_preview_annotations_missing_dataset_id(self, auth_client):
        """Test preview_annotations without dataset_id."""
        payload = {"static_column": ["some-id"]}
        response = auth_client.post(
            "/model-hub/annotations/preview_annotations/",
            payload,
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_preview_annotations_rejects_legacy_column_aliases(
        self, auth_client, dataset, column
    ):
        """preview_annotations accepts static_column/response_column only."""
        payload = {
            "dataset_id": str(dataset.id),
            "staticColumn": [str(column.id)],
        }
        response = auth_client.post(
            "/model-hub/annotations/preview_annotations/",
            payload,
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_preview_annotations_missing_columns(self, auth_client, dataset):
        """Test preview_annotations without any columns."""
        payload = {"dataset_id": str(dataset.id)}
        response = auth_client.post(
            "/model-hub/annotations/preview_annotations/",
            payload,
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST


# ==================== UserViewSet Tests ====================


@pytest.mark.django_db
class TestUserViewSet:
    """Tests for UserViewSet."""

    def test_list_users_in_organization(self, auth_client, organization, user):
        """Test listing users in an organization."""
        response = auth_client.get(f"/model-hub/organizations/{organization.id}/users/")
        assert response.status_code == status.HTTP_200_OK
        rows = response.json()["results"]
        assert [row["email"] for row in rows] == [user.email]
        assert rows[0]["id"] == str(user.id)

    def test_list_users_filter_active(self, auth_client, organization, user):
        """Test filtering users by is_active=true."""
        response = auth_client.get(
            f"/model-hub/organizations/{organization.id}/users/?is_active=true"
        )
        assert response.status_code == status.HTTP_200_OK
        rows = response.json()["results"]
        assert [row["id"] for row in rows] == [str(user.id)]

    def test_list_users_filter_inactive(self, auth_client, organization, user):
        """Test filtering users by is_active=false."""
        response = auth_client.get(
            f"/model-hub/organizations/{organization.id}/users/?is_active=false"
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["results"] == []

    def test_list_users_search_matches_name_or_email(
        self, auth_client, organization, user
    ):
        response = auth_client.get(
            f"/model-hub/organizations/{organization.id}/users/?search={user.email}"
        )

        assert response.status_code == status.HTTP_200_OK
        assert [row["id"] for row in response.json()["results"]] == [str(user.id)]

    def test_list_users_search_no_match_returns_empty_page(
        self, auth_client, organization
    ):
        response = auth_client.get(
            f"/model-hub/organizations/{organization.id}/users/?search=no-such-user"
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["results"] == []

    def test_list_users_nonexistent_organization(self, auth_client):
        """Test listing users for non-existent organization."""
        fake_org_id = uuid.uuid4()
        response = auth_client.get(f"/model-hub/organizations/{fake_org_id}/users/")
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_list_users_rejects_cross_organization_path(
        self, auth_client, other_organization, other_org_user
    ):
        from accounts.models.organization_membership import OrganizationMembership
        from tfc.constants.levels import Level
        from tfc.constants.roles import OrganizationRoles

        OrganizationMembership.no_workspace_objects.create(
            user=other_org_user,
            organization=other_organization,
            role=OrganizationRoles.MEMBER,
            level=Level.MEMBER,
            is_active=True,
        )

        response = auth_client.get(
            f"/model-hub/organizations/{other_organization.id}/users/"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert User.objects.filter(id=other_org_user.id).exists()

    def test_retrieve_user_scoped_to_requested_organization(
        self, auth_client, organization, user
    ):
        response = auth_client.get(
            f"/model-hub/organizations/{organization.id}/users/{user.id}/"
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["email"] == user.email

    @pytest.mark.parametrize(
        "method,payload",
        [
            ("post", {"email": "new-org-route@example.com", "name": "New Route User"}),
            ("put", {"email": "changed@example.com", "name": "Changed Name"}),
            ("patch", {"name": "Changed Name"}),
            ("delete", None),
        ],
    )
    def test_authenticated_user_mutations_are_disabled(
        self, auth_client, organization, user, method, payload
    ):
        path = f"/model-hub/organizations/{organization.id}/users/"
        if method != "post":
            path = f"{path}{user.id}/"
        before_count = User.objects.count()

        request = getattr(auth_client, method)
        response = (
            request(path, payload, format="json")
            if payload is not None
            else request(path)
        )

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED
        user.refresh_from_db()
        assert user.email == "test@example.com"
        assert user.name == "Test User"
        assert user.is_active is True
        assert User.objects.count() == before_count

    def test_list_users_includes_new_rbac_org_member_without_legacy_user_org(
        self, auth_client, organization
    ):
        """RBAC-created org members appear without manually setting user.organization."""
        from accounts.models.organization_membership import OrganizationMembership
        from tfc.constants.levels import Level
        from tfc.constants.roles import OrganizationRoles

        new_user = User.objects.create_user(
            email="new-member@example.com",
            password="testpassword123",
            name="New Member",
            organization=None,
        )
        OrganizationMembership.no_workspace_objects.create(
            user=new_user,
            organization=organization,
            role=OrganizationRoles.MEMBER,
            level=Level.MEMBER,
            is_active=True,
        )

        response = auth_client.get(f"/model-hub/organizations/{organization.id}/users/")

        assert response.status_code == status.HTTP_200_OK
        payload = response.json()
        rows = payload.get("results", payload)
        assert str(new_user.id) in {str(row["id"]) for row in rows}

    def test_workspace_member_queryset_includes_new_rbac_user_without_manual_fk(
        self, organization, workspace, user
    ):
        """Queue settings uses workspace membership, not the legacy User.organization FK."""
        from accounts.models.organization_membership import OrganizationMembership
        from accounts.models.workspace import WorkspaceMembership
        from model_hub.views.develop_annotations import UserViewSet
        from tfc.constants.levels import Level
        from tfc.constants.roles import OrganizationRoles

        new_user = User.objects.create_user(
            email="workspace-new-member@example.com",
            password="testpassword123",
            name="Workspace New Member",
            organization=None,
        )
        org_membership = OrganizationMembership.no_workspace_objects.create(
            user=new_user,
            organization=organization,
            role=OrganizationRoles.MEMBER,
            level=Level.MEMBER,
            is_active=True,
        )
        WorkspaceMembership.no_workspace_objects.create(
            user=new_user,
            workspace=workspace,
            role=OrganizationRoles.WORKSPACE_MEMBER,
            level=Level.WORKSPACE_MEMBER,
            is_active=True,
            organization_membership=org_membership,
        )

        view = UserViewSet()
        view.kwargs = {"organization_id": str(organization.id)}
        view.request = SimpleNamespace(query_params={}, workspace=workspace, user=user)

        assert str(new_user.id) in {
            str(user_id) for user_id in view.get_queryset().values_list("id", flat=True)
        }

    def test_workspace_member_queryset_includes_org_admin_auto_access_user(
        self, organization, workspace, user
    ):
        """Org Admin+ users appear in queue settings even without explicit WS rows."""
        from accounts.models.organization_membership import OrganizationMembership
        from model_hub.views.develop_annotations import UserViewSet
        from tfc.constants.levels import Level
        from tfc.constants.roles import OrganizationRoles

        admin_user = User.objects.create_user(
            email="workspace-auto-admin@example.com",
            password="testpassword123",
            name="Workspace Auto Admin",
            organization=None,
        )
        OrganizationMembership.no_workspace_objects.create(
            user=admin_user,
            organization=organization,
            role=OrganizationRoles.ADMIN,
            level=Level.ADMIN,
            is_active=True,
        )

        view = UserViewSet()
        view.kwargs = {"organization_id": str(organization.id)}
        view.request = SimpleNamespace(query_params={}, workspace=workspace, user=user)

        assert str(admin_user.id) in {
            str(user_id) for user_id in view.get_queryset().values_list("id", flat=True)
        }

    def test_workspace_only_requester_without_org_membership_passes_gate(
        self, organization, workspace
    ):
        """A workspace member with no OrganizationMembership can still list
        annotators — the access gate accepts workspace-based access, matching the
        member-listing it guards (TH-6156)."""
        from accounts.models.workspace import WorkspaceMembership
        from model_hub.views.develop_annotations import UserViewSet
        from tfc.constants.levels import Level
        from tfc.constants.roles import OrganizationRoles

        requester = User.objects.create_user(
            email="ws-only-requester@example.com",
            password="testpassword123",
            name="WS Only Requester",
            organization=None,
        )
        # Deliberate drift: workspace access but NO OrganizationMembership row.
        WorkspaceMembership.no_workspace_objects.create(
            user=requester,
            workspace=workspace,
            role=OrganizationRoles.WORKSPACE_MEMBER,
            level=Level.WORKSPACE_MEMBER,
            is_active=True,
        )

        view = UserViewSet()
        view.kwargs = {"organization_id": str(organization.id)}
        view.request = SimpleNamespace(
            query_params={},
            workspace=workspace,
            organization=organization,
            user=requester,
        )

        # Must not raise NotFound, and the requester is a selectable annotator.
        ids = {
            str(user_id) for user_id in view.get_queryset().values_list("id", flat=True)
        }
        assert str(requester.id) in ids

    def test_requesting_user_always_included_as_annotator(
        self, organization, workspace
    ):
        """The requester is always selectable for their own queue, even when they
        are neither a member of the active workspace nor an org admin — so an
        empty workspace no longer surfaces the misleading 404 (TH-6156)."""
        from accounts.models.organization_membership import OrganizationMembership
        from model_hub.views.develop_annotations import UserViewSet
        from tfc.constants.levels import Level
        from tfc.constants.roles import OrganizationRoles

        requester = User.objects.create_user(
            email="plain-org-member@example.com",
            password="testpassword123",
            name="Plain Org Member",
            organization=None,
        )
        # Org Member (not admin) with no WorkspaceMembership in `workspace`:
        # only self-inclusion can place them in the result.
        OrganizationMembership.no_workspace_objects.create(
            user=requester,
            organization=organization,
            role=OrganizationRoles.MEMBER,
            level=Level.MEMBER,
            is_active=True,
        )

        view = UserViewSet()
        view.kwargs = {"organization_id": str(organization.id)}
        view.request = SimpleNamespace(
            query_params={},
            workspace=workspace,
            organization=organization,
            user=requester,
        )

        ids = {
            str(user_id) for user_id in view.get_queryset().values_list("id", flat=True)
        }
        assert str(requester.id) in ids

    def test_lists_members_when_a_different_org_is_active(
        self, organization, workspace, user
    ):
        """The requested org may differ from the active org/workspace. A member
        of the requested org still gets its member list instead of a 404 — the
        exact failure from the annotator picker passing the queue's org while a
        different org is the active context (TH-6156)."""
        from accounts.models.organization_membership import OrganizationMembership
        from model_hub.views.develop_annotations import UserViewSet
        from tfc.constants.levels import Level
        from tfc.constants.roles import OrganizationRoles

        # `user` (fixture) is an Owner of `organization` with `workspace`.
        # Create a SECOND org + workspace and make it the active request context.
        other_org = Organization.objects.create(name="Other Active Org")
        other_ws = Workspace.objects.create(
            name="Other Active WS",
            organization=other_org,
            is_active=True,
            created_by=user,
        )
        OrganizationMembership.no_workspace_objects.create(
            user=user,
            organization=other_org,
            role=OrganizationRoles.OWNER,
            level=Level.OWNER,
            is_active=True,
        )

        view = UserViewSet()
        view.kwargs = {"organization_id": str(organization.id)}
        # Active context points at other_org/other_ws, but the URL asks for
        # `organization` — the mismatch that used to raise 404.
        view.request = SimpleNamespace(
            query_params={},
            workspace=other_ws,
            organization=other_org,
            user=user,
        )

        ids = {
            str(user_id) for user_id in view.get_queryset().values_list("id", flat=True)
        }
        # Must not 404, and must return the requested org's members (user owns it).
        assert str(user.id) in ids

    def test_removed_org_member_is_not_reauthorized_by_workspace_drift(
        self, organization, workspace
    ):
        """A deliberately removed org member (inactive OrganizationMembership) is
        rejected even if a stale active WorkspaceMembership lingers — the gate
        fails closed and never lets workspace drift resurrect access (TH-6156)."""
        from rest_framework.exceptions import NotFound

        from accounts.models.organization_membership import OrganizationMembership
        from accounts.models.workspace import WorkspaceMembership
        from model_hub.views.develop_annotations import UserViewSet
        from tfc.constants.levels import Level
        from tfc.constants.roles import OrganizationRoles

        removed = User.objects.create_user(
            email="removed-member@example.com",
            password="testpassword123",
            name="Removed Member",
            organization=None,
        )
        # Inactive (removed) org membership + a still-active workspace row.
        OrganizationMembership.no_workspace_objects.create(
            user=removed,
            organization=organization,
            role=OrganizationRoles.MEMBER,
            level=Level.MEMBER,
            is_active=False,
        )
        WorkspaceMembership.no_workspace_objects.create(
            user=removed,
            workspace=workspace,
            role=OrganizationRoles.WORKSPACE_MEMBER,
            level=Level.WORKSPACE_MEMBER,
            is_active=True,
        )

        view = UserViewSet()
        view.kwargs = {"organization_id": str(organization.id)}
        view.request = SimpleNamespace(
            query_params={},
            workspace=workspace,
            organization=organization,
            user=removed,
        )

        with pytest.raises(NotFound):
            list(view.get_queryset())

    def test_membership_in_dead_workspace_does_not_pass_gate(self, organization, user):
        """A workspace-only requester whose only membership is in a soft-deleted
        (or deactivated) workspace is rejected — a dead workspace's stale
        membership must not authorize (TH-6156)."""
        from rest_framework.exceptions import NotFound

        from accounts.models.workspace import Workspace, WorkspaceMembership
        from model_hub.views.develop_annotations import UserViewSet
        from tfc.constants.levels import Level
        from tfc.constants.roles import OrganizationRoles

        dead_ws = Workspace.objects.create(
            name="Dead WS",
            organization=organization,
            is_active=True,
            created_by=user,
        )
        requester = User.objects.create_user(
            email="dead-ws-requester@example.com",
            password="testpassword123",
            name="Dead WS Requester",
            organization=None,
        )
        WorkspaceMembership.no_workspace_objects.create(
            user=requester,
            workspace=dead_ws,
            role=OrganizationRoles.WORKSPACE_MEMBER,
            level=Level.WORKSPACE_MEMBER,
            is_active=True,
        )
        # Soft-delete the workspace after the membership exists.
        Workspace.objects.filter(pk=dead_ws.pk).update(deleted=True)

        view = UserViewSet()
        view.kwargs = {"organization_id": str(organization.id)}
        view.request = SimpleNamespace(
            query_params={},
            workspace=None,
            organization=organization,
            user=requester,
        )

        with pytest.raises(NotFound):
            list(view.get_queryset())

    def test_workspace_only_requester_scoped_to_own_workspace_members(
        self, organization, workspace, user
    ):
        """A workspace-only (drift) requester whose active workspace is a
        different org is scoped to the members of the workspace(s) they belong to
        — never the full org member list (TH-6156)."""
        from accounts.models.organization_membership import OrganizationMembership
        from accounts.models.workspace import WorkspaceMembership
        from model_hub.views.develop_annotations import UserViewSet
        from tfc.constants.levels import Level
        from tfc.constants.roles import OrganizationRoles

        # Drift requester: workspace access in `workspace`, no org membership.
        requester = User.objects.create_user(
            email="drift-scoped-requester@example.com",
            password="testpassword123",
            name="Drift Scoped Requester",
            organization=None,
        )
        WorkspaceMembership.no_workspace_objects.create(
            user=requester,
            workspace=workspace,
            role=OrganizationRoles.WORKSPACE_MEMBER,
            level=Level.WORKSPACE_MEMBER,
            is_active=True,
        )
        # A peer sharing the same workspace — should be visible.
        ws_peer = User.objects.create_user(
            email="ws-peer@example.com",
            password="testpassword123",
            name="WS Peer",
            organization=None,
        )
        WorkspaceMembership.no_workspace_objects.create(
            user=ws_peer,
            workspace=workspace,
            role=OrganizationRoles.WORKSPACE_MEMBER,
            level=Level.WORKSPACE_MEMBER,
            is_active=True,
        )
        # An org member NOT in `workspace` — must NOT leak to a workspace-only
        # requester.
        org_only = User.objects.create_user(
            email="org-only-member@example.com",
            password="testpassword123",
            name="Org Only Member",
            organization=None,
        )
        OrganizationMembership.no_workspace_objects.create(
            user=org_only,
            organization=organization,
            role=OrganizationRoles.MEMBER,
            level=Level.MEMBER,
            is_active=True,
        )

        # Active context is a *different* org, so the org-wide fallback branch is
        # the one under test.
        other_org = Organization.objects.create(name="Other Active Org 2")
        other_ws = Workspace.objects.create(
            name="Other Active WS 2",
            organization=other_org,
            is_active=True,
            created_by=user,
        )

        view = UserViewSet()
        view.kwargs = {"organization_id": str(organization.id)}
        view.request = SimpleNamespace(
            query_params={},
            workspace=other_ws,
            organization=other_org,
            user=requester,
        )

        ids = {
            str(user_id) for user_id in view.get_queryset().values_list("id", flat=True)
        }
        assert str(requester.id) in ids
        assert str(ws_peer.id) in ids
        # The org-wide member list must NOT leak to a workspace-scoped requester.
        assert str(org_only.id) not in ids


# ==================== AnnotationSummaryView Tests ====================


@pytest.mark.django_db
class TestAnnotationSummaryView:
    """Tests for AnnotationSummaryView."""

    @patch(
        "ee.usage.services.entitlements.Entitlements.check_feature",
        return_value=SimpleNamespace(allowed=True, reason=None),
    )
    @patch("model_hub.services.annotation_summary_service.get_annotation_summary_data")
    def test_get_annotation_summary(
        self, mock_summary_service, mock_check_feature, auth_client, dataset
    ):
        """Test getting annotation summary statistics."""
        import pandas as pd

        # Mock the summary service response
        mock_summary_service.return_value = {
            "header_data": pd.DataFrame({"label_id": [], "type": [], "name": []}),
            "metric_calc": pd.DataFrame(
                {"label_id": [], "row_id": [], "user_id": [], "value": []}
            ),
            "graph": pd.DataFrame(
                {"label_id": [], "bucket_min": [], "bucket_max": [], "count": []}
            ),
            "heatmap": pd.DataFrame(
                {
                    "label_id": [],
                    "user_id": [],
                    "bucket_min": [],
                    "bucket_max": [],
                    "count": [],
                }
            ),
            "annotator_performance": pd.DataFrame(
                {"user_id": [], "avg_time": [], "total_annotations": []}
            ),
            "dataset_annot_summary": pd.DataFrame(
                {"fully_annotated_rows": [10], "not_deleted_rows": [20]}
            ),
        }

        response = auth_client.get(
            f"/model-hub/dataset/{dataset.id}/annotation-summary/"
        )
        assert response.status_code == status.HTTP_200_OK

    def test_get_annotation_summary_invalid_dataset(self, auth_client):
        """Test getting summary for non-existent dataset."""
        fake_dataset_id = uuid.uuid4()
        response = auth_client.get(
            f"/model-hub/dataset/{fake_dataset_id}/annotation-summary/"
        )
        # Should handle gracefully
        assert response.status_code in [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST]


# ==================== Organization Isolation Tests ====================


@pytest.mark.django_db
class TestAnnotationsOrganizationIsolation:
    """Tests for organization isolation in annotations."""

    def test_cannot_access_other_org_annotation_labels(
        self, auth_client, other_organization, other_org_user, numeric_label_settings
    ):
        """Test that users cannot see annotation labels from other organizations."""
        # Create label in other org
        other_workspace = Workspace.objects.create(
            name="Other Workspace",
            organization=other_organization,
            is_default=True,
            created_by=other_org_user,
        )
        other_label = AnnotationsLabels.objects.create(
            name="Other Org Label",
            type=AnnotationTypeChoices.NUMERIC.value,
            organization=other_organization,
            workspace=other_workspace,
            settings=numeric_label_settings,
        )

        response = auth_client.get("/model-hub/annotations-labels/")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        # Should not contain the other org's label
        label_ids = [item["id"] for item in data.get("results", [])]
        assert str(other_label.id) not in label_ids

    def test_cannot_access_other_org_annotation(
        self, auth_client, other_organization, other_org_user, numeric_label_settings
    ):
        """Test that users cannot access annotations from other organizations."""
        # Create annotation in other org
        other_workspace = Workspace.objects.create(
            name="Other Workspace 2",
            organization=other_organization,
            is_default=True,
            created_by=other_org_user,
        )
        other_dataset = Dataset.objects.create(
            name="Other Dataset",
            organization=other_organization,
            workspace=other_workspace,
            source=DatasetSourceChoices.BUILD.value,
        )
        other_annotation = Annotations.objects.create(
            name="Other Org Annotation",
            organization=other_organization,
            workspace=other_workspace,
            dataset=other_dataset,
        )

        response = auth_client.get(f"/model-hub/annotations/{other_annotation.id}/")
        assert response.status_code == status.HTTP_404_NOT_FOUND


# ==================== Authentication Tests ====================


@pytest.mark.django_db
class TestAnnotationsAuthentication:
    """Tests for authentication requirements."""

    def test_unauthenticated_list_annotations(self):
        """Test that unauthenticated users cannot list annotations."""
        client = APIClient()
        response = client.get("/model-hub/annotations/")
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_unauthenticated_list_annotation_labels(self):
        """Test that unauthenticated users cannot list annotation labels."""
        client = APIClient()
        response = client.get("/model-hub/annotations-labels/")
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_unauthenticated_create_annotation(self):
        """Test that unauthenticated users cannot create annotations."""
        client = APIClient()
        response = client.post("/model-hub/annotations/", {}, format="json")
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_unauthenticated_annotation_summary(self, dataset):
        """Test that unauthenticated users cannot get annotation summary."""
        client = APIClient()
        response = client.get(f"/model-hub/dataset/{dataset.id}/annotation-summary/")
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]
