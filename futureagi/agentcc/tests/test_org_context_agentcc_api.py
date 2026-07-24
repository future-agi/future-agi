import pytest
from cryptography.fernet import Fernet

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from agentcc.models.blocklist import AgentccBlocklist
from agentcc.models.custom_property import AgentccCustomPropertySchema
from agentcc.models.email_alert import AgentccEmailAlert
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.models.routing_policy import AgentccRoutingPolicy
from agentcc.models.session import AgentccSession
from agentcc.models.webhook import AgentccWebhook
from conftest import WorkspaceAwareAPIClient
from integrations.services.credentials import CredentialManager
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles


@pytest.fixture
def secondary_org_context(user):
    org_b = Organization.objects.create(name="Agentcc Secondary Org")
    membership = OrganizationMembership.no_workspace_objects.create(
        user=user,
        organization=org_b,
        role=OrganizationRoles.OWNER,
        level=Level.OWNER,
        is_active=True,
    )
    workspace_b = Workspace.objects.create(
        name="Agentcc Secondary Workspace",
        organization=org_b,
        is_default=True,
        is_active=True,
        created_by=user,
    )
    WorkspaceMembership.objects.create(
        workspace=workspace_b,
        user=user,
        role=OrganizationRoles.WORKSPACE_ADMIN,
        level=Level.WORKSPACE_ADMIN,
        organization_membership=membership,
        is_active=True,
    )
    return org_b, workspace_b


@pytest.fixture
def secondary_org_client(user, secondary_org_context):
    _, workspace_b = secondary_org_context
    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=user)
    client.set_workspace(workspace_b)
    yield client
    client.stop_workspace_injection()


