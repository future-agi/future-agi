"""
Tests for ObservabilityService in tracer/services/observability_providers.py.

Fixes CORE-BACKEND-WCN (VAPI 401) and CORE-BACKEND-WTW (Retell 401).

Run with: pytest tracer/tests/test_observability_providers.py -v
"""

from unittest.mock import Mock, patch

import pytest
from requests.exceptions import HTTPError

from tracer.models.observability_provider import ProviderChoices


class TestValidateAgentApiKey:
    """Tests for _validate_agent_api_key helper method."""

    def test_returns_api_key_when_valid(self):
        """Returns the API key when agent and api_key exist."""
        from tracer.services.observability_providers import ObservabilityService

        mock_agent = Mock()
        mock_agent.api_key = "valid-api-key-123"
        mock_provider = Mock()
        mock_provider.id = "provider-123"

        result = ObservabilityService._validate_agent_api_key(
            mock_agent, mock_provider, "TestProvider"
        )

        assert result == "valid-api-key-123"

    def test_returns_none_when_agent_is_none(self):
        """Returns None when agent is None (logs warning instead of raising)."""
        from tracer.services.observability_providers import ObservabilityService

        mock_provider = Mock()
        mock_provider.id = "provider-123"

        result = ObservabilityService._validate_agent_api_key(
            None, mock_provider, "TestProvider"
        )

        assert result is None

    def test_returns_none_when_api_key_is_none(self):
        """Returns None when api_key is None (logs warning instead of raising)."""
        from tracer.services.observability_providers import ObservabilityService

        mock_agent = Mock()
        mock_agent.api_key = None
        mock_provider = Mock()
        mock_provider.id = "provider-456"

        result = ObservabilityService._validate_agent_api_key(
            mock_agent, mock_provider, "VAPI"
        )

        assert result is None

    def test_returns_none_when_api_key_is_empty_string(self):
        """Returns None when api_key is empty string (logs warning instead of raising)."""
        from tracer.services.observability_providers import ObservabilityService

        mock_agent = Mock()
        mock_agent.api_key = ""
        mock_provider = Mock()
        mock_provider.id = "provider-789"

        result = ObservabilityService._validate_agent_api_key(
            mock_agent, mock_provider, "Retell"
        )

        assert result is None


class TestVerifyApiKey:
    """Tests for provider API key verification requests."""

    @patch("tracer.services.observability_providers.requests.get")
    def test_vapi_verification_request_uses_timeout(self, mock_requests_get):
        from tracer.constants.external_endpoints import ObservabilityRoutes
        from tracer.services.observability_providers import (
            OBSERVABILITY_VERIFY_TIMEOUT_SECONDS,
            ObservabilityService,
        )

        mock_response = Mock()
        mock_response.status_code = 204
        mock_requests_get.return_value = mock_response

        result = ObservabilityService.verify_api_key(
            ProviderChoices.VAPI,
            "vapi-api-key",
        )

        assert result == 204
        mock_requests_get.assert_called_once_with(
            f"{ObservabilityRoutes.VAPI_CALL_URL.value}?limit=0",
            headers={"Authorization": "Bearer vapi-api-key"},
            timeout=OBSERVABILITY_VERIFY_TIMEOUT_SECONDS,
        )

    @patch("tracer.services.observability_providers.requests.get")
    def test_retell_verification_request_uses_timeout(self, mock_requests_get):
        from tracer.constants.external_endpoints import ObservabilityRoutes
        from tracer.services.observability_providers import (
            OBSERVABILITY_VERIFY_TIMEOUT_SECONDS,
            ObservabilityService,
        )

        mock_response = Mock()
        mock_response.status_code = 200
        mock_requests_get.return_value = mock_response

        result = ObservabilityService.verify_api_key(
            ProviderChoices.RETELL,
            "retell-api-key",
        )

        assert result == 200
        mock_requests_get.assert_called_once_with(
            f"{ObservabilityRoutes.RETELL_LIST_ASSISTANTS_URL.value}?limit=1",
            headers={"Authorization": "Bearer retell-api-key"},
            timeout=OBSERVABILITY_VERIFY_TIMEOUT_SECONDS,
        )

    @patch("tracer.services.observability_providers.requests.get")
    def test_bland_verification_hits_me_endpoint_with_raw_auth(self, mock_requests_get):
        # Bland takes the raw key in `authorization` (NO "Bearer " prefix) and
        # validates against its read-only /v1/me endpoint.
        from tracer.constants.external_endpoints import ObservabilityRoutes
        from tracer.services.observability_providers import (
            OBSERVABILITY_VERIFY_TIMEOUT_SECONDS,
            ObservabilityService,
        )

        mock_response = Mock()
        mock_response.status_code = 200
        mock_requests_get.return_value = mock_response

        result = ObservabilityService.verify_api_key(
            ProviderChoices.BLAND,
            "org_bland_key",
        )

        assert result == 200
        mock_requests_get.assert_called_once_with(
            ObservabilityRoutes.BLAND_ME_URL.value,
            headers={"authorization": "org_bland_key"},
            timeout=OBSERVABILITY_VERIFY_TIMEOUT_SECONDS,
        )

    @patch("tracer.services.observability_providers.requests.get")
    def test_bland_assistant_verification_hits_pathway_with_raw_auth(
        self, mock_requests_get
    ):
        # Bland's "assistant" is a pathway; verify GETs /v1/pathway/{id} with the
        # raw authorization header.
        from tracer.constants.external_endpoints import ObservabilityRoutes
        from tracer.services.observability_providers import (
            OBSERVABILITY_VERIFY_TIMEOUT_SECONDS,
            ObservabilityService,
        )

        mock_response = Mock()
        mock_response.status_code = 200
        mock_requests_get.return_value = mock_response

        result = ObservabilityService.verify_assistant_id(
            ProviderChoices.BLAND,
            "2fdd4db9-5e81-4422-b11c-168f0182d4fc",
            "org_bland_key",
        )

        assert result == 200
        mock_requests_get.assert_called_once_with(
            f"{ObservabilityRoutes.BLAND_PATHWAY_URL.value}/2fdd4db9-5e81-4422-b11c-168f0182d4fc",
            headers={"authorization": "org_bland_key"},
            timeout=OBSERVABILITY_VERIFY_TIMEOUT_SECONDS,
        )


