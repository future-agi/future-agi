"""Regression coverage for TH-3387: system prompt labels across workspaces.

Production/Staging/Development are global rows (``organization=NULL``,
``workspace=NULL``). The workspace-filtered default manager never returns
them, so:

* ``POST /model-hub/prompt-labels/remove/`` answered 400 for a system label;
* ``assign-label-by-id`` reported ``moved_from_versions`` but left the label on
  the old version, because Django's M2M ``remove()`` intersects the ids with
  that same filtered manager and silently deletes nothing.

``remove`` must also not reach a template that is not visible in the caller's
workspace, matching ``assign-label-by-id``.
"""

import uuid

import pytest
from rest_framework import status as http_status

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from conftest import WorkspaceAwareAPIClient
from model_hub.models.prompt_label import LabelTypeChoices, PromptLabel
from model_hub.models.run_prompt import PromptTemplate, PromptVersion

REMOVE_URL = "/model-hub/prompt-labels/remove/"
BULK_URL = "/model-hub/prompt-labels/assign-multiple-labels/"


def _assign_url(template, label_id):
    return f"/model-hub/prompt-labels/{template.id}/{label_id}/assign-label-by-id/"


def _result(response):
    payload = response.json()
    return payload.get("result", payload)


def _production_label(auth_client):
    response = auth_client.post("/model-hub/prompt-labels/create-system-labels/")
    assert response.status_code == http_status.HTTP_200_OK
    return PromptLabel.no_workspace_objects.get(
        name="Production",
        organization__isnull=True,
        type=LabelTypeChoices.SYSTEM.value,
    )


def _other_workspace(user):
    return Workspace.objects.create(
        name=f"TH-3387 workspace {uuid.uuid4()}",
        organization=user.organization,
        is_default=False,
        is_active=True,
        created_by=user,
    )


def _template_with_two_versions(organization, workspace, user, name):
    template = PromptTemplate.no_workspace_objects.create(
        name=name,
        organization=organization,
        workspace=workspace,
        created_by=user,
        variable_names=[],
    )
    version_one = PromptVersion.objects.create(
        original_template=template,
        template_version="v1",
        prompt_config_snapshot=[{"role": "user", "content": "v1"}],
        is_default=True,
    )
    version_two = PromptVersion.objects.create(
        original_template=template,
        template_version="v2",
        prompt_config_snapshot=[{"role": "user", "content": "v2"}],
        is_default=False,
    )
    return template, version_one, version_two


def _client_for(user, workspace):
    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=user)
    client.set_workspace(workspace)
    return client


@pytest.mark.django_db
def test_remove_detaches_system_label_in_default_workspace(
    auth_client, organization, workspace, user
):
    production = _production_label(auth_client)
    _, version_one, _ = _template_with_two_versions(
        organization, workspace, user, "TH-3387 default workspace"
    )
    version_one.labels.add(production)

    response = auth_client.post(
        REMOVE_URL,
        {"label_id": str(production.id), "version_id": str(version_one.id)},
        format="json",
    )

    assert response.status_code == http_status.HTTP_200_OK, response.json()
    assert not version_one.labels.filter(id=production.id).exists()


@pytest.mark.django_db
def test_remove_detaches_system_label_in_non_default_workspace(
    auth_client, organization, user
):
    production = _production_label(auth_client)
    other_workspace = _other_workspace(user)
    _, version_one, _ = _template_with_two_versions(
        organization, other_workspace, user, "TH-3387 other workspace"
    )
    version_one.labels.add(production)
    client = _client_for(user, other_workspace)

    response = client.post(
        REMOVE_URL,
        {"label_id": str(production.id), "version_id": str(version_one.id)},
        format="json",
    )

    assert response.status_code == http_status.HTTP_200_OK, response.json()
    assert not version_one.labels.filter(id=production.id).exists()


@pytest.mark.django_db
def test_remove_is_idempotent_for_unassigned_system_label(
    auth_client, organization, workspace, user
):
    production = _production_label(auth_client)
    _, version_one, _ = _template_with_two_versions(
        organization, workspace, user, "TH-3387 idempotent"
    )

    response = auth_client.post(
        REMOVE_URL,
        {"label_id": str(production.id), "version_id": str(version_one.id)},
        format="json",
    )

    assert response.status_code == http_status.HTTP_200_OK, response.json()
    assert not version_one.labels.exists()


