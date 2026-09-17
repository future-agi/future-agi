import uuid

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from ee.falcon_ai.models import (
    Conversation,
    FalconFile,
    FalconMemory,
    MCPConnector,
    Message,
    Skill,
)


@pytest.mark.django_db
class TestConversationListView:
    URL = "/falcon-ai/conversations/"

    def test_list_empty(self, auth_client):
        resp = auth_client.get(self.URL)
        assert resp.status_code == 200
        assert resp.data["status"] is True
        assert resp.data["results"] == []

    def test_list_with_conversations(self, auth_client, conversation):
        resp = auth_client.get(self.URL)
        assert resp.status_code == 200
        assert len(resp.data["results"]) == 1
        assert resp.data["results"][0]["title"] == "Test Conversation"

    def test_list_includes_message_count(self, auth_client, conversation):
        Message.objects.create(conversation=conversation, role="user", content="Hi")
        Message.objects.create(
            conversation=conversation, role="assistant", content="Hey"
        )
        resp = auth_client.get(self.URL)
        assert resp.data["results"][0]["message_count"] == 2

    def test_create_conversation_with_title(self, auth_client):
        resp = auth_client.post(self.URL, {"title": "My Chat"})
        assert resp.status_code == 201
        assert resp.data["status"] is True
        assert resp.data["result"]["title"] == "My Chat"

    def test_create_conversation_default_title(self, auth_client):
        resp = auth_client.post(self.URL, {})
        assert resp.status_code == 201
        assert resp.data["result"]["title"] == "New conversation"

    def test_create_conversation_with_context_page(self, auth_client):
        resp = auth_client.post(
            self.URL, {"title": "From Data", "context_page": "/dashboard/data"}
        )
        assert resp.status_code == 201
        assert resp.data["result"]["context_page"] == "/dashboard/data"

    def test_create_rejects_unknown_fields(self, auth_client):
        resp = auth_client.post(
            self.URL,
            {"title": "My Chat", "displayName": "legacy alias"},
            format="json",
        )
        assert resp.status_code == 400

    def test_create_rejects_invalid_title_shape(self, auth_client):
        resp = auth_client.post(
            self.URL,
            {"title": {"bad": "shape"}},
            format="json",
        )
        assert resp.status_code == 400
        assert Conversation.objects.count() == 0

    def test_unauthenticated_request(self, api_client):
        resp = api_client.get(self.URL)
        assert resp.status_code in (401, 403)


