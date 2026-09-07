"""Retained-history cutoff admission without source IO or new plan fields."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

import pytest

from tracer.services.clickhouse.v2.property_catalog.activation import RevisionBuildPlan
from tracer.services.clickhouse.v2.property_catalog.durable_lifecycle import (
    DurableLifecycleError,
    FreshSpanLifecycleCutoffFreezer,
    LifecycleRunMode,
    SourceWindow,
)
from tracer.services.clickhouse.v2.property_catalog.span_source import FrozenSpanSource
from tracer.tests.test_property_catalog_durable_lifecycle import (
    INITIAL_SINCE,
    INITIAL_UNTIL,
    TOKEN_A,
    TOKEN_B,
    _bounds,
    _Clock,
    _Freezer,
    _lifecycle,
    _scope,
    _State,
)

OLDEST = datetime(2021, 2, 3, 4, 5, 6, 123456, tzinfo=UTC)
REPAIR_MODES = (LifecycleRunMode.INITIAL_BACKFILL, LifecycleRunMode.FULL_REPAIR)


class CapturedReader:
    """Simulates only the explicit, parent-owned captured-reader capability."""

    def __init__(self, lower=OLDEST):
        self.lower = lower
        self.calls = []

    def retained_since(self, *, project_ids, until, fallback):
        self.calls.append(("retained", project_ids, until, fallback))
        return self.lower

    def freeze(self, *, project_ids, since, until):
        self.calls.append(("freeze", project_ids, since, until))
        return FrozenSpanSource(project_ids, since, until, 987)


def freeze(reader, mode, *, since=INITIAL_SINCE, until=INITIAL_UNTIL):
    return FreshSpanLifecycleCutoffFreezer(reader, now=lambda: until)(
        scope=_scope(),
        mode=mode,
        span_since=since,
        configured_until=until if mode is LifecycleRunMode.INITIAL_BACKFILL else None,
        prior_active=None,
    )


@pytest.mark.parametrize("mode", REPAIR_MODES)
def test_captured_earliest_interval_widens_initial_and_full_only(mode):
    reader = CapturedReader()
    cutoffs = freeze(reader, mode)
    assert cutoffs.span_window == SourceWindow(OLDEST, INITIAL_UNTIL)
    assert cutoffs.span_audit_generation == 987
    assert reader.calls == [
        ("retained", _scope().project_ids, INITIAL_UNTIL, INITIAL_SINCE),
        ("freeze", _scope().project_ids, OLDEST, INITIAL_UNTIL),
    ]


@pytest.mark.parametrize("mode", REPAIR_MODES)
@pytest.mark.parametrize("lower", [INITIAL_SINCE, INITIAL_SINCE + timedelta(days=1)])
def test_capture_discovery_never_narrows_requested_window(mode, lower):
    cutoffs = freeze(CapturedReader(lower), mode)
    assert cutoffs.span_window == SourceWindow(INITIAL_SINCE, INITIAL_UNTIL)


def test_incremental_never_uses_retained_history_capability():
    class Reader(CapturedReader):
        def retained_since(self, **kwargs):
            raise AssertionError("incremental cannot rediscover or widen history")

    reader = Reader()
    assert (
        freeze(reader, LifecycleRunMode.INCREMENTAL).span_window.since == INITIAL_SINCE
    )
    assert reader.calls == [
        ("freeze", _scope().project_ids, INITIAL_SINCE, INITIAL_UNTIL)
    ]


@pytest.mark.parametrize("mode", REPAIR_MODES)
def test_reader_without_discovery_preserves_wide_requested_history(mode):
    class Reader:
        def freeze(self, *, project_ids, since, until):
            assert since == OLDEST
            return FrozenSpanSource(project_ids, since, until, 123)

    assert freeze(Reader(), mode, since=OLDEST).span_window == SourceWindow(
        OLDEST, INITIAL_UNTIL
    )


@pytest.mark.parametrize(
    "lower",
    [
        None,
        "2021-01-01",
        True,
        OLDEST.replace(tzinfo=None),
        OLDEST.replace(tzinfo=timezone(timedelta(hours=1))),
        INITIAL_UNTIL,
        INITIAL_UNTIL + timedelta(microseconds=1),
    ],
)
def test_discovery_result_must_be_utc_and_before_upper_without_fallback(lower):
    reader = CapturedReader(lower)
    with pytest.raises((ValueError, DurableLifecycleError), match="retained"):
        freeze(reader, LifecycleRunMode.FULL_REPAIR)
    assert len(reader.calls) == 1


def test_discovery_error_propagates_once_without_typeerror_fallback():
    failure = TypeError("capture metadata could not be proven")

    class Reader(CapturedReader):
        def retained_since(self, **kwargs):
            self.calls.append("retained")
            raise failure

    reader = Reader()
    with pytest.raises(TypeError) as caught:
        freeze(reader, LifecycleRunMode.FULL_REPAIR)
    assert caught.value is failure
    assert reader.calls == ["retained"]


def test_invalid_requested_window_fails_before_discovery():
    reader = CapturedReader()
    with pytest.raises(ValueError, match="since must precede"):
        freeze(reader, LifecycleRunMode.INITIAL_BACKFILL, since=INITIAL_UNTIL)
    assert reader.calls == []


def lifecycle_case(mode):
    clock, state = _Clock(INITIAL_UNTIL), _State()
    lifecycle = _lifecycle(
        state=state, clock=clock, freezer=_Freezer(clock), tokens=[TOKEN_A, TOKEN_B]
    )
    if mode is not LifecycleRunMode.INITIAL_BACKFILL:
        initial = lifecycle.prepare(
            scope=_scope(),
            mode=LifecycleRunMode.INITIAL_BACKFILL,
            configured_bounds=_bounds(),
        )
        state.activate(initial, at=clock.current)
        clock.current += timedelta(minutes=2)
    return lifecycle, clock, state


@pytest.mark.parametrize("mode", REPAIR_MODES)
def test_widened_lower_persists_in_existing_plan_bytes_and_resume_never_rediscovers(
    mode,
):
    lifecycle, clock, state = lifecycle_case(mode)
    reader = CapturedReader()
    lifecycle._cutoff_freezer = FreshSpanLifecycleCutoffFreezer(reader, now=clock)
    prepared = lifecycle.prepare(scope=_scope(), mode=mode, configured_bounds=_bounds())
    plan = prepared.lease.build_plan
    encoded = plan.canonical_json
    assert RevisionBuildPlan.from_json(encoded) == plan
    assert prepared.cutoffs.span_window.since == OLDEST
    assert plan.source_scope.span_since_us == (
        OLDEST - datetime(1970, 1, 1, tzinfo=UTC)
    ) // timedelta(microseconds=1)

    def cannot_refreeze(**kwargs):
        raise AssertionError("restart must use durable plan, not current capture head")

    clock.current += timedelta(seconds=1)
    resumed = _lifecycle(
        state=state, clock=clock, freezer=cannot_refreeze, tokens=[]
    ).prepare(
        scope=_scope(),
        mode=LifecycleRunMode.AUTO,
        configured_bounds=_bounds(),
    )
    assert resumed.resumed
    assert resumed.lease.build_plan.canonical_json == encoded
    assert resumed.cutoffs == prepared.cutoffs
    assert len(reader.calls) == 2


@pytest.mark.parametrize(
    "mode,delta",
    [
        (LifecycleRunMode.INITIAL_BACKFILL, 1),
        (LifecycleRunMode.FULL_REPAIR, 1),
        (LifecycleRunMode.INCREMENTAL, 1),
        (LifecycleRunMode.INCREMENTAL, -1),
    ],
)
def test_reservation_rejects_narrowing_and_any_incremental_lower_change(mode, delta):
    lifecycle, clock, state = lifecycle_case(mode)
    original = lifecycle._cutoff_freezer

    def changed(**kwargs):
        cutoffs = original(**kwargs)
        return replace(
            cutoffs,
            span_window=SourceWindow(
                cutoffs.span_window.since + timedelta(microseconds=delta),
                cutoffs.span_window.until,
            ),
        )

    lifecycle._cutoff_freezer = changed
    with pytest.raises(DurableLifecycleError, match="required lower bound"):
        lifecycle.prepare(scope=_scope(), mode=mode, configured_bounds=_bounds())
    assert state.reservation is None


def test_full_repair_does_not_silently_clip_aged_origin_in_reservation():
    lifecycle, clock, _ = lifecycle_case(LifecycleRunMode.FULL_REPAIR)
    clock.current += timedelta(days=800)
    reader = CapturedReader(INITIAL_SINCE)
    lifecycle._cutoff_freezer = FreshSpanLifecycleCutoffFreezer(reader, now=clock)
    result = lifecycle.prepare(
        scope=_scope(), mode=LifecycleRunMode.FULL_REPAIR, configured_bounds=_bounds()
    )
    assert result.cutoffs.span_window == SourceWindow(INITIAL_SINCE, clock.current)
