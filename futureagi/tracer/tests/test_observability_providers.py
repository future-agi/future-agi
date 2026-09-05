"""
Tests for ObservabilityService in tracer/services/observability_providers.py.

Fixes CORE-BACKEND-WCN (VAPI 401) and CORE-BACKEND-WTW (Retell 401).

Run with: pytest tracer/tests/test_observability_providers.py -v
"""

from datetime import timedelta
from unittest.mock import Mock, call, patch

import pytest
import requests
from requests.exceptions import HTTPError
from structlog.testing import capture_logs

from tracer.models.observability_provider import ProviderChoices
from tracer.services.observability_providers import (
    RETELL_MAX_ATTEMPTS,
    RETELL_REQUEST_TIMEOUT_SECONDS,
    ObservabilityService,
    RetellConfigurationError,
    RetellCursorRejected,
)
from tracer.tests.fixtures.retell_calls import FAKE_AGENT_ID, detail, list_item, list_page


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


def _end(hours: int = 0):
    """An aware UTC end_time, offset by ``hours`` (positive = later)."""
    from datetime import UTC, datetime, timedelta

    return datetime(2026, 9, 3, 12, 0, 0, tzinfo=UTC) + timedelta(hours=hours)


def _provider_with_agent(assistant_id=None, api_key="legacy-retell-key"):
    """A Mock provider whose ``.agent_definition`` resolves like a real one,
    with no ProviderCredentials rows so the legacy ``agent.api_key`` wins."""
    from tracer.tests.fixtures.retell_calls import FAKE_AGENT_ID

    provider = Mock()
    provider.id = "retell-provider-1"
    agent = Mock()
    agent.assistant_id = assistant_id or FAKE_AGENT_ID
    agent.api_key = api_key
    provider.agent_definition = agent
    return provider


