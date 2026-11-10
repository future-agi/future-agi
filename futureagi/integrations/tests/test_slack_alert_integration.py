"""Slack OAuth, channel discovery, and message client coverage for alerts."""

from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

import pytest
from rest_framework import status

from integrations.models import (
    ConnectionStatus,
    IntegrationConnection,
    IntegrationPlatform,
)
from integrations.services import slack_oauth
from integrations.services.slack_service import SlackApiError, SlackService, slack_api


@pytest.fixture
def slack_connection(organization, workspace, user, encrypted_credentials):
    return IntegrationConnection.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        created_by=user,
        platform=IntegrationPlatform.SLACK,
        display_name="FutureAGI engineering",
        host_url="https://slack.com",
        encrypted_credentials=encrypted_credentials,
        external_project_name="T123",
        status=ConnectionStatus.ACTIVE,
        backfill_completed=True,
    )


class TestSlackOAuthState:
    @pytest.mark.django_db
    def test_install_state_contains_a_signed_single_use_workspace_binding(
        self, settings, organization, workspace, user
    ):
        settings.SLACK_CLIENT_ID = "client-id"
        settings.SLACK_CLIENT_SECRET = "client-secret"
        settings.BASE_URL = "https://app.example.com"
        started = slack_oauth.start_slack_install(
            organization=organization, workspace=workspace, user=user
        )

        parsed = parse_qs(urlparse(started["authorization_url"]).query)
        assert parsed["client_id"] == ["client-id"]
        assert set(parsed["scope"][0].split(",")) == {
            "channels:read",
            "channels:join",
            "groups:read",
            "chat:write",
        }
        state = parsed["state"][0]
        consumed = slack_oauth.consume_slack_state(state)
        assert consumed["organization_id"] == str(organization.id)
        assert consumed["workspace_id"] == str(workspace.id)
        assert consumed["user_id"] == str(user.id)
        with pytest.raises(ValueError, match="expired or was already used"):
            slack_oauth.consume_slack_state(state)

    def test_exchange_rejects_rotating_or_incomplete_bot_installations(self, settings):
        settings.SLACK_CLIENT_ID = "client-id"
        settings.SLACK_CLIENT_SECRET = "client-secret"
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "ok": True,
            "access_token": "xoxb-token",
            "refresh_token": "must-not-store",
            "team": {"id": "T1", "name": "Team"},
            "scope": "channels:read,channels:join,groups:read,chat:write",
        }
        with patch(
            "integrations.services.slack_oauth.requests.post", return_value=response
        ):
            with pytest.raises(ValueError, match="rotation is not supported"):
                slack_oauth.exchange_slack_code("code")

    def test_exchange_uses_configured_api_endpoint(self, settings):
        settings.SLACK_CLIENT_ID = "client-id"
        settings.SLACK_CLIENT_SECRET = "client-secret"
        settings.SLACK_API_URL = "http://mock-slack:8080/api/"
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "ok": True,
            "access_token": "xoxb-token",
            "team": {"id": "T1", "name": "Team"},
            "scope": "channels:read,channels:join,groups:read,chat:write",
        }
        with patch("integrations.services.slack_oauth.requests.post", return_value=response) as post:
            slack_oauth.exchange_slack_code("code")
        assert post.call_args.args[0] == "http://mock-slack:8080/api/oauth.v2.access"


