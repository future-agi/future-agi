"""
Tests for Phase 9: Ground Truth.
"""

import io
import json
import uuid

import pytest

from accounts.models import Organization, User
from accounts.models.workspace import Workspace
from model_hub.models.choices import OwnerChoices
from model_hub.models.evals_metric import (
    EvalGroundTruth,
    EvalGroundTruthEmbedding,
    EvalTemplate,
)
from tfc.constants.roles import OrganizationRoles


@pytest.fixture
def eval_template(organization, workspace):
    return EvalTemplate.no_workspace_objects.create(
        name="gt-test-eval",
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        config={"output": "Pass/Fail", "required_keys": ["input", "expected"]},
        criteria="Compare {{input}} with {{expected}}",
        visible_ui=True,
    )


@pytest.fixture
def ground_truth(eval_template, organization):
    return EvalGroundTruth.objects.create(
        eval_template=eval_template,
        name="test-gt",
        file_name="test.csv",
        columns=["input", "expected", "score", "notes"],
        data=[
            {"input": "hello", "expected": "world", "score": 0.9, "notes": "good"},
            {"input": "foo", "expected": "bar", "score": 0.5, "notes": "partial"},
            {"input": "alpha", "expected": "beta", "score": 1.0, "notes": "perfect"},
        ],
        row_count=3,
        organization=organization,
        workspace=eval_template.workspace,
    )


def create_other_org_ground_truth():
    suffix = uuid.uuid4().hex[:8]
    other_org = Organization.objects.create(name=f"Other GT Org {suffix}")
    other_user = User.objects.create_user(
        email=f"other-gt-{suffix}@futureagi.com",
        password="testpassword123",
        name="Other GT User",
        organization=other_org,
        organization_role=OrganizationRoles.OWNER,
    )
    other_workspace = Workspace.objects.create(
        name=f"Other GT Workspace {suffix}",
        organization=other_org,
        is_default=True,
        is_active=True,
        created_by=other_user,
    )
    other_template = EvalTemplate.no_workspace_objects.create(
        name=f"other-gt-eval-{suffix}",
        organization=other_org,
        workspace=other_workspace,
        owner=OwnerChoices.USER.value,
        config={"output": "Pass/Fail", "required_keys": ["input"]},
        criteria="Compare {{input}}",
        visible_ui=True,
    )
    other_gt = EvalGroundTruth.objects.create(
        eval_template=other_template,
        name=f"other-gt-{suffix}",
        file_name="other.csv",
        columns=["input"],
        data=[{"input": "secret"}],
        row_count=1,
        organization=other_org,
        workspace=other_workspace,
    )
    return other_template, other_gt


def create_same_org_other_workspace_ground_truth(organization, user):
    suffix = uuid.uuid4().hex[:8]
    other_workspace = Workspace.objects.create(
        name=f"Other Same Org GT Workspace {suffix}",
        organization=organization,
        is_default=False,
        is_active=True,
        created_by=user,
    )
    other_template = EvalTemplate.no_workspace_objects.create(
        name=f"same-org-other-workspace-gt-eval-{suffix}",
        organization=organization,
        workspace=other_workspace,
        owner=OwnerChoices.USER.value,
        config={"output": "Pass/Fail", "required_keys": ["input"]},
        criteria="Compare {{input}}",
        visible_ui=True,
    )
    other_gt = EvalGroundTruth.no_workspace_objects.create(
        eval_template=other_template,
        name=f"same-org-other-workspace-gt-{suffix}",
        file_name="other-workspace.csv",
        columns=["input", "expected"],
        data=[{"input": "secret", "expected": "hidden"}],
        row_count=1,
        embedding_status="completed",
        organization=organization,
        workspace=other_workspace,
    )
    return other_template, other_gt


# =========================================================================
# Upload API
# =========================================================================


