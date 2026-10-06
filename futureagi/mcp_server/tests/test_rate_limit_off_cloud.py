"""MCP commercial rate tiers are Cloud-only (TH-8084, A5).

Self-hosted: no per-org MCP call ceiling (review C6), and the subscription is
never read. Cloud keeps the 200/min and 5,000/day Free tier. Response size and
depth bounds stay on everywhere.
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest
from django.core.cache import cache

from mcp_server import rate_limiter
from mcp_server.constants import RATE_LIMITS
from mcp_server.exceptions import RateLimitExceededError
from mcp_server.rate_limiter import enforce_commercial_rate_limit
from mcp_server.response_limits import (
    MAX_RESPONSE_BYTES,
    ResponseTooLargeError,
    bounded_response,
)
from tfc.capabilities import edition

FREE = RATE_LIMITS["free"]


@pytest.fixture
def self_hosted(monkeypatch):
    monkeypatch.setattr(edition, "is_cloud", lambda: False)


@pytest.fixture
def org():
    org = SimpleNamespace(id=uuid.uuid4())
    yield org
    cache.delete_many([f"mcp_rl:min:{org.id}", f"mcp_rl:day:{org.id}"])


def _fill_day(org):
    cache.set(f"mcp_rl:day:{org.id}", FREE["per_day"], timeout=3600)


class TestEnforceCommercialRateLimit:
    def test_self_hosted_allows_the_201st_call_in_a_minute(
        self, self_hosted, org, monkeypatch
    ):
        """AC-06 / A5: no per-minute ceiling off-cloud."""
        tier_reads = []
        monkeypatch.setattr(
            rate_limiter, "get_rate_limit_tier", lambda o: tier_reads.append(o)
        )
        for _ in range(FREE["per_minute"] + 1):
            enforce_commercial_rate_limit(org)
        assert tier_reads == [], "the subscription tier must not be read off-cloud"

    def test_self_hosted_allows_the_5001st_call_in_a_day(self, self_hosted, org):
        """AC-06 / A5: no per-day ceiling off-cloud, even with a full counter."""
        _fill_day(org)
        enforce_commercial_rate_limit(org)

    @pytest.mark.django_db
    def test_self_hosted_never_queries_the_subscription(self, self_hosted, org):
        """AC-09-style: OrganizationSubscription is not read off-cloud."""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as ctx:
            enforce_commercial_rate_limit(org)
        assert ctx.captured_queries == []

    def test_cloud_still_refuses_the_201st_call_in_a_minute(
        self, edition_cloud, org, monkeypatch
    ):
        """AC-13 / A5: the Cloud Free tier keeps 200 calls/minute."""
        monkeypatch.setattr(rate_limiter, "get_rate_limit_tier", lambda o: "free")
        for _ in range(FREE["per_minute"]):
            enforce_commercial_rate_limit(org)
        with pytest.raises(RateLimitExceededError, match="calls/minute"):
            enforce_commercial_rate_limit(org)

    def test_cloud_still_refuses_the_5001st_call_in_a_day(
        self, edition_cloud, org, monkeypatch
    ):
        """AC-13 / A5: the Cloud Free tier keeps 5,000 calls/day."""
        monkeypatch.setattr(rate_limiter, "get_rate_limit_tier", lambda o: "free")
        _fill_day(org)
        with pytest.raises(RateLimitExceededError, match="calls/day"):
            enforce_commercial_rate_limit(org)


class TestStreamableToolCall:
    """The MCP protocol path (mcp_app.call_generated_tool)."""

    class _PastRateLimit(Exception):
        pass

    def _call(self, monkeypatch, org):
        from mcp_server import mcp_app, usage_helpers

        monkeypatch.setattr(
            mcp_app,
            "_current_context",
            lambda: SimpleNamespace(organization=org, user=None, workspace=None),
        )
        monkeypatch.setattr(
            mcp_app, "_enabled_tools_for_context", lambda ctx: (None, {"whoami"})
        )

        def _stop(*args, **kwargs):
            raise self._PastRateLimit

        monkeypatch.setattr(usage_helpers, "get_or_create_session", _stop)
        return asyncio.run(mcp_app.call_generated_tool("whoami", {}))

    def test_self_hosted_tool_call_skips_the_commercial_limiter(
        self, self_hosted, org, monkeypatch
    ):
        """AC-06 / A5: the protocol path never consults the commercial tiers."""

        def _never(*args, **kwargs):
            raise AssertionError("commercial MCP limiter consulted off-cloud")

        monkeypatch.setattr(rate_limiter, "check_rate_limit", _never)
        monkeypatch.setattr(rate_limiter, "get_rate_limit_tier", _never)
        with pytest.raises(self._PastRateLimit):
            self._call(monkeypatch, org)

    def test_cloud_tool_call_is_still_rate_limited(
        self, edition_cloud, org, monkeypatch
    ):
        """AC-13 / A5: Cloud keeps RATE_LIMITED on the protocol path."""
        monkeypatch.setattr(rate_limiter, "get_rate_limit_tier", lambda o: "free")

        def _exceeded(*args, **kwargs):
            raise RateLimitExceededError("Rate limit exceeded", retry_after=5)

        monkeypatch.setattr(rate_limiter, "check_rate_limit", _exceeded)
        result = self._call(monkeypatch, org)
        assert result.isError is True
        assert result.structuredContent["error"]["code"] == "RATE_LIMITED"


@pytest.mark.django_db
class TestInternalTransport:
    """The internal HTTP transport (mcp_server/views/transport.py)."""

    URL = "/mcp/internal/tool-call/"

    def test_self_hosted_call_over_the_free_tier_succeeds(
        self, self_hosted, auth_client, user
    ):
        """AC-06 / A5: a call past 5,000/day is served off-cloud."""
        cache.set(f"mcp_rl:day:{user.organization.id}", FREE["per_day"], timeout=3600)
        try:
            response = auth_client.post(
                self.URL, {"tool_name": "whoami", "params": {}}, format="json"
            )
        finally:
            cache.delete(f"mcp_rl:day:{user.organization.id}")
        assert response.status_code == 200, response.content

    def test_cloud_call_over_the_free_tier_is_429(
        self, edition_cloud, auth_client, user
    ):
        """AC-13 / A5: Cloud returns 429 with Retry-After as before."""
        cache.set(f"mcp_rl:day:{user.organization.id}", FREE["per_day"], timeout=3600)
        try:
            response = auth_client.post(
                self.URL, {"tool_name": "whoami", "params": {}}, format="json"
            )
        finally:
            cache.delete(f"mcp_rl:day:{user.organization.id}")
        assert response.status_code == 429, response.content
        assert "Retry-After" in response


class TestResponseBoundsStillApply:
    def test_oversized_response_is_rejected_off_cloud(self, self_hosted):
        """AC-06: payload bounds are a safety control and stay on."""
        with pytest.raises(ResponseTooLargeError):
            bounded_response({"rows": list(range(MAX_RESPONSE_BYTES + 1))})

    def test_long_blob_is_previewed_off_cloud(self, self_hosted):
        """AC-06: long blob strings are still truncated to a preview."""
        result = bounded_response({"output": "x" * 10_000})
        assert len(str(result)) < 10_000

    def test_deep_nesting_is_rejected_off_cloud(self, self_hosted):
        """AC-06: depth bound stays on."""
        nested: dict = {}
        cursor = nested
        for _ in range(150):
            cursor["child"] = {}
            cursor = cursor["child"]
        with pytest.raises(ResponseTooLargeError):
            bounded_response(nested)