class TestFetchVapiLogs:
    """Tests for _fetch_vapi_logs method."""

    @patch("tracer.services.observability_providers.requests.get")
    @patch.object(
        __import__(
            "tracer.services.observability_providers", fromlist=["ObservabilityService"]
        ).ObservabilityService,
        "_get_agent_definition",
    )
    def test_returns_empty_list_when_no_api_key(
        self, mock_get_agent, mock_requests_get
    ):
        """Returns empty list when agent has no API key (graceful handling)."""
        from tracer.services.observability_providers import ObservabilityService

        mock_get_agent.return_value = None
        mock_provider = Mock()
        mock_provider.id = "vapi-provider-123"

        result = ObservabilityService._fetch_vapi_logs(mock_provider)

        assert result == []
        # Should not make HTTP request when validation fails
        mock_requests_get.assert_not_called()

    @patch("tracer.services.observability_providers.requests.get")
    @patch.object(
        __import__(
            "tracer.services.observability_providers", fromlist=["ObservabilityService"]
        ).ObservabilityService,
        "_get_agent_definition",
    )
    def test_makes_request_with_valid_api_key(self, mock_get_agent, mock_requests_get):
        """Makes HTTP request when API key is valid."""
        from tracer.services.observability_providers import ObservabilityService

        mock_agent = Mock()
        mock_agent.api_key = "valid-vapi-key"
        mock_agent.assistant_id = "assistant-123"
        mock_get_agent.return_value = mock_agent

        mock_response = Mock()
        mock_response.json.return_value = []
        mock_response.raise_for_status = Mock()
        mock_requests_get.return_value = mock_response

        mock_provider = Mock()
        mock_provider.id = "vapi-provider-123"

        result = ObservabilityService._fetch_vapi_logs(mock_provider)

        mock_requests_get.assert_called_once()
        call_kwargs = mock_requests_get.call_args
        assert "Bearer valid-vapi-key" in str(call_kwargs)

    @patch("tracer.services.observability_providers.requests.get")
    @patch.object(
        __import__(
            "tracer.services.observability_providers", fromlist=["ObservabilityService"]
        ).ObservabilityService,
        "_get_agent_definition",
    )
    def test_paginates_when_batch_is_full(self, mock_get_agent, mock_requests_get):
        """Fetches multiple pages when a batch returns exactly VAPI_PAGE_LIMIT results."""
        from tracer.services.observability_providers import (
            VAPI_PAGE_LIMIT,
            ObservabilityService,
        )

        mock_agent = Mock()
        mock_agent.api_key = "valid-vapi-key"
        mock_agent.assistant_id = "assistant-123"
        mock_get_agent.return_value = mock_agent

        page1 = [
            {
                "id": f"call-{i}",
                "updatedAt": f"2025-01-01T{i // 60:02d}:{i % 60:02d}:00Z",
            }
            for i in range(VAPI_PAGE_LIMIT)
        ]
        page2 = [
            {
                "id": f"call-{VAPI_PAGE_LIMIT + i}",
                "updatedAt": f"2025-01-01T05:{i:02d}:00Z",
            }
            for i in range(30)
        ]

        mock_resp1 = Mock()
        mock_resp1.json.return_value = page1
        mock_resp1.raise_for_status = Mock()

        mock_resp2 = Mock()
        mock_resp2.json.return_value = page2
        mock_resp2.raise_for_status = Mock()

        mock_requests_get.side_effect = [mock_resp1, mock_resp2]

        mock_provider = Mock()
        mock_provider.id = "vapi-provider-123"

        result = ObservabilityService._fetch_vapi_logs(mock_provider)

        assert mock_requests_get.call_count == 2
        assert len(result) == VAPI_PAGE_LIMIT + 30

    @patch("tracer.services.observability_providers.requests.get")
    @patch.object(
        __import__(
            "tracer.services.observability_providers", fromlist=["ObservabilityService"]
        ).ObservabilityService,
        "_get_agent_definition",
    )
    def test_stops_at_max_pages(self, mock_get_agent, mock_requests_get):
        """Stops fetching after VAPI_MAX_PAGES even if batches are full."""
        from tracer.services.observability_providers import (
            VAPI_MAX_PAGES,
            VAPI_PAGE_LIMIT,
            ObservabilityService,
        )

        mock_agent = Mock()
        mock_agent.api_key = "valid-vapi-key"
        mock_agent.assistant_id = "assistant-123"
        mock_get_agent.return_value = mock_agent

        def make_response(page_num):
            resp = Mock()
            resp.json.return_value = [
                {
                    "id": f"call-{page_num}-{i}",
                    "updatedAt": f"2025-01-{page_num + 1:02d}T{i // 60:02d}:{i % 60:02d}:00Z",
                }
                for i in range(VAPI_PAGE_LIMIT)
            ]
            resp.raise_for_status = Mock()
            return resp

        mock_requests_get.side_effect = [
            make_response(p) for p in range(VAPI_MAX_PAGES + 5)
        ]

        mock_provider = Mock()
        mock_provider.id = "vapi-provider-123"

        result = ObservabilityService._fetch_vapi_logs(mock_provider)

        assert mock_requests_get.call_count == VAPI_MAX_PAGES
        assert len(result) == VAPI_MAX_PAGES * VAPI_PAGE_LIMIT

    @patch("tracer.services.observability_providers.requests.get")
    @patch.object(
        __import__(
            "tracer.services.observability_providers", fromlist=["ObservabilityService"]
        ).ObservabilityService,
        "_get_agent_definition",
    )
    def test_single_page_when_under_limit(self, mock_get_agent, mock_requests_get):
        """Makes only one request when results are under VAPI_PAGE_LIMIT."""
        from tracer.services.observability_providers import ObservabilityService

        mock_agent = Mock()
        mock_agent.api_key = "valid-vapi-key"
        mock_agent.assistant_id = "assistant-123"
        mock_get_agent.return_value = mock_agent

        mock_response = Mock()
        mock_response.json.return_value = [
            {"id": f"call-{i}", "updatedAt": f"2025-01-01T00:{i:02d}:00Z"}
            for i in range(50)
        ]
        mock_response.raise_for_status = Mock()
        mock_requests_get.return_value = mock_response

        mock_provider = Mock()
        mock_provider.id = "vapi-provider-123"

        result = ObservabilityService._fetch_vapi_logs(mock_provider)

        mock_requests_get.assert_called_once()
        assert len(result) == 50