@pytest.mark.e2e
@pytest.mark.django_db
class TestGroundTruthUploadAPI:
    def _url(self, template_id):
        return f"/model-hub/eval-templates/{template_id}/ground-truth/upload/"

    def test_upload_json_body(self, auth_client, eval_template):
        response = auth_client.post(
            self._url(eval_template.id),
            {
                "name": "my-ground-truth",
                "description": "Test dataset",
                "file_name": "data.csv",
                "columns": ["input", "expected"],
                "data": [
                    {"input": "hello", "expected": "world"},
                    {"input": "test", "expected": "result"},
                    {"input": "foo", "expected": "bar"},
                ],
            },
            format="json",
        )
        assert response.status_code == 200
        result = response.data["result"]
        assert result["name"] == "my-ground-truth"
        assert result["row_count"] == 3
        assert result["columns"] == ["input", "expected"]
        assert result["embedding_status"] == "pending"

    def test_upload_csv_file(self, auth_client, eval_template):
        csv_content = (
            "question,answer,score\nWhat is 1+1?,2,1.0\nCapital of France?,Paris,0.9\n"
        )
        csv_file = io.BytesIO(csv_content.encode("utf-8"))
        csv_file.name = "test_data.csv"

        response = auth_client.post(
            self._url(eval_template.id),
            {"file": csv_file, "name": "csv-upload"},
            format="multipart",
        )
        assert response.status_code == 200
        result = response.data["result"]
        assert result["name"] == "csv-upload"
        assert result["row_count"] == 2
        assert set(result["columns"]) == {"question", "answer", "score"}

    def test_upload_csv_file_with_multipart_json_mapping(
        self, auth_client, eval_template
    ):
        csv_content = "question,answer\nWhat is 1+1?,2\n"
        csv_file = io.BytesIO(csv_content.encode("utf-8"))
        csv_file.name = "mapped_data.csv"

        response = auth_client.post(
            self._url(eval_template.id),
            {
                "file": csv_file,
                "name": "mapped-upload",
                "variable_mapping": json.dumps(
                    {"input": "question", "expected": "answer"}
                ),
                "role_mapping": json.dumps(
                    {"input": "question", "expected_output": "answer"}
                ),
            },
            format="multipart",
        )

        assert response.status_code == 200, response.data
        gt = EvalGroundTruth.objects.get(id=response.data["result"]["id"])
        assert gt.variable_mapping == {"input": "question", "expected": "answer"}
        assert gt.role_mapping == {
            "input": "question",
            "expected_output": "answer",
        }

    def test_upload_json_file(self, auth_client, eval_template):
        json_data = [
            {"input": "hello", "output": "world"},
            {"input": "foo", "output": "bar"},
        ]
        json_file = io.BytesIO(json.dumps(json_data).encode("utf-8"))
        json_file.name = "test_data.json"

        response = auth_client.post(
            self._url(eval_template.id),
            {"file": json_file, "name": "json-upload"},
            format="multipart",
        )
        assert response.status_code == 200
        result = response.data["result"]
        assert result["row_count"] == 2
        assert result["columns"] == ["input", "output"]

    def test_upload_empty_columns_rejected(self, auth_client, eval_template):
        response = auth_client.post(
            self._url(eval_template.id),
            {"name": "bad-gt", "columns": [], "data": []},
            format="json",
        )
        assert response.status_code == 400

    def test_upload_nonexistent_template(self, auth_client):
        response = auth_client.post(
            "/model-hub/eval-templates/00000000-0000-0000-0000-000000000000/ground-truth/upload/",
            {"name": "gt", "columns": ["a"], "data": [{"a": 1}]},
            format="json",
        )
        assert response.status_code == 404

    def test_upload_rejects_same_org_other_workspace_template(
        self, auth_client, organization, user
    ):
        other_template, _ = create_same_org_other_workspace_ground_truth(
            organization, user
        )
        response = auth_client.post(
            self._url(other_template.id),
            {
                "name": "cross-workspace-gt",
                "columns": ["input"],
                "data": [{"input": "secret"}],
            },
            format="json",
        )
        assert response.status_code == 404

    def test_upload_unsupported_file_type(self, auth_client, eval_template):
        bad_file = io.BytesIO(b"not a real file")
        bad_file.name = "test.txt"

        response = auth_client.post(
            self._url(eval_template.id),
            {"file": bad_file, "name": "bad-type"},
            format="multipart",
        )
        assert response.status_code == 400

    def test_upload_with_role_mapping(self, auth_client, eval_template):
        response = auth_client.post(
            self._url(eval_template.id),
            {
                "name": "with-roles",
                "columns": ["q", "a", "s"],
                "data": [{"q": "hi", "a": "hello", "s": 1.0}],
                "role_mapping": {"input": "q", "expected_output": "a", "score": "s"},
            },
            format="json",
        )
        assert response.status_code == 200
        gt = EvalGroundTruth.objects.get(id=response.data["result"]["id"])
        assert gt.role_mapping == {"input": "q", "expected_output": "a", "score": "s"}


