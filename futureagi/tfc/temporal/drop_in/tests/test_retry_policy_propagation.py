"""
Tests for the @temporal_activity → TaskRunnerWorkflow retry-policy propagation chain.

Surface area covered:
- decorator default values (max_retries / retry_delay)
- TaskRunnerInput dataclass defaults
- _resolve_retry_policy mapping of decorator settings to Temporal RetryPolicy
- runner.start_activity reads registry metadata into TaskRunnerInput

These tests intentionally avoid spinning up a real Temporal worker; the focus
is the data-shape contract from decorator → registry → input → policy. The
end-to-end behavior is verified separately via the live Temporal UI.
"""

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tfc.temporal.drop_in.decorator import _ACTIVITY_REGISTRY, temporal_activity
from tfc.temporal.drop_in.workflow import (
    DEFAULT_RETRY_POLICY,
    TaskRunnerInput,
    _resolve_retry_policy,
)


# =============================================================================
# Helpers
# =============================================================================


def _decorate(name, **kwargs):
    """Apply @temporal_activity(**kwargs) to a no-op function and return its
    registry entry. Cleans up _ACTIVITY_REGISTRY between tests so leaks from
    one case can't pollute another."""

    @temporal_activity(name=name, **kwargs)
    def _noop():
        return None

    return _ACTIVITY_REGISTRY[name]


@pytest.fixture(autouse=True)
def _isolate_registry():
    """Snapshot _ACTIVITY_REGISTRY before each test, restore after.

    The decorator stores entries by activity name; without isolation a
    registry mutation in one test would survive into the next.
    """
    snapshot = dict(_ACTIVITY_REGISTRY)
    try:
        yield
    finally:
        _ACTIVITY_REGISTRY.clear()
        _ACTIVITY_REGISTRY.update(snapshot)


# =============================================================================
# Decorator default values
# =============================================================================


class TestDecoratorDefaults:
    """The decorator must store ``None`` for retry settings the user did not
    explicitly pass. Storing ``0`` (the previous default) silently flipped
    every undecorated-for-retries activity to maximum_attempts=1."""

    def test_no_retry_args_stores_none(self):
        meta = _decorate("test_no_retry_args_stores_none")
        assert meta["max_retries"] is None
        assert meta["retry_delay"] is None

    def test_explicit_max_retries_zero_is_preserved(self):
        meta = _decorate("test_explicit_zero", max_retries=0)
        assert meta["max_retries"] == 0
        # retry_delay still untouched → None
        assert meta["retry_delay"] is None

    def test_explicit_max_retries_positive_is_preserved(self):
        meta = _decorate("test_explicit_positive", max_retries=5)
        assert meta["max_retries"] == 5

    def test_explicit_retry_delay_is_preserved(self):
        meta = _decorate("test_explicit_delay", max_retries=2, retry_delay=10)
        assert meta["retry_delay"] == 10

    def test_other_metadata_still_present(self):
        meta = _decorate("test_other_meta", queue="custom", time_limit=42)
        assert meta["queue"] == "custom"
        assert meta["time_limit"] == 42


# =============================================================================
# TaskRunnerInput dataclass
# =============================================================================


class TestTaskRunnerInputDefaults:
    """TaskRunnerInput must default the new fields to None so workflows
    deserialized from in-flight pre-existing payloads (without these fields)
    don't crash and instead fall back to DEFAULT_RETRY_POLICY."""

    def test_max_retries_defaults_to_none(self):
        inp = TaskRunnerInput(activity_name="x", args=[], kwargs={})
        assert inp.max_retries is None

    def test_retry_delay_defaults_to_none(self):
        inp = TaskRunnerInput(activity_name="x", args=[], kwargs={})
        assert inp.retry_delay is None

    def test_explicit_values_round_trip(self):
        inp = TaskRunnerInput(
            activity_name="x", args=[], kwargs={}, max_retries=3, retry_delay=15
        )
        assert inp.max_retries == 3
        assert inp.retry_delay == 15


# =============================================================================
# _resolve_retry_policy
# =============================================================================


