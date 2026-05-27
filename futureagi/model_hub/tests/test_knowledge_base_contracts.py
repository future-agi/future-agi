import json
import uuid
from unittest.mock import patch

import pytest
from rest_framework import status

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from model_hub.models.choices import StatusType
from model_hub.models.develop_dataset import Files, KnowledgeBaseFile
from model_hub.models.kb import KnowledgeBase as StructuredKnowledgeBase
from model_hub.serializers.contracts import LegacyKnowledgeBaseTableQuerySerializer


def _create_file(name="kb-file.txt", status_value=None):
    return Files.objects.create(
        name=name,
        status=status_value or StatusType.COMPLETED.value,
        metadata=json.dumps({"size": 12}),
        updated_by="Test User",
        uploaded_url="https://example.com/test.txt",
    )


def _create_kb(organization, workspace, name="kb-contract", files=None, **kwargs):
    kb = KnowledgeBaseFile.objects.create(
        name=name,
        organization=organization,
        workspace=workspace,
        status=kwargs.pop("status", StatusType.COMPLETED.value),
        created_by="Test User",
        size=kwargs.pop("size", 12),
        **kwargs,
    )
    if files:
        kb.files.set(files)
    return kb


class TestKnowledgeBaseTableContracts:
    def test_knowledge_base_table_query_accepts_canonical_sort_and_pagination(self):
        serializer = LegacyKnowledgeBaseTableQuerySerializer(
            data={
                "search": "docs",
                "sort": json.dumps(
                    [{"column_id": "updated_at", "type": "descending"}]
                ),
                "page_number": "1",
                "page_size": "25",
            }
        )

        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["page_number"] == 1
        assert serializer.validated_data["sort"][0]["column_id"] == "updated_at"

    def test_knowledge_base_table_query_rejects_legacy_pagination_aliases(self):
        serializer = LegacyKnowledgeBaseTableQuerySerializer(
            data={
                "pageNumber": "1",
                "pageSize": "25",
            }
        )

        assert not serializer.is_valid()
        assert "pageNumber" in serializer.errors
        assert "pageSize" in serializer.errors


