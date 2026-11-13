"""API-key dispatch and worker isolation for legacy auto-annotation."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from django.core.exceptions import ObjectDoesNotExist
from rest_framework.test import APIClient

from accounts.models import Organization, OrgApiKey, User
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from model_hub.models.choices import (
    AnnotationTypeChoices,
    DataTypeChoices,
    SourceChoices,
)
from model_hub.models.develop_annotations import Annotations, AnnotationsLabels
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.utils import auto_annotate
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import clear_workspace_context
from tracer.models.project import Project

pytestmark = pytest.mark.django_db


def _tenant(suffix, organization=None):
    clear_workspace_context()
    organization = organization or Organization.objects.create(name=f"Tenant {suffix}")
    user = User.objects.create_user(
        email=f"auto-{suffix}@example.com",
        password=None,  # These tests authenticate with API keys, not passwords.
        organization=organization,
    )
    membership, _ = OrganizationMembership.no_workspace_objects.get_or_create(
        user=user,
        organization=organization,
        defaults={
            "role": OrganizationRoles.OWNER,
            "level": Level.OWNER,
            "is_active": True,
        },
    )
    workspace = Workspace.objects.create(
        name=f"Workspace {suffix}", organization=organization, created_by=user
    )
    WorkspaceMembership.no_workspace_objects.create(
        user=user,
        workspace=workspace,
        organization_membership=membership,
        role="Workspace Owner",
        level=Level.OWNER,
        is_active=True,
    )
    project = Project.objects.create(
        name="Colliding project name",
        organization=organization,
        workspace=workspace,
        model_type="GenerativeLLM",
        trace_type="observe",
    )
    dataset = Dataset.objects.create(
        name="Colliding dataset name", organization=organization, workspace=workspace
    )
    label = AnnotationsLabels.objects.create(
        name="Colliding label name",
        organization=organization,
        workspace=workspace,
        project=project,
        type=AnnotationTypeChoices.CATEGORICAL.value,
        settings={
            "rule_prompt": "Choose a category",
            "options": [{"label": "Yes"}, {"label": "No"}],
            "multi_choice": False,
            "auto_annotate": True,
            "strategy": None,
            "inputs": [],
        },
    )
    row = Row.objects.create(dataset=dataset, order=0)
    key = OrgApiKey.objects.create(
        name="Auto-annotation test",
        organization=organization,
        workspace=workspace,
        user=user,
        type="user",
    )
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=key.api_key, HTTP_X_SECRET_KEY=key.secret_key)
    return SimpleNamespace(
        organization=organization,
        user=user,
        workspace=workspace,
        project=project,
        dataset=dataset,
        label=label,
        row=row,
        client=client,
    )


@pytest.fixture
def tenants():
    own = _tenant("own")
    foreign = _tenant("foreign")
    sibling = _tenant("sibling", organization=own.organization)
    yield own, foreign, sibling
    clear_workspace_context()


def _annotation(tenant):
    annotation = Annotations.objects.create(
        name="Colliding annotation name",
        organization=tenant.organization,
        workspace=tenant.workspace,
        dataset=tenant.dataset,
    )
    annotation.labels.add(tenant.label)
    column = _output_column(
        tenant.dataset, f"{annotation.id}-sourceid-{tenant.label.id}"
    )
    annotation.columns.add(column)
    return annotation, column


def _output_column(dataset, source_id):
    return Column.objects.create(
        name="Colliding output",
        dataset=dataset,
        source_id=source_id,
        source=SourceChoices.ANNOTATION_LABEL.value,
        data_type=DataTypeChoices.TEXT.value,
    )


def _payload(tenant):
    return {
        "name": "API automatic annotation",
        "dataset": str(tenant.dataset.id),
        "assigned_users": [str(tenant.user.id)],
        "labels": [{"id": str(tenant.label.id), "required": False}],
        "responses": 1,
    }


@pytest.fixture
def model_boundaries():
    agent = Mock()
    agent.evaluate.return_value = {"choices": "Yes", "explanation": "Test response"}
    embeddings = Mock()
    embeddings.inputs_type_list.return_value = []
    with (
        patch.object(auto_annotate, "DeterministicAgent", return_value=agent),
        patch.object(auto_annotate, "EmbeddingManager", return_value=embeddings),
        patch(
            "ee.usage.services.metering.check_usage",
            return_value=SimpleNamespace(allowed=True),
        ) as usage,
        patch("ee.usage.services.emitter.emit"),
    ):
        yield agent, usage


@pytest.mark.parametrize("uuid_shaped_name", [False, True])
def test_real_api_key_dispatch_serializes_organization_id_and_runs_worker(
    tenants, model_boundaries, uuid_shaped_name
):
    own, foreign, _ = tenants
    if uuid_shaped_name:
        own.organization.name = str(foreign.organization.id)
        own.organization.save()
    temporal_client = SimpleNamespace(namespace="test", start_workflow=AsyncMock())
    # Only the network client is mocked: authentication, apply_async, sync bridge,
    # runner conversion and worker ORM operations remain real.
    with patch(
        "tfc.temporal.common.client.get_client", AsyncMock(return_value=temporal_client)
    ):
        response = own.client.post(
            "/model-hub/annotations/", _payload(own), format="json"
        )
    assert response.status_code == 200, response.content
    temporal_client.start_workflow.assert_awaited_once()
    wire_input = temporal_client.start_workflow.call_args.args[1]
    clear_workspace_context()  # Workers have no authenticated request context.
    auto_annotate.generate_annotations_task._original_func(
        *wire_input.args, **wire_input.kwargs
    )
    assert wire_input.args[2] == str(own.organization.id)
    annotation = Annotations.objects.get(id=wire_input.args[1])
    output = Cell.objects.get(
        dataset=own.dataset, row=own.row, column=annotation.columns.get()
    )
    assert output.value == "Yes"
    assert output.feedback_info["annotation"]["auto_annotate"] is True
    model_boundaries[0].evaluate.assert_called_once()
    assert model_boundaries[1].call_args.args[0] == str(own.organization.id)
    assert not Cell.objects.filter(dataset=foreign.dataset).exists()


@pytest.mark.parametrize("other_index", [1, 2])
@pytest.mark.parametrize("field", ["dataset", "labels"])
def test_api_key_rejects_foreign_scope_before_dispatch(tenants, other_index, field):
    own, *_ = tenants
    other = tenants[other_index]
    payload = _payload(own)
    payload[field] = (
        str(other.dataset.id)
        if field == "dataset"
        else [{"id": str(other.label.id), "required": False}]
    )
    with patch.object(
        auto_annotate.generate_annotations_task, "apply_async"
    ) as dispatch:
        response = own.client.post("/model-hub/annotations/", payload, format="json")
    assert response.status_code in (400, 404), response.content
    dispatch.assert_not_called()
    clear_workspace_context()
    assert not Annotations.objects.filter(dataset=other.dataset).exists()
    assert not Column.objects.filter(dataset=other.dataset).exists()


@pytest.mark.parametrize(
    "bad_scope",
    [
        "organization",
        "label",
        "workspace_label",
        "dataset",
        "workspace_dataset",
        "missing_org",
    ],
)
def test_worker_rejects_mismatched_scope_before_model_or_writes(tenants, bad_scope):
    own, foreign, sibling = tenants
    annotation, _ = _annotation(own)
    label_id, org_id = own.label.id, own.organization.id
    if bad_scope == "organization":
        org_id = foreign.organization.id
    elif bad_scope == "label":
        label_id = foreign.label.id
    elif bad_scope == "workspace_label":
        label_id = sibling.label.id
    elif bad_scope == "dataset":
        Annotations.objects.filter(pk=annotation.pk).update(dataset=foreign.dataset)
    elif bad_scope == "workspace_dataset":
        Annotations.objects.filter(pk=annotation.pk).update(dataset=sibling.dataset)
    else:
        org_id = None
    with patch.object(auto_annotate.AutoAnnotation, "generate_annotations") as generate:
        with pytest.raises((ObjectDoesNotExist, ValueError)):
            auto_annotate.generate_annotations_task._original_func(
                label_id, annotation.id, org_id
            )
    generate.assert_not_called()
    assert not Cell.objects.exists()


@pytest.mark.parametrize("is_default", [False, True])
def test_worker_rejects_consistent_references_to_foreign_workspace(
    tenants, model_boundaries, is_default
):
    own, foreign, _ = tenants
    annotation, _ = _annotation(own)
    workspace = foreign.workspace
    if is_default:
        workspace = Workspace.objects.create(
            organization=foreign.organization,
            is_default=True,
            name="Default",
            created_by=foreign.user,
        )
    # All three records still belong to A, and agree on B's workspace. Agreement
    # alone is insufficient: the workspace itself must also belong to A.
    for record in (annotation, own.dataset, own.label):
        type(record).no_workspace_objects.filter(pk=record.pk).update(
            workspace=workspace
        )
    with pytest.raises(ValueError, match="Workspace does not belong"):
        auto_annotate.generate_annotations_task._original_func(
            own.label.id, annotation.id, own.organization.id
        )
    model_boundaries[0].evaluate.assert_not_called()
    model_boundaries[1].assert_not_called()
    assert not Cell.objects.exists()


@pytest.mark.parametrize("annotation_default", [False, True])
@pytest.mark.parametrize("dataset_default", [False, True])
@pytest.mark.parametrize("label_default", [False, True])
def test_worker_accepts_own_default_and_null_workspace_combinations(
    tenants, model_boundaries, annotation_default, dataset_default, label_default
):
    own = tenants[0]
    annotation, column = _annotation(own)
    default = Workspace.objects.create(
        organization=own.organization,
        is_default=True,
        name="Default",
        created_by=own.user,
    )
    for record, use_default in (
        (annotation, annotation_default),
        (own.dataset, dataset_default),
        (own.label, label_default),
    ):
        type(record).no_workspace_objects.filter(pk=record.pk).update(
            workspace=default if use_default else None
        )
    auto_annotate.generate_annotations_task._original_func(
        own.label.id, annotation.id, own.organization.id
    )
    assert Cell.objects.get(column=column, row=own.row).value == "Yes"
    model_boundaries[0].evaluate.assert_called_once()


def test_worker_rejects_foreign_row_before_model_or_writes(tenants, model_boundaries):
    own, foreign, _ = tenants
    annotation, _ = _annotation(own)
    with pytest.raises(Row.DoesNotExist):
        auto_annotate.generate_annotations_task._original_func(
            own.label.id,
            annotation.id,
            own.organization.id,
            row_id=foreign.row.id,
        )
    model_boundaries[0].evaluate.assert_not_called()
    assert not Cell.objects.exists()


@pytest.mark.parametrize("failure", [False, True])
def test_worker_only_updates_requested_row_and_preserves_reviewed_cells(
    tenants, model_boundaries, failure
):
    own = tenants[0]
    annotation, column = _annotation(own)
    other_row = Row.objects.create(dataset=own.dataset, order=1)
    reviewed_column = _output_column(own.dataset, column.source_id)
    reviewed = Cell.objects.create(
        dataset=own.dataset,
        row=own.row,
        column=reviewed_column,
        value="Human value",
        feedback_info={"annotation": {"verified": True}},
    )
    if failure:
        model_boundaries[0].evaluate.side_effect = RuntimeError("Model unavailable")
    auto_annotate.generate_annotations_task._original_func(
        own.label.id,
        annotation.id,
        own.organization.id,
        row_id=own.row.id,
    )
    reviewed.refresh_from_db()
    assert reviewed.value == "Human value"
    assert not Cell.objects.filter(row=other_row).exists()
    generated = Cell.objects.get(row=own.row, column=column)
    assert generated.status == ("error" if failure else "pass")


def test_input_values_require_consistent_dataset_foreign_keys(tenants):
    own, foreign, _ = tenants
    own_column = _output_column(own.dataset, "input")
    foreign_column = _output_column(foreign.dataset, "input")
    Cell.objects.create(
        dataset=own.dataset, row=own.row, column=own_column, value="Own"
    )
    Cell.objects.create(
        dataset=own.dataset, row=own.row, column=foreign_column, value="Foreign"
    )
    assert auto_annotate._get_input_values(
        [str(own_column.id)], own.dataset.id, own.row.id
    ) == ["Own"]
    with pytest.raises(Cell.DoesNotExist):
        auto_annotate._get_input_values(
            [str(foreign_column.id)], own.dataset.id, own.row.id
        )


@pytest.mark.parametrize("other_index", [1, 2])
def test_worker_does_not_write_colliding_output_identifiers(
    tenants, model_boundaries, other_index
):
    own = tenants[0]
    other = tenants[other_index]
    annotation, own_column = _annotation(own)
    foreign_column = _output_column(other.dataset, own_column.source_id)
    sentinel = Cell.objects.create(
        dataset=other.dataset, row=other.row, column=foreign_column, value="Unchanged"
    )
    auto_annotate.generate_annotations_task._original_func(
        own.label.id, annotation.id, own.organization.id
    )
    sentinel.refresh_from_db()
    assert sentinel.value == "Unchanged"
    assert list(
        Cell.objects.filter(column=foreign_column).values_list("id", flat=True)
    ) == [sentinel.id]
    assert Cell.objects.get(row=own.row, column=own_column).value == "Yes"


def test_worker_does_not_write_colliding_identifier_in_another_dataset_same_workspace(
    tenants, model_boundaries
):
    own = tenants[0]
    annotation, own_column = _annotation(own)
    other_dataset = Dataset.objects.create(
        name="Same workspace, separate dataset",
        organization=own.organization,
        workspace=own.workspace,
    )
    other_column = _output_column(other_dataset, own_column.source_id)
    auto_annotate.generate_annotations_task._original_func(
        own.label.id, annotation.id, own.organization.id
    )
    assert not Cell.objects.filter(column=other_column).exists()
    assert Cell.objects.get(column=own_column).value == "Yes"


def test_dynamic_prompt_rejects_cell_with_foreign_column(tenants):
    own, foreign, _ = tenants
    column = _output_column(foreign.dataset, "collision")
    # A Cell's three FKs are not constrained to the same dataset by the DB.
    Cell.objects.create(
        dataset=own.dataset, row=own.row, column=column, value="Foreign"
    )
    with pytest.raises(ValueError):
        auto_annotate.replace_dynamic_ids(f"{{{{{column.id}}}}}", own.row)


@pytest.mark.parametrize("field", ["dataset", "labels"])
def test_update_rejects_foreign_scope_without_persisting_changes(tenants, field):
    own, foreign, _ = tenants
    annotation, column = _annotation(own)
    payload = {"name": "Should not persist"}
    payload[field] = (
        str(foreign.dataset.id)
        if field == "dataset"
        else [{"id": str(foreign.label.id), "required": False}]
    )
    with patch.object(
        auto_annotate.generate_annotations_task, "apply_async"
    ) as dispatch:
        response = own.client.patch(
            f"/model-hub/annotations/{annotation.id}/", payload, format="json"
        )
    assert response.status_code in (400, 404), response.content
    clear_workspace_context()
    annotation.refresh_from_db()
    assert annotation.name == "Colliding annotation name"
    assert annotation.dataset_id == own.dataset.id
    assert list(annotation.labels.values_list("id", flat=True)) == [own.label.id]
    column.refresh_from_db()
    assert not column.deleted
    dispatch.assert_not_called()


@pytest.mark.parametrize("field", ["row_id", "column_id", "label_id"])
def test_feedback_rejects_foreign_references_without_writes(tenants, field):
    own, foreign, _ = tenants
    annotation, own_column = _annotation(own)
    annotation.assigned_users.add(own.user)
    _, foreign_column = _annotation(foreign)
    update = {
        "row_id": str(own.row.id),
        "column_id": str(own_column.id),
        "label_id": str(own.label.id),
        "value": ["Yes"],
    }
    update[field] = str(
        {
            "row_id": foreign.row.id,
            "column_id": foreign_column.id,
            "label_id": foreign.label.id,
        }[field]
    )
    with (
        patch.object(
            auto_annotate.generate_annotations_task, "apply_async"
        ) as dispatch,
        patch("model_hub.views.develop_annotations.EmbeddingManager"),
    ):
        response = own.client.post(
            f"/model-hub/annotations/{annotation.id}/update_cells/",
            {"label_values": [update]},
            format="json",
        )
    assert response.status_code in (400, 404), response.content
    dispatch.assert_not_called()
    clear_workspace_context()
    assert not Cell.objects.exists()


def test_feedback_dispatch_supplies_org_id_and_row_id_separately(tenants):
    own = tenants[0]
    annotation, column = _annotation(own)
    annotation.assigned_users.add(own.user)
    temporal_client = SimpleNamespace(namespace="test", start_workflow=AsyncMock())
    with (
        patch(
            "tfc.temporal.common.client.get_client",
            AsyncMock(return_value=temporal_client),
        ),
        patch("model_hub.views.develop_annotations.EmbeddingManager"),
    ):
        response = own.client.post(
            f"/model-hub/annotations/{annotation.id}/update_cells/",
            {
                "label_values": [
                    {
                        "row_id": str(own.row.id),
                        "column_id": str(column.id),
                        "label_id": str(own.label.id),
                        "value": ["Yes"],
                    }
                ]
            },
            format="json",
        )
    assert response.status_code == 200, response.content
    wire_input = temporal_client.start_workflow.call_args.args[1]
    assert wire_input.args[:3] == [
        str(own.label.id),
        str(annotation.id),
        str(own.organization.id),
    ]
    assert wire_input.kwargs["row_id"] == str(own.row.id)


def test_feedback_dataframe_does_not_read_colliding_source_id(tenants):
    own, foreign, _ = tenants
    for tenant in (own, foreign):
        column = _output_column(tenant.dataset, "colliding-source-id")
        Cell.objects.create(
            dataset=tenant.dataset,
            row=tenant.row,
            column=column,
            value=tenant.organization.name,
            feedback_info={"update": True},
        )
    dataframe = auto_annotate.dataset_to_dataframe(
        own.dataset.id, "colliding-source-id"
    )
    assert dataframe["label_value"].tolist() == [own.organization.name]