@pytest.mark.django_db
class TestConversationDetailView:
    def _url(self, conv_id):
        return f"/falcon-ai/conversations/{conv_id}/"

    def test_get_conversation(self, auth_client, conversation):
        resp = auth_client.get(self._url(conversation.id))
        assert resp.status_code == 200
        assert resp.data["status"] is True
        assert resp.data["result"]["id"] == str(conversation.id)

    def test_get_conversation_with_messages(self, auth_client, conversation, message):
        resp = auth_client.get(self._url(conversation.id))
        assert len(resp.data["result"]["messages"]) == 1
        assert resp.data["result"]["messages"][0]["role"] == "assistant"

    def test_get_nonexistent_conversation(self, auth_client):
        fake_id = uuid.uuid4()
        resp = auth_client.get(self._url(fake_id))
        assert resp.status_code == 404
        assert resp.data["status"] is False
        assert resp.data["type"] == "not_found"
        assert resp.data["code"] == "not_found"
        assert resp.data["detail"] == "Conversation not found"

    def test_rename_conversation(self, auth_client, conversation):
        resp = auth_client.patch(self._url(conversation.id), {"title": "Renamed"})
        assert resp.status_code == 200
        assert resp.data["result"]["title"] == "Renamed"
        conversation.refresh_from_db()
        assert conversation.title == "Renamed"

    def test_rename_rejects_unknown_fields(self, auth_client, conversation):
        resp = auth_client.patch(
            self._url(conversation.id),
            {"title": "Renamed", "displayName": "legacy alias"},
            format="json",
        )
        assert resp.status_code == 400
        conversation.refresh_from_db()
        assert conversation.title == "Test Conversation"

    def test_rename_rejects_invalid_title_shape(self, auth_client, conversation):
        resp = auth_client.patch(
            self._url(conversation.id),
            {"title": {"bad": "shape"}},
            format="json",
        )
        assert resp.status_code == 400
        conversation.refresh_from_db()
        assert conversation.title == "Test Conversation"

    def test_delete_conversation(self, auth_client, conversation):
        resp = auth_client.delete(self._url(conversation.id))
        assert resp.status_code == 204
        # Should be soft-deleted
        assert not Conversation.objects.filter(id=conversation.id).exists()
        assert Conversation.all_objects.filter(id=conversation.id).exists()

    def test_workspace_isolation(self, auth_client, user):
        """User cannot access conversations from a different organization."""
        from accounts.models.organization import Organization
        from accounts.models.workspace import Workspace

        other_org = Organization.objects.create(name="Other Org")
        other_ws = Workspace.objects.create(
            name="Other WS",
            organization=other_org,
            is_default=True,
            is_active=True,
            created_by=user,
        )
        other_conv = Conversation.no_workspace_objects.create(
            user=user,  # same user but different org
            organization=other_org,
            workspace=other_ws,
            title="Other Org Conv",
        )
        resp = auth_client.get(f"/falcon-ai/conversations/{other_conv.id}/")
        assert resp.status_code == 404

    def test_same_org_other_workspace_isolation(self, auth_client, user):
        """User cannot access conversations from another active workspace."""
        from accounts.models.workspace import Workspace

        other_ws = Workspace.objects.create(
            name="Other Same Org WS",
            organization=user.organization,
            is_default=False,
            is_active=True,
            created_by=user,
        )
        other_conv = Conversation.no_workspace_objects.create(
            user=user,
            organization=user.organization,
            workspace=other_ws,
            title="Other Workspace Conv",
        )
        message = Message.objects.create(
            conversation=other_conv,
            role="assistant",
            content="Other workspace response",
        )

        resp = auth_client.get(f"/falcon-ai/conversations/{other_conv.id}/")
        assert resp.status_code == 404

        stream_resp = auth_client.get(
            f"/falcon-ai/conversations/{other_conv.id}/stream-status/"
        )
        assert stream_resp.status_code == 404

        feedback_resp = auth_client.post(
            f"/falcon-ai/messages/{message.id}/feedback/",
            {"feedback": "thumbs_up"},
        )
        assert feedback_resp.status_code == 404


@pytest.mark.django_db
class TestMessageFeedbackView:
    def _url(self, msg_id):
        return f"/falcon-ai/messages/{msg_id}/feedback/"

    def test_submit_feedback(self, auth_client, message):
        resp = auth_client.post(self._url(message.id), {"feedback": "thumbs_down"})
        assert resp.status_code == 200
        assert resp.data["status"] is True
        assert resp.data["result"]["feedback"] == "thumbs_down"
        message.refresh_from_db()
        assert message.feedback == "thumbs_down"

    def test_clear_feedback(self, auth_client, message):
        message.feedback = "thumbs_down"
        message.save()
        resp = auth_client.post(self._url(message.id), {"feedback": ""})
        assert resp.status_code == 200
        message.refresh_from_db()
        assert message.feedback == ""

    def test_invalid_feedback_value(self, auth_client, message):
        resp = auth_client.post(self._url(message.id), {"feedback": "invalid_value"})
        assert resp.status_code == 400

    def test_rejects_unknown_feedback_field(self, auth_client, message):
        resp = auth_client.post(
            self._url(message.id),
            {"feedback": "thumbs_up", "legacy_extra": True},
        )
        assert resp.status_code == 400
        assert resp.data["details"]["legacy_extra"] == ["Unknown field."]

    def test_nonexistent_message(self, auth_client):
        fake_id = uuid.uuid4()
        resp = auth_client.post(self._url(fake_id), {"feedback": "thumbs_down"})
        assert resp.status_code == 404
        assert resp.data["status"] is False
        assert resp.data["type"] == "not_found"
        assert resp.data["code"] == "not_found"
        assert resp.data["detail"] == "Message not found"


