"""Functional coverage for the legacy ``/model-hub/knowledge-base/`` endpoint.

S3 upload, file parsing and Temporal ingestion are patched at their boundaries,
and entitlements are patched on the class, so the module behaves identically
with and without ``ee/`` on the path.
"""

import json

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status

from model_hub.constants import MAX_KB_SIZE
from model_hub.models.choices import StatusType
from model_hub.models.develop_dataset import Files, KnowledgeBaseFile
from tfc.constants.api_calls import APICallStatusChoices

VIEW = "model_hub.views.develop_dataset.CreateKnowledgeBaseView"
MODULE = "model_hub.views.develop_dataset"
ENTITLEMENTS = "ee.usage.services.entitlements.Entitlements"
KB_URL = "/model-hub/knowledge-base/"


def _upload(name="kb-doc.txt", content=b"knowledge base content"):
    return SimpleUploadedFile(name, content, content_type="text/plain")


def _existing_file(name="already-there.txt"):
    return Files.objects.create(
        name=name,
        status=StatusType.COMPLETED.value,
        metadata=json.dumps({"size": 12}),
        updated_by="Test User",
        uploaded_url="https://example.com/test.txt",
    )


@pytest.fixture
def allow_entitlements(mocker):
    """Both entitlement gates open, for tests targeting a later branch."""
    allowed = mocker.MagicMock(allowed=True, reason="")
    mocker.patch(f"{ENTITLEMENTS}.can_create", return_value=allowed)
    mocker.patch(f"{ENTITLEMENTS}.check_feature", return_value=allowed)


@pytest.fixture
def stub_upload_pipeline(mocker):
    """Patch every side-effecting boundary of the create pipeline."""
    mocker.patch(
        f"{VIEW}.validate_all_files",
        return_value={"valid": True, "files_with_issues": []},
    )
    created_file = _existing_file("uploaded-doc.txt")
    create_files = mocker.patch(
        f"{VIEW}.create_files_and_upload",
        return_value={
            "files": [str(created_file.id)],
            "file_metadata": {
                str(created_file.id): {"name": "kb-doc.txt", "extension": "txt"}
            },
        },
    )
    schedule = mocker.patch(f"{MODULE}.schedule_kb_ingestion_on_commit")
    return {
        "created_file": created_file,
        "create_files": create_files,
        "schedule": schedule,
    }