class TestFetchRetellLogs:
    """Tests for _fetch_retell_logs method."""

    @patch("tracer.services.observability_providers.requests.post")
    @patch.object(
        __import__(
            "tracer.services.observability_providers", fromlist=["ObservabilityService"]
        ).ObservabilityService,
        "_get_agent_definition",
    )
    def test_returns_empty_list_when_no_api_key(
        self, mock_get_agent, mock_requests_post
    ):
        """Returns empty list when agent has no API key (graceful handling)."""
        from tracer.services.observability_providers import ObservabilityService

        mock_get_agent.return_value = None
        mock_provider = Mock()
        mock_provider.id = "retell-provider-123"

        result = ObservabilityService._fetch_retell_logs(mock_provider)

        assert result == []
        # Should not make HTTP request when validation fails
        mock_requests_post.assert_not_called()

    @patch("tracer.services.observability_providers.requests.post")
    @patch.object(
        __import__(
            "tracer.services.observability_providers", fromlist=["ObservabilityService"]
        ).ObservabilityService,
        "_get_agent_definition",
    )
    def test_makes_request_with_valid_api_key(self, mock_get_agent, mock_requests_post):
        """Makes HTTP request when API key is valid."""
        from tracer.services.observability_providers import ObservabilityService

        mock_agent = Mock()
        mock_agent.api_key = "valid-retell-key"
        mock_agent.assistant_id = "agent-123"
        mock_get_agent.return_value = mock_agent

        mock_response = Mock()
        mock_response.json.return_value = []
        mock_response.raise_for_status = Mock()
        mock_requests_post.return_value = mock_response

        mock_provider = Mock()
        mock_provider.id = "retell-provider-123"

        result = ObservabilityService._fetch_retell_logs(mock_provider)

        mock_requests_post.assert_called_once()
        call_kwargs = mock_requests_post.call_args
        assert "Bearer valid-retell-key" in str(call_kwargs)


