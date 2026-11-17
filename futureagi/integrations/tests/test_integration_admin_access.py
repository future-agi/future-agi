"""Integrations are added, changed and removed only by the roles the console
offers Add Integration to: an organization Owner or Admin, or an Admin of the
workspace the request acts on. Every other member keeps read access.
"""

import uuid
from contextlib import contextmanager
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

import pytest
from rest_framework import status

from accounts.models import User
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import WorkspaceMembership
from conftest import WorkspaceAwareAPIClient
from integrations.models import (
    ConnectionStatus,
    IntegrationConnection,
    IntegrationPlatform,
)
from integrations.services import slack_oauth
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles

CONNECTIONS = "/integrations/connections/"

ORG_ROLES = {
    Level.OWNER: OrganizationRoles.OWNER,
    Level.ADMIN: OrganizationRoles.ADMIN,
    Level.MEMBER: OrganizationRoles.MEMBER,
    Level.VIEWER: OrganizationRoles.MEMBER_VIEW_ONLY,
}
WORKSPACE_ROLES = {
    Level.WORKSPACE_ADMIN: OrganizationRoles.WORKSPACE_ADMIN,
    Level.WORKSPACE_MEMBER: OrganizationRoles.WORKSPACE_MEMBER,
    Level.WORKSPACE_VIEWER: OrganizationRoles.WORKSPACE_VIEWER,
}

VALIDATED = {"valid": True, "projects": [{"id": "p1", "name": "lf-project"}]}


def _user_in(organization, workspace, org_level, workspace_level):
    user = User.objects.create_user(
        email=f"int-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name="Integration Role User",
        organization=organization,
        organization_role=ORG_ROLES[org_level],
    )
    membership = OrganizationMembership.no_workspace_objects.create(
        user=user,
        organization=organization,
        role=ORG_ROLES[org_level],
        level=org_level,
        is_active=True,
    )
    WorkspaceMembership.no_workspace_objects.create(
        user=user,
        workspace=workspace,
        role=WORKSPACE_ROLES[workspace_level],
        level=workspace_level,
        organization_membership=membership,
        is_active=True,
    )
    return user


@pytest.fixture
def make_client():
    clients = []

    def build(user, workspace):
        client = WorkspaceAwareAPIClient()
        client.force_authenticate(user=user)
        client.set_workspace(workspace)
        clients.append(client)
        return client

    yield build
    for client in clients:
        client.stop_workspace_injection()


@pytest.fixture
def member_user(organization, workspace):
    return _user_in(organization, workspace, Level.MEMBER, Level.WORKSPACE_MEMBER)


@pytest.fixture
def member_client(make_client, member_user, workspace):
    return make_client(member_user, workspace)


@pytest.fixture
def viewer_client(make_client, organization, workspace):
    viewer = _user_in(organization, workspace, Level.VIEWER, Level.WORKSPACE_VIEWER)
    return make_client(viewer, workspace)


@pytest.fixture
def workspace_admin_client(make_client, organization, workspace):
    ws_admin = _user_in(organization, workspace, Level.MEMBER, Level.WORKSPACE_ADMIN)
    return make_client(ws_admin, workspace)


@pytest.fixture
def org_admin_client(make_client, organization, workspace):
    admin = _user_in(organization, workspace, Level.ADMIN, Level.WORKSPACE_ADMIN)
    return make_client(admin, workspace)


def _langfuse_payload(**extra):
    return {
        "platform": "langfuse",
        "host_url": "https://langfuse.example.com",
        "public_key": "pk-lf-test-value",
        "secret_key": "sk-lf-test-value",
        "external_project_name": "lf-project",
        "backfill_option": "new_only",
        **extra,
    }


@contextmanager
def _platform_service():
    service = MagicMock()
    service.validate_credentials.return_value = VALIDATED
    with patch(
        "integrations.views.integration_connection.get_integration_service",
        return_value=service,
    ):
        yield service


def _assert_refused(response):
    assert response.status_code == status.HTTP_403_FORBIDDEN, response.content