@pytest.mark.django_db
class TestFileUploadView:
    URL = "/falcon-ai/files/upload/"

    def test_upload_text_file_uses_s3_env_and_extracts_text(
        self, auth_client, monkeypatch
    ):
        class FakeMinio:
            instances = []

            def __init__(self, endpoint, access_key, secret_key, secure):
                self.endpoint = endpoint
                self.access_key = access_key
                self.secret_key = secret_key
                self.secure = secure
                self.put_calls = []
                self.__class__.instances.append(self)

            def bucket_exists(self, bucket):
                return True

            def put_object(self, bucket, object_key, data, size, content_type):
                self.put_calls.append(
                    {
                        "bucket": bucket,
                        "object_key": object_key,
                        "data": data.read(),
                        "size": size,
                        "content_type": content_type,
                    }
                )

        monkeypatch.delenv("MINIO_ENDPOINT", raising=False)
        monkeypatch.delenv("MINIO_ROOT_USER", raising=False)
        monkeypatch.delenv("MINIO_ROOT_PASSWORD", raising=False)
        monkeypatch.setenv("S3_ENDPOINT_URL", "http://minio:9000")
        monkeypatch.setenv("S3_ACCESS_KEY", "futureagi")
        monkeypatch.setenv("S3_SECRET_KEY", "futureagi")
        monkeypatch.setattr("minio.Minio", FakeMinio)

        upload = SimpleUploadedFile(
            "falcon notes.txt",
            b"Falcon file upload text",
            content_type="application/octet-stream",
        )
        resp = auth_client.post(self.URL, {"file": upload}, format="multipart")

        assert resp.status_code == 201
        assert resp.data["result"]["name"] == "falcon notes.txt"
        assert resp.data["result"]["content_type"] == "text/plain"
        falcon_file = FalconFile.objects.get(id=resp.data["result"]["id"])
        assert falcon_file.text_content == "Falcon file upload text"
        assert falcon_file.storage_key.endswith("/falcon notes.txt")
        assert FakeMinio.instances[0].endpoint == "minio:9000"
        assert FakeMinio.instances[0].access_key == "futureagi"
        assert FakeMinio.instances[0].secret_key == "futureagi"
        assert FakeMinio.instances[0].secure is False
        assert FakeMinio.instances[0].put_calls[0]["content_type"] == "text/plain"

    def test_upload_rejects_unsupported_type(self, auth_client):
        upload = SimpleUploadedFile(
            "payload.bin",
            b"plain bytes",
            content_type="application/octet-stream",
        )
        resp = auth_client.post(self.URL, {"file": upload}, format="multipart")

        assert resp.status_code == 400
        assert FalconFile.objects.count() == 0

    def test_upload_rejects_dangerous_signature(self, auth_client):
        upload = SimpleUploadedFile(
            "script.txt",
            b"#!/bin/sh\necho unsafe\n",
            content_type="text/plain",
        )
        resp = auth_client.post(self.URL, {"file": upload}, format="multipart")

        assert resp.status_code == 400
        assert FalconFile.objects.count() == 0