class TestFetchElevenLabsLogs:
    """Tests for ElevenLabs fetch methods."""

    @patch("tracer.services.observability_providers.requests.get")
    @patch.object(
        __import__(
            "tracer.services.observability_providers", fromlist=["ObservabilityService"]
        ).ObservabilityService,
        "_get_agent_definition",
    )
    def test_list_conversations_returns_empty_when_no_api_key(
        self, mock_get_agent, mock_requests_get
    ):
        """Returns empty list when agent has no API key (graceful handling)."""
        from tracer.services.observability_providers import ObservabilityService

        mock_get_agent.return_value = None
        mock_provider = Mock()
        mock_provider.id = "eleven-labs-provider-123"

        result = ObservabilityService._list_eleven_labs_conversations(mock_provider)

        assert result == []
        # Should not make HTTP request when validation fails
        mock_requests_get.assert_not_called()

    @patch("tracer.services.observability_providers.requests.get")
    @patch.object(
        __import__(
            "tracer.services.observability_providers", fromlist=["ObservabilityService"]
        ).ObservabilityService,
        "_get_agent_definition",
    )
    def test_fetch_details_returns_none_when_no_api_key(
        self, mock_get_agent, mock_requests_get
    ):
        """Returns None when agent has no API key for conversation details (graceful handling)."""
        from tracer.services.observability_providers import ObservabilityService

        mock_get_agent.return_value = None
        mock_provider = Mock()
        mock_provider.id = "eleven-labs-provider-456"

        result = ObservabilityService._fetch_eleven_labs_conversation_details(
            mock_provider, "conv-123"
        )

        assert result is None
        # Should not make HTTP request when validation fails
        mock_requests_get.assert_not_called()

    @patch("tracer.services.observability_providers.requests.get")
    @patch.object(
        __import__(
            "tracer.services.observability_providers", fromlist=["ObservabilityService"]
        ).ObservabilityService,
        "_get_agent_definition",
    )
    def test_list_conversations_with_valid_api_key(
        self, mock_get_agent, mock_requests_get
    ):
        """Makes HTTP request when API key is valid."""
        from tracer.services.observability_providers import ObservabilityService

        mock_agent = Mock()
        mock_agent.api_key = "valid-eleven-labs-key"
        mock_agent.assistant_id = "agent-123"
        mock_get_agent.return_value = mock_agent

        mock_response = Mock()
        mock_response.json.return_value = {"conversations": []}
        mock_response.raise_for_status = Mock()
        mock_requests_get.return_value = mock_response

        mock_provider = Mock()
        mock_provider.id = "eleven-labs-provider-123"

        result = ObservabilityService._list_eleven_labs_conversations(mock_provider)

        mock_requests_get.assert_called_once()
        call_kwargs = mock_requests_get.call_args
        # ElevenLabs uses xi-api-key header
        assert "valid-eleven-labs-key" in str(call_kwargs)