class TestResolveRetryPolicy:
    """The mapping from decorator metadata to Temporal RetryPolicy.

    Critical invariants:
    - max_retries=None → fall back to DEFAULT_RETRY_POLICY (3 attempts).
    - max_retries=0   → maximum_attempts=1 (decorator's "no retry"; counts
                        first attempt).
    - max_retries=N   → maximum_attempts=N+1.
    - retry_delay=None when max_retries is set → initial_interval defaults to 5s.
    """

    def _input(self, max_retries=None, retry_delay=None):
        return TaskRunnerInput(
            activity_name="x",
            args=[],
            kwargs={},
            max_retries=max_retries,
            retry_delay=retry_delay,
        )

    def test_max_retries_none_returns_default_policy(self):
        policy = _resolve_retry_policy(self._input(max_retries=None))
        assert policy is DEFAULT_RETRY_POLICY

    def test_max_retries_zero_yields_one_attempt(self):
        policy = _resolve_retry_policy(self._input(max_retries=0))
        assert policy is not DEFAULT_RETRY_POLICY
        assert policy.maximum_attempts == 1

    def test_max_retries_positive_adds_one(self):
        policy = _resolve_retry_policy(self._input(max_retries=2))
        assert policy.maximum_attempts == 3
        policy = _resolve_retry_policy(self._input(max_retries=5))
        assert policy.maximum_attempts == 6

    def test_retry_delay_none_falls_back_to_5_seconds(self):
        policy = _resolve_retry_policy(self._input(max_retries=2, retry_delay=None))
        assert policy.initial_interval == timedelta(seconds=5)

    def test_retry_delay_propagates_when_set(self):
        policy = _resolve_retry_policy(self._input(max_retries=2, retry_delay=20))
        assert policy.initial_interval == timedelta(seconds=20)

    def test_backoff_and_maximum_interval_are_fixed(self):
        policy = _resolve_retry_policy(self._input(max_retries=2, retry_delay=1))
        assert policy.backoff_coefficient == 2.0
        assert policy.maximum_interval == timedelta(minutes=5)

    def test_negative_max_retries_clamped_to_one(self):
        # Defensive: max(1, N+1) ensures we never schedule zero attempts.
        policy = _resolve_retry_policy(self._input(max_retries=-5))
        assert policy.maximum_attempts == 1


# =============================================================================
# runner._start_activity_async wires registry → TaskRunnerInput
# =============================================================================


class TestRunnerInputConstruction:
    """The runner is responsible for reading ``max_retries`` / ``retry_delay``
    out of _ACTIVITY_REGISTRY and putting them on TaskRunnerInput. This test
    captures the TaskRunnerInput that would be sent to Temporal so we can
    assert the values were threaded correctly."""

    @pytest.mark.asyncio
    async def test_runner_threads_metadata_from_registry(self):
        # Register a fake activity so the registry has metadata to read.
        _decorate(
            "fixture_activity_for_runner",
            max_retries=2,
            retry_delay=15,
            queue="trace_ingestion",
        )

        captured = {}

        async def _capture_start_workflow(workflow_run, run_input, **kwargs):
            captured["input"] = run_input
            captured["task_queue"] = kwargs.get("task_queue")
            return MagicMock()

        fake_client = MagicMock()
        fake_client.start_workflow = AsyncMock(side_effect=_capture_start_workflow)
        fake_client.namespace = "default"

        with patch(
            "tfc.temporal.common.client.get_client",
            new=AsyncMock(return_value=fake_client),
        ):
            from tfc.temporal.drop_in.runner import _start_activity_async

            await _start_activity_async(
                activity_name="fixture_activity_for_runner",
                args=(),
                kwargs={},
                queue="trace_ingestion",
                task_id="t-1",
            )

        run_input = captured["input"]
        assert run_input.activity_name == "fixture_activity_for_runner"
        assert run_input.max_retries == 2
        assert run_input.retry_delay == 15

    @pytest.mark.asyncio
    async def test_runner_passes_none_when_activity_unknown(self):
        # Unknown activity name → registry returns {}; both fields stay None
        # so the workflow falls back to DEFAULT_RETRY_POLICY.
        captured = {}

        async def _capture_start_workflow(workflow_run, run_input, **kwargs):
            captured["input"] = run_input
            return MagicMock()

        fake_client = MagicMock()
        fake_client.start_workflow = AsyncMock(side_effect=_capture_start_workflow)
        fake_client.namespace = "default"

        with patch(
            "tfc.temporal.common.client.get_client",
            new=AsyncMock(return_value=fake_client),
        ):
            from tfc.temporal.drop_in.runner import _start_activity_async

            await _start_activity_async(
                activity_name="never_registered_activity",
                args=(),
                kwargs={},
                queue="default",
                task_id="t-2",
            )

        run_input = captured["input"]
        assert run_input.max_retries is None
        assert run_input.retry_delay is None