@pytest.mark.django_db
@pytest.mark.parametrize("workspace_kind", ["default", "non_default"])
def test_assign_by_id_moves_system_label_between_versions(
    auth_client, organization, workspace, user, workspace_kind
):
    production = _production_label(auth_client)
    target_workspace = (
        workspace if workspace_kind == "default" else _other_workspace(user)
    )
    template, version_one, version_two = _template_with_two_versions(
        organization, target_workspace, user, f"TH-3387 exclusivity {workspace_kind}"
    )
    version_one.labels.add(production)
    client = _client_for(user, target_workspace)

    response = client.post(
        _assign_url(template, production.id), {"version": "v2"}, format="json"
    )

    assert response.status_code == http_status.HTTP_200_OK, response.json()
    assert _result(response)["moved_from_versions"] == ["v1"]
    assert not version_one.labels.filter(id=production.id).exists()
    assert version_two.labels.filter(id=production.id).exists()


@pytest.mark.django_db
def test_bulk_assign_moves_system_label_between_versions_in_non_default_workspace(
    auth_client, organization, user
):
    production = _production_label(auth_client)
    other_workspace = _other_workspace(user)
    _, version_one, version_two = _template_with_two_versions(
        organization, other_workspace, user, "TH-3387 exclusivity bulk"
    )
    version_one.labels.add(production)
    client = _client_for(user, other_workspace)

    response = client.post(
        BULK_URL,
        {
            "template_version_id": str(version_two.id),
            "label_ids": [str(production.id)],
        },
        format="json",
    )

    assert response.status_code == http_status.HTTP_200_OK, response.json()
    assert not version_one.labels.filter(id=production.id).exists()
    assert version_two.labels.filter(id=production.id).exists()


@pytest.mark.django_db
def test_remove_rejects_custom_label_from_another_workspace(
    auth_client, organization, workspace, user
):
    other_workspace = _other_workspace(user)
    foreign_label = PromptLabel.no_workspace_objects.create(
        name="TH-3387 foreign label",
        type=LabelTypeChoices.CUSTOM.value,
        organization=organization,
        workspace=other_workspace,
    )
    _, version_one, _ = _template_with_two_versions(
        organization, workspace, user, "TH-3387 foreign label target"
    )
    version_one.labels.add(foreign_label)

    response = auth_client.post(
        REMOVE_URL,
        {"label_id": str(foreign_label.id), "version_id": str(version_one.id)},
        format="json",
    )

    assert response.status_code == http_status.HTTP_400_BAD_REQUEST
    assert version_one.labels.filter(id=foreign_label.id).exists()


@pytest.mark.django_db
def test_remove_rejects_version_of_template_in_another_workspace(
    auth_client, organization, workspace, user
):
    # The label is visible to the caller (its own workspace); only the
    # template lives in another workspace of the same organization.
    own_label = PromptLabel.no_workspace_objects.create(
        name="TH-3387 caller label",
        type=LabelTypeChoices.CUSTOM.value,
        organization=organization,
        workspace=workspace,
    )
    other_workspace = _other_workspace(user)
    _, version_one, _ = _template_with_two_versions(
        organization, other_workspace, user, "TH-3387 foreign template custom"
    )
    version_one.labels.add(own_label)

    response = auth_client.post(
        REMOVE_URL,
        {"label_id": str(own_label.id), "version_id": str(version_one.id)},
        format="json",
    )

    assert response.status_code == http_status.HTTP_400_BAD_REQUEST
    assert version_one.labels.filter(id=own_label.id).exists()


@pytest.mark.django_db
def test_remove_rejects_system_label_on_template_in_another_workspace(
    auth_client, organization, user
):
    production = _production_label(auth_client)
    other_workspace = _other_workspace(user)
    _, version_one, _ = _template_with_two_versions(
        organization, other_workspace, user, "TH-3387 foreign template system"
    )
    version_one.labels.add(production)

    # auth_client is bound to the default workspace; the template lives elsewhere.
    response = auth_client.post(
        REMOVE_URL,
        {"label_id": str(production.id), "version_id": str(version_one.id)},
        format="json",
    )

    assert response.status_code == http_status.HTTP_400_BAD_REQUEST
    assert version_one.labels.filter(id=production.id).exists()


@pytest.mark.django_db
def test_remove_rejects_version_owned_by_another_organization(auth_client, user):
    production = _production_label(auth_client)
    other_org = Organization.objects.create(name="TH-3387 other org")
    other_workspace = Workspace.objects.create(
        name="TH-3387 other org workspace",
        organization=other_org,
        is_default=True,
        is_active=True,
        created_by=user,
    )
    _, version_one, _ = _template_with_two_versions(
        other_org, other_workspace, user, "TH-3387 other org template"
    )
    version_one.labels.add(production)

    response = auth_client.post(
        REMOVE_URL,
        {"label_id": str(production.id), "version_id": str(version_one.id)},
        format="json",
    )

    assert response.status_code == http_status.HTTP_400_BAD_REQUEST
    assert version_one.labels.filter(id=production.id).exists()
