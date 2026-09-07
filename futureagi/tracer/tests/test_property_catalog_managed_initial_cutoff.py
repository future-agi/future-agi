"""Managed discovery freezes now; manual and persisted source windows stay exact."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from tracer.services.clickhouse.v2.property_catalog.dev_runtime import (
    CheckedInPropertyCatalogDevRuntime,
    PropertyCatalogDevRuntimeFactory,
)
from tracer.services.clickhouse.v2.property_catalog.durable_lifecycle import (
    DurableLifecycleError,
    LifecycleRunMode,
    PersistedReservation,
    ReservationStatus,
)
from tracer.services.clickhouse.v2.property_catalog.span_source import FrozenSpanSource
from tracer.tests import test_property_catalog_durable_lifecycle as durable
from tracer.tests.test_property_catalog_dev_rollout import (
    _project_bindings,
    _provenance_observation,
    _request,
    _runtime_settings,
)
from tracer.tests.test_property_catalog_managed_runtime_activation import Driver

NOW = datetime(2026, 8, 14, 13, 16, 37, 123456, tzinfo=UTC)


class PreparedBoundary(Exception):
    """Stop after real lifecycle allocation, before capture/stream side effects."""


@pytest.fixture
def lane(tmp_path, monkeypatch):
    state = durable._State()
    coordinator = durable._Coordinator(state)
    clock = durable._Clock(NOW)
    h = SimpleNamespace(
        state=state,
        coordinator=coordinator,
        clock=clock,
        freezes=[],
        prepared=None,
        bounds=None,
        runtimes=[],
    )
    tokens = iter((durable.TOKEN_A, durable.TOKEN_B, durable.TOKEN_C))
    # Recovery and selection have dedicated composed tests. This lane keeps real
    # factory authorization, runtime caller, FreshCutoffFreezer and lifecycle.
    for method, result in (
        ("_recover_native_writes", ()),
        ("_recover_fenced_scope_drift", False),
        ("_recover_completed_capture_retirement", ()),
        ("_notice_preplanning_source_part_changes", False),
    ):
        monkeypatch.setattr(
            CheckedInPropertyCatalogDevRuntime, method, lambda *_, result=result: result
        )

    def make(*, managed=True):
        runtime = PropertyCatalogDevRuntimeFactory(
            settings_object=_runtime_settings(str(tmp_path)),
            native_client_factory=Driver,
            provenance_probe=lambda *_: _provenance_observation(),
            project_tenant_binding_probe=lambda ids, _: _project_bindings(ids),
            now=clock,
            new_build_token=lambda: next(tokens),
        )(_request(execute=True))
        h.runtimes.append(runtime)
        # No physical capture is made: stop immediately after preparing its lease.
        # Presence models the admitted factory's existing managed-only capability.
        object.__setattr__(runtime, "_source_capture", object() if managed else None)
        runtime.lifecycle._state_reader = state
        runtime.lifecycle._coordinator = coordinator

        def freeze(**kwargs):
            h.freezes.append(kwargs)
            return FrozenSpanSource(**kwargs, audit_generation=1000 + len(h.freezes))

        monkeypatch.setattr(runtime.span_reader, "freeze", freeze)
        monkeypatch.setattr(
            runtime.span_reader, "retained_since", lambda **kw: kw["fallback"]
        )
        prepare = runtime.lifecycle.prepare

        def prepare_and_stop(**kwargs):
            h.bounds = kwargs["configured_bounds"]
            h.prepared = prepare(**kwargs)
            raise PreparedBoundary

        monkeypatch.setattr(runtime.lifecycle, "prepare", prepare_and_stop)
        return runtime

    h.make = make
    yield h
    for runtime in h.runtimes:
        runtime.close()


@pytest.mark.parametrize("managed", [True, False])
def test_new_initial_freezes_current_microsecond_only_for_managed(lane, managed):
    h = lane
    runtime = h.make(managed=managed)
    configured = runtime.config.span_until
    assert configured == NOW.replace(minute=0, second=0, microsecond=0)
    with pytest.raises(PreparedBoundary):
        # Actual inactive supervisor uses this public INITIAL entrypoint.
        runtime.backfill(runtime.bound_request)
    prepared = h.prepared
    expected = NOW if managed else configured
    assert prepared.mode is LifecycleRunMode.INITIAL_BACKFILL and not prepared.resumed
    assert prepared.cutoffs.snapshot_upper == expected
    assert prepared.cutoffs.span_window.until == expected
    assert prepared.cutoffs.span_window.since == runtime.config.span_since
    assert h.freezes == [
        {
            "project_ids": runtime.config.project_ids,
            "since": runtime.config.span_since,
            "until": expected,
        }
    ]
    assert prepared.lease.build_plan.source_scope.span_until_us == int(
        expected.timestamp() * 1_000_000
    )
    assert prepared.cutoffs.span_audit_generation == 1001
    assert runtime.config.span_until == configured  # Never rewrite admitted config.
    # The 13:16 import is inside the new initial interval; >= cutoff stays out.
    events = (NOW - timedelta(seconds=1), NOW, NOW + timedelta(microseconds=1))
    selected = tuple(
        t for t in events if prepared.cutoffs.span_window.since <= t < expected
    )
    assert selected == (events[:1] if managed else ())


@pytest.mark.parametrize("managed", [True, False])
def test_auto_still_requires_existing_active_catalog(lane, managed):
    runtime = lane.make(managed=managed)
    with pytest.raises(
        DurableLifecycleError, match="auto lifecycle requires an active"
    ):
        runtime._prepare_revision(LifecycleRunMode.AUTO)
    assert not lane.freezes and lane.coordinator.allocate_calls == 0


@pytest.mark.parametrize("status", list(ReservationStatus))
def test_restart_keeps_original_plan_cutoffs_and_audit_without_refreezing(lane, status):
    h = lane
    first_runtime = h.make()
    with pytest.raises(PreparedBoundary):
        first_runtime.backfill(first_runtime.bound_request)
    first = h.prepared
    h.state.reservation = PersistedReservation(first.lease, status)
    if status is ReservationStatus.FENCED:
        h.state.resumes = durable._complete_checkpoints(first)
    h.clock.current += timedelta(minutes=2)
    restarted = h.make()
    with pytest.raises(PreparedBoundary):
        restarted.backfill(restarted.bound_request)
    assert h.bounds.initial_until == h.clock()
    assert h.prepared.resumed and h.prepared.lease == first.lease
    assert h.prepared.lease.build_plan_json == first.lease.build_plan_json
    assert h.prepared.cutoffs == first.cutoffs and len(h.freezes) == 1


@pytest.mark.parametrize("mode", [LifecycleRunMode.AUTO, LifecycleRunMode.FULL_REPAIR])
def test_active_revision_keeps_its_existing_fresh_freezer_contract(lane, mode):
    h = lane
    runtime = h.make()
    with pytest.raises(PreparedBoundary):
        runtime.backfill(runtime.bound_request)
    first = h.prepared
    h.state.activate(first, at=h.clock())
    h.clock.current += timedelta(minutes=2)
    restarted = h.make()
    with pytest.raises(PreparedBoundary):
        restarted._prepare_revision(mode)
    assert h.prepared.cutoffs.snapshot_upper == h.clock()
    assert h.prepared.lease.build_token != first.lease.build_token
    assert h.prepared.cutoffs.span_window.since == (
        first.cutoffs.span_window.until
        if mode is LifecycleRunMode.AUTO
        else restarted.config.span_since
    )
    assert len(h.freezes) == 2
