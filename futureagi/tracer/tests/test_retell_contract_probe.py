"""Executable form of the fetcher ↔ orchestrator contract for Retell polling.

Both sides are built in parallel against the written contract; this file is the
part of it a machine can check, so drift shows up as a failing test instead of
at assembly time. Behavioural cases live in the phase test files.
"""

import dataclasses
import inspect
from datetime import timedelta

import pytest


def _fetcher():
    from tracer.services import observability_providers as m

    return m


def _orchestrator():
    from tracer.utils import observability_provider as m

    return m


class TestFetcherSurface:
    def test_page_dataclass_fields(self):
        m = _fetcher()
        names = [f.name for f in dataclasses.fields(m.RetellPage)]
        assert names == ["calls", "has_more", "next_key", "dropped_no_end", "dropped_missing", "dropped_failed"]
        assert m.RetellPage.__dataclass_params__.frozen

    def test_exception_types(self):
        m = _fetcher()
        assert m.RetellConfigurationError.__bases__ == (Exception,)
        assert m.RetellCursorRejected.__bases__ == (Exception,)
        assert m.RetellCursorRejected(cause="missing_key").cause == "missing_key"

    def test_fetch_signature(self):
        m = _fetcher()
        assert isinstance(inspect.getattr_static(m.ObservabilityService, "fetch_retell_page"), staticmethod)
        params = list(inspect.signature(m.ObservabilityService.fetch_retell_page).parameters.values())
        assert [p.name for p in params] == ["provider", "start_time", "end_time", "pagination_key", "skip"]
        for p in params[3:]:
            assert p.kind is inspect.Parameter.KEYWORD_ONLY
            assert p.default is None

    def test_constants(self):
        m = _fetcher()
        assert m.RETELL_LIST_PAGE_LIMIT == 1000
        assert m.RETELL_REQUEST_TIMEOUT_SECONDS == 30
        assert m.RETELL_MAX_ATTEMPTS == 3
        assert not hasattr(m, "RETELL_CALL_HYDRATION_BOUND")
        assert callable(m._sleep)

    def test_get_call_logs_no_longer_serves_retell(self):
        from unittest.mock import Mock

        from tracer.models.observability_provider import ProviderChoices

        m = _fetcher()
        provider = Mock()
        provider.provider = ProviderChoices.RETELL
        with pytest.raises(NotImplementedError):
            m.ObservabilityService.get_call_logs(provider, None, None)


