"""Positive preplanning hints must not accept inventory or reinterpret resumes."""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tracer.services.clickhouse.v2.property_catalog import source_parts, source_repair
from tracer.services.clickhouse.v2.property_catalog.coordinator import (
    FileSupersessionJournal,
)
from tracer.services.clickhouse.v2.property_catalog.dev_runtime import (
    PropertyCatalogDevRuntimeError,
    PropertyCatalogDevRuntimeFactory,
)
from tracer.services.clickhouse.v2.property_catalog.durable_lifecycle import (
    ConfiguredSourceBounds,
    LifecycleRunMode,
    PersistedReservation,
    ReservationStatus,
    WorkspaceCatalogScope,
)
from tracer.tests import test_property_catalog_durable_lifecycle as durable
from tracer.tests.test_property_catalog_dev_rollout import (
    ATTESTED_AT,
    _project_bindings,
    _provenance_observation,
    _request,
    _runtime_settings,
)
from tracer.tests.test_property_catalog_managed_runtime_activation import Driver
from tracer.tests.test_property_catalog_source_parts import NEW, OLD
from tracer.tests.test_property_catalog_source_repair import PROJECT

HISTORY_ORIGIN = datetime(1970, 1, 1, 0, 0, 0, 1, tzinfo=UTC)


def baseline(identity, *, now):
    assert not source_parts.notice_part_changes(
        **identity,
        reader=SimpleNamespace(parts_snapshot=lambda: OLD),
        project_ids=(PROJECT,),
        since=HISTORY_ORIGIN,
        until=now - timedelta(minutes=1),
        observed_at=now,
        full_replacement=True,
        started_parts=OLD,
    )
    key = (
        f"source-parts:{identity['organization_id']}:{identity['workspace_id']}:"
        f"{identity['source_database']}"
    )
    return FileSupersessionJournal(identity["state_directory"])._path(key)


@pytest.fixture
def runtime_lane(tmp_path, monkeypatch):
    """Real factory/auth/serializer; typed lifecycle evidence, external reads mocked."""
    h = SimpleNamespace(
        locked=False, events=[], inventory=_project_bindings((PROJECT,))
    )

    def authorize(ids, identity):
        h.events.append(("auth", h.locked))
        assert ids == (PROJECT,)
        return h.inventory

    runtime_settings = _runtime_settings(str(tmp_path))
    # Admit a historical window at construction; keep its authorization binding.
    runtime_settings.PROPERTY_CATALOG_DEV_SPAN_SINCE = (
        ATTESTED_AT - timedelta(hours=1)
    ).isoformat()
    factory = PropertyCatalogDevRuntimeFactory(
        settings_object=runtime_settings,
        native_client_factory=Driver,
        provenance_probe=lambda *_: _provenance_observation(),
        project_tenant_binding_probe=authorize,
        now=lambda: ATTESTED_AT,
    )
    h.runtime = factory(_request(execute=True))
    runtime = h.runtime
    h.scope = WorkspaceCatalogScope(
        runtime.bound_request.organization_id,
        runtime.bound_request.workspace_id,
        runtime.config.catalog_epoch,
        runtime.config.projection_version,
        runtime.config.project_ids,
    )
    state = durable._State()
    clock = durable._Clock(ATTESTED_AT - timedelta(minutes=2))
    bounds = ConfiguredSourceBounds(ATTESTED_AT - timedelta(days=1), clock())
    lifecycle = durable._lifecycle(
        state=state,
        clock=clock,
        freezer=durable._Freezer(clock),
        tokens=[durable.TOKEN_A, durable.TOKEN_B],
    )
    h.initial = lifecycle.prepare(
        scope=h.scope, mode=LifecycleRunMode.INITIAL_BACKFILL, configured_bounds=bounds
    )
    state.activate(h.initial, at=clock())
    h.active = state.active
    clock.current += timedelta(minutes=1)
    h.next = lifecycle.prepare(
        scope=h.scope, mode=LifecycleRunMode.AUTO, configured_bounds=bounds
    )
    state.activate(h.next, at=clock())
    h.next_active = state.active
    h.reservation = PersistedReservation(h.initial.lease, ReservationStatus.FENCED)
    monkeypatch.setattr(
        runtime.lifecycle_state, "load_nonterminal", lambda _: h.reservation
    )
    monkeypatch.setattr(
        runtime.lifecycle_state, "load_latest_active", lambda _: h.active
    )
    original_serialize = runtime.coordinator._serializer.serialize

    def serialize(key, operation):
        assert not h.locked

        def locked():
            h.locked = True
            try:
                return operation()
            finally:
                h.locked = False

        return original_serialize(key, locked)

    monkeypatch.setattr(runtime.coordinator._serializer, "serialize", serialize)
    h.event = ATTESTED_AT - timedelta(hours=2)

    def parts():
        assert h.locked and h.events[-1] == ("auth", True)
        return NEW

    h.parts = Mock(side_effect=parts)
    h.history = Mock(return_value=h.event)
    monkeypatch.setattr(runtime.span_reader, "parts_snapshot", h.parts)
    monkeypatch.setattr(runtime.span_reader, "history_in_parts", h.history)
    h.baseline = baseline(runtime._source_repair_scope(), now=ATTESTED_AT)
    h.before = h.baseline.read_bytes()
    # Enable only the existing managed-capture gate; no capture operation runs.
    object.__setattr__(runtime, "_source_capture", object())
    h.events.clear()
    yield h
    runtime.close()