# =========================================================================
# List API
# =========================================================================


@pytest.mark.e2e
@pytest.mark.django_db
class TestGroundTruthListAPI:
    def _url(self, template_id):
        return f"/model-hub/eval-templates/{template_id}/ground-truth/"

    def test_list_empty(self, auth_client, eval_template):
        response = auth_client.get(self._url(eval_template.id))
        assert response.status_code == 200
        assert response.data["result"]["total"] == 0

    def test_list_with_data(self, auth_client, eval_template, ground_truth):
        response = auth_client.get(self._url(eval_template.id))
        assert response.status_code == 200
        assert response.data["result"]["total"] == 1
        item = response.data["result"]["items"][0]
        assert item["name"] == "test-gt"
        assert item["row_count"] == 3
        assert item["embedding_status"] == "pending"

    def test_list_rejects_other_org_template(self, auth_client):
        other_template, _ = create_other_org_ground_truth()
        response = auth_client.get(self._url(other_template.id))
        assert response.status_code == 404

    def test_list_rejects_same_org_other_workspace_template(
        self, auth_client, organization, user
    ):
        other_template, _ = create_same_org_other_workspace_ground_truth(
            organization, user
        )
        response = auth_client.get(self._url(other_template.id))
        assert response.status_code == 404


# =========================================================================
# Mapping API
# =========================================================================


@pytest.mark.e2e
@pytest.mark.django_db
class TestGroundTruthMappingAPI:
    def test_update_mapping(self, auth_client, ground_truth):
        response = auth_client.put(
            f"/model-hub/ground-truth/{ground_truth.id}/mapping/",
            {"variable_mapping": {"input": "input", "expected": "expected"}},
            format="json",
        )
        assert response.status_code == 200
        ground_truth.refresh_from_db()
        assert ground_truth.variable_mapping == {
            "input": "input",
            "expected": "expected",
        }

    def test_mapping_nonexistent(self, auth_client):
        response = auth_client.put(
            "/model-hub/ground-truth/00000000-0000-0000-0000-000000000000/mapping/",
            {"variable_mapping": {"a": "b"}},
            format="json",
        )
        assert response.status_code == 404


# =========================================================================
# Role Mapping API
# =========================================================================