@pytest.mark.api
@pytest.mark.django_db
class TestSlackIntegrationAPI:
    def test_install_endpoint_returns_authorization_url_without_leaking_state_payload(
        self, settings, auth_client, organization, workspace, user
    ):
        settings.SLACK_CLIENT_ID = "client-id"
        settings.SLACK_CLIENT_SECRET = "client-secret"
        response = auth_client.post("/integrations/slack/install/", {}, format="json")
        assert response.status_code == status.HTTP_200_OK
        authorization_url = response.json()["result"]["authorization_url"]
        assert authorization_url.startswith(slack_oauth.AUTHORIZE_URL)
        assert str(workspace.id) not in authorization_url
        assert str(user.id) not in authorization_url

    def test_callback_exchanges_a_single_use_state_and_creates_action_connection(
        self, settings, api_client, organization, workspace, user
    ):
        settings.SLACK_CLIENT_ID = "client-id"
        settings.SLACK_CLIENT_SECRET = "client-secret"
        settings.APP_BASE_URL = "https://app.example.com"
        started = slack_oauth.start_slack_install(
            organization=organization, workspace=workspace, user=user
        )
        state_value = parse_qs(urlparse(started["authorization_url"]).query)["state"][0]
        credentials = {
            "bot_token": "xoxb-installed-token",
            "team_id": "T-NEW",
            "team_name": "FutureAGI",
            "bot_user_id": "U-BOT",
            "scope": "channels:read,channels:join,groups:read,chat:write",
        }
        with (
            patch(
                "integrations.views.slack.exchange_slack_code", return_value=credentials
            ),
            patch(
                "integrations.views.slack.SlackService.validate_credentials",
                return_value={"valid": True},
            ),
        ):
            response = api_client.get(
                "/integrations/slack/callback/",
                {"state": state_value, "code": "temporary-code"},
            )

        assert response.status_code == status.HTTP_302_FOUND
        assert "slack=connected" in response["Location"]
        connection = IntegrationConnection.no_workspace_objects.get(
            organization=organization,
            workspace=workspace,
            platform=IntegrationPlatform.SLACK,
        )
        assert connection.display_name == "FutureAGI"
        assert connection.status == ConnectionStatus.ACTIVE

        replay = api_client.get(
            "/integrations/slack/callback/",
            {"state": state_value, "code": "temporary-code"},
        )
        assert replay.status_code == status.HTTP_302_FOUND
        assert "slack=error" in replay["Location"]

    def test_channels_are_scoped_to_the_current_workspace_and_decrypt_server_side(
        self, monkeypatch, auth_client, slack_connection
    ):
        monkeypatch.setattr(
            "integrations.views.slack.CredentialManager.decrypt",
            lambda value: {"bot_token": "xoxb-test"},
        )
        with patch(
            "integrations.views.slack.SlackService.get_channels",
            return_value={
                "channels": [
                    {"id": "C1", "name": "production-alerts", "is_private": False}
                ],
                "next_cursor": None,
            },
        ) as channels:
            response = auth_client.get(
                f"/integrations/connections/{slack_connection.id}/slack/channels/"
            )
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["result"]["channels"][0]["name"] == "production-alerts"
        channels.assert_called_once_with({"bot_token": "xoxb-test"}, cursor="")

    def test_channel_list_rejects_unknown_connection(self, auth_client):
        import uuid

        response = auth_client.get(
            f"/integrations/connections/{uuid.uuid4()}/slack/channels/"
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestSlackWebClient:
    def test_web_api_uses_configured_endpoint_for_local_slack_test_server(self, settings):
        settings.SLACK_API_URL = "http://mock-slack:8080/api/"
        response = MagicMock(status_code=200)
        response.raise_for_status.return_value = None
        response.json.return_value = {"ok": True, "team_id": "T1"}
        with patch("integrations.services.slack_service.requests.get", return_value=response) as get:
            assert slack_api("auth.test", "xoxb-test")["team_id"] == "T1"
        assert get.call_args.args[0] == "http://mock-slack:8080/api/auth.test"

    def test_rate_limit_preserves_retry_after(self):
        response = MagicMock(status_code=429, headers={"Retry-After": "90"})
        with patch(
            "integrations.services.slack_service.requests.get", return_value=response
        ):
            with pytest.raises(SlackApiError, match="ratelimited") as error:
                slack_api("conversations.list", "xoxb-test")
        assert error.value.retry_after == 90

    def test_get_channels_includes_public_channels_and_joined_private_channels(self):
        client = SlackService()
        with patch(
            "integrations.services.slack_service.slack_api",
            return_value={
                "ok": True,
                "channels": [
                    {
                        "id": "C1",
                        "name": "alerts",
                        "is_member": True,
                        "is_private": False,
                    },
                    {
                        "id": "C2",
                        "name": "private",
                        "is_member": False,
                        "is_private": True,
                    },
                    {
                        "id": "C3",
                        "name": "public-not-joined",
                        "is_member": False,
                        "is_private": False,
                    },
                    {
                        "id": "G4",
                        "name": "joined-private",
                        "is_member": True,
                        "is_private": True,
                    },
                ],
                "response_metadata": {"next_cursor": "next"},
            },
        ) as api:
            result = client.get_channels({"bot_token": "xoxb-test"})
        assert result == {
            "channels": [
                {"id": "C1", "name": "alerts", "is_private": False, "is_member": True},
                {"id": "C3", "name": "public-not-joined", "is_private": False, "is_member": False},
                {"id": "G4", "name": "joined-private", "is_private": True, "is_member": True},
            ],
            "next_cursor": "next",
        }
        assert api.call_args.args[0] == "conversations.list"

    def test_validate_channel_joins_selected_public_channel(self):
        client = SlackService()

        def respond(method, token, **kwargs):
            if method == "conversations.info":
                return {"channel": {"id": "C123", "name": "alerts", "is_private": False, "is_member": False}}
            assert method == "conversations.join"
            assert kwargs["payload"] == {"channel": "C123"}
            return {"channel": {"id": "C123", "name": "alerts", "is_private": False, "is_member": True}}

        with patch("integrations.services.slack_service.slack_api", side_effect=respond) as api:
            result = client.validate_channel({"bot_token": "xoxb-test"}, "C123", join_public=True)
        assert result == {"id": "C123", "name": "alerts", "is_private": False}
        assert [call.args[0] for call in api.call_args_list] == ["conversations.info", "conversations.join"]

    def test_validate_channel_never_joins_private_channel(self):
        client = SlackService()
        with patch(
            "integrations.services.slack_service.slack_api",
            return_value={"channel": {"id": "G123", "name": "private", "is_private": True, "is_member": False}},
        ) as api:
            with pytest.raises(SlackApiError, match="not_in_channel"):
                client.validate_channel({"bot_token": "xoxb-test"}, "G123", join_public=True)
        api.assert_called_once()

    def test_post_message_does_not_send_to_a_channel_without_bot_membership(self):
        client = SlackService()
        with (
            patch.object(
                client, "validate_channel", side_effect=SlackApiError("not_in_channel")
            ),
            patch("integrations.services.slack_service.slack_api") as api,
        ):
            with pytest.raises(SlackApiError, match="not_in_channel"):
                client.post_message({"bot_token": "xoxb-test"}, "C0123456789", "Alert")
        api.assert_not_called()