@pytest.mark.django_db
@pytest.mark.api
class TestOnlyIntegrationAdminsChangeConnections:
    def test_adding_a_connection_is_limited_to_integration_admins(
        self, member_client, viewer_client
    ):
        with _platform_service() as service:
            for client in (member_client, viewer_client):
                _assert_refused(
                    client.post(CONNECTIONS, _langfuse_payload(), format="json")
                )
        service.validate_credentials.assert_not_called()
        assert not IntegrationConnection.no_workspace_objects.exists()

    def test_checking_credentials_is_limited_to_integration_admins(self, member_client):
        with _platform_service() as service:
            response = member_client.post(
                f"{CONNECTIONS}validate/",
                {
                    "platform": "langfuse",
                    "host_url": "https://langfuse.example.com",
                    "public_key": "pk-lf-test-value",
                    "secret_key": "sk-lf-test-value",
                },
                format="json",
            )
        _assert_refused(response)
        service.validate_credentials.assert_not_called()

    def test_replacing_a_connection_is_limited_to_integration_admins(
        self, member_client, integration_connection
    ):
        response = member_client.put(
            f"{CONNECTIONS}{integration_connection.id}/",
            {"display_name": "Renamed"},
            format="json",
        )
        _assert_refused(response)
        integration_connection.refresh_from_db()
        assert integration_connection.display_name == "Test Langfuse"

    def test_editing_a_connection_is_limited_to_integration_admins(
        self, member_client, integration_connection
    ):
        response = member_client.patch(
            f"{CONNECTIONS}{integration_connection.id}/",
            {"display_name": "Renamed"},
            format="json",
        )
        _assert_refused(response)
        integration_connection.refresh_from_db()
        assert integration_connection.display_name == "Test Langfuse"

    def test_removing_a_connection_is_limited_to_integration_admins(
        self, member_client, viewer_client, integration_connection
    ):
        for client in (member_client, viewer_client):
            _assert_refused(client.delete(f"{CONNECTIONS}{integration_connection.id}/"))
        assert IntegrationConnection.no_workspace_objects.filter(
            pk=integration_connection.pk, deleted=False
        ).exists()

    def test_pausing_a_connection_is_limited_to_integration_admins(
        self, member_client, integration_connection
    ):
        response = member_client.post(
            f"{CONNECTIONS}{integration_connection.id}/pause/", {}, format="json"
        )
        _assert_refused(response)
        integration_connection.refresh_from_db()
        assert integration_connection.status == ConnectionStatus.ACTIVE

    def test_resuming_a_connection_is_limited_to_integration_admins(
        self, member_client, paused_connection
    ):
        response = member_client.post(
            f"{CONNECTIONS}{paused_connection.id}/resume/", {}, format="json"
        )
        _assert_refused(response)
        paused_connection.refresh_from_db()
        assert paused_connection.status == ConnectionStatus.PAUSED

    def test_every_member_still_reads_connections(
        self, member_client, viewer_client, integration_connection
    ):
        for client in (member_client, viewer_client):
            listed = client.get(CONNECTIONS)
            assert listed.status_code == status.HTTP_200_OK
            assert len(listed.json()["result"]["connections"]) == 1
            detail = client.get(f"{CONNECTIONS}{integration_connection.id}/")
            assert detail.status_code == status.HTTP_200_OK


@pytest.mark.django_db
@pytest.mark.api
class TestIntegrationAdminsKeepFullAccess:
    def test_a_workspace_admin_adds_and_removes_a_connection(
        self, workspace_admin_client
    ):
        with _platform_service():
            created = workspace_admin_client.post(
                CONNECTIONS, _langfuse_payload(), format="json"
            )
        assert created.status_code == status.HTTP_201_CREATED, created.content
        connection_id = created.json()["result"]["id"]
        removed = workspace_admin_client.delete(f"{CONNECTIONS}{connection_id}/")
        assert removed.status_code == status.HTTP_200_OK

    def test_an_org_admin_edits_pauses_and_resumes_a_connection(
        self, org_admin_client, integration_connection
    ):
        url = f"{CONNECTIONS}{integration_connection.id}/"
        edited = org_admin_client.patch(url, {"display_name": "Renamed"}, format="json")
        assert edited.status_code == status.HTTP_200_OK
        paused = org_admin_client.post(f"{url}pause/", {}, format="json")
        assert paused.status_code == status.HTTP_200_OK
        resumed = org_admin_client.post(f"{url}resume/", {}, format="json")
        assert resumed.status_code == status.HTTP_200_OK


@pytest.mark.django_db
@pytest.mark.api
class TestOnlyIntegrationAdminsConnectSlack:
    @pytest.fixture(autouse=True)
    def _slack_app(self, settings):
        settings.SLACK_CLIENT_ID = "client-id"
        settings.SLACK_CLIENT_SECRET = "client-secret"
        settings.APP_BASE_URL = "https://app.example.com"

    def test_starting_a_slack_install_is_limited_to_integration_admins(
        self, member_client, viewer_client
    ):
        for client in (member_client, viewer_client):
            _assert_refused(
                client.post("/integrations/slack/install/", {}, format="json")
            )

    def test_a_workspace_admin_starts_a_slack_install(self, workspace_admin_client):
        response = workspace_admin_client.post(
            "/integrations/slack/install/", {}, format="json"
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["result"]["authorization_url"].startswith(
            slack_oauth.AUTHORIZE_URL
        )

    def test_the_slack_callback_connects_only_for_an_integration_admin(
        self, api_client, organization, workspace, member_user
    ):
        started = slack_oauth.start_slack_install(
            organization=organization, workspace=workspace, user=member_user
        )
        state = parse_qs(urlparse(started["authorization_url"]).query)["state"][0]
        credentials = {
            "bot_token": "bot-token-test-value",
            "team_id": "T-NEW",
            "team_name": "FutureAGI",
            "bot_user_id": "U-BOT",
            "scope": "channels:read,channels:join,groups:read,chat:write",
        }
        with (
            patch(
                "integrations.views.slack.exchange_slack_code",
                return_value=credentials,
            ) as exchange,
            patch(
                "integrations.views.slack.SlackService.validate_credentials",
                return_value={"valid": True},
            ),
        ):
            response = api_client.get(
                "/integrations/slack/callback/",
                {"state": state, "code": "temporary-code"},
            )

        assert response.status_code == status.HTTP_302_FOUND
        assert "slack=error" in response["Location"]
        exchange.assert_not_called()
        assert not IntegrationConnection.no_workspace_objects.filter(
            organization=organization, platform=IntegrationPlatform.SLACK
        ).exists()
