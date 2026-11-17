"""KB file queries are reads, but file deletion is still a write.

Exercise the resolved route with real workspace authentication and role checks.
Only credential decoding is stubbed; force_authenticate would skip the guard
that caused viewers' file grids to return 403.
"""

import json

import pytest
from django.urls import resolve
from rest_framework.test import APIRequestFactory

from accounts.authentication import APIKeyAuthentication
from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from model_hub.models.choices import StatusType
from model_hub.models.develop_dataset import Files, KnowledgeBaseFile
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import clear_workspace_context

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("urlconf_loaded_without_workspace_context"),
]

FILES_URL = "/model-hub/knowledge-base/files/"
REMOVE_FILES = "model_hub.views.develop_dataset.remove_kb_files.delay"


def _file(name, processing=False):
    return Files.objects.create(
        name=name,
        status=(
            StatusType.PROCESSING.value if processing else StatusType.COMPLETED.value
        ),
        metadata=json.dumps({"size": 12}),
        updated_by="KB reader test",
        uploaded_url="https://example.com/document.txt",
    )


def _kb(organization, workspace, files):
    kb = KnowledgeBaseFile.no_workspace_objects.create(
        name="Reader documents",
        organization=organization,
        workspace=workspace,
        status=StatusType.COMPLETED.value,
        created_by="KB reader test",
        size=12,
    )
    kb.files.set(files)
    return kb


@pytest.fixture(
    params=[
        OrganizationRoles.MEMBER_VIEW_ONLY,
        OrganizationRoles.WORKSPACE_VIEWER,
        OrganizationRoles.MEMBER,
    ],
    ids=["Viewer", "workspace_viewer", "Member"],
)
def requester(request, user, organization, workspace, monkeypatch, mocker):
    role = request.param
    org_role = (
        OrganizationRoles.MEMBER_VIEW_ONLY
        if role == OrganizationRoles.MEMBER_VIEW_ONLY
        else OrganizationRoles.MEMBER
    )
    org_level = (
        Level.VIEWER
        if org_role == OrganizationRoles.MEMBER_VIEW_ONLY
        else Level.MEMBER
    )
    OrganizationMembership.no_workspace_objects.filter(
        user=user, organization=organization
    ).update(role=org_role, level=org_level, is_active=True)
    # Cover legacy Viewer/Member roles as well as the level-backed workspace role.
    WorkspaceMembership.no_workspace_objects.filter(
        user=user, workspace=workspace
    ).update(
        role=role,
        level=(
            Level.WORKSPACE_VIEWER
            if role == OrganizationRoles.WORKSPACE_VIEWER
            else None
        ),
        is_active=True,
    )
    user.organization_role = org_role
    user.save(update_fields=["organization_role"])
    assert user.get_workspace_role(workspace) == role
    assert user.can_read_from_workspace(workspace)
    assert user.can_write_to_workspace(workspace) == (role == OrganizationRoles.MEMBER)

    match = resolve(FILES_URL)
    monkeypatch.setattr(match.func.cls, "authentication_classes", [APIKeyAuthentication])
    decode = mocker.patch.object(
        APIKeyAuthentication, "_decode_bearer", return_value=(user, None, None)
    )
    factory = APIRequestFactory()

    def send(method, data, selected_workspace=None):
        clear_workspace_context()
        http_request = getattr(factory, method)(
            FILES_URL,
            data,
            format="json",
            HTTP_AUTHORIZATION="Bearer kb-read-fixture",
            HTTP_X_ORGANIZATION_ID=str(organization.pk),
            HTTP_X_WORKSPACE_ID=str((selected_workspace or workspace).pk),
        )
        http_request.resolver_match = match
        calls_before = decode.call_count
        response = match.func(http_request, *match.args, **match.kwargs)
        assert decode.call_count == calls_before + 1
        return response

    yield role, send
    clear_workspace_context()


def test_read_roles_can_list_filtered_files_and_processing_status(
    requester, organization, workspace, mocker
):
    _role, send = requester
    pending = _file("a-docs.txt", processing=True)
    complete = _file("z-docs.txt")
    omitted = _file("other.txt")
    kb = _kb(organization, workspace, [pending, complete, omitted])
    remove = mocker.patch(REMOVE_FILES)

    response = send(
        "post",
        {
            "kb_id": str(kb.pk),
            "search": "docs",
            "sort": [{"column_id": "name", "type": "ascending"}],
            "page_number": 0,
            "page_size": 1,
        },
    )

    assert response.status_code == 200, response.data
    result = response.data["result"]
    assert result["total_rows"] == 2
    assert [row["name"] for row in result["table_data"]] == ["a-docs.txt"]
    assert result["table_data"][0]["status"] == StatusType.PROCESSING.value
    assert result["status"] == StatusType.PROCESSING.value
    assert result["status_count"] == 1
    pending.refresh_from_db()
    complete.refresh_from_db()
    assert pending.status == StatusType.PROCESSING.value
    assert complete.status == StatusType.COMPLETED.value
    remove.assert_not_called()


def test_file_delete_still_requires_write_access(
    requester, organization, workspace, mocker
):
    role, send = requester
    document = _file("delete-me.txt")
    kb = _kb(organization, workspace, [document])
    remove = mocker.patch(REMOVE_FILES)

    response = send("delete", {"kb_id": str(kb.pk), "file_ids": [str(document.pk)]})

    document.refresh_from_db()
    if role == OrganizationRoles.MEMBER:
        assert response.status_code == 200, response.data
        assert document.status == StatusType.DELETING.value
        remove.assert_called_once()
    else:
        assert response.status_code == 403, response.data
        assert "Write access denied to this workspace" in str(response.data)
        assert document.status == StatusType.COMPLETED.value
        assert kb.files.filter(pk=document.pk).exists()
        remove.assert_not_called()


@pytest.mark.parametrize("scope", ["organization", "workspace"])
def test_file_reads_still_enforce_knowledge_base_scope(
    requester, organization, workspace, user, scope
):
    _role, send = requester
    other_org = (
        Organization.objects.create(name="Other KB organization")
        if scope == "organization"
        else organization
    )
    other_workspace = Workspace.objects.create(
        name="Other KB workspace",
        organization=other_org,
        created_by=user,
        is_default=False,
    )
    other_kb = _kb(other_org, other_workspace, [_file("private.txt")])

    # An accessible request workspace does not authorize a foreign KB id.
    response = send("post", {"kb_id": str(other_kb.pk)})
    assert response.status_code == 400, response.data
    assert "private.txt" not in str(response.data)

    # Marking the POST as a read must not waive access to its workspace either.
    if scope == "workspace":
        response = send(
            "post", {"kb_id": str(other_kb.pk)}, selected_workspace=other_workspace
        )
        assert response.status_code == 403, response.data