# ============================================================================
# Integration Tests with Django Models
# ============================================================================


@pytest.fixture
def test_project(organization, workspace, db):
    """Create a test project for observability provider."""
    from tracer.models.project import Project

    project = Project.objects.create(
        name="Test Voice Project",
        organization=organization,
        workspace=workspace,
        model_type="Numeric",  # Required field
        trace_type="observe",  # Required field
    )
    return project


@pytest.fixture
def vapi_provider_without_agent(test_project, organization, workspace, db):
    """Create VAPI provider WITHOUT an associated AgentDefinition."""
    from tracer.models.observability_provider import ObservabilityProvider

    provider = ObservabilityProvider.objects.create(
        project=test_project,
        provider=ProviderChoices.VAPI,
        enabled=True,
        organization=organization,
        workspace=workspace,
    )
    return provider


@pytest.fixture
def vapi_provider_with_agent(test_project, organization, workspace, db):
    """Create VAPI provider WITH an associated AgentDefinition that has an API key."""
    from simulate.models.agent_definition import AgentDefinition
    from tracer.models.observability_provider import ObservabilityProvider

    provider = ObservabilityProvider.objects.create(
        project=test_project,
        provider=ProviderChoices.VAPI,
        enabled=True,
        organization=organization,
        workspace=workspace,
    )

    AgentDefinition.objects.create(
        agent_name="Test VAPI Agent",
        agent_type="voice",
        inbound=True,
        description="Test agent for VAPI",
        api_key="test-vapi-api-key-12345",
        assistant_id="asst_vapi_123",
        provider="vapi",
        organization=organization,
        workspace=workspace,
        observability_provider=provider,
    )

    return provider


@pytest.fixture
def retell_provider_without_agent(test_project, organization, workspace, db):
    """Create Retell provider WITHOUT an associated AgentDefinition."""
    from tracer.models.observability_provider import ObservabilityProvider

    provider = ObservabilityProvider.objects.create(
        project=test_project,
        provider=ProviderChoices.RETELL,
        enabled=True,
        organization=organization,
        workspace=workspace,
    )
    return provider


@pytest.fixture
def retell_provider_with_agent(test_project, organization, workspace, db):
    """Create Retell provider WITH an associated AgentDefinition that has an API key."""
    from simulate.models.agent_definition import AgentDefinition
    from tracer.models.observability_provider import ObservabilityProvider

    provider = ObservabilityProvider.objects.create(
        project=test_project,
        provider=ProviderChoices.RETELL,
        enabled=True,
        organization=organization,
        workspace=workspace,
    )

    AgentDefinition.objects.create(
        agent_name="Test Retell Agent",
        agent_type="voice",
        inbound=True,
        description="Test agent for Retell",
        api_key="test-retell-api-key-67890",
        assistant_id="agent_retell_456",
        provider="retell",
        organization=organization,
        workspace=workspace,
        observability_provider=provider,
    )

    return provider


@pytest.fixture
def vapi_provider_with_agent_no_api_key(test_project, organization, workspace, db):
    """Create VAPI provider WITH AgentDefinition but WITHOUT API key."""
    from simulate.models.agent_definition import AgentDefinition
    from tracer.models.observability_provider import ObservabilityProvider

    provider = ObservabilityProvider.objects.create(
        project=test_project,
        provider=ProviderChoices.VAPI,
        enabled=True,
        organization=organization,
        workspace=workspace,
    )

    AgentDefinition.objects.create(
        agent_name="Agent Without API Key",
        agent_type="voice",
        inbound=True,
        description="Test agent without API key",
        api_key=None,  # No API key!
        assistant_id="asst_no_key",
        provider="vapi",
        organization=organization,
        workspace=workspace,
        observability_provider=provider,
    )

    return provider


