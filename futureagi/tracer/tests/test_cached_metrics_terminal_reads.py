"""Late complete compatibility catalogs remain usable; misses need admission."""
# ruff: noqa: F811

from contextlib import nullcontext
from inspect import unwrap
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tracer.services import dashboard_metrics_catalog as catalog
from tracer.services.clickhouse.read_budget import ReadDeadlineExceeded
from tracer.tests.test_completed_terminal_reads import clock as clock
from tracer.views import dashboard

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("rows", [[], [{"name": "owned"}]])
@pytest.mark.parametrize(
    "phase",
    ["hit", "build", "disabled", "set", "set_error", "build_error", "query_error"],
)
def test_cached_catalog_late_complete_or_failure(monkeypatch, clock, rows, phase):
    deadline, expire = clock
    error = (
        catalog.MetricsCatalogUnavailable("owned")
        if phase == "build_error"
        else RuntimeError("query failed")
    )

    def get(_key):
        if phase == "hit":
            expire()
            return rows
        return None

    def build(*args, **kwargs):
        assert kwargs["deadline"] is deadline
        if phase in {"build", "disabled", "build_error", "query_error"}:
            expire()
        if phase in {"build_error", "query_error"}:
            raise error
        return rows

    def put(_key, value, *, timeout):
        assert value is rows and timeout == 37
        if phase in {"set", "set_error"}:
            expire()
        if phase == "set_error":
            raise RuntimeError("cache write failed")

    cache = SimpleNamespace(get=Mock(side_effect=get), set=Mock(side_effect=put))
    builder = Mock(side_effect=build)
    monkeypatch.setattr(catalog, "cache", cache)
    monkeypatch.setattr(
        catalog, "_can_use_metrics_catalog_cache", lambda: phase != "disabled"
    )
    monkeypatch.setattr(catalog, "build_metrics_catalog", builder)
    failed = phase in {"build_error", "query_error"}
    with pytest.raises(type(error)) if failed else nullcontext() as caught:
        result = catalog.get_cached_metrics_catalog(
            SimpleNamespace(id="owned-workspace"),
            project_ids_param="owned-project",
            deadline=deadline,
            ttl=37,
        )
        assert result is rows
    assert builder.call_count == (0 if phase == "hit" else 1)
    assert cache.get.call_count == (0 if phase == "disabled" else 1)
    assert cache.set.call_count == (0 if failed or phase in {"hit", "disabled"} else 1)
    if cache.set.called:
        assert cache.set.call_args.args[0] == cache.get.call_args.args[0]
    if failed:
        assert caught.value is error


@pytest.mark.parametrize("phase", ["miss", "get_error", "disabled"])
def test_late_cache_miss_cannot_start_required_build(monkeypatch, clock, phase):
    deadline, expire = clock

    def get(_key):
        expire()
        if phase == "get_error":
            raise RuntimeError("cache read failed")
        return None

    def eligible():
        if phase == "disabled":
            expire()
            return False
        return True

    cache = SimpleNamespace(get=Mock(side_effect=get), set=Mock())
    builder = Mock()
    monkeypatch.setattr(catalog, "cache", cache)
    monkeypatch.setattr(catalog, "_can_use_metrics_catalog_cache", eligible)
    monkeypatch.setattr(catalog, "build_metrics_catalog", builder)
    with pytest.raises(ReadDeadlineExceeded):
        catalog.get_cached_metrics_catalog(
            SimpleNamespace(id="owned"), deadline=deadline
        )
    builder.assert_not_called()
    cache.set.assert_not_called()


@pytest.mark.parametrize("rows", [[], [{"name": "owned"}]])
@pytest.mark.parametrize("failed", [False, True])
def test_deprecated_catalog_late_result_or_real_failure(
    monkeypatch, clock, rows, failed
):
    deadline, expire = clock

    def read(*args, **kwargs):
        assert kwargs["deadline"] is deadline
        expire()
        if failed:
            raise catalog.MetricsCatalogUnavailable("owned")
        return rows

    monkeypatch.setattr(dashboard.ReadDeadline, "start", lambda _wall: deadline)
    monkeypatch.setattr(dashboard, "get_cached_metrics_catalog", read)
    page_builder = Mock()
    monkeypatch.setattr(dashboard, "build_metrics_catalog_page", page_builder)
    request = SimpleNamespace(
        workspace=SimpleNamespace(id="owned"),
        query_params={},
        validated_query_data={"project_ids": [], "exclude_custom_attributes": True},
    )
    response = unwrap(dashboard.DashboardViewSet.metrics)(
        dashboard.DashboardViewSet(), request
    )
    page_builder.assert_not_called()
    if failed:
        assert (
            response.status_code == 503
            and response.data["code"] == "service_unavailable"
        )
    else:
        assert response.status_code == 200 and response.data["result"] == {
            "metrics": rows
        }
        assert response["Deprecation"] == "true"
