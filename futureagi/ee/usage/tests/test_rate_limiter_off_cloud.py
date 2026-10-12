"""Carried-over Cloud state cannot cap a self-hosted install (TH-8084, B3/B3b).

B3: RateLimiter.check (API middleware, OTLP HTTP and gRPC ingestion) reads
PlanEntitlement rows and the ``ent:`` cache. B3b: metering.check_usage reads
billing status, plan, ``usage:`` and ``pause:`` keys. Off-cloud both allow
before touching any of that; Cloud is unchanged.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from django.test import RequestFactory

from ee.usage.models.usage import PlanEntitlement
from ee.usage.services import metering
from ee.usage.services.emitter import get_redis
from ee.usage.services.entitlements import Entitlements
from ee.usage.services.rate_limiter import RateLimiter
from tfc.capabilities import edition

pytestmark = pytest.mark.requires_ee


@pytest.fixture
def self_hosted(monkeypatch):
    monkeypatch.setattr(edition, "is_cloud", lambda: False)


@pytest.fixture
def stale_cloud_state(organization):
    """A self-host that once ran against Cloud data: a 1 rpm PlanEntitlement
    override, the matching ``ent:`` cache entries, and exhausted ``usage:``,
    ``quota:`` and ``pause:`` keys."""
    org_id = str(organization.id)
    for feature in ("api_rate_rpm", "ingestion_rate_epm"):
        PlanEntitlement.objects.create(
            feature=feature, plan="free", value_int=1, organization=organization
        )
    r = get_redis()
    keys = [
        f"ent:{org_id}:api_rate_rpm",
        f"ent:{org_id}:ingestion_rate_epm",
        f"usage:{org_id}:traces:{metering._get_current_period()}",
        f"pause:{org_id}:traces",
        f"quota:{org_id}:gateway_requests",
    ]
    r.set(keys[0], "1")
    r.set(keys[1], "1")
    r.set(keys[2], "999999999")
    r.set(keys[3], "1")
    r.set(keys[4], "999999999")
    yield org_id
    r.delete(*keys)
    for key in r.scan_iter(match=f"rl:{org_id}:*"):
        r.delete(key)


@pytest.mark.django_db
class TestRateLimiterB3:
    @pytest.mark.parametrize("limit_type", ["api", "ingestion"])
    def test_stale_entitlements_never_limit_off_cloud(
        self, self_hosted, stale_cloud_state, limit_type
    ):
        """AC-07 / B3: stale PlanEntitlement rows and ent: cache are ignored."""
        for _ in range(5):
            assert RateLimiter.check(stale_cloud_state, limit_type).allowed is True

    def test_entitlements_are_not_read_off_cloud(self, self_hosted):
        """AC-09-style: off-cloud short-circuits before Entitlements.get_limit."""
        with patch.object(
            Entitlements, "get_limit", side_effect=AssertionError("read off-cloud")
        ):
            assert RateLimiter.check("org-x", "api").allowed is True

    def test_cloud_still_limits_with_the_same_state(
        self, edition_cloud, stale_cloud_state
    ):
        """AC-13 / B3: Cloud still enforces the 1 rpm entitlement."""
        assert RateLimiter.check(stale_cloud_state, "api").allowed is True
        denied = RateLimiter.check(stale_cloud_state, "api")
        assert denied.allowed is False
        assert denied.limit == 1

    def test_api_middleware_lets_requests_through_off_cloud(
        self, self_hosted, stale_cloud_state, organization, user
    ):
        """AC-07 / B3: RateLimitMiddleware never returns 429 off-cloud."""
        from ee.usage.middleware.rate_limit import RateLimitMiddleware

        middleware = RateLimitMiddleware(
            lambda request: SimpleNamespace(status_code=200)
        )
        for _ in range(3):
            request = RequestFactory().get("/model-hub/anything/")
            request.user = user
            request.organization = organization
            assert middleware(request).status_code == 200

    def test_api_middleware_still_429s_on_cloud(
        self, edition_cloud, stale_cloud_state, organization, user
    ):
        """AC-13 / B3: Cloud middleware keeps the 429 + Retry-After."""
        from ee.usage.middleware.rate_limit import RateLimitMiddleware

        middleware = RateLimitMiddleware(
            lambda request: SimpleNamespace(status_code=200)
        )
        statuses = []
        for _ in range(2):
            request = RequestFactory().get("/model-hub/anything/")
            request.user = user
            request.organization = organization
            statuses.append(middleware(request).status_code)
        assert statuses == [200, 429]


def _grpc_export(org_id):
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
        ExportTraceServiceRequest,
    )

    from tracer.services.grpc import ObservationSpanService

    request = ExportTraceServiceRequest()
    span = request.resource_spans.add().scope_spans.add().spans.add()
    span.trace_id = bytes.fromhex("0" * 32)
    span.span_id = bytes.fromhex("0" * 15 + "1")
    span.name = "stale-state-span"
    context = MagicMock()
    context.user = MagicMock(id="00000000-0000-0000-0000-000000000456")
    context.user.organization = MagicMock(id=org_id)
    context.abort = AsyncMock()
    with (
        patch("tracer.services.grpc.bulk_create_observation_span_task") as task,
        patch("tracer.services.grpc.payload_storage.store", return_value="key"),
        patch.object(Entitlements, "get_limit", return_value=1),
    ):
        asyncio.run(ObservationSpanService().Export(request, context))
        asyncio.run(ObservationSpanService().Export(request, context))
    return task, context


class TestGrpcIngestionB3:
    def test_grpc_ingestion_persists_despite_a_stale_limit(self, self_hosted):
        """AC-07 / B3: gRPC ingestion is never RESOURCE_EXHAUSTED off-cloud."""
        task, context = _grpc_export("00000000-0000-0000-0000-00000000b301")
        assert task.apply_async.call_count == 2
        context.abort.assert_not_called()

    def test_grpc_ingestion_still_limited_on_cloud(self, edition_cloud):
        """AC-13 / B3: Cloud aborts the call over the limit."""
        org_id = "00000000-0000-0000-0000-00000000b302"
        try:
            _task, context = _grpc_export(org_id)
            assert context.abort.await_count == 1
        finally:
            r = get_redis()
            for key in r.scan_iter(match=f"rl:{org_id}:*"):
                r.delete(key)


@pytest.mark.django_db
class TestCheckUsageB3b:
    def _hard_capped_config(self):
        """A billing.yaml as Cloud ships it: a hard-capped free plan."""
        call_type = SimpleNamespace(dimension="traces", per_call=1)
        return SimpleNamespace(
            get_call_type=lambda event_type: call_type,
            get_plan=lambda plan: SimpleNamespace(usage_caps="hard"),
            get_dimension=lambda dim: SimpleNamespace(
                native_to_display_divisor=1,
                display_name="Traces",
                native_unit="traces",
            ),
            get_free_allowance=lambda dim, plan: 10,
        )

    def test_stale_usage_and_pause_keys_never_block_off_cloud(
        self, self_hosted, stale_cloud_state
    ):
        """AC-07 / AC-18 / B3b: usage:, pause: and billing state are not read."""
        with (
            patch.object(
                metering.BillingConfig, "get", return_value=self._hard_capped_config()
            ),
            patch.object(
                metering, "_get_cached_billing_status", return_value="unpaid"
            ) as status,
            patch.object(metering, "_get_cached_plan", return_value="free"),
        ):
            result = metering.check_usage(stale_cloud_state, "traces_ingested")
        assert result.allowed is True
        status.assert_not_called()

    def test_cloud_still_honours_pause_and_hard_caps(
        self, edition_cloud, stale_cloud_state
    ):
        """AC-13 / B3b: Cloud is unchanged."""
        with (
            patch.object(
                metering.BillingConfig, "get", return_value=self._hard_capped_config()
            ),
            patch.object(metering, "_get_cached_billing_status", return_value="active"),
            patch.object(metering, "_get_cached_plan", return_value="free"),
        ):
            result = metering.check_usage(stale_cloud_state, "traces_ingested")
        assert result.allowed is False
        assert result.error_code in ("FREE_TIER_LIMIT", "BUDGET_PAUSED")