@pytest.mark.integration
@pytest.mark.django_db
class TestObservabilityServiceIntegration:
    """Integration tests using actual Django models."""

    def test_get_agent_definition_returns_agent(self, vapi_provider_with_agent):
        """Verify _get_agent_definition returns the linked agent."""
        from tracer.services.observability_providers import ObservabilityService

        agent = ObservabilityService._get_agent_definition(vapi_provider_with_agent)

        assert agent is not None
        assert agent.api_key == "test-vapi-api-key-12345"
        assert agent.assistant_id == "asst_vapi_123"

    def test_get_agent_definition_returns_none_when_no_agent(
        self, vapi_provider_without_agent
    ):
        """Verify _get_agent_definition returns None when no agent linked."""
        from tracer.services.observability_providers import ObservabilityService

        agent = ObservabilityService._get_agent_definition(vapi_provider_without_agent)

        assert agent is None

    def test_validate_returns_none_when_provider_has_no_agent(
        self, vapi_provider_without_agent
    ):
        """Verify validation returns None when provider has no agent (logs warning instead)."""
        from tracer.services.observability_providers import ObservabilityService

        agent = ObservabilityService._get_agent_definition(vapi_provider_without_agent)

        result = ObservabilityService._validate_agent_api_key(
            agent, vapi_provider_without_agent, "VAPI"
        )

        assert result is None

    def test_validate_returns_none_when_agent_has_no_api_key(
        self, vapi_provider_with_agent_no_api_key
    ):
        """Verify validation returns None when agent has no API key (logs warning instead)."""
        from tracer.services.observability_providers import ObservabilityService

        agent = ObservabilityService._get_agent_definition(
            vapi_provider_with_agent_no_api_key
        )

        assert agent is not None  # Agent exists
        assert agent.api_key is None  # But has no API key

        result = ObservabilityService._validate_agent_api_key(
            agent, vapi_provider_with_agent_no_api_key, "VAPI"
        )

        assert result is None

    def test_validate_succeeds_when_agent_has_api_key(self, vapi_provider_with_agent):
        """Verify validation returns API key when agent has one."""
        from tracer.services.observability_providers import ObservabilityService

        agent = ObservabilityService._get_agent_definition(vapi_provider_with_agent)
        api_key = ObservabilityService._validate_agent_api_key(
            agent, vapi_provider_with_agent, "VAPI"
        )

        assert api_key == "test-vapi-api-key-12345"

    @patch("tracer.services.observability_providers.requests.get")
    def test_fetch_vapi_logs_returns_empty_when_no_agent(
        self, mock_get, vapi_provider_without_agent
    ):
        """Verify _fetch_vapi_logs returns empty list when no agent (graceful handling)."""
        from tracer.services.observability_providers import ObservabilityService

        result = ObservabilityService._fetch_vapi_logs(vapi_provider_without_agent)

        assert result == []
        # Should not make HTTP request when validation fails
        mock_get.assert_not_called()

    @patch("tracer.services.observability_providers.requests.get")
    def test_fetch_vapi_logs_makes_request_with_valid_agent(
        self, mock_get, vapi_provider_with_agent
    ):
        """Verify _fetch_vapi_logs makes request when agent has API key."""
        from tracer.services.observability_providers import ObservabilityService

        mock_response = Mock()
        mock_response.json.return_value = []
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        result = ObservabilityService._fetch_vapi_logs(vapi_provider_with_agent)

        mock_get.assert_called_once()
        # Verify the Authorization header contains the API key
        call_args = mock_get.call_args
        headers = call_args.kwargs.get("headers", {})
        assert headers.get("Authorization") == "Bearer test-vapi-api-key-12345"

    @patch("tracer.services.observability_providers.requests.post")
    def test_fetch_retell_logs_returns_empty_when_no_agent(
        self, mock_post, retell_provider_without_agent
    ):
        """Verify _fetch_retell_logs returns empty list when no agent (graceful handling)."""
        from tracer.services.observability_providers import ObservabilityService

        result = ObservabilityService._fetch_retell_logs(retell_provider_without_agent)

        assert result == []
        # Should not make HTTP request when validation fails
        mock_post.assert_not_called()

    @patch("tracer.services.observability_providers.requests.post")
    def test_fetch_retell_logs_makes_request_with_valid_agent(
        self, mock_post, retell_provider_with_agent
    ):
        """Verify _fetch_retell_logs makes request when agent has API key."""
        from tracer.services.observability_providers import ObservabilityService

        mock_response = Mock()
        mock_response.json.return_value = []
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response

        result = ObservabilityService._fetch_retell_logs(retell_provider_with_agent)

        mock_post.assert_called_once()
        # Verify the Authorization header contains the API key
        call_args = mock_post.call_args
        headers = call_args.kwargs.get("headers", {})
        assert headers.get("Authorization") == "Bearer test-retell-api-key-67890"