@pytest.mark.integration
@pytest.mark.api
class TestLegacyKbCreateGuards:
    """Every reject branch of ``CreateKnowledgeBaseView.post``, in view order."""

    def test_duplicate_filenames_are_rejected(
        self, auth_client, allow_entitlements, organization
    ):
        response = auth_client.post(
            KB_URL,
            {
                "name": "dup-file-kb",
                "file": [_upload("same.txt"), _upload("same.txt")],
            },
            format="multipart",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not KnowledgeBaseFile.objects.filter(name="dup-file-kb").exists()

    def test_upload_over_size_cap_is_rejected(
        self, mocker, auth_client, allow_entitlements
    ):
        mocker.patch(f"{VIEW}.calculate_total_size", return_value=MAX_KB_SIZE + 1)

        response = auth_client.post(
            KB_URL,
            {"name": "oversize-kb", "file": _upload()},
            format="multipart",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not KnowledgeBaseFile.objects.filter(name="oversize-kb").exists()

    def test_can_create_entitlement_denied_returns_403(self, mocker, auth_client):
        mocker.patch(
            f"{ENTITLEMENTS}.can_create",
            return_value=mocker.MagicMock(allowed=False, reason="KB quota reached"),
        )

        response = auth_client.post(
            KB_URL, {"name": "quota-kb", "file": _upload()}, format="multipart"
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert not KnowledgeBaseFile.objects.filter(name="quota-kb").exists()

    def test_check_feature_entitlement_denied_returns_403(self, mocker, auth_client):
        mocker.patch(
            f"{ENTITLEMENTS}.can_create",
            return_value=mocker.MagicMock(allowed=True, reason=""),
        )
        mocker.patch(
            f"{ENTITLEMENTS}.check_feature",
            return_value=mocker.MagicMock(allowed=False, reason="KB not in plan"),
        )

        response = auth_client.post(
            KB_URL, {"name": "unplanned-kb", "file": _upload()}, format="multipart"
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert not KnowledgeBaseFile.objects.filter(name="unplanned-kb").exists()

    @pytest.mark.parametrize(
        "call_log_row_factory",
        [
            pytest.param(lambda mocker: None, id="no-call-log-row"),
            pytest.param(
                lambda mocker: mocker.MagicMock(
                    status=APICallStatusChoices.RESOURCE_LIMIT.value
                ),
                id="resource-limit-status",
            ),
        ],
    )
    def test_resource_limit_returns_429_when_entitlements_absent(
        self, mocker, auth_client, call_log_row_factory
    ):
        """With no entitlements module the view falls back to cost metering."""
        mocker.patch.dict("sys.modules", {"ee.usage.services.entitlements": None})
        mocker.patch(
            f"{MODULE}.log_and_deduct_cost_for_resource_request",
            return_value=call_log_row_factory(mocker),
        )

        response = auth_client.post(
            KB_URL, {"name": "throttled-kb", "file": _upload()}, format="multipart"
        )

        assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS
        assert not KnowledgeBaseFile.objects.filter(name="throttled-kb").exists()

    def test_unreadable_files_are_reported_before_any_kb_is_created(
        self, mocker, auth_client, allow_entitlements
    ):
        mocker.patch(
            f"{VIEW}.validate_all_files",
            return_value={
                "valid": False,
                "files_with_issues": [
                    {"name": "broken.txt", "error": "Unable to upload broken.txt"}
                ],
            },
        )
        create_files = mocker.patch(f"{VIEW}.create_files_and_upload")

        response = auth_client.post(
            KB_URL,
            {"name": "unreadable-kb", "file": _upload("broken.txt")},
            format="multipart",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        body = response.json()
        assert "broken.txt" in json.dumps(body)
        create_files.assert_not_called()
        assert not KnowledgeBaseFile.objects.filter(name="unreadable-kb").exists()

    def test_duplicate_kb_name_is_rejected(
        self, auth_client, allow_entitlements, stub_upload_pipeline, organization
    ):
        KnowledgeBaseFile.objects.create(
            name="taken-name",
            organization=organization,
            status=StatusType.COMPLETED.value,
            created_by="Test User",
            size=12,
        )

        response = auth_client.post(
            KB_URL, {"name": "taken-name", "file": _upload()}, format="multipart"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert (
            KnowledgeBaseFile.objects.filter(
                name="taken-name", organization=organization
            ).count()
            == 1
        )
        stub_upload_pipeline["schedule"].assert_not_called()


@pytest.mark.integration
@pytest.mark.api
class TestLegacyKbCreateHappyPath:
    def test_creates_kb_attaches_files_and_schedules_ingestion(
        self, auth_client, allow_entitlements, stub_upload_pipeline, organization
    ):
        response = auth_client.post(
            KB_URL, {"name": "brand-new-kb", "file": _upload()}, format="multipart"
        )

        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]
        assert result["detail"] == "Creating Knowledge Base"
        assert result["kb_name"] == "brand-new-kb"

        kb = KnowledgeBaseFile.objects.get(id=result["kb_id"])
        assert kb.organization_id == organization.id
        assert list(kb.files.values_list("id", flat=True)) == [
            stub_upload_pipeline["created_file"].id
        ]
        stub_upload_pipeline["schedule"].assert_called_once()

    def test_omitted_name_autogenerates_sequential_names(
        self, auth_client, allow_entitlements, stub_upload_pipeline, organization
    ):
        first = auth_client.post(KB_URL, {"file": _upload()}, format="multipart")

        assert first.status_code == status.HTTP_200_OK
        assert first.json()["result"]["kb_name"] == "Knowledge Base - 1"

        second = auth_client.post(KB_URL, {"file": _upload()}, format="multipart")

        assert second.status_code == status.HTTP_200_OK
        assert second.json()["result"]["kb_name"] == "Knowledge Base - 2"

    def test_kb_created_without_files_is_scoped_to_caller_org(
        self, auth_client, allow_entitlements, stub_upload_pipeline, organization
    ):
        response = auth_client.post(
            KB_URL, {"name": "empty-kb"}, format="multipart"
        )

        assert response.status_code == status.HTTP_200_OK
        kb = KnowledgeBaseFile.objects.get(id=response.json()["result"]["kb_id"])
        assert kb.organization_id == organization.id
        assert kb.size == 0


@pytest.mark.integration
@pytest.mark.api
class TestLegacyKbSdkSnippet:
    """``GET /model-hub/knowledge-base/`` returns a copy-paste SDK snippet."""

    def test_create_branch_embeds_kb_name_and_create_call(self, auth_client):
        response = auth_client.get(KB_URL, {"name": "snippet-kb"})

        assert response.status_code == status.HTTP_200_OK
        code = response.json()["result"]["code"]
        assert "client.create_kb(name=\"snippet-kb\"" in code
        assert "from fi.kb.client import KnowledgeBase" in code

    def test_update_branch_is_selected_by_type_param(self, auth_client):
        response = auth_client.get(KB_URL, {"name": "snippet-kb", "type": "update"})

        assert response.status_code == status.HTTP_200_OK
        code = response.json()["result"]["code"]
        assert "create_kb(name=" not in code
        assert "snippet-kb" in code

    def test_defaults_to_create_branch_without_type(self, auth_client):
        default = auth_client.get(KB_URL, {"name": "snippet-kb"})
        explicit = auth_client.get(KB_URL, {"name": "snippet-kb", "type": "create"})

        assert default.json()["result"]["code"] == explicit.json()["result"]["code"]