@pytest.mark.integration
@pytest.mark.api
class TestAgentccRequestOrganizationContext:
    def test_session_create_uses_active_request_organization(
        self, user, secondary_org_context, secondary_org_client
    ):
        org_b, _ = secondary_org_context

        response = secondary_org_client.post(
            "/agentcc/sessions/",
            {"session_id": "sec-org-session", "name": "secondary"},
            format="json",
        )

        assert response.status_code == 200, response.json()
        session = AgentccSession.no_workspace_objects.get(session_id="sec-org-session")
        assert session.organization_id == org_b.id
        assert session.organization_id != user.organization_id

    @pytest.mark.requires_ee
    def test_webhook_create_uses_active_request_organization(
        self, user, secondary_org_context, secondary_org_client
    ):
        org_b, _ = secondary_org_context

        response = secondary_org_client.post(
            "/agentcc/webhooks/",
            {
                "name": "secondary_webhook",
                "url": "https://example.com/webhook",
                "events": ["request.completed"],
            },
            format="json",
        )

        assert response.status_code == 200, response.json()
        webhook = AgentccWebhook.no_workspace_objects.get(name="secondary_webhook")
        assert webhook.organization_id == org_b.id
        assert webhook.organization_id != user.organization_id

    def test_custom_property_create_uses_active_request_organization(
        self, user, secondary_org_context, secondary_org_client
    ):
        org_b, _ = secondary_org_context

        response = secondary_org_client.post(
            "/agentcc/custom-properties/",
            {"name": "secondary_property", "property_type": "string"},
            format="json",
        )

        assert response.status_code == 200, response.json()
        schema = AgentccCustomPropertySchema.no_workspace_objects.get(
            name="secondary_property"
        )
        assert schema.organization_id == org_b.id
        assert schema.organization_id != user.organization_id

    def test_custom_property_validate_rejects_boolean_for_number(
        self, secondary_org_client
    ):
        create_response = secondary_org_client.post(
            "/agentcc/custom-properties/",
            {
                "name": "secondary_number_property",
                "property_type": "number",
            },
            format="json",
        )
        assert create_response.status_code == 200, create_response.json()

        response = secondary_org_client.post(
            "/agentcc/custom-properties/validate/",
            {"properties": {"secondary_number_property": True}},
            format="json",
        )

        assert response.status_code == 200, response.json()
        assert response.json()["result"]["valid"] is False
        assert "must be a number" in str(response.json()["result"]["errors"])

    def test_custom_property_delete_stamps_deleted_at(self, secondary_org_client):
        create_response = secondary_org_client.post(
            "/agentcc/custom-properties/",
            {"name": "secondary_property_delete_stamp", "property_type": "string"},
            format="json",
        )
        assert create_response.status_code == 200, create_response.json()
        schema_id = create_response.json()["result"]["id"]

        response = secondary_org_client.delete(
            f"/agentcc/custom-properties/{schema_id}/"
        )

        assert response.status_code == 200, response.json()
        schema = AgentccCustomPropertySchema.all_objects.get(id=schema_id)
        assert schema.deleted is True
        assert schema.deleted_at is not None

    def test_blocklist_create_uses_active_request_organization(
        self, user, secondary_org_context, secondary_org_client
    ):
        org_b, _ = secondary_org_context

        response = secondary_org_client.post(
            "/agentcc/blocklists/",
            {"name": "secondary_blocklist", "words": ["foo"]},
            format="json",
        )

        assert response.status_code == 200, response.json()
        blocklist = AgentccBlocklist.no_workspace_objects.get(
            name="secondary_blocklist"
        )
        assert blocklist.organization_id == org_b.id
        assert blocklist.organization_id != user.organization_id

    def test_blocklist_add_words_rejects_non_string_values(self, secondary_org_client):
        create_response = secondary_org_client.post(
            "/agentcc/blocklists/",
            {"name": "secondary_blocklist_word_validation", "words": ["foo"]},
            format="json",
        )
        assert create_response.status_code == 200, create_response.json()
        blocklist_id = create_response.json()["result"]["id"]

        response = secondary_org_client.post(
            f"/agentcc/blocklists/{blocklist_id}/add-words/",
            {"words": ["bar", 123]},
            format="json",
        )

        assert response.status_code == 400
        assert "Word at index 1 must be a string" in str(response.json())
        blocklist = AgentccBlocklist.no_workspace_objects.get(id=blocklist_id)
        assert blocklist.words == ["foo"]

    def test_blocklist_delete_stamps_deleted_at(self, secondary_org_client):
        create_response = secondary_org_client.post(
            "/agentcc/blocklists/",
            {"name": "secondary_blocklist_delete_stamp", "words": ["foo"]},
            format="json",
        )
        assert create_response.status_code == 200, create_response.json()
        blocklist_id = create_response.json()["result"]["id"]

        response = secondary_org_client.delete(f"/agentcc/blocklists/{blocklist_id}/")

        assert response.status_code == 200, response.json()
        blocklist = AgentccBlocklist.all_objects.get(id=blocklist_id)
        assert blocklist.deleted is True
        assert blocklist.deleted_at is not None

    def test_routing_policy_create_uses_active_request_organization(
        self, user, secondary_org_context, secondary_org_client
    ):
        org_b, _ = secondary_org_context

        response = secondary_org_client.post(
            "/agentcc/routing-policies/",
            {"name": "secondary_policy", "config": {"strategy": "latency"}},
            format="json",
        )

        assert response.status_code == 200, response.json()
        policy = AgentccRoutingPolicy.no_workspace_objects.get(name="secondary_policy")
        assert policy.organization_id == org_b.id
        assert policy.organization_id != user.organization_id
        assert policy.created_by_id == user.id

    def test_routing_policy_delete_stamps_deleted_at(self, secondary_org_client):
        create_response = secondary_org_client.post(
            "/agentcc/routing-policies/",
            {"name": "secondary_policy_delete_stamp", "config": {"strategy": "cost"}},
            format="json",
        )
        assert create_response.status_code == 200, create_response.json()
        policy_id = create_response.json()["result"]["id"]

        response = secondary_org_client.delete(
            f"/agentcc/routing-policies/{policy_id}/"
        )

        assert response.status_code == 200, response.json()
        policy = AgentccRoutingPolicy.all_objects.get(id=policy_id)
        assert policy.deleted is True
        assert policy.deleted_at is not None

    @pytest.mark.requires_ee
    def test_email_alert_create_uses_active_request_organization(
        self, user, secondary_org_context, secondary_org_client
    ):
        org_b, _ = secondary_org_context

        response = secondary_org_client.post(
            "/agentcc/email-alerts/",
            {
                "name": "secondary_alert",
                "recipients": ["alerts@example.com"],
                "events": ["budget.exceeded"],
                "provider": "sendgrid",
                "provider_config": {"api_key": "sg.test"},
            },
            format="json",
        )

        assert response.status_code == 200, response.json()
        alert = AgentccEmailAlert.no_workspace_objects.get(name="secondary_alert")
        assert alert.organization_id == org_b.id
        assert alert.organization_id != user.organization_id

    @pytest.mark.requires_ee
    def test_email_alert_update_preserves_existing_secret_when_omitted(
        self, user, secondary_org_context, secondary_org_client, settings
    ):
        settings.INTEGRATION_ENCRYPTION_KEY = Fernet.generate_key().decode()

        create_response = secondary_org_client.post(
            "/agentcc/email-alerts/",
            {
                "name": "secondary_alert_secret",
                "recipients": ["alerts@example.com"],
                "events": ["budget.exceeded"],
                "provider": "sendgrid",
                "provider_config": {
                    "api_key": "sg.original-secret",
                    "from_email": "old@example.com",
                },
            },
            format="json",
        )
        assert create_response.status_code == 200, create_response.json()
        alert_id = create_response.json()["result"]["id"]

        update_response = secondary_org_client.patch(
            f"/agentcc/email-alerts/{alert_id}/",
            {
                "provider_config": {"from_email": "new@example.com"},
                "cooldown_minutes": 10,
            },
            format="json",
        )

        assert update_response.status_code == 200, update_response.json()
        alert = AgentccEmailAlert.no_workspace_objects.get(id=alert_id)
        config = CredentialManager.decrypt(bytes(alert.encrypted_config))
        assert config["api_key"] == "sg.original-secret"
        assert config["from_email"] == "new@example.com"
        assert alert.cooldown_minutes == 10

    def test_org_config_active_uses_active_request_organization(
        self, user, secondary_org_context, secondary_org_client
    ):
        org_b, _ = secondary_org_context
        AgentccOrgConfig.no_workspace_objects.create(
            organization=user.organization,
            version=1,
            is_active=True,
            cache={"enabled": False},
        )
        AgentccOrgConfig.no_workspace_objects.create(
            organization=org_b,
            version=7,
            is_active=True,
            cache={"enabled": True},
        )

        response = secondary_org_client.get("/agentcc/org-configs/active/")

        assert response.status_code == 200, response.json()
        payload = response.json()["result"]
        assert payload["version"] == 7
        assert payload["cache"]["enabled"] is True