@pytest.mark.e2e
@pytest.mark.django_db
class TestGroundTruthRoleMappingAPI:
    def _url(self, gt_id):
        return f"/model-hub/ground-truth/{gt_id}/role-mapping/"

    def test_set_role_mapping(self, auth_client, ground_truth):
        response = auth_client.put(
            self._url(ground_truth.id),
            {
                "role_mapping": {
                    "input": "input",
                    "expected_output": "expected",
                    "score": "score",
                    "reasoning": "notes",
                }
            },
            format="json",
        )
        assert response.status_code == 200
        ground_truth.refresh_from_db()
        assert ground_truth.role_mapping["input"] == "input"
        assert ground_truth.role_mapping["score"] == "score"

    def test_invalid_role_rejected(self, auth_client, ground_truth):
        response = auth_client.put(
            self._url(ground_truth.id),
            {"role_mapping": {"bad_role": "input"}},
            format="json",
        )
        assert response.status_code == 400

    def test_invalid_column_rejected(self, auth_client, ground_truth):
        response = auth_client.put(
            self._url(ground_truth.id),
            {"role_mapping": {"input": "nonexistent_column"}},
            format="json",
        )
        assert response.status_code == 400

    def test_role_mapping_nonexistent(self, auth_client):
        response = auth_client.put(
            "/model-hub/ground-truth/00000000-0000-0000-0000-000000000000/role-mapping/",
            {"role_mapping": {"input": "col"}},
            format="json",
        )
        assert response.status_code == 404


# =========================================================================
# Data Preview API
# =========================================================================


@pytest.mark.e2e
@pytest.mark.django_db
class TestGroundTruthDataAPI:
    def _url(self, gt_id):
        return f"/model-hub/ground-truth/{gt_id}/data/"

    def test_get_data_default_pagination(self, auth_client, ground_truth):
        response = auth_client.get(self._url(ground_truth.id))
        assert response.status_code == 200
        result = response.data["result"]
        assert result["total_rows"] == 3
        assert result["page"] == 1
        assert len(result["rows"]) == 3

    def test_get_data_with_pagination(self, auth_client, ground_truth):
        response = auth_client.get(f"{self._url(ground_truth.id)}?page=1&page_size=2")
        assert response.status_code == 200
        result = response.data["result"]
        assert len(result["rows"]) == 2
        assert result["total_pages"] == 2

    def test_get_data_page_2(self, auth_client, ground_truth):
        response = auth_client.get(f"{self._url(ground_truth.id)}?page=2&page_size=2")
        assert response.status_code == 200
        result = response.data["result"]
        assert len(result["rows"]) == 1

    def test_data_nonexistent(self, auth_client):
        response = auth_client.get(
            "/model-hub/ground-truth/00000000-0000-0000-0000-000000000000/data/"
        )
        assert response.status_code == 404

    def test_data_rejects_other_org_ground_truth(self, auth_client):
        _, other_gt = create_other_org_ground_truth()
        response = auth_client.get(self._url(other_gt.id))
        assert response.status_code == 404

    def test_data_rejects_same_org_other_workspace_ground_truth(
        self, auth_client, organization, user
    ):
        _, other_gt = create_same_org_other_workspace_ground_truth(organization, user)
        response = auth_client.get(self._url(other_gt.id))
        assert response.status_code == 404


# =========================================================================
# Status API
# =========================================================================


@pytest.mark.e2e
@pytest.mark.django_db
class TestGroundTruthStatusAPI:
    def _url(self, gt_id):
        return f"/model-hub/ground-truth/{gt_id}/status/"

    def test_get_status_pending(self, auth_client, ground_truth):
        response = auth_client.get(self._url(ground_truth.id))
        assert response.status_code == 200
        result = response.data["result"]
        assert result["embedding_status"] == "pending"
        assert result["total_rows"] == 3
        assert result["embedded_row_count"] == 0
        assert result["progress_percent"] == 0.0

    def test_status_nonexistent(self, auth_client):
        response = auth_client.get(
            "/model-hub/ground-truth/00000000-0000-0000-0000-000000000000/status/"
        )
        assert response.status_code == 404


# =========================================================================
# Delete API
# =========================================================================


