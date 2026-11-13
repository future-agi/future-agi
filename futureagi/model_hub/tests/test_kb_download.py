"""Download boundary regression tests; storage is synthetic, authorization is real ORM."""

from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from model_hub.models.choices import StatusType
from model_hub.models.develop_dataset import Files, KnowledgeBaseFile
from model_hub.serializers.kb import KnowledgeBaseDownloadQuerySerializer
from model_hub.utils.kb_download import download_knowledge_base_file


@pytest.fixture
def download_case(db):
    org = Organization.objects.create(name="Download fixture")
    user = User.objects.create_user(
        email=f"{uuid4()}@example.invalid",
        password="Test-password-42",
        organization=org,
    )
    org_member, _ = OrganizationMembership.no_workspace_objects.update_or_create(
        user=user,
        organization=org,
        defaults={"role": "Member", "level": 3, "is_active": True, "deleted": False},
    )
    workspace = Workspace.objects.create(
        name="Download workspace", organization=org, created_by=user
    )
    ws_member = WorkspaceMembership.no_workspace_objects.create(
        user=user,
        workspace=workspace,
        role="workspace_member",
        level=3,
    )
    kb = KnowledgeBaseFile.no_workspace_objects.create(
        name="Downloads", organization=org, workspace=workspace
    )
    file = Files.no_workspace_objects.create(
        name='manual "quoted".txt',
        uploaded_url="http://invalid.invalid/ignored",
        status=StatusType.COMPLETED.value,
    )
    kb.files.add(file)
    request = SimpleNamespace(
        user=user, organization=org, workspace=workspace, headers={}
    )
    stored = Mock()
    stored.stream.return_value = iter([b"owned file bytes"])
    with patch("model_hub.utils.kb_download.get_storage_client") as client:
        client.return_value.get_object.return_value = stored
        yield SimpleNamespace(
            request=request,
            kb=kb,
            file=file,
            org_member=org_member,
            ws_member=ws_member,
            client=client,
            stored=stored,
        )


def test_download_uses_owned_file_identity_and_closes_connection(download_case):
    c = download_case
    response = download_knowledge_base_file(c.request, c.kb.id, c.file.id)
    assert b"".join(response.streaming_content) == b"owned file bytes"
    response.close()
    assert response.status_code == 200
    assert response["Cache-Control"] == "private, no-store"
    assert response["Content-Type"] == "text/plain"
    assert (
        response["Content-Disposition"]
        == 'attachment; filename="manual \\"quoted\\".txt"'
    )
    assert (
        c.client.return_value.get_object.call_args.args[1]
        == f"knowledge-base/{c.kb.id}/{c.file.id}.txt"
    )
    c.stored.close.assert_called_once()
    c.stored.release_conn.assert_called_once()
    c.client.return_value.presigned_get_object.assert_not_called()


@pytest.mark.parametrize(
    "change",
    [
        "removed-org",
        "deleted-org-membership",
        "removed-workspace",
        "deleted-workspace-membership",
        "zero-role",
        "unknown-role",
        "sibling-workspace",
        "foreign-org",
        "header-mismatch",
        "missing-scope",
    ],
)
def test_unauthorized_scope_never_touches_storage(download_case, change):
    c = download_case
    if change == "removed-org":
        c.org_member.is_active = False
        c.org_member.save()
    elif change == "deleted-org-membership":
        c.org_member.deleted = True
        c.org_member.save()
    elif change == "removed-workspace":
        c.ws_member.is_active = False
        c.ws_member.save()
    elif change == "deleted-workspace-membership":
        c.ws_member.deleted = True
        c.ws_member.save()
    elif change == "zero-role":
        c.ws_member.level = 0
        c.ws_member.save()
    elif change == "unknown-role":
        c.ws_member.level = None
        c.ws_member.role = "unknown"
        c.ws_member.save()
    elif change == "sibling-workspace":
        c.kb.workspace = Workspace.objects.create(
            name="Sibling",
            organization=c.request.organization,
            created_by=c.request.user,
        )
        c.kb.save()
    elif change == "foreign-org":
        c.kb.organization = Organization.objects.create(name="Foreign")
        c.kb.save()
    elif change == "header-mismatch":
        c.request.headers = {"X-Workspace-Id": str(uuid4())}
    elif change == "missing-scope":
        c.request.workspace = None
    with pytest.raises(PermissionDenied):
        download_knowledge_base_file(c.request, c.kb.id, c.file.id)
    c.client.assert_not_called()


@pytest.mark.parametrize(
    "change", ["deleted", "deleting", "unrelated", "invalid-extension"]
)
def test_unavailable_file_never_touches_storage(download_case, change):
    c = download_case
    if change == "deleted":
        c.file.deleted = True
        c.file.save()
    elif change == "deleting":
        c.file.status = StatusType.DELETING.value
        c.file.save()
    elif change == "unrelated":
        c.kb.files.remove(c.file)
    else:
        c.file.name = "file.txt/other"
        c.file.save()
    with pytest.raises(NotFound):
        download_knowledge_base_file(c.request, c.kb.id, c.file.id)
    c.client.assert_not_called()


def test_viewer_has_existing_read_permission(download_case):
    c = download_case
    c.org_member.level = 1
    c.org_member.save()
    c.ws_member.level = 1
    c.ws_member.save()
    response = download_knowledge_base_file(c.request, c.kb.id, c.file.id)
    assert response.status_code == 200
    response.close()


def test_header_filename_normalizes_paths_and_control_characters(download_case):
    c = download_case
    c.file.name = "../folder\\report\r\nSuffix.txt"
    c.file.save()
    response = download_knowledge_base_file(c.request, c.kb.id, c.file.id)
    assert (
        response["Content-Disposition"] == 'attachment; filename="reportSuffix.txt"'
    )
    response.close()


@pytest.mark.parametrize(
    "file_id",
    ["../other", "a/b", "https://storage.invalid/object", "", "..%2fother", "a\\b"],
)
def test_query_rejects_non_uuid_object_selectors(file_id):
    serializer = KnowledgeBaseDownloadQuerySerializer(data={"file_id": file_id})
    assert not serializer.is_valid()


def test_invalid_collection_id(download_case):
    c = download_case
    with pytest.raises(ValidationError):
        download_knowledge_base_file(c.request, "../other", c.file.id)
    c.client.assert_not_called()