@pytest.mark.integration
@pytest.mark.api
def test_knowledge_base_table_api_rejects_legacy_pagination_aliases(auth_client):
    response = auth_client.get(
        "/model-hub/knowledge-base/get/",
        {"pageNumber": "1", "pageSize": "25"},
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.integration
@pytest.mark.api
class TestLegacyKnowledgeBaseLifecycle:
    def test_delete_soft_deletes_kb_with_deleted_at_and_scopes_to_org(
        self, auth_client, organization, workspace
    ):
        own_kb = _create_kb(organization, workspace, name="own-kb")
        other_org = Organization.objects.create(name="Other KB Org")
        other_kb = _create_kb(other_org, None, name="other-kb")

        with patch(
            "model_hub.views.develop_dataset.cancel_kb_ingestion_workflow"
        ) as cancel_workflow, patch(
            "model_hub.views.develop_dataset.remove_kb_files.delay"
        ) as remove_kb_files:
            response = auth_client.delete(
                "/model-hub/knowledge-base/",
                {"kb_ids": [str(own_kb.id), str(other_kb.id)]},
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK
        cancel_workflow.assert_not_called()
        remove_kb_files.assert_called_once_with(
            None, str(organization.id), [str(own_kb.id)]
        )

        own_kb.refresh_from_db()
        other_kb = KnowledgeBaseFile.no_workspace_objects.get(id=other_kb.id)

        assert own_kb.deleted is True
        assert own_kb.deleted_at is not None
        assert other_kb.deleted is False
        assert other_kb.deleted_at is None

    def test_files_endpoint_rejects_deleted_kb(
        self, auth_client, organization, workspace
    ):
        kb_file = _create_file("deleted-kb-file.txt")
        deleted_kb = _create_kb(
            organization,
            workspace,
            name="deleted-kb",
            files=[kb_file],
            deleted=True,
        )

        response = auth_client.post(
            "/model-hub/knowledge-base/files/",
            {"kb_id": str(deleted_kb.id), "page_number": 0, "page_size": 10},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_file_delete_rejects_files_not_attached_to_selected_kb(
        self, auth_client, organization, workspace
    ):
        target_file = _create_file("target-kb-file.txt")
        outside_file = _create_file("outside-kb-file.txt")
        target_kb = _create_kb(
            organization, workspace, name="target-kb", files=[target_file]
        )
        _create_kb(organization, workspace, name="outside-kb", files=[outside_file])

        with patch(
            "model_hub.views.develop_dataset.remove_kb_files.delay"
        ) as remove_kb_files:
            response = auth_client.delete(
                "/model-hub/knowledge-base/files/",
                {"kb_id": str(target_kb.id), "file_ids": [str(outside_file.id)]},
                format="json",
            )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        remove_kb_files.assert_not_called()
        outside_file.refresh_from_db()
        target_file.refresh_from_db()
        assert outside_file.status == StatusType.COMPLETED.value
        assert target_file.status == StatusType.COMPLETED.value


@pytest.mark.integration
@pytest.mark.api
class TestStructuredKnowledgeBaseViewSet:
    def test_lifecycle_uses_selected_request_org_and_supports_partial_update(
        self, auth_client, organization, workspace, user
    ):
        selected_org = Organization.objects.create(name="Selected KB Org")
        selected_workspace = Workspace.objects.create(
            name="Selected KB Workspace",
            organization=selected_org,
            is_default=True,
            is_active=True,
            created_by=user,
        )
        auth_client.set_workspace(selected_workspace)

        with patch("tfc.ee_gating.check_ee_feature", return_value=None):
            created = auth_client.post(
                "/model-hub/kb/",
                {
                    "name": "structured-kb",
                    "embedding_model": "BAAI/bge-small-en-v1.5",
                    "chunk_size": 256,
                },
                format="json",
            )

        assert created.status_code == status.HTTP_201_CREATED
        kb_id = created.data["result"]["id"]

        kb = StructuredKnowledgeBase.objects.get(id=kb_id)
        assert kb.organization == selected_org
        assert kb.workspace == selected_workspace
        assert kb.organization != organization
        assert kb.workspace != workspace

        listed = auth_client.get("/model-hub/kb/", {"search": "structured-kb"})
        assert listed.status_code == status.HTTP_200_OK
        rows = listed.data["result"].get("results", [])
        assert any(row["id"] == kb_id for row in rows)

        detail = auth_client.get(f"/model-hub/kb/{kb_id}/")
        assert detail.status_code == status.HTTP_200_OK
        assert detail.data["result"]["name"] == "structured-kb"

        patched = auth_client.patch(
            f"/model-hub/kb/{kb_id}/",
            {"name": "structured-kb-renamed"},
            format="json",
        )
        assert patched.status_code == status.HTTP_200_OK
        assert patched.data["result"]["name"] == "structured-kb-renamed"

        updated = auth_client.put(
            f"/model-hub/kb/{kb_id}/",
            {
                "name": "structured-kb-updated",
                "embedding_model": "BAAI/bge-small-en-v1.5",
                "chunk_size": 512,
            },
            format="json",
        )
        assert updated.status_code == status.HTTP_200_OK
        assert updated.data["result"]["chunk_size"] == 512

        deleted = auth_client.delete(f"/model-hub/kb/{kb_id}/")
        assert deleted.status_code == status.HTTP_204_NO_CONTENT

        kb = StructuredKnowledgeBase.all_objects.get(id=kb_id)
        assert kb.deleted is True
        assert kb.deleted_at is not None

    def test_missing_structured_kb_returns_404_not_500(self, auth_client):
        missing_id = uuid.uuid4()

        detail = auth_client.get(f"/model-hub/kb/{missing_id}/")
        assert detail.status_code == status.HTTP_404_NOT_FOUND

        patched = auth_client.patch(
            f"/model-hub/kb/{missing_id}/",
            {"name": "missing"},
            format="json",
        )
        assert patched.status_code == status.HTTP_404_NOT_FOUND

        deleted = auth_client.delete(f"/model-hub/kb/{missing_id}/")
        assert deleted.status_code == status.HTTP_404_NOT_FOUND

    def test_supported_embedding_models_aliases(self, auth_client):
        for path in (
            "/model-hub/kb/supported-embedding-models",
            "/model-hub/kb/supported_embedding_models/",
        ):
            response = auth_client.get(path)
            assert response.status_code == status.HTTP_200_OK
            values = [row["value"] for row in response.data["result"]]
            assert "BAAI/bge-small-en-v1.5" in values