class TestFetchLogsForProviderAuthErrors:
    """Tests for fetch_logs_for_provider handling of HTTP 401/403 errors."""

    @patch("tracer.utils.observability_provider.ObservabilityService.get_call_logs")
    @patch("tracer.utils.observability_provider.ObservabilityProvider.objects")
    def test_returns_none_on_401_without_sentry_exception(
        self, mock_provider_objects, mock_get_call_logs
    ):
        """401 errors should use logger.error (not logger.exception) and return None."""
        from tracer.utils.observability_provider import fetch_logs_for_provider

        mock_provider = Mock()
        mock_provider.provider = ProviderChoices.RETELL
        mock_provider.last_fetched_at = None
        mock_provider_objects.get.return_value = mock_provider

        mock_response = Mock()
        mock_response.status_code = 401
        http_error = HTTPError(response=mock_response)
        mock_get_call_logs.side_effect = http_error

        with patch("tracer.utils.observability_provider.logger") as mock_logger:
            result = fetch_logs_for_provider(provider_id="test-provider-id")

            assert result is None
            mock_logger.error.assert_called_once()
            assert (
                mock_logger.error.call_args[0][0]
                == "authentication_failed_for_provider"
            )
            mock_logger.exception.assert_not_called()

    @patch("tracer.utils.observability_provider.ObservabilityService.get_call_logs")
    @patch("tracer.utils.observability_provider.ObservabilityProvider.objects")
    def test_returns_none_on_403_without_sentry_exception(
        self, mock_provider_objects, mock_get_call_logs
    ):
        """403 errors should use logger.error (not logger.exception) and return None."""
        from tracer.utils.observability_provider import fetch_logs_for_provider

        mock_provider = Mock()
        mock_provider.provider = ProviderChoices.RETELL
        mock_provider.last_fetched_at = None
        mock_provider_objects.get.return_value = mock_provider

        mock_response = Mock()
        mock_response.status_code = 403
        http_error = HTTPError(response=mock_response)
        mock_get_call_logs.side_effect = http_error

        with patch("tracer.utils.observability_provider.logger") as mock_logger:
            result = fetch_logs_for_provider(provider_id="test-provider-id")

            assert result is None
            mock_logger.error.assert_called_once()
            assert (
                mock_logger.error.call_args[0][0]
                == "authentication_failed_for_provider"
            )
            mock_logger.exception.assert_not_called()

    @patch("tracer.utils.observability_provider.ObservabilityService.get_call_logs")
    @patch("tracer.utils.observability_provider.ObservabilityProvider.objects")
    def test_uses_logger_exception_for_non_auth_http_errors(
        self, mock_provider_objects, mock_get_call_logs
    ):
        """Non-auth HTTP errors (e.g. 500) should still use logger.exception."""
        from tracer.utils.observability_provider import fetch_logs_for_provider

        mock_provider = Mock()
        mock_provider.provider = ProviderChoices.RETELL
        mock_provider.last_fetched_at = None
        mock_provider_objects.get.return_value = mock_provider

        mock_response = Mock()
        mock_response.status_code = 500
        http_error = HTTPError(response=mock_response)
        mock_get_call_logs.side_effect = http_error

        with patch("tracer.utils.observability_provider.logger") as mock_logger:
            result = fetch_logs_for_provider(provider_id="test-provider-id")

            assert result is None
            mock_logger.exception.assert_called_once()
            mock_logger.error.assert_not_called()

    @patch("tracer.utils.observability_provider.ObservabilityService.get_call_logs")
    @patch("tracer.utils.observability_provider.ObservabilityProvider.objects")
    def test_logs_provider_id_and_status_code_on_auth_error(
        self, mock_provider_objects, mock_get_call_logs
    ):
        """Auth error log should include provider_id, provider_type, and status_code."""
        from tracer.utils.observability_provider import fetch_logs_for_provider

        mock_provider = Mock()
        mock_provider.provider = ProviderChoices.RETELL
        mock_provider.last_fetched_at = None
        mock_provider_objects.get.return_value = mock_provider

        mock_response = Mock()
        mock_response.status_code = 401
        http_error = HTTPError(response=mock_response)
        mock_get_call_logs.side_effect = http_error

        with patch("tracer.utils.observability_provider.logger") as mock_logger:
            fetch_logs_for_provider(provider_id="test-provider-id")

            call_kwargs = mock_logger.error.call_args[1]
            assert call_kwargs["provider_id"] == "test-provider-id"
            assert call_kwargs["provider_type"] == ProviderChoices.RETELL
            assert call_kwargs["status_code"] == 401