class _EmptyCredentialsQuerySet(list):
    """Stands in for a ProviderCredentials queryset with zero rows."""

    def filter(self, *args, **kwargs):
        return self

    def exclude(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def first(self):
        return None


@pytest.fixture
def no_extra_credentials():
    """Patches ProviderCredentials.objects so key resolution falls back to
    the Mock agent's plaintext ``api_key`` field without touching the DB."""
    with patch(
        "tracer.services.observability_providers.ProviderCredentials.objects"
    ) as mock_manager:
        mock_manager.filter.return_value = _EmptyCredentialsQuerySet()
        yield mock_manager


def _list_response(items, *, has_more=False, pagination_key=None, status_code=200):
    response = Mock()
    response.status_code = status_code
    response.json.return_value = list_page(
        items, has_more=has_more, pagination_key=pagination_key
    )
    response.raise_for_status = Mock()
    return response


def _detail_response(call_id, start_ms, end_ms, *, status_code=200, **kwargs):
    response = Mock()
    response.status_code = status_code
    response.json.return_value = detail(call_id, start_ms, end_ms, **kwargs)
    response.raise_for_status = Mock()
    return response


def _http_error_response(status_code):
    response = Mock()
    response.status_code = status_code
    response.raise_for_status = Mock(side_effect=HTTPError(response=response))
    return response


class TestFetchRetellPageRequestBody:
    """Literal request-body assertions (§3): nesting, mode selection, cursor/skip placement."""

    def test_bootstrap_body_is_literal(self, no_extra_credentials):
        provider = _provider_with_agent()
        end = _end()

        with patch(
            "tracer.services.observability_providers.requests.post"
        ) as mock_post:
            mock_post.return_value = _list_response([])
            ObservabilityService.fetch_retell_page(provider, None, end)

        mock_post.assert_called_once()
        assert (
            mock_post.call_args.args[0] == "https://api.retellai.com/v3/list-calls"
        )
        assert mock_post.call_args.kwargs["headers"]["Authorization"] == (
            "Bearer legacy-retell-key"
        )
        assert mock_post.call_args.kwargs["timeout"] == RETELL_REQUEST_TIMEOUT_SECONDS
        body = mock_post.call_args.kwargs["json"]
        assert body == {
            "sort_order": "descending",
            "limit": 1000,
            "filter_criteria": {
                "agent": [{"agent_id": FAKE_AGENT_ID}],
                "call_status": {"type": "enum", "op": "in", "value": ["ended", "error"]},
                "end_timestamp": {
                    "type": "range",
                    "op": "bt",
                    "value": [0, int(end.timestamp() * 1000)],
                },
            },
        }
        assert "include_total" not in body

    def test_windowed_body_page_one_has_no_cursor_or_skip(self, no_extra_credentials):
        provider = _provider_with_agent()
        start = _end(-1)
        end = _end()

        with patch(
            "tracer.services.observability_providers.requests.post"
        ) as mock_post:
            mock_post.return_value = _list_response([])
            ObservabilityService.fetch_retell_page(provider, start, end)

        body = mock_post.call_args.kwargs["json"]
        assert "pagination_key" not in body
        assert "skip" not in body
        assert body == {
            "sort_order": "ascending",
            "limit": 1000,
            "filter_criteria": {
                "agent": [{"agent_id": FAKE_AGENT_ID}],
                "call_status": {"type": "enum", "op": "in", "value": ["ended", "error"]},
                "end_timestamp": {
                    "type": "range",
                    "op": "bt",
                    "value": [
                        int(start.timestamp() * 1000) - 1,
                        int(end.timestamp() * 1000),
                    ],
                },
            },
        }

    def test_windowed_body_carries_pagination_key(self, no_extra_credentials):
        provider = _provider_with_agent()
        start = _end(-1)
        end = _end()

        with patch(
            "tracer.services.observability_providers.requests.post"
        ) as mock_post:
            mock_post.return_value = _list_response([])
            ObservabilityService.fetch_retell_page(
                provider, start, end, pagination_key="cursor-1"
            )

        body = mock_post.call_args.kwargs["json"]
        assert body["pagination_key"] == "cursor-1"
        assert "skip" not in body

    def test_windowed_body_carries_skip_zero(self, no_extra_credentials):
        """skip=0 is a valid offset and must still be sent (falsy but not None)."""
        provider = _provider_with_agent()
        start = _end(-1)
        end = _end()

        with patch(
            "tracer.services.observability_providers.requests.post"
        ) as mock_post:
            mock_post.return_value = _list_response([])
            ObservabilityService.fetch_retell_page(provider, start, end, skip=0)

        body = mock_post.call_args.kwargs["json"]
        assert body["skip"] == 0
        assert "pagination_key" not in body

    def test_never_sends_both_pagination_key_and_skip(self, no_extra_credentials):
        provider = _provider_with_agent()
        start = _end(-1)
        end = _end()

        with patch("tracer.services.observability_providers.requests.post"):
            with pytest.raises(RetellConfigurationError):
                ObservabilityService.fetch_retell_page(
                    provider, start, end, pagination_key="k", skip=0
                )

    def test_one_list_request_per_call(self, no_extra_credentials):
        """fetch_retell_page never loops; it returns exactly one page."""
        provider = _provider_with_agent()
        end = _end()

        with patch(
            "tracer.services.observability_providers.requests.post"
        ) as mock_post:
            mock_post.return_value = _list_response(
                [], has_more=True, pagination_key="k"
            )
            ObservabilityService.fetch_retell_page(provider, None, end)

        mock_post.assert_called_once()


class TestFetchRetellPageConfigurationErrors:
    """§3: end_time naive/missing; start_time naive; start_time >= end_time; cursor/skip in bootstrap."""

    def test_missing_end_time_raises(self, no_extra_credentials):
        provider = _provider_with_agent()
        with pytest.raises(RetellConfigurationError):
            ObservabilityService.fetch_retell_page(provider, None, None)

    def test_naive_end_time_raises(self, no_extra_credentials):
        from datetime import datetime

        provider = _provider_with_agent()
        with pytest.raises(RetellConfigurationError):
            ObservabilityService.fetch_retell_page(
                provider, None, datetime(2026, 9, 3, 12, 0, 0)
            )

    def test_naive_start_time_raises(self, no_extra_credentials):
        from datetime import datetime

        provider = _provider_with_agent()
        end = _end()
        with pytest.raises(RetellConfigurationError):
            ObservabilityService.fetch_retell_page(
                provider, datetime(2026, 9, 3, 10, 0, 0), end
            )

    def test_start_after_end_raises(self, no_extra_credentials):
        provider = _provider_with_agent()
        end = _end()
        with pytest.raises(RetellConfigurationError):
            ObservabilityService.fetch_retell_page(provider, end + timedelta(hours=1), end)

    def test_start_equal_end_raises(self, no_extra_credentials):
        provider = _provider_with_agent()
        end = _end()
        with pytest.raises(RetellConfigurationError):
            ObservabilityService.fetch_retell_page(provider, end, end)

    def test_pagination_key_in_bootstrap_raises(self, no_extra_credentials):
        provider = _provider_with_agent()
        end = _end()
        with pytest.raises(RetellConfigurationError):
            ObservabilityService.fetch_retell_page(provider, None, end, pagination_key="x")

    def test_skip_in_bootstrap_raises(self, no_extra_credentials):
        provider = _provider_with_agent()
        end = _end()
        with pytest.raises(RetellConfigurationError):
            ObservabilityService.fetch_retell_page(provider, None, end, skip=0)


class TestFetchRetellPageEnvelope:
    """§3 modes: has_more / next_key pass-through, cursor page 1 included."""

    def test_cursor_mode_page_one_has_more_returns_key(self, no_extra_credentials):
        provider = _provider_with_agent()
        start = _end(-1)
        end = _end()

        with patch(
            "tracer.services.observability_providers.requests.post"
        ) as mock_post:
            mock_post.return_value = _list_response(
                [], has_more=True, pagination_key="next-key"
            )
            page = ObservabilityService.fetch_retell_page(provider, start, end)

        assert page.has_more is True
        assert page.next_key == "next-key"

    def test_cursor_mode_no_more_pages_next_key_none(self, no_extra_credentials):
        provider = _provider_with_agent()
        start = _end(-1)
        end = _end()

        with patch(
            "tracer.services.observability_providers.requests.post"
        ) as mock_post:
            mock_post.return_value = _list_response([], has_more=False)
            page = ObservabilityService.fetch_retell_page(provider, start, end)

        assert page.has_more is False
        assert page.next_key is None

    def test_bootstrap_has_more_true_returns_next_key_none_without_raising(
        self, no_extra_credentials
    ):
        provider = _provider_with_agent()
        end = _end()

        with patch(
            "tracer.services.observability_providers.requests.post"
        ) as mock_post:
            # A real key in the envelope proves bootstrap discards it, not that
            # the response happened to carry none.
            mock_post.return_value = _list_response(
                [], has_more=True, pagination_key="should-not-surface"
            )
            page = ObservabilityService.fetch_retell_page(provider, None, end)

        assert page.has_more is True
        assert page.next_key is None

    def test_cursor_mode_has_more_without_key_raises_missing_key(
        self, no_extra_credentials
    ):
        provider = _provider_with_agent()
        start = _end(-1)
        end = _end()

        with patch(
            "tracer.services.observability_providers.requests.post"
        ) as mock_post:
            mock_post.return_value = _list_response([], has_more=True)
            with pytest.raises(RetellCursorRejected) as exc_info:
                ObservabilityService.fetch_retell_page(provider, start, end)

        assert exc_info.value.cause == "missing_key"

    def test_offset_mode_has_more_without_key_does_not_raise(
        self, no_extra_credentials
    ):
        provider = _provider_with_agent()
        start = _end(-1)
        end = _end()

        with patch(
            "tracer.services.observability_providers.requests.post"
        ) as mock_post:
            # A real key in the envelope proves offset mode discards it, not
            # that the response happened to carry none.
            mock_post.return_value = _list_response(
                [], has_more=True, pagination_key="should-not-surface"
            )
            page = ObservabilityService.fetch_retell_page(provider, start, end, skip=0)

        assert page.has_more is True
        assert page.next_key is None


class TestFetchRetellPageListFailures:
    """§3 HTTP failure table for the LIST request."""

    def test_400_with_pagination_key_raises_cursor_rejected(self, no_extra_credentials):
        provider = _provider_with_agent()
        start = _end(-1)
        end = _end()

        with patch(
            "tracer.services.observability_providers.requests.post"
        ) as mock_post:
            mock_post.return_value = _http_error_response(400)
            with pytest.raises(RetellCursorRejected) as exc_info:
                ObservabilityService.fetch_retell_page(
                    provider, start, end, pagination_key="k"
                )

        assert exc_info.value.cause == "http_400"

    def test_404_with_skip_raises_cursor_rejected(self, no_extra_credentials):
        provider = _provider_with_agent()
        start = _end(-1)
        end = _end()

        with patch(
            "tracer.services.observability_providers.requests.post"
        ) as mock_post:
            mock_post.return_value = _http_error_response(404)
            with pytest.raises(RetellCursorRejected) as exc_info:
                ObservabilityService.fetch_retell_page(provider, start, end, skip=0)

        assert exc_info.value.cause == "http_404"

    def test_422_with_pagination_key_raises_cursor_rejected(self, no_extra_credentials):
        provider = _provider_with_agent()
        start = _end(-1)
        end = _end()

        with patch(
            "tracer.services.observability_providers.requests.post"
        ) as mock_post:
            mock_post.return_value = _http_error_response(422)
            with pytest.raises(RetellCursorRejected) as exc_info:
                ObservabilityService.fetch_retell_page(
                    provider, start, end, pagination_key="k"
                )

        assert exc_info.value.cause == "http_422"

    @pytest.mark.parametrize("status_code", [400, 404, 422])
    def test_4xx_without_cursor_or_skip_raises_plain_http_error(
        self, no_extra_credentials, status_code
    ):
        provider = _provider_with_agent()
        start = _end(-1)
        end = _end()

        with patch(
            "tracer.services.observability_providers.requests.post"
        ) as mock_post:
            mock_post.return_value = _http_error_response(status_code)
            with pytest.raises(HTTPError) as exc_info:
                ObservabilityService.fetch_retell_page(provider, start, end)

        assert exc_info.value.response.status_code == status_code

    def test_list_500_x3_raises_and_sleeps_1_then_2(self, no_extra_credentials):
        provider = _provider_with_agent()
        end = _end()

        with patch(
            "tracer.services.observability_providers.requests.post"
        ) as mock_post, patch(
            "tracer.services.observability_providers._sleep"
        ) as mock_sleep:
            mock_post.return_value = _http_error_response(500)
            with pytest.raises(HTTPError):
                ObservabilityService.fetch_retell_page(provider, None, end)

        assert mock_post.call_count == RETELL_MAX_ATTEMPTS
        assert mock_sleep.call_args_list == [call(1), call(2)]

    def test_list_429_x3_raises(self, no_extra_credentials):
        provider = _provider_with_agent()
        end = _end()

        with patch(
            "tracer.services.observability_providers.requests.post"
        ) as mock_post, patch("tracer.services.observability_providers._sleep"):
            mock_post.return_value = _http_error_response(429)
            with pytest.raises(HTTPError):
                ObservabilityService.fetch_retell_page(provider, None, end)

        assert mock_post.call_count == RETELL_MAX_ATTEMPTS

    def test_list_401_raises_at_once_no_retry(self, no_extra_credentials):
        provider = _provider_with_agent()
        end = _end()

        with patch(
            "tracer.services.observability_providers.requests.post"
        ) as mock_post, patch(
            "tracer.services.observability_providers._sleep"
        ) as mock_sleep:
            mock_post.return_value = _http_error_response(401)
            with pytest.raises(HTTPError):
                ObservabilityService.fetch_retell_page(provider, None, end)

        mock_post.assert_called_once()
        mock_sleep.assert_not_called()

    def test_list_403_raises_at_once_no_retry(self, no_extra_credentials):
        provider = _provider_with_agent()
        end = _end()

        with patch(
            "tracer.services.observability_providers.requests.post"
        ) as mock_post, patch(
            "tracer.services.observability_providers._sleep"
        ) as mock_sleep:
            mock_post.return_value = _http_error_response(403)
            with pytest.raises(HTTPError):
                ObservabilityService.fetch_retell_page(provider, None, end)

        mock_post.assert_called_once()
        mock_sleep.assert_not_called()

    def test_list_connection_error_then_success_sleeps_once(
        self, no_extra_credentials
    ):
        provider = _provider_with_agent()
        end = _end()

        with patch(
            "tracer.services.observability_providers.requests.post"
        ) as mock_post, patch(
            "tracer.services.observability_providers._sleep"
        ) as mock_sleep:
            mock_post.side_effect = [
                requests.ConnectionError("connection reset"),
                _list_response([]),
            ]
            page = ObservabilityService.fetch_retell_page(provider, None, end)

        assert page.calls == []
        assert mock_post.call_count == 2
        mock_sleep.assert_called_once_with(1)

    def test_list_timeout_x3_raises(self, no_extra_credentials):
        provider = _provider_with_agent()
        end = _end()

        with patch(
            "tracer.services.observability_providers.requests.post"
        ) as mock_post, patch(
            "tracer.services.observability_providers._sleep"
        ) as mock_sleep:
            mock_post.side_effect = requests.Timeout("timed out")
            with pytest.raises(requests.Timeout):
                ObservabilityService.fetch_retell_page(provider, None, end)

        assert mock_post.call_count == RETELL_MAX_ATTEMPTS
        assert mock_sleep.call_args_list == [call(1), call(2)]


class TestFetchRetellPageHydration:
    """§3 hydration: non-null merge, dropped_no_end, get-call failure table, dedup."""

    def test_null_detail_fields_do_not_overwrite_list_values(self, no_extra_credentials):
        provider = _provider_with_agent()
        end = _end()
        item = list_item("c1", 1_000, 2_000)

        with patch(
            "tracer.services.observability_providers.requests.post"
        ) as mock_post, patch(
            "tracer.services.observability_providers.requests.get"
        ) as mock_get:
            mock_post.return_value = _list_response([item])
            mock_get.return_value = _detail_response(
                "c1", 1_000, 2_000, null_fields=("end_timestamp",)
            )
            page = ObservabilityService.fetch_retell_page(provider, None, end)

        assert page.dropped_no_end == 0
        assert page.calls[0]["end_timestamp"] == 2_000
        assert page.calls[0]["call_id"] == "c1"

    def test_dropped_no_end_excludes_item_and_skips_hydration(
        self, no_extra_credentials
    ):
        provider = _provider_with_agent()
        end = _end()
        item = list_item("c1", 1_000, None)

        with patch(
            "tracer.services.observability_providers.requests.post"
        ) as mock_post, patch(
            "tracer.services.observability_providers.requests.get"
        ) as mock_get:
            mock_post.return_value = _list_response([item])
            page = ObservabilityService.fetch_retell_page(provider, None, end)

        assert page.calls == []
        assert page.dropped_no_end == 1
        mock_get.assert_not_called()

    def test_get_call_404_counts_as_dropped_missing(self, no_extra_credentials):
        provider = _provider_with_agent()
        end = _end()
        item = list_item("c1", 1_000, 2_000)

        with patch(
            "tracer.services.observability_providers.requests.post"
        ) as mock_post, patch(
            "tracer.services.observability_providers.requests.get"
        ) as mock_get, patch(
            "tracer.services.observability_providers._sleep"
        ) as mock_sleep, capture_logs() as logs:
            mock_post.return_value = _list_response([item])
            mock_get.return_value = _http_error_response(404)
            page = ObservabilityService.fetch_retell_page(provider, None, end)

        assert page.calls == []
        assert page.dropped_missing == 1
        assert page.dropped_failed == 0
        mock_sleep.assert_not_called()

        missing_events = [e for e in logs if e["event"] == "retell_call_detail_missing"]
        assert len(missing_events) == 1
        fields = {
            k: v for k, v in missing_events[0].items() if k not in ("event", "log_level")
        }
        assert set(fields) == {"provider_id", "count"}
        assert fields["count"] == 1

    def test_get_call_422_counts_as_dropped_missing(self, no_extra_credentials):
        provider = _provider_with_agent()
        end = _end()
        item = list_item("c1", 1_000, 2_000)

        with patch(
            "tracer.services.observability_providers.requests.post"
        ) as mock_post, patch(
            "tracer.services.observability_providers.requests.get"
        ) as mock_get:
            mock_post.return_value = _list_response([item])
            mock_get.return_value = _http_error_response(422)
            page = ObservabilityService.fetch_retell_page(provider, None, end)

        assert page.dropped_missing == 1

    def test_get_call_400_counts_as_dropped_missing(self, no_extra_credentials):
        """The get-call HTTP failure table only excepts 401/403 from being counted."""
        provider = _provider_with_agent()
        end = _end()
        item = list_item("c1", 1_000, 2_000)

        with patch(
            "tracer.services.observability_providers.requests.post"
        ) as mock_post, patch(
            "tracer.services.observability_providers.requests.get"
        ) as mock_get:
            mock_post.return_value = _list_response([item])
            mock_get.return_value = _http_error_response(400)
            page = ObservabilityService.fetch_retell_page(provider, None, end)

        assert page.dropped_missing == 1

    def test_get_call_500_x3_counts_as_dropped_failed_other_calls_hydrated(
        self, no_extra_credentials
    ):
        provider = _provider_with_agent()
        end = _end()
        items = [list_item("c1", 1_000, 2_000), list_item("c2", 1_000, 2_000)]

        def get_side_effect(url, **kwargs):
            if url.endswith("/c1"):
                return _http_error_response(500)
            return _detail_response("c2", 1_000, 2_000)

        with patch(
            "tracer.services.observability_providers.requests.post"
        ) as mock_post, patch(
            "tracer.services.observability_providers.requests.get"
        ) as mock_get, patch(
            "tracer.services.observability_providers._sleep"
        ) as mock_sleep, capture_logs() as logs:
            mock_post.return_value = _list_response(items)
            mock_get.side_effect = get_side_effect
            page = ObservabilityService.fetch_retell_page(provider, None, end)

        assert page.dropped_failed == 1
        assert page.dropped_missing == 0
        assert [c["call_id"] for c in page.calls] == ["c2"]
        assert mock_sleep.call_args_list == [call(1), call(2)]

        failed_events = [e for e in logs if e["event"] == "retell_call_detail_failed"]
        assert len(failed_events) == 1
        fields = {
            k: v for k, v in failed_events[0].items() if k not in ("event", "log_level")
        }
        assert set(fields) == {"provider_id", "count"}
        assert fields["count"] == 1

    def test_get_call_429_x3_counts_as_dropped_failed_other_calls_hydrated(
        self, no_extra_credentials
    ):
        provider = _provider_with_agent()
        end = _end()
        items = [list_item("c1", 1_000, 2_000), list_item("c2", 1_000, 2_000)]

        def get_side_effect(url, **kwargs):
            if url.endswith("/c1"):
                return _http_error_response(429)
            return _detail_response("c2", 1_000, 2_000)

        with patch(
            "tracer.services.observability_providers.requests.post"
        ) as mock_post, patch(
            "tracer.services.observability_providers.requests.get"
        ) as mock_get, patch(
            "tracer.services.observability_providers._sleep"
        ) as mock_sleep:
            mock_post.return_value = _list_response(items)
            mock_get.side_effect = get_side_effect
            page = ObservabilityService.fetch_retell_page(provider, None, end)

        assert page.dropped_failed == 1
        assert page.dropped_missing == 0
        assert [c["call_id"] for c in page.calls] == ["c2"]
        assert mock_sleep.call_args_list == [call(1), call(2)]

    def test_get_call_401_raises_at_once_no_retry(self, no_extra_credentials):
        provider = _provider_with_agent()
        end = _end()
        item = list_item("c1", 1_000, 2_000)

        with patch(
            "tracer.services.observability_providers.requests.post"
        ) as mock_post, patch(
            "tracer.services.observability_providers.requests.get"
        ) as mock_get, patch(
            "tracer.services.observability_providers._sleep"
        ) as mock_sleep:
            mock_post.return_value = _list_response([item])
            mock_get.return_value = _http_error_response(401)
            with pytest.raises(HTTPError):
                ObservabilityService.fetch_retell_page(provider, None, end)

        mock_get.assert_called_once()
        mock_sleep.assert_not_called()

    def test_get_call_timeout_x3_counts_as_dropped_failed(self, no_extra_credentials):
        provider = _provider_with_agent()
        end = _end()
        item = list_item("c1", 1_000, 2_000)

        with patch(
            "tracer.services.observability_providers.requests.post"
        ) as mock_post, patch(
            "tracer.services.observability_providers.requests.get"
        ) as mock_get, patch(
            "tracer.services.observability_providers._sleep"
        ) as mock_sleep:
            mock_post.return_value = _list_response([item])
            mock_get.side_effect = requests.Timeout("timed out")
            page = ObservabilityService.fetch_retell_page(provider, None, end)

        assert page.calls == []
        assert page.dropped_failed == 1
        assert mock_get.call_count == RETELL_MAX_ATTEMPTS
        assert mock_sleep.call_args_list == [call(1), call(2)]

    def test_get_call_non_json_body_counts_as_dropped_failed_others_hydrated(
        self, no_extra_credentials
    ):
        """response.json() is inside the guarded region — a non-JSON 200
        never propagates and never fails the page."""
        provider = _provider_with_agent()
        end = _end()
        items = [list_item("c1", 1_000, 2_000), list_item("c2", 1_000, 2_000)]

        def get_side_effect(url, **kwargs):
            if url.endswith("/c1"):
                response = Mock()
                response.status_code = 200
                response.raise_for_status = Mock()
                response.json.side_effect = ValueError("not JSON")
                return response
            return _detail_response("c2", 1_000, 2_000)

        with patch(
            "tracer.services.observability_providers.requests.post"
        ) as mock_post, patch(
            "tracer.services.observability_providers.requests.get"
        ) as mock_get:
            mock_post.return_value = _list_response(items)
            mock_get.side_effect = get_side_effect
            page = ObservabilityService.fetch_retell_page(provider, None, end)

        assert page.dropped_failed == 1
        assert page.dropped_missing == 0
        assert [c["call_id"] for c in page.calls] == ["c2"]

    def test_get_call_non_dict_body_counts_as_dropped_failed(
        self, no_extra_credentials
    ):
        """A detail body that parses but isn't a dict is a failed
        hydration, not a silently un-hydrated success."""
        provider = _provider_with_agent()
        end = _end()
        item = list_item("c1", 1_000, 2_000)

        with patch(
            "tracer.services.observability_providers.requests.post"
        ) as mock_post, patch(
            "tracer.services.observability_providers.requests.get"
        ) as mock_get:
            mock_post.return_value = _list_response([item])
            response = Mock()
            response.status_code = 200
            response.raise_for_status = Mock()
            response.json.return_value = ["not", "a", "dict"]
            mock_get.return_value = response
            page = ObservabilityService.fetch_retell_page(provider, None, end)

        assert page.calls == []
        assert page.dropped_failed == 1
        assert page.dropped_missing == 0

    def test_missing_call_id_excluded_before_any_get_call(self, no_extra_credentials):
        """No call_id means no get-call is ever attempted (never
        ``.../get-call/None``, never a KeyError) — counted as dropped_missing:
        permanent, like a Retell-confirmed unknown id, since no request can
        ever make this item retrievable."""
        provider = _provider_with_agent()
        end = _end()
        item = list_item("c1", 1_000, 2_000)
        del item["call_id"]

        with patch(
            "tracer.services.observability_providers.requests.post"
        ) as mock_post, patch(
            "tracer.services.observability_providers.requests.get"
        ) as mock_get:
            mock_post.return_value = _list_response([item])
            page = ObservabilityService.fetch_retell_page(provider, None, end)

        assert page.calls == []
        assert page.dropped_missing == 1
        assert page.dropped_failed == 0
        mock_get.assert_not_called()

    def test_unhashable_call_id_excluded_before_any_get_call(
        self, no_extra_credentials
    ):
        """A call_id that isn't a plain string (so it could later collide with
        the dedup dict, or simply isn't hashable) is guarded the same way as a
        missing one: excluded before any request, counted as dropped_missing."""
        provider = _provider_with_agent()
        end = _end()
        item = list_item("c1", 1_000, 2_000)
        item["call_id"] = ["not", "hashable"]

        with patch(
            "tracer.services.observability_providers.requests.post"
        ) as mock_post, patch(
            "tracer.services.observability_providers.requests.get"
        ) as mock_get:
            mock_post.return_value = _list_response([item])
            page = ObservabilityService.fetch_retell_page(provider, None, end)

        assert page.calls == []
        assert page.dropped_missing == 1
        assert page.dropped_failed == 0
        mock_get.assert_not_called()

    def test_non_dict_list_item_excluded_without_raising(self, no_extra_credentials):
        """A malformed envelope entry (not a dict) is counted and skipped
        rather than raising AttributeError mid-hydration; well-formed items
        around it still hydrate."""
        provider = _provider_with_agent()
        end = _end()
        items = ["not-a-dict", list_item("c1", 1_000, 2_000)]

        with patch(
            "tracer.services.observability_providers.requests.post"
        ) as mock_post, patch(
            "tracer.services.observability_providers.requests.get"
        ) as mock_get:
            mock_post.return_value = _list_response(items)
            mock_get.return_value = _detail_response("c1", 1_000, 2_000)
            page = ObservabilityService.fetch_retell_page(provider, None, end)

        assert page.dropped_failed == 1
        assert [c["call_id"] for c in page.calls] == ["c1"]

    def test_dedup_by_call_id_last_wins(self, no_extra_credentials):
        provider = _provider_with_agent()
        end = _end()
        items = [list_item("c1", 1_000, 2_000), list_item("c1", 1_000, 2_000)]

        with patch(
            "tracer.services.observability_providers.requests.post"
        ) as mock_post, patch(
            "tracer.services.observability_providers.requests.get"
        ) as mock_get:
            mock_post.return_value = _list_response(items)
            mock_get.side_effect = [
                _detail_response("c1", 1_000, 2_000, with_recording=False),
                _detail_response("c1", 1_000, 2_000, with_recording=True),
            ]
            page = ObservabilityService.fetch_retell_page(provider, None, end)

        assert len(page.calls) == 1
        assert page.calls[0].get("recording_url") is not None


@pytest.mark.django_db
class TestRetellKeyResolution:
    """§3 key resolution, transcribed literally; verified against real models."""

    def _agent(
        self,
        test_project,
        organization,
        workspace,
        *,
        legacy_api_key="",
        assistant_id="agent-real-1",
    ):
        from simulate.models.agent_definition import AgentDefinition
        from tracer.models.observability_provider import ObservabilityProvider

        provider = ObservabilityProvider.objects.create(
            project=test_project,
            provider=ProviderChoices.RETELL,
            enabled=True,
            organization=organization,
            workspace=workspace,
        )
        agent = AgentDefinition.objects.create(
            agent_name="Retell Key Ranking Agent",
            agent_type="voice",
            inbound=True,
            description="test agent for key ranking",
            api_key=legacy_api_key,
            assistant_id=assistant_id,
            provider="retell",
            organization=organization,
            workspace=workspace,
            observability_provider=provider,
        )
        return provider, agent

    def test_versioned_credential_beats_legacy_row(
        self, test_project, organization, workspace
    ):
        from simulate.models.agent_definition import ProviderCredentials

        provider, agent = self._agent(test_project, organization, workspace)
        ProviderCredentials.objects.create(
            agent_definition=agent,
            provider_type=ProviderCredentials.ProviderType.RETELL,
            api_key="legacy-row-key",
        )
        version = agent.create_version(description="v1", commit_message="v1")
        ProviderCredentials.objects.create(
            agent_version=version,
            provider_type=ProviderCredentials.ProviderType.RETELL,
            api_key="versioned-key",
        )
        end = _end()

        with patch(
            "tracer.services.observability_providers.requests.post"
        ) as mock_post:
            mock_post.return_value = _list_response([])
            ObservabilityService.fetch_retell_page(provider, None, end)

        assert mock_post.call_args.kwargs["headers"]["Authorization"] == (
            "Bearer versioned-key"
        )

    def test_resolves_exactly_one_decrypt_with_multiple_candidate_rows(
        self, test_project, organization, workspace
    ):
        """§5 forbids decrypting more than the chosen row — pin the count."""
        from simulate.models.agent_definition import ProviderCredentials

        provider, agent = self._agent(test_project, organization, workspace)
        ProviderCredentials.objects.create(
            agent_definition=agent,
            provider_type=ProviderCredentials.ProviderType.RETELL,
            api_key="legacy-row-key",
        )
        v1 = agent.create_version(description="v1", commit_message="v1")
        ProviderCredentials.objects.create(
            agent_version=v1,
            provider_type=ProviderCredentials.ProviderType.RETELL,
            api_key="v1-key",
        )
        v2 = agent.create_version(description="v2", commit_message="v2")
        ProviderCredentials.objects.create(
            agent_version=v2,
            provider_type=ProviderCredentials.ProviderType.RETELL,
            api_key="v2-key",
        )
        end = _end()

        with patch.object(
            ProviderCredentials, "get_api_key", return_value="decrypted-key"
        ) as mock_get_api_key, patch(
            "tracer.services.observability_providers.requests.post"
        ) as mock_post:
            mock_post.return_value = _list_response([])
            ObservabilityService.fetch_retell_page(provider, None, end)

        assert mock_get_api_key.call_count == 1
        assert mock_post.call_args.kwargs["headers"]["Authorization"] == (
            "Bearer decrypted-key"
        )

    def test_highest_agent_version_number_wins(
        self, test_project, organization, workspace
    ):
        from simulate.models.agent_definition import ProviderCredentials

        provider, agent = self._agent(test_project, organization, workspace)
        v1 = agent.create_version(description="v1", commit_message="v1")
        ProviderCredentials.objects.create(
            agent_version=v1,
            provider_type=ProviderCredentials.ProviderType.RETELL,
            api_key="v1-key",
        )
        v2 = agent.create_version(description="v2", commit_message="v2")
        ProviderCredentials.objects.create(
            agent_version=v2,
            provider_type=ProviderCredentials.ProviderType.RETELL,
            api_key="v2-key",
        )
        end = _end()

        with patch(
            "tracer.services.observability_providers.requests.post"
        ) as mock_post:
            mock_post.return_value = _list_response([])
            ObservabilityService.fetch_retell_page(provider, None, end)

        assert mock_post.call_args.kwargs["headers"]["Authorization"] == "Bearer v2-key"

    def test_vapi_credential_row_is_ignored(self, test_project, organization, workspace):
        from simulate.models.agent_definition import ProviderCredentials

        provider, agent = self._agent(
            test_project, organization, workspace, legacy_api_key="legacy-plaintext"
        )
        version = agent.create_version(description="v1", commit_message="v1")
        ProviderCredentials.objects.create(
            agent_version=version,
            provider_type=ProviderCredentials.ProviderType.VAPI,
            api_key="vapi-key",
        )
        end = _end()

        with patch(
            "tracer.services.observability_providers.requests.post"
        ) as mock_post:
            mock_post.return_value = _list_response([])
            ObservabilityService.fetch_retell_page(provider, None, end)

        assert mock_post.call_args.kwargs["headers"]["Authorization"] == (
            "Bearer legacy-plaintext"
        )

    def test_legacy_plaintext_field_is_last_resort(
        self, test_project, organization, workspace
    ):
        provider, agent = self._agent(
            test_project, organization, workspace, legacy_api_key="plain-legacy-field"
        )
        end = _end()

        with patch(
            "tracer.services.observability_providers.requests.post"
        ) as mock_post:
            mock_post.return_value = _list_response([])
            ObservabilityService.fetch_retell_page(provider, None, end)

        assert mock_post.call_args.kwargs["headers"]["Authorization"] == (
            "Bearer plain-legacy-field"
        )

    def test_decrypt_failure_raises_fixed_message_and_never_leaks_key(
        self, test_project, organization, workspace
    ):
        from simulate.models.agent_definition import ProviderCredentials

        provider, agent = self._agent(test_project, organization, workspace)
        ProviderCredentials.objects.create(
            agent_definition=agent,
            provider_type=ProviderCredentials.ProviderType.RETELL,
            api_key="never-should-appear-in-error",
        )
        end = _end()

        with patch.object(
            ProviderCredentials,
            "get_api_key",
            side_effect=ValueError("Failed to decrypt credentials."),
        ):
            with pytest.raises(RetellConfigurationError) as exc_info:
                ObservabilityService.fetch_retell_page(provider, None, end)

        assert str(exc_info.value) == "Retell credential could not be decrypted"
        assert "never-should-appear-in-error" not in str(exc_info.value)

    def test_missing_assistant_id_raises(self, test_project, organization, workspace):
        provider, agent = self._agent(
            test_project,
            organization,
            workspace,
            legacy_api_key="some-key",
            assistant_id="",
        )
        end = _end()

        with pytest.raises(RetellConfigurationError, match="agent id"):
            ObservabilityService.fetch_retell_page(provider, None, end)

    def test_missing_key_raises_and_key_never_in_message(
        self, test_project, organization, workspace
    ):
        provider, agent = self._agent(
            test_project, organization, workspace, legacy_api_key=""
        )
        end = _end()

        with pytest.raises(RetellConfigurationError) as exc_info:
            ObservabilityService.fetch_retell_page(provider, None, end)

        assert "key" in str(exc_info.value).lower()

    def test_missing_linked_agent_raises_configuration_error(
        self, test_project, organization, workspace
    ):
        from tracer.models.observability_provider import ObservabilityProvider

        provider = ObservabilityProvider.objects.create(
            project=test_project,
            provider=ProviderChoices.RETELL,
            enabled=True,
            organization=organization,
            workspace=workspace,
        )
        end = _end()

        with pytest.raises(RetellConfigurationError, match="linked agent"):
            ObservabilityService.fetch_retell_page(provider, None, end)


class TestGetCallLogsNoLongerServesRetell:
    def test_raises_not_implemented(self):
        provider = Mock()
        provider.provider = ProviderChoices.RETELL
        with pytest.raises(NotImplementedError):
            ObservabilityService.get_call_logs(provider, None, None)


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
    def test_fetch_retell_page_raises_when_no_agent(
        self, mock_post, retell_provider_without_agent
    ):
        """fetch_retell_page raises a typed configuration error with no linked agent."""
        from datetime import UTC, datetime

        from tracer.services.observability_providers import (
            ObservabilityService,
            RetellConfigurationError,
        )

        end = datetime(2026, 9, 3, 12, 0, 0, tzinfo=UTC)
        with pytest.raises(RetellConfigurationError, match="linked agent"):
            ObservabilityService.fetch_retell_page(
                retell_provider_without_agent, None, end
            )

        # Should not make HTTP request when key resolution fails
        mock_post.assert_not_called()

    @patch("tracer.services.observability_providers.requests.post")
    def test_fetch_retell_page_makes_request_with_valid_agent(
        self, mock_post, retell_provider_with_agent
    ):
        """fetch_retell_page makes an HTTP request when the agent has a key."""
        from datetime import UTC, datetime

        from tracer.services.observability_providers import ObservabilityService

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = list_page([], has_more=False)
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response

        end = datetime(2026, 9, 3, 12, 0, 0, tzinfo=UTC)
        page = ObservabilityService.fetch_retell_page(
            retell_provider_with_agent, None, end
        )

        mock_post.assert_called_once()
        # Verify the Authorization header contains the API key
        call_args = mock_post.call_args
        headers = call_args.kwargs.get("headers", {})
        assert headers.get("Authorization") == "Bearer test-retell-api-key-67890"
        assert page.calls == []