@pytest.mark.e2e
@pytest.mark.django_db
class TestGroundTruthDeleteAPI:
    def _url(self, gt_id):
        return f"/model-hub/ground-truth/{gt_id}/"

    def test_delete_ground_truth(self, auth_client, ground_truth):
        response = auth_client.delete(self._url(ground_truth.id))
        assert response.status_code == 200
        assert response.data["result"]["deleted"] is True

        # Verify soft-deleted
        ground_truth.refresh_from_db()
        assert ground_truth.deleted is True
        assert ground_truth.deleted_at is not None

    def test_delete_nonexistent(self, auth_client):
        response = auth_client.delete(
            "/model-hub/ground-truth/00000000-0000-0000-0000-000000000000/"
        )
        assert response.status_code == 404

    def test_deleted_gt_not_in_list(self, auth_client, eval_template, ground_truth):
        # Delete it
        auth_client.delete(self._url(ground_truth.id))

        # Verify not in list
        response = auth_client.get(
            f"/model-hub/eval-templates/{eval_template.id}/ground-truth/"
        )
        assert response.status_code == 200
        assert response.data["result"]["total"] == 0


# =========================================================================
# Ground Truth Config API
# =========================================================================


@pytest.mark.e2e
@pytest.mark.django_db
class TestGroundTruthConfigAPI:
    def _url(self, template_id):
        return f"/model-hub/eval-templates/{template_id}/ground-truth-config/"

    def test_get_default_config(self, auth_client, eval_template):
        response = auth_client.get(self._url(eval_template.id))
        assert response.status_code == 200
        gt_config = response.data["result"]["ground_truth"]
        assert gt_config["enabled"] is False
        assert gt_config["mode"] == "auto"
        assert gt_config["max_examples"] == 3

    def test_set_config(self, auth_client, eval_template, ground_truth):
        response = auth_client.put(
            self._url(eval_template.id),
            {
                "enabled": True,
                "ground_truth_id": str(ground_truth.id),
                "mode": "auto",
                "max_examples": 5,
                "similarity_threshold": 0.8,
            },
            format="json",
        )
        assert response.status_code == 200
        gt_config = response.data["result"]["ground_truth"]
        assert gt_config["enabled"] is True
        assert gt_config["max_examples"] == 5
        assert gt_config["similarity_threshold"] == 0.8

    def test_config_invalid_gt_id(self, auth_client, eval_template):
        response = auth_client.put(
            self._url(eval_template.id),
            {
                "enabled": True,
                "ground_truth_id": "00000000-0000-0000-0000-000000000000",
            },
            format="json",
        )
        assert response.status_code == 400

    def test_config_rejects_same_org_other_workspace_ground_truth(
        self, auth_client, eval_template, organization, user
    ):
        _, other_gt = create_same_org_other_workspace_ground_truth(organization, user)
        response = auth_client.put(
            self._url(eval_template.id),
            {
                "enabled": True,
                "ground_truth_id": str(other_gt.id),
            },
            format="json",
        )
        assert response.status_code == 400

    def test_config_rejects_same_org_other_workspace_template(
        self, auth_client, organization, user
    ):
        other_template, _ = create_same_org_other_workspace_ground_truth(
            organization, user
        )
        response = auth_client.get(self._url(other_template.id))
        assert response.status_code == 404

    def test_config_nonexistent_template(self, auth_client):
        response = auth_client.get(
            "/model-hub/eval-templates/00000000-0000-0000-0000-000000000000/ground-truth-config/"
        )
        assert response.status_code == 404


# =========================================================================
# Search API
# =========================================================================