@pytest.mark.parametrize("reservation", ["matching", "older", "none"])
def test_active_fenced_allows_exact_historical_probe_without_environment(
    runtime_lane, monkeypatch, reservation
):
    h = runtime_lane
    if reservation == "older":
        h.active = h.next_active
    elif reservation == "none":
        h.reservation = None

    def forbidden(*args, **kwargs):
        pytest.fail("preplanning must use its bound configuration, not environment")

    assert h.runtime._execution is None
    with monkeypatch.context() as patch:
        patch.setattr(os, "getenv", forbidden)
        patch.setattr(os.environ, "get", forbidden)
        assert h.runtime._notice_preplanning_source_part_changes(h.scope)
    assert h.events == [("auth", True)]
    h.history.assert_called_once_with(
        project_ids=h.scope.project_ids,
        since=HISTORY_ORIGIN,
        until=h.next.cutoffs.span_window.until
        if reservation == "older"
        else h.initial.cutoffs.span_window.until,
        part_names=(NEW[-1][0],),
    )
    assert h.baseline.read_bytes() == h.before
    assert h.runtime._execution is None and not h.locked


@pytest.mark.parametrize(
    "status",
    [ReservationStatus.OPEN, ReservationStatus.DRAINING, ReservationStatus.FENCED],
)
def test_actual_unfinished_reservations_skip_source_and_remain_immutable(
    runtime_lane, status
):
    h = runtime_lane
    h.reservation = PersistedReservation(h.next.lease, status)
    before = h.reservation
    assert not h.runtime._notice_preplanning_source_part_changes(h.scope)
    assert h.reservation == before
    h.parts.assert_not_called()
    h.history.assert_not_called()
    assert h.baseline.read_bytes() == h.before
    assert (
        source_repair.pending_source_repair(**h.runtime._source_repair_scope()) is None
    )


@pytest.mark.parametrize("changed", ["removed", "foreign"])
def test_exact_live_tenant_authorization_rejects_before_any_source_query(
    runtime_lane, changed
):
    h = runtime_lane
    # The prior check succeeds; ownership changes before the locked hook recheck.
    h.runtime._refresh_project_tenant_authorization()
    h.inventory = (
        _project_bindings(())
        if changed == "removed"
        else _project_bindings((PROJECT,), organization_id=durable.TOKEN_C)
    )
    with pytest.raises(PropertyCatalogDevRuntimeError, match="ownership|owned"):
        h.runtime._notice_preplanning_source_part_changes(h.scope)
    h.parts.assert_not_called()
    h.history.assert_not_called()
    assert h.baseline.read_bytes() == h.before
    assert not h.locked


def test_auto_records_positive_then_selects_full_before_prepare(
    runtime_lane, monkeypatch
):
    h = runtime_lane

    class Prepared(Exception):
        pass

    def prepare(**kwargs):
        assert not h.locked, "lifecycle must acquire its own locks after observation"
        assert kwargs["scope"] == h.scope
        assert kwargs["mode"] is LifecycleRunMode.FULL_REPAIR
        notice = source_repair.pending_source_repair(**h.runtime._source_repair_scope())
        assert notice is not None and h.runtime._source_repair.raw == notice.raw
        assert not Path(str(notice.path) + ".ack").exists()
        h.events.append(("prepare", False))
        raise Prepared

    monkeypatch.setattr(h.runtime.lifecycle, "prepare", prepare)
    with pytest.raises(Prepared):
        h.runtime._prepare_revision(LifecycleRunMode.AUTO)
    assert h.history.call_count == 1 and h.events[-1] == ("prepare", False)
    assert h.baseline.read_bytes() == h.before
