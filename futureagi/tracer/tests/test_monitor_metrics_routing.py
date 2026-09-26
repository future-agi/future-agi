"""MONITOR_METRICS v1/v2 dispatch across routing modes."""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.test import override_settings

from tracer.services.clickhouse.query_builders.monitor_metrics import (
    MonitorMetricsQueryBuilder,
)
from tracer.services.clickhouse.v2.dispatch import get_query_builder_class
from tracer.services.clickhouse.v2.query_builders.monitor_metrics import (
    MonitorMetricsQueryBuilderV2,
)


def _routing(**overrides: Any) -> Any:
    # Preserve non-routing keys (connection config) from the real settings.
    base = {
        **settings.CLICKHOUSE_V2,
        "QUERY_TYPES_V2_PRIMARY": "",
        "QUERY_TYPES_V2_ONLY": "",
        "QUERY_TYPES_SHADOW": "",
        "QUERY_TYPES_DISABLED": "",
    }
    base.update(overrides)
    return override_settings(CLICKHOUSE_V2=base)


def test_default_routes_v1() -> None:
    with _routing():
        assert get_query_builder_class("MONITOR_METRICS") is MonitorMetricsQueryBuilder


def test_shadow_still_returns_v1() -> None:
    with _routing(QUERY_TYPES_SHADOW="monitor_metrics"):
        assert get_query_builder_class("MONITOR_METRICS") is MonitorMetricsQueryBuilder


def test_v2_primary_routes_v2() -> None:
    with _routing(QUERY_TYPES_V2_PRIMARY="monitor_metrics"):
        assert (
            get_query_builder_class("MONITOR_METRICS") is MonitorMetricsQueryBuilderV2
        )


def test_v2_only_routes_v2() -> None:
    with _routing(QUERY_TYPES_V2_ONLY="monitor_metrics"):
        assert (
            get_query_builder_class("MONITOR_METRICS") is MonitorMetricsQueryBuilderV2
        )