@pytest.mark.e2e
@pytest.mark.django_db
class TestGroundTruthSearchAPI:
    def _url(self, gt_id):
        return f"/model-hub/ground-truth/{gt_id}/search/"

    def test_search_requires_completed_embeddings(self, auth_client, ground_truth):
        response = auth_client.post(
            self._url(ground_truth.id),
            {"query": "hello world", "max_results": 2},
            format="json",
        )
        assert response.status_code == 400
        assert "Embeddings not ready" in response.data["message"]

    def test_search_returns_similar_rows(
        self, auth_client, ground_truth, monkeypatch
    ):
        ground_truth.embedding_status = "completed"
        ground_truth.embedded_row_count = 3
        ground_truth.save(
            update_fields=["embedding_status", "embedded_row_count", "updated_at"]
        )
        EvalGroundTruthEmbedding.objects.create(
            ground_truth=ground_truth,
            row_index=0,
            text_content="input: hello\nexpected_output: world",
            embedding=[1.0, 0.0],
            row_data=ground_truth.data[0],
        )
        EvalGroundTruthEmbedding.objects.create(
            ground_truth=ground_truth,
            row_index=1,
            text_content="input: foo\nexpected_output: bar",
            embedding=[0.1, 0.9],
            row_data=ground_truth.data[1],
        )
        EvalGroundTruthEmbedding.objects.create(
            ground_truth=ground_truth,
            row_index=2,
            text_content="input: alpha\nexpected_output: beta",
            embedding=[-1.0, 0.0],
            row_data=ground_truth.data[2],
        )

        monkeypatch.setattr(
            "model_hub.utils.ground_truth_retrieval.generate_embedding",
            lambda text: [1.0, 0.0],
        )

        response = auth_client.post(
            self._url(ground_truth.id),
            {"query": "hello", "max_results": 2},
            format="json",
        )

        assert response.status_code == 200, response.data
        result = response.data["result"]
        assert result["query"] == "hello"
        assert result["total"] == 2
        assert result["results"][0]["row_index"] == 0
        assert result["results"][0]["row_data"]["input"] == "hello"
        assert result["results"][0]["similarity"] == 1.0

    def test_search_rejects_same_org_other_workspace_ground_truth(
        self, auth_client, organization, user
    ):
        _, other_gt = create_same_org_other_workspace_ground_truth(organization, user)
        response = auth_client.post(
            self._url(other_gt.id),
            {"query": "secret", "max_results": 1},
            format="json",
        )
        assert response.status_code == 404


# =========================================================================
# Embed API
# =========================================================================


@pytest.mark.e2e
@pytest.mark.django_db
class TestGroundTruthEmbedAPI:
    def _url(self, gt_id):
        return f"/model-hub/ground-truth/{gt_id}/embed/"

    def test_embed_rejects_empty_ground_truth(
        self, auth_client, eval_template, organization, workspace
    ):
        empty_gt = EvalGroundTruth.objects.create(
            eval_template=eval_template,
            name="empty-gt",
            file_name="empty.json",
            columns=["input"],
            data=[],
            row_count=0,
            organization=organization,
            workspace=workspace,
        )

        response = auth_client.post(self._url(empty_gt.id), {}, format="json")
        assert response.status_code == 400
        assert response.data["message"] == "No data rows to embed."

    def test_embed_rejects_processing_ground_truth(self, auth_client, ground_truth):
        ground_truth.embedding_status = "processing"
        ground_truth.save(update_fields=["embedding_status", "updated_at"])

        response = auth_client.post(self._url(ground_truth.id), {}, format="json")
        assert response.status_code == 400
        assert response.data["message"] == "Embedding generation is already in progress."

    def test_embed_resets_status_and_triggers_workflow(
        self, auth_client, ground_truth, monkeypatch
    ):
        calls = []

        async def fake_trigger_embedding_generation(ground_truth_id):
            calls.append(ground_truth_id)
            return "test-run-id"

        monkeypatch.setattr(
            "tfc.temporal.ground_truth.client.trigger_embedding_generation",
            fake_trigger_embedding_generation,
        )
        ground_truth.embedding_status = "failed"
        ground_truth.embedded_row_count = 2
        ground_truth.save(
            update_fields=["embedding_status", "embedded_row_count", "updated_at"]
        )

        response = auth_client.post(self._url(ground_truth.id), {}, format="json")

        assert response.status_code == 200, response.data
        result = response.data["result"]
        assert result["id"] == str(ground_truth.id)
        assert result["embedding_status"] == "pending"
        assert calls == [str(ground_truth.id)]
        ground_truth.refresh_from_db()
        assert ground_truth.embedding_status == "pending"
        assert ground_truth.embedded_row_count == 0

    def test_embed_marks_failed_when_workflow_dispatch_fails(
        self, auth_client, ground_truth, monkeypatch
    ):
        async def fake_trigger_embedding_generation(ground_truth_id):
            return None

        monkeypatch.setattr(
            "tfc.temporal.ground_truth.client.trigger_embedding_generation",
            fake_trigger_embedding_generation,
        )
        ground_truth.embedding_status = "completed"
        ground_truth.embedded_row_count = 2
        ground_truth.save(
            update_fields=["embedding_status", "embedded_row_count", "updated_at"]
        )

        response = auth_client.post(self._url(ground_truth.id), {}, format="json")

        assert response.status_code == 400
        assert response.data["message"] == "Failed to trigger embedding generation."
        ground_truth.refresh_from_db()
        assert ground_truth.embedding_status == "failed"
        assert ground_truth.embedded_row_count == 0

    def test_embed_rejects_same_org_other_workspace_ground_truth(
        self, auth_client, organization, user
    ):
        _, other_gt = create_same_org_other_workspace_ground_truth(organization, user)
        response = auth_client.post(self._url(other_gt.id), {}, format="json")
        assert response.status_code == 404