@pytest.mark.django_db
class TestMemoryView:
    URL = "/falcon-ai/memory/"

    def test_create_rejects_unknown_fields(self, auth_client):
        resp = auth_client.post(
            self.URL,
            {"key": "preferred_project", "value": "demo", "displayName": "legacy"},
            format="json",
        )

        assert resp.status_code == 400
        assert FalconMemory.objects.count() == 0

    def test_same_org_other_workspace_delete_isolation(self, auth_client, user):
        from accounts.models.workspace import Workspace

        other_ws = Workspace.objects.create(
            name="Other Falcon Memory WS",
            organization=user.organization,
            is_default=False,
            is_active=True,
            created_by=user,
        )
        other_memory = FalconMemory.no_workspace_objects.create(
            organization=user.organization,
            workspace=other_ws,
            key="other_workspace_key",
            value="other workspace value",
            created_by=user,
        )

        list_resp = auth_client.get(self.URL)
        assert list_resp.status_code == 200
        assert all(
            row["id"] != str(other_memory.id) for row in list_resp.data["results"]
        )

        delete_resp = auth_client.delete(f"{self.URL}{other_memory.id}/")
        assert delete_resp.status_code == 404
        assert FalconMemory.no_workspace_objects.filter(id=other_memory.id).exists()


@pytest.mark.django_db
class TestMCPConnectorDetailView:
    def _url(self, connector_id):
        return f"/falcon-ai/mcp-connectors/{connector_id}/"

    def _connector(self, user, workspace):
        return MCPConnector.objects.create(
            organization=user.organization,
            workspace=workspace,
            name="Docs",
            server_url="https://example.com/mcp",
            created_by=user,
        )

    def _other_workspace_connector(self, user):
        from accounts.models.workspace import Workspace

        other_ws = Workspace.objects.create(
            name="Other Falcon Connector WS",
            organization=user.organization,
            is_default=False,
            is_active=True,
            created_by=user,
        )
        return MCPConnector.no_workspace_objects.create(
            organization=user.organization,
            workspace=other_ws,
            name="Other Workspace Docs",
            server_url="https://example.com/other-workspace-mcp",
            discovered_tools=[
                {
                    "name": "search_docs",
                    "description": "Other workspace tool",
                }
            ],
            created_by=user,
        )

    def test_update_rejects_unknown_fields(self, auth_client, user, workspace):
        connector = self._connector(user, workspace)

        resp = auth_client.patch(
            self._url(connector.id),
            {"name": "Docs 2", "serverUrl": "https://bad.example.com/mcp"},
            format="json",
        )

        assert resp.status_code == 400
        connector.refresh_from_db()
        assert connector.name == "Docs"
        assert connector.server_url == "https://example.com/mcp"

    def test_update_rejects_invalid_url(self, auth_client, user, workspace):
        connector = self._connector(user, workspace)

        resp = auth_client.patch(
            self._url(connector.id),
            {"server_url": "not-a-url"},
            format="json",
        )

        assert resp.status_code == 400
        connector.refresh_from_db()
        assert connector.server_url == "https://example.com/mcp"

    def test_update_rejects_invalid_transport(self, auth_client, user, workspace):
        connector = self._connector(user, workspace)

        resp = auth_client.patch(
            self._url(connector.id),
            {"transport": "websocket"},
            format="json",
        )

        assert resp.status_code == 400
        connector.refresh_from_db()
        assert connector.transport == "streamable_http"

    def test_get_missing_connector_uses_error_envelope(self, auth_client):
        resp = auth_client.get(self._url(uuid.uuid4()))

        assert resp.status_code == 404
        assert resp.data["status"] is False
        assert resp.data["type"] == "not_found"
        assert resp.data["code"] == "not_found"
        assert resp.data["detail"] == "Connector not found"

    def test_same_org_other_workspace_detail_update_delete_isolation(
        self, auth_client, user
    ):
        other_connector = self._other_workspace_connector(user)

        resp = auth_client.get(self._url(other_connector.id))
        assert resp.status_code == 404

        patch_resp = auth_client.patch(
            self._url(other_connector.id),
            {"name": "mutated"},
            format="json",
        )
        assert patch_resp.status_code == 404

        delete_resp = auth_client.delete(self._url(other_connector.id))
        assert delete_resp.status_code == 404
        other_connector.refresh_from_db()
        assert other_connector.name == "Other Workspace Docs"
        assert MCPConnector.no_workspace_objects.filter(id=other_connector.id).exists()

    @pytest.mark.parametrize(
        ("method", "suffix", "payload"),
        [
            ("post", "discover/", {}),
            ("post", "test/", {}),
            ("post", "authenticate/", {}),
            ("patch", "tools/", {"enabled_tool_names": ["search_docs"]}),
        ],
    )
    def test_same_org_other_workspace_action_isolation(
        self, auth_client, user, method, suffix, payload
    ):
        other_connector = self._other_workspace_connector(user)

        request = getattr(auth_client, method)
        resp = request(
            f"{self._url(other_connector.id)}{suffix}",
            payload,
            format="json",
        )

        assert resp.status_code == 404
        other_connector.refresh_from_db()
        assert other_connector.enabled_tool_names == []


