"""Cross-organization isolation for the knowledge-base endpoints.

Anonymous rejection for every KB route is parametrized in
``tfc/tests/test_model_hub_api_contract_debt.py`` alongside the other model-hub
guard lists; this module covers the authenticated cross-tenant boundary for both
the structured ``/model-hub/kb/`` viewset and the legacy
``/model-hub/knowledge-base/`` views.

Every reject path double-asserts: the status code *and* that no row changed and
no async removal task was dispatched.
"""

import json
import uuid

import pytest
from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from conftest import WorkspaceAwareAPIClient
from model_hub.models.choices import StatusType
from model_hub.models.develop_dataset import Files, KnowledgeBaseFile
from model_hub.models.kb import KnowledgeBase as StructuredKnowledgeBase
from rest_framework import status
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)

REMOVE_KB_FILES = "model_hub.views.develop_dataset.remove_kb_files.delay"


def _create_file(name="isolation-file.txt"):
    return Files.objects.create(
        name=name,
        status=StatusType.COMPLETED.value,
        metadata=json.dumps({"size": 12}),
        updated_by="Test User",
        uploaded_url="https://example.com/test.txt",
    )


def _create_legacy_kb(organization, workspace, name, files=None):
    kb = KnowledgeBaseFile.no_workspace_objects.create(
        name=name,
        organization=organization,
        workspace=workspace,
        status=StatusType.COMPLETED.value,
        created_by="Test User",
        size=12,
    )
    if files:
        kb.files.set(files)
    return kb


def _create_structured_kb(organization, workspace, name):
    return StructuredKnowledgeBase.no_workspace_objects.create(
        name=name,
        organization=organization,
        workspace=workspace,
        chunk_size=256,
        embedding_model="BAAI/bge-small-en-v1.5",
    )


@pytest.fixture
def other_org(db, organization):
    """A second organization with its own owner, workspace and API client.

    The thread-local workspace context is restored to the caller's organization
    on teardown so the ``auth_client`` fixture keeps operating on org A.
    """
    org_b = Organization.objects.create(name="KB Isolation Org B")

    clear_workspace_context()
    set_workspace_context(organization=org_b)

    user_b = User.objects.create_user(
        email=f"kb-isolation-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name="KB Isolation User B",
        organization=org_b,
        organization_role=OrganizationRoles.OWNER,
    )
    workspace_b = Workspace.objects.create(
        name="KB Isolation WS B",
        organization=org_b,
        is_default=True,
        is_active=True,
        created_by=user_b,
    )
    membership = OrganizationMembership.no_workspace_objects.create(
        user=user_b,
        organization=org_b,
        role=OrganizationRoles.OWNER,
        level=Level.OWNER,
        is_active=True,
    )
    WorkspaceMembership.no_workspace_objects.create(
        user=user_b,
        workspace=workspace_b,
        role="Workspace Owner",
        level=Level.OWNER,
        is_active=True,
        organization_membership=membership,
    )

    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=user_b)
    client.set_workspace(workspace_b)

    clear_workspace_context()
    set_workspace_context(organization=organization)

    yield {"org": org_b, "user": user_b, "workspace": workspace_b, "client": client}

    client.stop_workspace_injection()


