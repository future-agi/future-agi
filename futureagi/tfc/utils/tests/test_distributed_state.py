"""
Tests for tfc/utils/distributed_state.py (lease renewal semantics).

Run with: pytest tfc/utils/tests/test_distributed_state.py -v
"""

import json
import uuid
from datetime import datetime
from unittest.mock import patch

import pytest

from tfc.utils.distributed_state import DistributedEvaluationTracker


def _make_tracker() -> DistributedEvaluationTracker:
    """Real tracker on an isolated key prefix; skips when Redis is unavailable."""
    tracker = DistributedEvaluationTracker(default_ttl=300)
    if not tracker._redis_available:
        pytest.skip("redis not available")
    tracker.key_prefix = f"test_lease_{uuid.uuid4().hex[:8]}:"
    return tracker


class TestRefreshRunning:
    """refresh_running is the lease-renewal primitive: only the owner may
    renew, each renewal stamps metadata["renewed_at"], and the write is
    aborted if the entry changed underneath (WATCH), so a renewal racing
    mark_completed cannot resurrect a deleted lease."""

    def test_owner_renewal_stamps_renewed_at_and_ttl(self):
        tracker = _make_tracker()
        assert tracker.mark_running(1, ttl=60)

        assert tracker.refresh_running(1, ttl=300) is True

        key = tracker._get_key("1")
        stored = json.loads(tracker._redis_client.get(key))
        assert "renewed_at" in stored["metadata"]
        assert tracker._redis_client.ttl(key) > 200
        tracker.mark_completed(1)

    def test_non_owner_cannot_renew(self):
        owner = _make_tracker()
        other = DistributedEvaluationTracker(default_ttl=300)
        other.key_prefix = owner.key_prefix
        assert owner.mark_running(2)

        assert other.refresh_running(2) is False
        assert (
            "renewed_at"
            not in json.loads(owner._redis_client.get(owner._get_key("2")))["metadata"]
        )
        owner.mark_completed(2)

    def test_missing_entry_returns_false(self):
        tracker = _make_tracker()
        assert tracker.refresh_running(3) is False
        assert tracker._redis_client.exists(tracker._get_key("3")) == 0

    def test_deleted_between_read_and_write_is_not_resurrected(self):
        """WATCH must abort the SET when mark_completed deletes the entry mid-renewal."""
        tracker = _make_tracker()
        assert tracker.mark_running(4)
        key = tracker._get_key("4")

        class _DeletingClock:
            """utcnow() runs between GET and SET inside the transaction; delete the key there."""

            @staticmethod
            def utcnow():
                tracker._redis_client.delete(key)
                return datetime.utcnow()

        with patch("tfc.utils.distributed_state.datetime", _DeletingClock):
            assert tracker.refresh_running(4) is False
        assert tracker._redis_client.exists(key) == 0

    def test_redis_error_returns_false(self):
        tracker = _make_tracker()
        with patch.object(
            tracker._redis_client, "transaction", side_effect=Exception("redis down")
        ):
            assert tracker.refresh_running(5) is False


class TestScopedCancel:
    """A cancel can name the run it is for. Only that run honours it; an
    untargeted cancel (manual) applies to whoever is running; callers that
    pass no token keep the legacy exists() semantics."""

    def test_targeted_cancel_is_honoured_only_by_that_run(self):
        tracker = _make_tracker()
        assert tracker.request_cancel(10, reason="edit", target="run-A")

        assert tracker.should_cancel(10, run_token="run-A") is True
        assert tracker.should_cancel(10, run_token="run-B") is False
        assert tracker.should_cancel(10) is True  # legacy: flag exists
        tracker.clear_cancel_flag(10)

    def test_untargeted_cancel_applies_to_any_run(self):
        tracker = _make_tracker()
        assert tracker.request_cancel(11, reason="manual")

        assert tracker.should_cancel(11, run_token="run-A") is True
        assert tracker.should_cancel(11, run_token="run-B") is True
        tracker.clear_cancel_flag(11)

    def test_no_flag_means_no_cancel(self):
        tracker = _make_tracker()
        assert tracker.should_cancel(12, run_token="run-A") is False
        assert tracker.should_cancel(12) is False