# =========================================================================
# File Parser Unit Tests
# =========================================================================


class TestGroundTruthParser:
    def test_parse_csv(self):
        from model_hub.utils.ground_truth_parser import parse_ground_truth_file

        csv_content = "name,value,category\nAlice,100,A\nBob,200,B\n"
        file_obj = io.BytesIO(csv_content.encode("utf-8"))
        columns, data = parse_ground_truth_file(file_obj, "test.csv")

        assert columns == ["name", "value", "category"]
        assert len(data) == 2
        assert data[0]["name"] == "Alice"
        assert data[1]["value"] == "200"

    def test_parse_json_array(self):
        from model_hub.utils.ground_truth_parser import parse_ground_truth_file

        json_data = [{"q": "What?", "a": "That"}, {"q": "Why?", "a": "Because"}]
        file_obj = io.BytesIO(json.dumps(json_data).encode("utf-8"))
        columns, data = parse_ground_truth_file(file_obj, "test.json")

        assert columns == ["q", "a"]
        assert len(data) == 2

    def test_parse_json_with_columns_data_format(self):
        from model_hub.utils.ground_truth_parser import parse_ground_truth_file

        json_data = {"columns": ["x", "y"], "data": [{"x": 1, "y": 2}]}
        file_obj = io.BytesIO(json.dumps(json_data).encode("utf-8"))
        columns, data = parse_ground_truth_file(file_obj, "test.json")

        assert columns == ["x", "y"]
        assert len(data) == 1

    def test_unsupported_format_raises(self):
        from model_hub.utils.ground_truth_parser import parse_ground_truth_file

        file_obj = io.BytesIO(b"whatever")
        with pytest.raises(ValueError, match="Unsupported file type"):
            parse_ground_truth_file(file_obj, "test.txt")

    def test_empty_csv_raises(self):
        from model_hub.utils.ground_truth_parser import parse_ground_truth_file

        file_obj = io.BytesIO(b"")
        with pytest.raises(ValueError):
            parse_ground_truth_file(file_obj, "empty.csv")

    def test_empty_json_array_raises(self):
        from model_hub.utils.ground_truth_parser import parse_ground_truth_file

        file_obj = io.BytesIO(b"[]")
        with pytest.raises(ValueError, match="empty"):
            parse_ground_truth_file(file_obj, "empty.json")

    def test_csv_with_bom(self):
        from model_hub.utils.ground_truth_parser import parse_ground_truth_file

        csv_content = "\ufeffname,value\nAlice,100\n"
        file_obj = io.BytesIO(csv_content.encode("utf-8-sig"))
        columns, data = parse_ground_truth_file(file_obj, "bom.csv")

        assert columns == ["name", "value"]
        assert len(data) == 1