@pytest.mark.integration
@pytest.mark.api
class TestStructuredKbCrossOrg:
    """``/model-hub/kb/`` must not expose another organization's rows."""

    def test_retrieve_of_other_org_kb_returns_404(
        self, organization, workspace, other_org
    ):
        kb = _create_structured_kb(organization, workspace, "org-a-structured-kb")

        response = other_org["client"].get(f"/model-hub/kb/{kb.id}/")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_put_of_other_org_kb_returns_404_and_leaves_row_intact(
        self, organization, workspace, other_org
    ):
        kb = _create_structured_kb(organization, workspace, "org-a-put-target")

        response = other_org["client"].put(
            f"/model-hub/kb/{kb.id}/",
            {
                "name": "hijacked",
                "embedding_model": "BAAI/bge-small-en-v1.5",
                "chunk_size": 999,
            },
            format="json",
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        kb.refresh_from_db()
        assert kb.name == "org-a-put-target"
        assert kb.chunk_size == 256

    def test_patch_of_other_org_kb_returns_404_and_leaves_row_intact(
        self, organization, workspace, other_org
    ):
        kb = _create_structured_kb(organization, workspace, "org-a-patch-target")

        response = other_org["client"].patch(
            f"/model-hub/kb/{kb.id}/", {"name": "hijacked"}, format="json"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        kb.refresh_from_db()
        assert kb.name == "org-a-patch-target"

    def test_delete_of_other_org_kb_returns_404_and_does_not_soft_delete(
        self, organization, workspace, other_org
    ):
        kb = _create_structured_kb(organization, workspace, "org-a-delete-target")

        response = other_org["client"].delete(f"/model-hub/kb/{kb.id}/")

        assert response.status_code == status.HTTP_404_NOT_FOUND
        kb.refresh_from_db()
        assert kb.deleted is False
        assert kb.deleted_at is None

    def test_list_excludes_other_org_kb(self, organization, workspace, other_org):
        own = _create_structured_kb(organization, workspace, "org-a-listed-kb")
        theirs = _create_structured_kb(
            other_org["org"], other_org["workspace"], "org-b-listed-kb"
        )

        response = other_org["client"].get("/model-hub/kb/")

        assert response.status_code == status.HTTP_200_OK
        ids = {row["id"] for row in response.data["result"]["results"]}
        assert str(theirs.id) in ids
        assert str(own.id) not in ids

    def test_create_ignores_client_supplied_organization(
        self, mocker, auth_client, organization, other_org
    ):
        """A payload naming another org still lands on the caller's org.

        Pins the guard in ``KnowledgeBaseCreateSerializer.create``. Returned 500
        before the create path stopped re-validating its own validated data.
        """
        mocker.patch("tfc.ee_gating.check_ee_feature", return_value=None)

        response = auth_client.post(
            "/model-hub/kb/",
            {
                "name": "org-override-attempt",
                "embedding_model": "BAAI/bge-small-en-v1.5",
                "chunk_size": 256,
                "organization": str(other_org["org"].id),
            },
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        kb = StructuredKnowledgeBase.no_workspace_objects.get(
            id=response.data["result"]["id"]
        )
        assert kb.organization_id == organization.id


@pytest.mark.integration
@pytest.mark.api
class TestStructuredKbEeGating:
    """``check_ee_feature`` is wired to ``create`` only.

    Pins the shipped asymmetry, not a desired one: an unentitled org cannot
    create a structured KB but can still read, rename and delete existing ones.
    """

    def test_create_is_blocked_when_feature_unavailable(self, mocker, auth_client):
        from tfc.ee_gating import FeatureUnavailable

        mocker.patch(
            "tfc.ee_gating.check_ee_feature",
            side_effect=FeatureUnavailable("knowledge_base"),
        )

        response = auth_client.post(
            "/model-hub/kb/",
            {
                "name": "gated-kb",
                "embedding_model": "BAAI/bge-small-en-v1.5",
                "chunk_size": 256,
            },
            format="json",
        )

        assert response.status_code == status.HTTP_402_PAYMENT_REQUIRED
        assert not StructuredKnowledgeBase.no_workspace_objects.filter(
            name="gated-kb"
        ).exists()

    def test_read_update_delete_are_not_gated(
        self, mocker, auth_client, organization, workspace
    ):
        from tfc.ee_gating import FeatureUnavailable

        kb = _create_structured_kb(organization, workspace, "ungated-kb")
        mocker.patch(
            "tfc.ee_gating.check_ee_feature",
            side_effect=FeatureUnavailable("knowledge_base"),
        )

        assert auth_client.get("/model-hub/kb/").status_code == status.HTTP_200_OK
        assert (
            auth_client.get(f"/model-hub/kb/{kb.id}/").status_code == status.HTTP_200_OK
        )

        patched = auth_client.patch(
            f"/model-hub/kb/{kb.id}/", {"name": "ungated-renamed"}, format="json"
        )
        assert patched.status_code == status.HTTP_200_OK

        deleted = auth_client.delete(f"/model-hub/kb/{kb.id}/")
        assert deleted.status_code == status.HTTP_204_NO_CONTENT

        kb = StructuredKnowledgeBase.all_objects.get(id=kb.id)
        assert kb.name == "ungated-renamed"
        assert kb.deleted is True


@pytest.mark.integration
@pytest.mark.api
class TestLegacyKbCrossOrg:
    """The legacy ``KnowledgeBaseFile`` routes must reject other-org ids."""

    def test_patch_of_other_org_kb_is_rejected_and_leaves_name_intact(
        self, organization, workspace, other_org
    ):
        kb = _create_legacy_kb(organization, workspace, "org-a-legacy-patch")

        response = other_org["client"].patch(
            "/model-hub/knowledge-base/",
            {"kb_id": str(kb.id), "name": "hijacked"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        kb.refresh_from_db()
        assert kb.name == "org-a-legacy-patch"

    def test_bulk_delete_of_other_org_kb_is_a_no_op(
        self, mocker, organization, workspace, other_org
    ):
        kb = _create_legacy_kb(organization, workspace, "org-a-legacy-delete")
        remove_kb_files = mocker.patch(REMOVE_KB_FILES)

        response = other_org["client"].delete(
            "/model-hub/knowledge-base/",
            {"kb_ids": [str(kb.id)]},
            format="json",
        )

        # The bulk endpoint filters by organization and reports success even
        # when nothing matched; the row must survive and no removal dispatched.
        assert response.status_code == status.HTTP_200_OK
        remove_kb_files.assert_not_called()
        kb.refresh_from_db()
        assert kb.deleted is False
        assert kb.deleted_at is None

    def test_files_list_of_other_org_kb_is_rejected(
        self, organization, workspace, other_org
    ):
        kb_file = _create_file("org-a-secret-file.txt")
        kb = _create_legacy_kb(
            organization, workspace, "org-a-legacy-files", files=[kb_file]
        )

        response = other_org["client"].post(
            "/model-hub/knowledge-base/files/",
            {"kb_id": str(kb.id), "page_number": 0, "page_size": 10},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "org-a-secret-file.txt" not in response.content.decode()

    def test_file_delete_of_other_org_kb_is_rejected(
        self, mocker, organization, workspace, other_org
    ):
        kb_file = _create_file("org-a-protected-file.txt")
        kb = _create_legacy_kb(
            organization, workspace, "org-a-legacy-file-delete", files=[kb_file]
        )
        remove_kb_files = mocker.patch(REMOVE_KB_FILES)

        response = other_org["client"].delete(
            "/model-hub/knowledge-base/files/",
            {"kb_id": str(kb.id), "file_ids": [str(kb_file.id)]},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        remove_kb_files.assert_not_called()
        kb_file.refresh_from_db()
        assert kb_file.status == StatusType.COMPLETED.value

    def test_table_view_excludes_other_org_kb(self, organization, workspace, other_org):
        own = _create_legacy_kb(organization, workspace, "org-a-table-kb")
        _create_legacy_kb(other_org["org"], other_org["workspace"], "org-b-table-kb")

        response = other_org["client"].get("/model-hub/knowledge-base/get/")

        assert response.status_code == status.HTTP_200_OK
        names = {row["name"] for row in response.json()["result"]["table_data"]}
        assert "org-b-table-kb" in names
        assert own.name not in names

    # GET /model-hub/knowledge-base/list/ org scoping lives in
    # accounts/tests/test_data_isolation_e2e.py::TestCrossOrgKnowledgeBaseIsolation.
