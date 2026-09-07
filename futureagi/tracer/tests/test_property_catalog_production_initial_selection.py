"""Production onboarding uses the same qualified pending-control contract."""

from dataclasses import asdict

import pytest

from tracer.services.clickhouse.v2.property_catalog import activation_control as control
from tracer.services.clickhouse.v2.property_catalog.dev_runtime import (
    PropertyCatalogDevRuntimeError,
)
from tracer.tests.test_property_catalog_initial_reader_publication import (
    ReaderExecutor,
    manual_event,
)
from tracer.tests.test_property_catalog_managed_runtime_activation import (
    publication_runtime,
)
from tracer.tests.test_property_catalog_reader_activation import SCOPE, target


def production_runtime(monkeypatch, tmp_path, *, managed=True):
    runtime, execution, client, steps = publication_runtime(
        monkeypatch, tmp_path, callback_enabled=managed
    )
    database = "isolated_catalog_initial_test"
    monkeypatch.setenv("PROPERTY_CATALOG_PRODUCTION_DATABASE", database)
    runtime.config.deployment = client.deployment = "prod"
    client.catalog_database = database
    return runtime, execution, client, steps


def test_production_qualified_initial_is_pending_then_readable_without_ui_versions(
    monkeypatch, tmp_path
):
    runtime, _, client, steps = production_runtime(monkeypatch, tmp_path)
    prepared = runtime.reconcile_reader_selection(prepare_initial=True)
    assert prepared.status == "prepared" and prepared.selected_target is None
    assert steps == ["prepare_follow"] and len(client.events) == 1
    selector = control.ClickHouseActivationControlSelector(
        ReaderExecutor(client), database=client.catalog_database, deployment="prod"
    )
    with pytest.raises(control.ActivationControlBootstrapPending):
        selector.select_for_read(scope=asdict(SCOPE), timeout_ms=1000)
    client.targets = [target()]
    selected = selector.select_for_read(scope=asdict(SCOPE), timeout_ms=1000)
    assert selected.target == target() and selected.follows_latest


@pytest.mark.parametrize(
    "bad",
    ["unqualified", "missing_fence", "manifest_mismatch", "prior_active", "status"],
)
def test_production_does_not_publish_unqualified_or_read_only_initial(
    monkeypatch, tmp_path, bad
):
    runtime, execution, client, _ = production_runtime(monkeypatch, tmp_path)
    if bad == "unqualified":
        execution.qualification.qualified = False
    elif bad == "missing_fence":
        execution.fence = None
    elif bad == "manifest_mismatch":
        execution.manifest.sha256 = "d" * 64
    elif bad == "prior_active":
        execution.prepared.prior_active = object()
    else:
        runtime.bound_request.status = True
    with pytest.raises(PropertyCatalogDevRuntimeError):
        runtime.reconcile_reader_selection(prepare_initial=True)
    assert not client.events and not client.attempts


def test_production_initial_does_not_invent_missing_control_for_existing_history(
    monkeypatch, tmp_path
):
    runtime, _, client, _ = production_runtime(monkeypatch, tmp_path)
    client.targets = [target()]
    with pytest.raises(control.ActivationControlRejected, match="history_not_empty"):
        runtime.reconcile_reader_selection(prepare_initial=True)
    assert not client.events and not client.attempts


@pytest.mark.parametrize("action", ["disable", "rollback"])
def test_production_initial_recovery_preserves_operator_override(
    monkeypatch, tmp_path, action
):
    runtime, _, client, _ = production_runtime(monkeypatch, tmp_path)
    initial = runtime.reconcile_reader_selection(prepare_initial=True).event
    client.events.append(manual_event(initial, action).as_row())
    result = runtime.reconcile_reader_selection(prepare_initial=True)
    assert result.status == action and len(client.attempts) == 1


def test_unmanaged_production_runtime_does_not_auto_prepare(monkeypatch, tmp_path):
    runtime, _, client, _ = production_runtime(monkeypatch, tmp_path, managed=False)
    assert runtime.reconcile_reader_selection(prepare_initial=True) is None
    assert not client.events and not client.attempts