@pytest.mark.django_db
class TestSkillDetailView:
    def _url(self, skill_id):
        return f"/falcon-ai/skills/{skill_id}/"

    def _skill(self, user, workspace):
        return Skill.objects.create(
            organization=user.organization,
            workspace=workspace,
            name="Custom Skill",
            slug="custom-skill",
            description="",
            instructions="",
            is_builtin=False,
            created_by=user,
        )

    def test_update_rejects_unknown_fields(self, auth_client, user, workspace):
        skill = self._skill(user, workspace)

        resp = auth_client.patch(
            self._url(skill.id),
            {"name": "New Skill", "displayName": "legacy alias"},
            format="json",
        )

        assert resp.status_code == 400
        skill.refresh_from_db()
        assert skill.name == "Custom Skill"

    def test_update_rejects_invalid_tool_names_shape(
        self, auth_client, user, workspace
    ):
        skill = self._skill(user, workspace)

        resp = auth_client.patch(
            self._url(skill.id),
            {"tool_names": "not-a-list"},
            format="json",
        )

        assert resp.status_code == 400
        skill.refresh_from_db()
        assert skill.tool_names == []

    def test_get_missing_skill_uses_error_envelope(self, auth_client):
        resp = auth_client.get(self._url(uuid.uuid4()))

        assert resp.status_code == 404
        assert resp.data["status"] is False
        assert resp.data["type"] == "not_found"
        assert resp.data["code"] == "not_found"
        assert resp.data["detail"] == "Skill not found"

    def test_same_org_other_workspace_isolation(self, auth_client, user):
        from accounts.models.workspace import Workspace

        other_ws = Workspace.objects.create(
            name="Other Falcon Skill WS",
            organization=user.organization,
            is_default=False,
            is_active=True,
            created_by=user,
        )
        other_skill = Skill.no_workspace_objects.create(
            organization=user.organization,
            workspace=other_ws,
            name="Other Workspace Skill",
            slug="other-workspace-skill",
            description="",
            instructions="Do not leak",
            is_builtin=False,
            created_by=user,
        )

        resp = auth_client.get(self._url(other_skill.id))
        assert resp.status_code == 404

        patch_resp = auth_client.patch(
            self._url(other_skill.id),
            {"description": "mutated"},
            format="json",
        )
        assert patch_resp.status_code == 404

        delete_resp = auth_client.delete(self._url(other_skill.id))
        assert delete_resp.status_code == 404
        assert Skill.no_workspace_objects.filter(id=other_skill.id).exists()


@pytest.mark.django_db
class TestSkillListView:
    URL = "/falcon-ai/skills/"

    def test_create_rejects_unknown_fields(self, auth_client):
        resp = auth_client.post(
            self.URL,
            {
                "name": "Custom Skill",
                "description": "",
                "instructions": "Help safely",
                "trigger_phrases": ["help safely"],
                "displayName": "legacy alias",
            },
            format="json",
        )

        assert resp.status_code == 400
        assert Skill.objects.count() == 0