class TestOrchestratorSurface:
    def test_store_outcome_fields(self):
        m = _orchestrator()
        assert [f.name for f in dataclasses.fields(m.StoreOutcome)] == ["stored", "malformed", "export_failed"]
        assert m.StoreOutcome.__dataclass_params__.frozen

    def test_activity_signature(self):
        m = _orchestrator()
        target = getattr(m.fetch_observability_logs, "__wrapped__", m.fetch_observability_logs)
        assert list(inspect.signature(target).parameters) == ["start_time", "end_time", "provider_id"]

    def test_fetch_logs_for_provider_signature(self):
        m = _orchestrator()
        sig = inspect.signature(m.fetch_logs_for_provider)
        assert list(sig.parameters) == ["provider_id", "scheduled", "start_time", "end_time"]
        for name in ("scheduled", "start_time", "end_time"):
            assert sig.parameters[name].kind is inspect.Parameter.KEYWORD_ONLY

    def test_state_and_watermark_helpers_exist(self):
        m = _orchestrator()
        for name in ("_advance_watermark", "_repair_future_watermark", "_write_retell_state", "_read_retell_state", "_page_digest", "_parse",
                     "_backoff_delay", "_hint_for", "_classify", "_restart_window", "_on_total_failure", "_log_counts", "_log_incomplete",
                     "_log_behind", "_valid_window", "_poll_retell_provider", "_manual_retell_run", "_poll_other_provider"):
            assert callable(getattr(m, name)), name
        assert not hasattr(m, "_update_last_fetched_at")
        assert not hasattr(m, "normalize_and_store_logs")

    def test_valid_window_rejects_malformed_state(self):
        m = _orchestrator()
        good = {"start": "2026-09-03T00:00:00+00:00", "end": "2026-09-03T00:10:00+00:00", "opened_at_hint": True, "narrowed": False,
                "key": None, "skip": None, "pages_stored": 0, "digests": [], "restarts": 0}
        assert m._valid_window(good)
        assert not m._valid_window({**good, "digests": "notalist"})
        assert not m._valid_window({**good, "start": 12345})
        assert not m._valid_window({**good, "start": "2026-09-03T00:00:00"})
        assert not m._valid_window("nope")

    def test_page_digest_is_a_hash_not_ids(self):
        m = _orchestrator()
        digest = m._page_digest([{"call_id": "call_b"}, {"call_id": "call_a"}])
        assert len(digest) == 64 and "call_a" not in digest
        assert digest == m._page_digest([{"call_id": "call_a"}, {"call_id": "call_b"}])
        assert m._page_digest([]) is None

    def test_constants(self):
        m = _orchestrator()
        assert m.RETELL_VISIBILITY_LAG == timedelta(seconds=60)
        assert m.RETELL_FUTURE_WATERMARK_LOOKBACK == timedelta(hours=1)
        assert m.RETELL_MIN_WINDOW == timedelta(seconds=1)
        assert m.RETELL_BACKOFF_BASE == timedelta(minutes=10)
        assert m.RETELL_BACKOFF_MAX == timedelta(hours=6)
        assert m.RETELL_MAX_FAILED_RUNS == 3
        assert m.RETELL_MAX_WINDOW_RESTARTS == 3
        assert m.RETELL_MANUAL_RUN_MAX_PAGES == 5
        assert m.RETELL_WINDOW_HINT_MAX == timedelta(hours=6)
        assert m.RETELL_WINDOW_GROW_AFTER == 3
        assert m.RETELL_DIGEST_HISTORY == 8
        assert m.RETELL_MAX_PAGES_PER_WINDOW == 50
        assert m.RETELL_BEHIND_WARN == timedelta(minutes=20)
        assert m.RETELL_BEHIND_ERROR == timedelta(hours=6)
        assert m.RETELL_MAX_BACKOFF_EXPONENT == 6
        assert m.RETELL_LIST_PAGE_LIMIT == 1000

    def test_classify_boundaries(self):
        from tracer.services.observability_providers import RetellPage

        m = _orchestrator()
        page = lambda failed: RetellPage(calls=[], has_more=False, next_key=None, dropped_no_end=0, dropped_missing=0, dropped_failed=failed)
        assert m._classify(page(0), m.StoreOutcome(0, 0, 0)) == "ok"
        assert m._classify(page(0), m.StoreOutcome(0, 1000, 0)) == "ok"
        assert m._classify(page(0), m.StoreOutcome(0, 1, 999)) == "total"
        assert m._classify(page(0), m.StoreOutcome(0, 999, 1)) == "partial"
        assert m._classify(page(1), m.StoreOutcome(999, 0, 0)) == "partial"

    def test_backoff_never_overflows(self):
        m = _orchestrator()
        assert m._backoff_delay(1) == timedelta(minutes=10)
        assert m._backoff_delay(40) == timedelta(hours=6)
        assert m._backoff_delay(10_000) == timedelta(hours=6)

    def test_poll_state_field_exists_and_is_not_exposed(self):
        from tracer.models.observability_provider import ObservabilityProvider
        from tracer.serializers.observability_provider import ObservabilityProviderSerializer

        field = ObservabilityProvider._meta.get_field("poll_state")
        assert field.get_internal_type() == "JSONField"
        assert "poll_state" not in ObservabilityProviderSerializer.Meta.fields

    def test_stale_reemit_caveat_is_gone(self):
        m = _orchestrator()
        assert "reuse the same" not in (m._provider_collector_span_id.__doc__ or "")


class TestFixtureShapes:
    def test_list_item_is_lean_and_detail_is_full(self):
        from tracer.tests.fixtures.retell_calls import detail, list_item

        item = list_item("c1", 1_000, 2_000)
        for key in ("transcript", "transcript_with_tool_calls", "recording_url"):
            assert key not in item
        full = detail("c1", 1_000, 2_000)
        for key in ("transcript_with_tool_calls", "recording_url", "call_id", "end_timestamp"):
            assert full[key] is not None

    def test_null_timestamp_levers(self):
        from tracer.tests.fixtures.retell_calls import list_item

        assert list_item("c1", None, 2_000)["start_timestamp"] is None
        assert list_item("c1", 1_000, None)["end_timestamp"] is None
