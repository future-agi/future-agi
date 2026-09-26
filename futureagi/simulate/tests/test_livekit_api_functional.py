"""Functional tests for LiveKit listener-token and validate-credentials endpoints (SDK patched)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from django.test import override_settings
from model_hub.models.choices import StatusType

from simulate.models import AgentDefinition, RunTest, Scenarios
from simulate.models.test_execution import (
    CallExecution,
    TestExecution as SimulationTestExecution,
)
from tracer.models.observability_provider import ProviderChoices


@pytest.fixture
def agent_definition(db, organization, workspace):
    return AgentDefinition.objects.create(
        agent_name="LiveKit Listener Test Agent",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        contact_number="+1231112222",
        inbound=True,
        description="Agent for LiveKit listener functional tests",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )


@pytest.fixture
def scenario(db, organization, workspace, agent_definition):
    return Scenarios.objects.create(
        name="LiveKit Listener Test Scenario",
        description="Scenario for LiveKit listener functional tests",
        source="test",
        scenario_type=Scenarios.ScenarioTypes.DATASET,
        organization=organization,
        workspace=workspace,
        agent_definition=agent_definition,
        status=StatusType.COMPLETED.value,
    )


@pytest.fixture
def run_test(db, organization, workspace, agent_definition, scenario):
    rt = RunTest.objects.create(
        name="LiveKit Listener Run Test",
        description="Run for LiveKit listener functional tests",
        agent_definition=agent_definition,
        organization=organization,
        workspace=workspace,
    )
    rt.scenarios.add(scenario)
    return rt


@pytest.fixture
def test_execution(db, run_test, agent_definition):
    return SimulationTestExecution.objects.create(
        run_test=run_test,
        status=SimulationTestExecution.ExecutionStatus.RUNNING,
        total_scenarios=1,
        total_calls=1,
        completed_calls=0,
        failed_calls=0,
        agent_definition=agent_definition,
    )


@pytest.fixture
def ongoing_call(db, test_execution, scenario):
    return CallExecution.objects.create(
        test_execution=test_execution,
        scenario=scenario,
        phone_number="+15550009999",
        status=CallExecution.CallStatus.ONGOING,
        provider_call_data={
            ProviderChoices.LIVEKIT: {"room_name": "call_seeded_room_xyz"},
        },
    )


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestListenerTokenHappyPath:
    URL_TEMPLATE = "/simulate/api/livekit/listener-token/{}/"

    @override_settings(
        LIVEKIT_API_KEY="test-livekit-key",
        LIVEKIT_API_SECRET="test-livekit-secret",
        LIVEKIT_URL="wss://test-livekit.example.com",
    )
    def test_listener_token_happy_path_returns_signed_token(
        self, auth_client, user, ongoing_call
    ):
        expected_jwt = "signed-listener-jwt-value"

        fake_token_instance = MagicMock()
        fake_token_instance.with_identity.return_value = fake_token_instance
        fake_token_instance.with_name.return_value = fake_token_instance
        fake_token_instance.with_grants.return_value = fake_token_instance
        fake_token_instance.to_jwt.return_value = expected_jwt

        access_token_cls = MagicMock(return_value=fake_token_instance)
        video_grants_cls = MagicMock()

        with patch("livekit.api.AccessToken", access_token_cls), patch(
            "livekit.api.VideoGrants", video_grants_cls
        ):
            response = auth_client.get(self.URL_TEMPLATE.format(ongoing_call.id))

        assert response.status_code == 200
        body = response.json()
        assert body["status"] is True
        result = body["result"]
        assert result["token"] == expected_jwt
        assert result["room_name"] == "call_seeded_room_xyz"
        assert result["url"] == "wss://test-livekit.example.com"

        # SDK constructed with configured server credentials.
        access_token_cls.assert_called_once_with(
            "test-livekit-key", "test-livekit-secret"
        )
        # Grants bound to the seeded room, listener-only permissions.
        video_grants_cls.assert_called_once()
        grant_kwargs = video_grants_cls.call_args.kwargs
        assert grant_kwargs["room"] == "call_seeded_room_xyz"
        assert grant_kwargs["room_join"] is True
        assert grant_kwargs["can_subscribe"] is True
        assert grant_kwargs["can_publish"] is False
        assert grant_kwargs["can_publish_data"] is False
        # Identity encodes the requesting user id so LiveKit can distinguish
        # concurrent listeners.
        identity_arg = fake_token_instance.with_identity.call_args.args[0]
        assert str(user.id) in identity_arg

    @override_settings(
        LIVEKIT_API_KEY="test-livekit-key",
        LIVEKIT_API_SECRET="test-livekit-secret",
        LIVEKIT_URL="wss://test-livekit.example.com",
    )
    def test_listener_token_missing_call_returns_404(self, auth_client):
        """Unknown call_id returns 404 with the platform error envelope."""
        unknown_call_id = "00000000-0000-4000-8000-0000deadbeef"

        response = auth_client.get(self.URL_TEMPLATE.format(unknown_call_id))

        assert response.status_code == 404
        body = response.json()
        assert body["status"] is False
        assert "not found" in body["message"].lower()


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestValidateCredentials:
    URL = "/simulate/api/livekit/validate-credentials/"

    def _payload(self, **overrides):
        base = {
            "livekit_url": "wss://customer.example.livekit.cloud",
            "api_key": "APIcustomer-key",
            "api_secret": "customer-plaintext-secret-32chars",
        }
        base.update(overrides)
        return base

    def test_validate_credentials_valid_returns_ok(self, auth_client):
        create_room = AsyncMock()
        delete_room = AsyncMock()
        aclose = AsyncMock()

        fake_lk_api = MagicMock()
        fake_lk_api.room.create_room = create_room
        fake_lk_api.room.delete_room = delete_room
        fake_lk_api.aclose = aclose

        livekit_api_cls = MagicMock(return_value=fake_lk_api)

        with patch("livekit.api.LiveKitAPI", livekit_api_cls):
            response = auth_client.post(
                self.URL, self._payload(), format="json"
            )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] is True
        assert body["result"] == {"valid": True}

        # SDK constructed with the wss->https rewrite and the customer creds
        # verbatim.
        init_kwargs = livekit_api_cls.call_args.kwargs
        assert init_kwargs["url"].startswith("https://")
        assert init_kwargs["api_key"] == "APIcustomer-key"
        assert init_kwargs["api_secret"] == "customer-plaintext-secret-32chars"

        create_room.assert_awaited_once()
        create_req = create_room.await_args.args[0]
        probe_room_name = create_req.name
        assert probe_room_name.startswith("_validate_creds_")

        delete_room.assert_awaited_once()
        delete_req = delete_room.await_args.args[0]
        # The same temp room the probe created must be torn down so no live
        # session is left behind on the customer's LiveKit server.
        assert delete_req.room == probe_room_name

        aclose.assert_awaited_once()

    def test_validate_credentials_invalid_returns_error(self, auth_client):
        create_room = AsyncMock(side_effect=RuntimeError("401 unauthorized"))
        delete_room = AsyncMock()
        aclose = AsyncMock()

        fake_lk_api = MagicMock()
        fake_lk_api.room.create_room = create_room
        fake_lk_api.room.delete_room = delete_room
        fake_lk_api.aclose = aclose

        livekit_api_cls = MagicMock(return_value=fake_lk_api)

        with patch("livekit.api.LiveKitAPI", livekit_api_cls):
            response = auth_client.post(
                self.URL, self._payload(), format="json"
            )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] is True
        result = body["result"]
        assert result["valid"] is False
        assert "401 unauthorized" in result["error"]

        # Even on failure the view still calls aclose so the SDK does not
        # leak a client session.
        aclose.assert_awaited_once()
