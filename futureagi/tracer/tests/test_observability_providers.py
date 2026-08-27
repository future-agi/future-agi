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

    @patch("tracer.services.observability_providers.requests.post")
    def test_retell_verification_request_uses_timeout(self, mock_requests_post):
        from tracer.constants.external_endpoints import ObservabilityRoutes
        from tracer.services.observability_providers import (
            OBSERVABILITY_VERIFY_TIMEOUT_SECONDS,
            ObservabilityService,
        )

        mock_response = Mock()
        mock_response.status_code = 200
        mock_requests_post.return_value = mock_response

        result = ObservabilityService.verify_api_key(
            ProviderChoices.RETELL,
            "retell-api-key",
        )

        assert result == 200
        mock_requests_post.assert_called_once_with(
            ObservabilityRoutes.RETELL_LIST_AGENTS_URL.value,
            params={"limit": 1},
            headers={"Authorization": "Bearer retell-api-key"},
            json={
                "filter_criteria": {
                    "channel": {
                        "type": "string",
                        "op": "eq",
                        "value": "voice",
                    }
                }
            },
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
        mock_response.json.return_value = {"items": []}
        mock_response.raise_for_status = Mock()
        mock_requests_post.return_value = mock_response

        mock_provider = Mock()
        mock_provider.id = "retell-provider-123"

        result = ObservabilityService._fetch_retell_logs(mock_provider)

        mock_requests_post.assert_called_once()
        call_kwargs = mock_requests_post.call_args
        assert "Bearer valid-retell-key" in str(call_kwargs)
        # Verify v3 request body shape
        body = call_kwargs[1]["json"]
        assert "agent" in body["filter_criteria"]
        assert body["filter_criteria"]["agent"] == [{"agent_id": "agent-123"}]
        assert body["filter_criteria"]["call_status"]["type"] == "enum"
        assert body["filter_criteria"]["call_status"]["op"] == "in"
        assert body["filter_criteria"]["call_status"]["value"] == ["ended", "error"]
        assert body["limit"] == 250

    @patch("tracer.services.observability_providers.requests.get")
    @patch("tracer.services.observability_providers.requests.post")
    @patch.object(
        __import__(
            "tracer.services.observability_providers", fromlist=["ObservabilityService"]
        ).ObservabilityService,
        "_get_agent_definition",
    )
    def test_follows_pagination_cursor(
        self, mock_get_agent, mock_requests_post, mock_requests_get
    ):
        """Fetches all pages returned by the v3 cursor pagination response."""
        from tracer.services.observability_providers import ObservabilityService

        mock_agent = Mock()
        mock_agent.api_key = "valid-retell-key"
        mock_agent.assistant_id = "agent-123"
        mock_get_agent.return_value = mock_agent

        first_response = Mock()
        first_response.json.return_value = {
            "items": [{"call_id": "call-1"}],
            "has_more": True,
            "pagination_key": "next-page",
        }
        first_response.raise_for_status = Mock()

        second_response = Mock()
        second_response.json.return_value = {
            "items": [{"call_id": "call-2"}],
            "has_more": False,
        }
        second_response.raise_for_status = Mock()
        mock_requests_post.side_effect = [first_response, second_response]

        def get_call_detail(url, **kwargs):
            call_id = url.rsplit("/", 1)[-1]
            detail_response = Mock()
            detail_response.json.return_value = {
                "call_id": call_id,
                "transcript_with_tool_calls": [
                    {
                        "role": "agent",
                        "content": f"transcript for {call_id}",
                        "words": [{"start": 0.0, "end": 1.0}],
                    }
                ],
                "recording_url": f"https://recordings.example/{call_id}.wav",
            }
            detail_response.raise_for_status = Mock()
            return detail_response

        mock_requests_get.side_effect = get_call_detail

        mock_provider = Mock()
        mock_provider.id = "retell-provider-123"

        result = ObservabilityService._fetch_retell_logs(mock_provider)

        assert [call["call_id"] for call in result] == ["call-1", "call-2"]
        assert result[0]["transcript_with_tool_calls"][0]["content"] == (
            "transcript for call-1"
        )
        assert result[0]["recording_url"] == ("https://recordings.example/call-1.wav")

        processed = ObservabilityService._process_retell_logs(result[0])
        assert processed["transcript_available"] is True
        assert processed["transcript"][0]["content"] == "transcript for call-1"
        assert processed["recording_available"] is True
        assert mock_requests_post.call_count == 2
        first_body = mock_requests_post.call_args_list[0].kwargs["json"]
        second_body = mock_requests_post.call_args_list[1].kwargs["json"]
        assert "pagination_key" not in first_body
        assert second_body["pagination_key"] == "next-page"
        assert {call.args[0] for call in mock_requests_get.call_args_list} == {
            "https://api.retellai.com/v2/get-call/call-1",
            "https://api.retellai.com/v2/get-call/call-2",
        }
        assert all(
            call.kwargs["headers"]["Authorization"] == "Bearer valid-retell-key"
            and call.kwargs["timeout"] == 30
            for call in mock_requests_get.call_args_list
        )

    @patch("tracer.services.observability_providers.requests.get")
    @patch("tracer.services.observability_providers.requests.post")
    @patch.object(
        __import__(
            "tracer.services.observability_providers", fromlist=["ObservabilityService"]
        ).ObservabilityService,
        "_get_agent_definition",
    )
    def test_raises_on_repeated_pagination_cursor(
        self, mock_get_agent, mock_requests_post, mock_requests_get
    ):
        """Fails the poll rather than accepting a partial result on a cursor loop."""
        from tracer.services.observability_providers import ObservabilityService

        mock_agent = Mock()
        mock_agent.api_key = "valid-retell-key"
        mock_agent.assistant_id = "agent-123"
        mock_get_agent.return_value = mock_agent

        response = Mock()
        response.json.return_value = {
            "items": [{"call_id": "call-1"}],
            "has_more": True,
            "pagination_key": "next-page",
        }
        response.raise_for_status = Mock()
        mock_requests_post.return_value = response

        mock_provider = Mock()
        mock_provider.id = "retell-provider-123"

        with pytest.raises(RuntimeError, match="repeated cursor"):
            ObservabilityService._fetch_retell_logs(mock_provider)

        assert mock_requests_post.call_count == 2
        mock_requests_get.assert_not_called()

    @patch("tracer.services.observability_providers.requests.get")
    @patch("tracer.services.observability_providers.requests.post")
    @patch.object(
        __import__(
            "tracer.services.observability_providers", fromlist=["ObservabilityService"]
        ).ObservabilityService,
        "_get_agent_definition",
    )
    def test_raises_when_has_more_omits_pagination_cursor(
        self, mock_get_agent, mock_requests_post, mock_requests_get
    ):
        """Fails the poll when Retell claims another page without a cursor."""
        from tracer.services.observability_providers import ObservabilityService

        mock_agent = Mock()
        mock_agent.api_key = "valid-retell-key"
        mock_agent.assistant_id = "agent-123"
        mock_get_agent.return_value = mock_agent

        response = Mock()
        response.json.return_value = {
            "items": [{"call_id": "call-1"}],
            "has_more": True,
        }
        response.raise_for_status = Mock()
        mock_requests_post.return_value = response

        mock_provider = Mock()
        mock_provider.id = "retell-provider-123"

        with pytest.raises(RuntimeError, match="without a pagination_key"):
            ObservabilityService._fetch_retell_logs(mock_provider)

        mock_requests_post.assert_called_once()
        mock_requests_get.assert_not_called()


    @patch("tracer.services.observability_providers.requests.get")
    def test_raises_when_get_call_json_is_invalid(self, mock_requests_get):
        """Hydration JSON errors propagate instead of accepting the lean list item."""
        from tracer.services.observability_providers import ObservabilityService

        list_item = {"call_id": "call-1", "call_status": "ended"}
        mock_response = Mock()
        mock_response.raise_for_status = Mock()
        mock_response.json.side_effect = ValueError("invalid JSON")
        mock_requests_get.return_value = mock_response

        with pytest.raises(ValueError, match="invalid JSON"):
            ObservabilityService._fetch_retell_call_detail(
                list_item, {"Authorization": "Bearer valid-retell-key"}
            )

    @patch("tracer.services.observability_providers.requests.get")
    def test_raises_when_get_call_missing_id(self, mock_requests_get):
        """Hydration raises ValueError when the call entry has no call_id."""
        from tracer.services.observability_providers import ObservabilityService

        list_item = {"call_status": "ended"}

        with pytest.raises(ValueError, match="missing call_id"):
            ObservabilityService._fetch_retell_call_detail(
                list_item, {"Authorization": "Bearer valid-retell-key"}
            )
        mock_requests_get.assert_not_called()

    @patch("tracer.services.observability_providers.requests.get")
    def test_raises_when_get_call_returns_non_dict(self, mock_requests_get):
        """Hydration raises TypeError when the response JSON is not a dict."""
        from tracer.services.observability_providers import ObservabilityService

        list_item = {"call_id": "call-1", "call_status": "ended"}
        mock_response = Mock()
        mock_response.json.return_value = ["not", "a", "dict"]
        mock_response.raise_for_status = Mock()
        mock_requests_get.return_value = mock_response

        with pytest.raises(TypeError, match="must be a dict"):
            ObservabilityService._fetch_retell_call_detail(
                list_item, {"Authorization": "Bearer valid-retell-key"}
            )

    @patch("tracer.services.observability_providers.requests.get")
    @patch("tracer.services.observability_providers.requests.post")
    @patch.object(
        __import__(
            "tracer.services.observability_providers", fromlist=["ObservabilityService"]
        ).ObservabilityService,
        "_get_agent_definition",
    )
    def test_filters_by_end_timestamp(
        self, mock_get_agent, mock_requests_post, mock_requests_get
    ):
        """Uses end_timestamp (not start_timestamp) filter when times are provided."""
        from datetime import UTC, datetime, timedelta

        from tracer.services.observability_providers import ObservabilityService

        mock_agent = Mock()
        mock_agent.api_key = "valid-retell-key"
        mock_agent.assistant_id = "agent-123"
        mock_get_agent.return_value = mock_agent

        mock_response = Mock()
        mock_response.json.return_value = {"items": []}
        mock_response.raise_for_status = Mock()
        mock_requests_post.return_value = mock_response

        mock_provider = Mock()
        mock_provider.id = "retell-provider-123"

        now = datetime.now(tz=UTC)
        start = now - timedelta(hours=2)
        end = now

        result = ObservabilityService._fetch_retell_logs(
            mock_provider, start_time=start, end_time=end
        )

        assert result == []
        body = mock_requests_post.call_args.kwargs["json"]
        assert "end_timestamp" in body["filter_criteria"]
        assert "start_timestamp" not in body["filter_criteria"]
        ts_range = body["filter_criteria"]["end_timestamp"]
        assert ts_range["type"] == "range"
        assert ts_range["op"] == "bt"
        assert ts_range["value"] == [
            int(start.timestamp() * 1000),
            int(end.timestamp() * 1000),
        ]

    @patch("tracer.services.observability_providers.requests.get")
    @patch("tracer.services.observability_providers.requests.post")
    @patch.object(
        __import__(
            "tracer.services.observability_providers", fromlist=["ObservabilityService"]
        ).ObservabilityService,
        "_get_agent_definition",
    )
    def test_raises_on_over_bound_has_more(
        self, mock_get_agent, mock_requests_post, mock_requests_get
    ):
        """When has_more=True at the hydration bound, raises before issuing Get Call requests."""
        from tracer.services.observability_providers import (
            RETELL_CALL_HYDRATION_BOUND,
            ObservabilityService,
        )

        mock_agent = Mock()
        mock_agent.api_key = "valid-retell-key"
        mock_agent.assistant_id = "agent-123"
        mock_get_agent.return_value = mock_agent

        # Return equal to bound with has_more=True (window exceeds limit)
        response = Mock()
        boundsize_payload = [
            {"call_id": f"call-{i}"} for i in range(RETELL_CALL_HYDRATION_BOUND)
        ]
        response.json.return_value = {
            "items": boundsize_payload,
            "has_more": True,
        }
        response.raise_for_status = Mock()
        mock_requests_post.return_value = response

        mock_provider = Mock()
        mock_provider.id = "retell-provider-123"

        with pytest.raises(RuntimeError) as exc_info:
            ObservabilityService._fetch_retell_logs(mock_provider)

        assert "hydration bound" in str(exc_info.value).lower()
        # No detail GET calls should have been made
        mock_requests_get.assert_not_called()

    @patch("tracer.services.observability_providers.requests.get")
    @patch("tracer.services.observability_providers.requests.post")
    @patch.object(
        __import__(
            "tracer.services.observability_providers", fromlist=["ObservabilityService"]
        ).ObservabilityService,
        "_get_agent_definition",
    )
    def test_raises_when_response_exceeds_hydration_bound(
        self, mock_get_agent, mock_requests_post, mock_requests_get
    ):
        """Never hydrates more than the per-poll Retell call bound."""
        from tracer.services.observability_providers import (
            RETELL_CALL_HYDRATION_BOUND,
            ObservabilityService,
        )

        mock_agent = Mock()
        mock_agent.api_key = "valid-retell-key"
        mock_agent.assistant_id = "agent-123"
        mock_get_agent.return_value = mock_agent

        response = Mock()
        response.json.return_value = {
            "items": [
                {"call_id": f"call-{index}"}
                for index in range(RETELL_CALL_HYDRATION_BOUND + 1)
            ],
            "has_more": False,
        }
        response.raise_for_status = Mock()
        mock_requests_post.return_value = response

        mock_provider = Mock()
        mock_provider.id = "retell-provider-123"

        with pytest.raises(RuntimeError, match="hydration bound"):
            ObservabilityService._fetch_retell_logs(mock_provider)

        mock_requests_get.assert_not_called()


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
        mock_response.json.return_value = {"items": []}
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

    @patch("tracer.utils.observability_provider.process_and_store_logs")
    @patch("tracer.utils.observability_provider._update_last_fetched_at")
    @patch("tracer.utils.observability_provider.ObservabilityService.get_call_logs")
    @patch("tracer.utils.observability_provider.ObservabilityProvider.objects")
    def test_retell_provider_skips_poll_after_initial_fetch(
        self,
        mock_provider_objects,
        mock_get_call_logs,
        mock_update_last_fetched_at,
        mock_process_and_store_logs,
    ):
        """After watermark, Retell relies on webhook; scheduled poll is a no-op."""
        from tracer.utils.observability_provider import fetch_logs_for_provider

        mock_provider = Mock()
        mock_provider.provider = ProviderChoices.RETELL
        mock_provider.last_fetched_at = Mock()
        mock_provider_objects.get.return_value = mock_provider

        result = fetch_logs_for_provider(
            provider_id="test-provider-id", end_time=Mock()
        )

        assert result == []
        mock_get_call_logs.assert_not_called()
        mock_update_last_fetched_at.assert_not_called()
        mock_process_and_store_logs.assert_not_called()

    @patch("tracer.utils.observability_provider.process_and_store_logs")
    @patch("tracer.utils.observability_provider._update_last_fetched_at")
    @patch("tracer.utils.observability_provider.ObservabilityService.get_call_logs")
    @patch("tracer.utils.observability_provider.ObservabilityProvider.objects")
    def test_retell_provider_polls_when_last_fetched_at_is_none(
        self,
        mock_provider_objects,
        mock_get_call_logs,
        mock_update_last_fetched_at,
        mock_process_and_store_logs,
    ):
        """Retell bootstrap: null watermark means poll once, then webhook-primary."""
        from tracer.utils.observability_provider import fetch_logs_for_provider

        end_time = Mock()
        mock_provider = Mock()
        mock_provider.provider = ProviderChoices.RETELL
        mock_provider.last_fetched_at = None
        mock_provider_objects.get.return_value = mock_provider
        mock_get_call_logs.return_value = []

        result = fetch_logs_for_provider(
            provider_id="test-provider-id", end_time=end_time
        )

        assert result == []
        mock_get_call_logs.assert_called_once_with(
            provider=mock_provider,
            start_time=None,
            end_time=end_time,
        )
        mock_update_last_fetched_at.assert_called_once_with(mock_provider, end_time)
        mock_process_and_store_logs.assert_called_once_with([], mock_provider)

    @patch("tracer.utils.observability_provider.process_and_store_logs")
    @patch("tracer.utils.observability_provider._update_last_fetched_at")
    @patch("tracer.utils.observability_provider.ObservabilityService.get_call_logs")
    @patch("tracer.utils.observability_provider.ObservabilityProvider.objects")
    def test_retell_provider_polls_when_start_time_override(
        self,
        mock_provider_objects,
        mock_get_call_logs,
        mock_update_last_fetched_at,
        mock_process_and_store_logs,
    ):
        """Explicit start_time forces a Retell backfill poll even with a watermark."""
        from tracer.utils.observability_provider import fetch_logs_for_provider

        start_time = Mock()
        end_time = Mock()
        mock_provider = Mock()
        mock_provider.provider = ProviderChoices.RETELL
        mock_provider.last_fetched_at = Mock()
        mock_provider_objects.get.return_value = mock_provider
        mock_get_call_logs.return_value = []

        result = fetch_logs_for_provider(
            provider_id="test-provider-id",
            start_time=start_time,
            end_time=end_time,
        )

        assert result == []
        mock_get_call_logs.assert_called_once_with(
            provider=mock_provider,
            start_time=start_time,
            end_time=end_time,
        )
        mock_update_last_fetched_at.assert_called_once_with(mock_provider, end_time)
        mock_process_and_store_logs.assert_called_once_with([], mock_provider)




