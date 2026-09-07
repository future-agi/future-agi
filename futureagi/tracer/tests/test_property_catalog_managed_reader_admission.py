from __future__ import annotations

from types import SimpleNamespace

import pytest

from tfc.settings.settings import (
    property_catalog_managed_oss_reads,
    property_catalog_reads_all_workspaces,
)
from tracer.services.clickhouse.v2.property_catalog.activation_control import (
    ActivationControlBootstrapPending,
    ActivationControlUnavailable,
    activation_control_selector_for_deployment,
)
from tracer.services.clickhouse.v2.property_catalog.connection import (
    PropertyCatalogConnectionConfig,
)
from tracer.services.clickhouse.v2.property_catalog.reader import (
    PropertyCatalogBootstrapPending,
    PropertyCatalogReader,
    PropertyCatalogUnavailable,
)
from tracer.services.clickhouse.v2.property_catalog.value_reader import (
    PropertyCatalogValuePending,
    PropertyCatalogValueReader,
    PropertyCatalogValueUnavailable,
)

DATABASE = "property_catalog_dev_oss"
SCOPE = {"organization_id": "00000000-0000-0000-0000-000000000001", "workspace_id": "00000000-0000-0000-0000-000000000002"}


def settings_for_oss(**overrides):
    values = {
        "PROPERTY_CATALOG_READ_MODE": "managed", "PROPERTY_CATALOG_READ_DEPLOYMENT": "dev",
        "ENV_TYPE": "local", "CLOUD_DEPLOYMENT": "",
        "PROPERTY_CATALOG_DATABASE": DATABASE, "PROPERTY_CATALOG_CH_HOST": "clickhouse",
        "PROPERTY_CATALOG_CH_PORT": 9000, "PROPERTY_CATALOG_CH_USER": "property_catalog_oss_api",
        "PROPERTY_CATALOG_CH_PASSWORD": "test-only", "PROPERTY_CATALOG_DEV_READ_ACK": "",
        "PROPERTY_CATALOG_PROD_READ_ACK": "", "PROPERTY_CATALOG_DEV_WORKSPACE_ALLOWLIST": (),
        "PROPERTY_CATALOG_PROD_WORKSPACE_ALLOWLIST": (), "PROPERTY_CATALOG_PROD_WORKSPACE_SCOPE_MODE": "allowlist",
        "CLICKHOUSE_V2": {"CH25_USER": "default"}, "CLICKHOUSE": {"CH_USERNAME": "default"},
        # Stale legacy values deliberately do not participate in read binding.
        "PROPERTY_CATALOG_DEV_CATALOG_EPOCH": 65535, "PROPERTY_CATALOG_DEV_PROJECTION_VERSION": 1,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_managed_oss_admission_needs_no_allowlist_ack_or_version_setting():
    source = settings_for_oss()
    assert PropertyCatalogConnectionConfig.from_settings(source).database == DATABASE
    assert property_catalog_managed_oss_reads(source)
    assert property_catalog_reads_all_workspaces(source)


@pytest.mark.parametrize("overrides", [
    {"ENV_TYPE": "production"}, {"CLOUD_DEPLOYMENT": "US"}, {"CLOUD_DEPLOYMENT": "DEV"},
    {"PROPERTY_CATALOG_DATABASE": "default"}, {"PROPERTY_CATALOG_DATABASE": "property_catalog"},
    {"PROPERTY_CATALOG_CH_USER": "default"}, {"PROPERTY_CATALOG_CH_PASSWORD": ""},
    {"PROPERTY_CATALOG_PROD_READ_ACK": "anything"}, {"PROPERTY_CATALOG_DEV_WORKSPACE_ALLOWLIST": ("workspace",)},
])
def test_managed_admission_keeps_isolation_and_rejects_cross_wiring(overrides):
    with pytest.raises(ValueError):
        PropertyCatalogConnectionConfig.from_settings(settings_for_oss(**overrides))


class Executor:
    def __init__(self, results):
        self.results, self.calls = list(results), []

    def execute(self, sql, params, **kwargs):
        self.calls.append((sql, params, kwargs))
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return SimpleNamespace(data=result)


def selector(executor):
    return activation_control_selector_for_deployment(executor, database=DATABASE, deployment="dev", managed=True)


def test_fresh_empty_workspace_is_typed_bootstrap_not_fake_active_catalog():
    executor = Executor([[], [], []])
    with pytest.raises(ActivationControlBootstrapPending):
        selector(executor).select_target(scope=SCOPE, timeout_ms=100)
    assert len(executor.calls) == 3
    assert all(call[1]["catalog_workspace_id"] == SCOPE["workspace_id"] for call in executor.calls)
    assert "property_catalog_activations" in executor.calls[1][0]


@pytest.mark.parametrize("history", [[{"catalog_history_exists": 1}], [{"bad": 1}], RuntimeError("offline")])
def test_missing_control_with_history_or_failed_proof_never_falls_back(history):
    with pytest.raises(ActivationControlUnavailable) as caught:
        selector(Executor([[], history, []])).select_target(scope=SCOPE, timeout_ms=100)
    assert not isinstance(caught.value, ActivationControlBootstrapPending)


@pytest.mark.parametrize("reason", ["control_disabled", "control_invalid", "control_missing"])
def test_only_proven_bootstrap_can_enter_reader_compatibility(reason):
    def fail(**kwargs):
        raise ActivationControlUnavailable(reason)
    reader = PropertyCatalogReader(Executor([]), catalog_database=DATABASE, activation_selector=SimpleNamespace(select_target=fail))
    with pytest.raises(PropertyCatalogUnavailable) as caught:
        reader._activation(scope=SCOPE, cursor=None, budget=SimpleNamespace(remaining_ms=lambda: 100))
    assert not isinstance(caught.value, PropertyCatalogBootstrapPending)


@pytest.mark.parametrize("reader_class,error_class", [(PropertyCatalogReader, PropertyCatalogBootstrapPending), (PropertyCatalogValueReader, PropertyCatalogValuePending)])
def test_bootstrap_exit_is_first_page_only(reader_class, error_class):
    def fail(**kwargs):
        raise ActivationControlBootstrapPending("control_bootstrap_pending")
    reader = reader_class(Executor([]), catalog_database=DATABASE, activation_selector=SimpleNamespace(select_target=fail))
    with pytest.raises(error_class):
        reader._activation(scope=SCOPE, cursor=None, budget=SimpleNamespace(remaining_ms=lambda: 100))
    with pytest.raises((PropertyCatalogUnavailable, PropertyCatalogValueUnavailable)) as caught:
        reader._activation(scope=SCOPE, cursor=object(), budget=SimpleNamespace(remaining_ms=lambda: 100))
    assert not isinstance(caught.value, error_class)
