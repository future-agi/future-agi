"""Uncapped application reads must not advertise bounded optimizer probes."""

from types import SimpleNamespace

import pytest

from tracer.services.clickhouse.application_read_policy import (
    supports_bounded_speculative_reads,
)
from tracer.services.clickhouse.graph_dispatch import _DeadlineBoundGraphAnalytics
from tracer.services.clickhouse.query_service import AnalyticsQueryService
from tracer.services.clickhouse.read_budget import ReadDeadline
from tracer.services.clickhouse.session_graph import _DeadlineBoundAnalytics

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("locked", [False, True])
def test_application_service_keeps_settings_and_abort_capabilities_separate(locked):
    service = AnalyticsQueryService(
        ch_client=SimpleNamespace(
            server_profile_locked=locked, server_enforced_readonly=False
        )
    )
    assert service.supports_per_query_read_settings is (not locked)
    assert service.supports_bounded_speculative_reads is False
    assert supports_bounded_speculative_reads(service) is False


@pytest.mark.parametrize(
    "wrapper", [_DeadlineBoundAnalytics, _DeadlineBoundGraphAnalytics]
)
def test_graph_wrapper_cannot_restore_removed_statement_guards(wrapper):
    delegate = SimpleNamespace(
        supports_per_query_read_settings=True, supports_bounded_speculative_reads=False
    )
    wrapped = wrapper(delegate, ReadDeadline.start(9500))
    assert wrapped.supports_per_query_read_settings is True
    assert supports_bounded_speculative_reads(wrapped) is False


@pytest.mark.parametrize("legacy", [True, False])
def test_legacy_executor_capability_remains_explicit(legacy):
    executor = SimpleNamespace(supports_per_query_read_settings=legacy)
    assert supports_bounded_speculative_reads(executor) is legacy
