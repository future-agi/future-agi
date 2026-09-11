"""Executable form of the fetcher ↔ orchestrator contract for Retell polling.

Both sides are built in parallel against the written contract; this file is the
part of it a machine can check, so drift shows up as a failing test instead of
at assembly time. Behavioural cases live in the phase test files.
"""

import dataclasses
import inspect
from datetime import timedelta
from unittest.mock import Mock, patch

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
        assert names == [
            "calls",
            "has_more",
            "next_key",
            "dropped_no_end",
            "dropped_missing",
            "dropped_failed",
            # amendment v1.15 A1: the listed page's identity and how far into
            # it this response got, so a page can be resumed call by call.
            "digest",
            "listed",
            "consumed",
            "indices",
            # v1.15.2 F8: every drop with the list index it happened at, so the
            # orchestrator counts each one exactly once across resumes.
            "drops",
        ]
        assert m.RetellPage.__dataclass_params__.frozen
        assert (m._DROP_SLOT_NO_END, m._DROP_SLOT_MISSING, m._DROP_SLOT_FAILED) == (
            3,
            4,
            5,
        )  # the orchestrator's `page_counts` slots, by position

    def test_page_digest_is_a_hash_of_listed_ids_in_list_order(self):
        """v1.15.1 F2: the digest is over the call_ids of the page in the order
        Retell listed them, and never carries an id itself. Order is part of
        the page's identity because a resume skips a prefix by position."""
        m = _fetcher()
        digest = m._retell_page_digest([{"call_id": "call_b"}, {"call_id": "call_a"}])
        assert len(digest) == 64 and "call_a" not in digest
        assert digest != m._retell_page_digest(
            [{"call_id": "call_a"}, {"call_id": "call_b"}]
        )
        assert digest == m._retell_page_digest(
            [{"call_id": "call_b"}, {"call_id": "call_a"}]
        )  # a literal repeat still hashes the same, so page_repeated survives
        assert m._retell_page_digest([]) is None

    def test_exception_types(self):
        m = _fetcher()
        assert m.RetellConfigurationError.__bases__ == (Exception,)
        assert m.RetellCursorRejected.__bases__ == (Exception,)
        assert m.RetellCursorRejected(cause="missing_key").cause == "missing_key"

    def test_fetch_signature(self):
        m = _fetcher()
        assert isinstance(
            inspect.getattr_static(m.ObservabilityService, "fetch_retell_page"),
            staticmethod,
        )
        params = list(
            inspect.signature(
                m.ObservabilityService.fetch_retell_page
            ).parameters.values()
        )
        assert [p.name for p in params] == [
            "provider",
            "start_time",
            "end_time",
            "pagination_key",
            "skip",
            # amendment v1.15 A2
            "resume_from",
            "resume_digest",
            "deadline",
        ]
        for p in params[3:]:
            assert p.kind is inspect.Parameter.KEYWORD_ONLY
        defaults = {p.name: p.default for p in params[3:]}
        assert defaults == {
            "pagination_key": None,
            "skip": None,
            "resume_from": 0,
            "resume_digest": None,
            "deadline": None,
        }

    def test_constants(self):
        m = _fetcher()
        assert m.RETELL_LIST_PAGE_LIMIT == 100  # amendment v1.14 A1: was 1000
        assert m.RETELL_REQUEST_TIMEOUT_SECONDS == 30
        assert m.RETELL_MAX_ATTEMPTS == 3
        assert m.RETELL_HYDRATION_WORKERS == 4  # amendment v1.14 A5
        assert not hasattr(m, "RETELL_CALL_HYDRATION_BOUND")
        assert callable(m._sleep)

    def test_bootstrap_mode_accepts_pagination_key_and_skip(self):
        """A2: the old "pagination_key or skip given in bootstrap mode"
        RetellConfigurationError case is deleted; bootstrap pages under the
        same one-of pagination_key/skip rule as windowed mode."""
        from datetime import UTC, datetime

        m = _fetcher()
        provider = Mock()
        end = datetime(2026, 9, 3, 12, 0, 0, tzinfo=UTC)
        response = Mock()
        response.status_code = 200
        response.raise_for_status = Mock()
        response.json.return_value = {"items": [], "has_more": False}

        with (
            patch.object(
                m.ObservabilityService,
                "_resolve_retell_key",
                return_value=("k", "agent_x"),
            ),
            patch(
                "tracer.services.observability_providers.requests.post",
                return_value=response,
            ) as mock_post,
        ):
            m.ObservabilityService.fetch_retell_page(
                provider, None, end, pagination_key="k1"
            )
            assert mock_post.call_args.kwargs["json"]["pagination_key"] == "k1"

            m.ObservabilityService.fetch_retell_page(provider, None, end, skip=0)
            assert mock_post.call_args.kwargs["json"]["skip"] == 0

    def test_hydration_pool_is_bounded_by_workers_constant(self):
        """A4: per-item hydration runs through a ThreadPoolExecutor sized by
        RETELL_HYDRATION_WORKERS, not one request at a time."""
        m = _fetcher()
        source = inspect.getsource(m.ObservabilityService._hydrate_retell_calls)
        assert "ThreadPoolExecutor" in source
        assert "RETELL_HYDRATION_WORKERS" in source

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
        assert [f.name for f in dataclasses.fields(m.StoreOutcome)] == [
            "stored",
            "malformed",
            "export_failed",
        ]
        assert m.StoreOutcome.__dataclass_params__.frozen

    def test_activity_signature(self):
        m = _orchestrator()
        target = getattr(
            m.fetch_observability_logs, "__wrapped__", m.fetch_observability_logs
        )
        assert list(inspect.signature(target).parameters) == [
            "start_time",
            "end_time",
            "provider_id",
        ]

    def test_fetch_logs_for_provider_signature(self):
        m = _orchestrator()
        sig = inspect.signature(m.fetch_logs_for_provider)
        assert (
            list(sig.parameters)
            == [
                "provider_id",
                "scheduled",
                "start_time",
                "end_time",
                "deadline",  # F1/N1: activity-level deadline, threaded to _poll_retell_provider
            ]
        )
        for name in ("scheduled", "start_time", "end_time", "deadline"):
            assert sig.parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
        assert sig.parameters["deadline"].default is None

    def test_poll_retell_provider_accepts_a_keyword_only_deadline(self):
        """F1/N1: the activity-level deadline is threaded down to
        `_poll_retell_provider` as an optional, keyword-only argument
        defaulting to None (manual runs / direct callers)."""
        m = _orchestrator()
        sig = inspect.signature(m._poll_retell_provider)
        assert "deadline" in sig.parameters
        assert sig.parameters["deadline"].kind is inspect.Parameter.KEYWORD_ONLY
        assert sig.parameters["deadline"].default is None

    def test_state_and_watermark_helpers_exist(self):
        m = _orchestrator()
        for name in (
            "_advance_watermark",
            "_repair_future_watermark",
            "_write_retell_state",
            "_read_retell_state",
            "_parse",
            "_backoff_delay",
            "_hint_for",
            "_classify",
            "_restart_window",
            "_on_total_failure",
            "_log_counts",
            "_log_incomplete",
            "_log_behind",
            "_valid_window",
            "_new_window",  # v1.14 review fix F6
            "_reset_page_progress",  # amendment v1.15 B1
            "_complete_bootstrap",
            "_poll_retell_provider",
            "_manual_retell_run",
            "_poll_other_provider",
        ):
            assert callable(getattr(m, name)), name
        assert not hasattr(m, "_update_last_fetched_at")
        assert not hasattr(m, "normalize_and_store_logs")
        # v1.15 B1/B4: the digest now belongs to the fetcher (it is over the
        # LISTED page, which only the fetcher sees), and `total_pages` — with
        # its deploy-compat backfill — is gone with the cap it fed.
        assert not hasattr(m, "_page_digest")
        assert not hasattr(m, "_backfill_total_pages")

    def test_valid_window_rejects_malformed_state(self):
        m = _orchestrator()
        good = {
            "start": "2026-09-03T00:00:00+00:00",
            "end": "2026-09-03T00:10:00+00:00",
            "opened_at_hint": True,
            "narrowed": False,
            "key": None,
            "skip": None,
            "pages_stored": 0,
            "digests": [],
            "restarts": 0,
            "progress": 0,
            "page_digest": None,
            "page_counts": [0, 0, 0, 0, 0, 0],
        }
        assert m._valid_window(good)
        assert not m._valid_window({**good, "digests": "notalist"})
        assert not m._valid_window({**good, "start": 12345})
        assert not m._valid_window({**good, "start": "2026-09-03T00:00:00"})
        # v1.15 B1: the three page-progress keys are required and typed — no
        # compat shim, a window missing or mistyping one is simply discarded.
        assert not m._valid_window({k: v for k, v in good.items() if k != "progress"})
        assert not m._valid_window({**good, "progress": "0"})
        assert not m._valid_window({**good, "page_digest": 7})
        assert not m._valid_window({**good, "page_counts": "000"})
        # v1.15.1 F4: `page_counts` is the one key the store loop indexes, so
        # its LENGTH is part of the shape — a wrong-width list would otherwise
        # pass here and then raise on every run for this provider for ever.
        assert not m._valid_window({**good, "page_counts": []})
        assert not m._valid_window({**good, "page_counts": [1]})
        assert not m._valid_window({**good, "page_counts": [0, 0, 0]})
        assert not m._valid_window({**good, "page_counts": [0, 0, 0, 0, 0, 0, 0]})
        assert not m._valid_window({**good, "page_counts": [0, 0, 0, 0, 0, "0"]})
        # v1.15.2 F12: `progress` is a position in the listed page, so its sign
        # is part of its shape — a negative one is corruption, not a resume, and
        # nothing downstream clamps it any more.
        assert not m._valid_window({**good, "progress": -1})
        assert m._valid_window({**good, "progress": 7})
        assert not m._valid_window("nope")

    def test_valid_window_accepts_bootstrap_shape(self):
        """B1: "start": None is a legal bootstrap window, driven by the same
        page loop and the same _valid_window check as an ordinary window."""
        m = _orchestrator()
        bootstrap = {
            "start": None,
            "end": "2026-09-03T00:10:00+00:00",
            "opened_at_hint": False,
            "narrowed": False,
            "key": None,
            "skip": None,
            "pages_stored": 0,
            "digests": [],
            "restarts": 0,
            "progress": 0,
            "page_digest": None,
            "page_counts": [0, 0, 0, 0, 0, 0],
        }
        assert m._valid_window(bootstrap)
        assert not m._valid_window(
            {**bootstrap, "end": "2026-09-03T00:00:00"}
        )  # end must still be aware

    def test_restart_window_bootstrap_skips_halving(self):
        """B2: a bootstrap restart uses the same bookkeeping as a windowed
        one, but there is no start to halve — at RETELL_MAX_WINDOW_RESTARTS
        it goes straight to offset mode, and window_hint_seconds is never
        written by a bootstrap restart."""
        m = _orchestrator()
        window = {
            "start": None,
            "end": "2026-09-03T00:10:00+00:00",
            "opened_at_hint": False,
            "narrowed": False,
            "key": "k",
            "skip": None,
            "pages_stored": 3,
            "digests": ["d"],
            "restarts": m.RETELL_MAX_WINDOW_RESTARTS - 1,
            "progress": 4,
            "page_digest": "d0",
            "page_counts": [4, 0, 0, 0, 0, 0],
        }
        state = {"window": window}
        with patch.object(m, "_write_retell_state", return_value=True):
            m._restart_window("pid", state, cause="missing_key")
        assert window["skip"] == 0
        assert window["restarts"] == 0
        assert window["narrowed"] is False  # never halved
        assert "window_hint_seconds" not in state

    def test_restart_window_resets_page_progress(self):
        """v1.15 B1: a restart re-lists from page 1, so the progress into the
        page it was on — and that page's accumulated counts — must go with the
        cursor and digests it already resets."""
        m = _orchestrator()
        window = {
            "start": None,
            "end": "2026-09-03T00:10:00+00:00",
            "opened_at_hint": False,
            "narrowed": False,
            "key": "k",
            "skip": None,
            "pages_stored": 3,
            "digests": ["d"],
            "restarts": 0,
            "progress": 37,
            "page_digest": "d0",
            "page_counts": [37, 0, 0, 0, 0, 0],
        }
        state = {"window": window}
        with patch.object(m, "_write_retell_state", return_value=True):
            m._restart_window("pid", state, cause="missing_key")
        assert window["pages_stored"] == 0
        assert window["progress"] == 0
        assert window["page_digest"] is None
        assert window["page_counts"] == [0, 0, 0, 0, 0, 0]

    def test_restart_window_bootstrap_offset_stuck_backs_off_without_completing(self):
        """v1.15 B6: a bootstrap whose offset-mode fallback also exhausts its
        restarts must NEVER complete — completing would advance the watermark
        over history it never covered. It keeps the window (same frozen `end`),
        resets to a fresh cursor-mode attempt, and backs off with escalation."""
        m = _orchestrator()
        window = {
            "start": None,
            "end": "2026-09-03T00:10:00+00:00",
            "opened_at_hint": False,
            "narrowed": False,
            "key": None,
            "skip": 0,
            "pages_stored": 5,
            "digests": ["d"],
            "restarts": m.RETELL_MAX_WINDOW_RESTARTS - 1,
            "progress": 0,
            "page_digest": None,
            "page_counts": [0, 0, 0, 0, 0, 0],
        }
        state = {"window": window}
        with (
            patch.object(m, "_complete_bootstrap") as mock_complete,
            patch.object(m, "_write_retell_state", return_value=True),
        ):
            m._restart_window("pid", state, cause="missing_key")
        mock_complete.assert_not_called()
        assert state["bootstrap_stuck"] == 1
        assert "backoff_until" in state
        assert window["end"] == "2026-09-03T00:10:00+00:00"  # frozen end kept
        assert window["skip"] is None and window["key"] is None  # cursor mode again
        assert window["pages_stored"] == 0 and window["restarts"] == 0

    def test_complete_bootstrap_has_a_single_caller(self):
        """v1.15 B7: `_complete_bootstrap` is kept as a helper but is now
        reached only from the page loop's completion path — the stuck branch
        of `_restart_window` must not complete a bootstrap at all."""
        m = _orchestrator()
        assert "_complete_bootstrap(" in inspect.getsource(m._poll_retell_provider)
        assert "_complete_bootstrap(" not in inspect.getsource(m._restart_window)

    def test_run_budget_loop_exists_and_is_gated_on_the_budget_constant(self):
        """v1.14 review fix F2: `_poll_retell_provider` loops on stored pages
        within RETELL_RUN_BUDGET instead of always returning after one page."""
        m = _orchestrator()
        assert m.RETELL_RUN_BUDGET == timedelta(minutes=20)
        source = inspect.getsource(m._poll_retell_provider)
        assert "while True" in source
        assert "RETELL_RUN_BUDGET" in source

    def test_v1_15_events_are_logged_and_the_capped_event_lost_total_pages(self):
        """v1.15 B8: the three new events exist on the paths that own them,
        and `retell_bootstrap_capped` is back to provider_id + pages_stored."""
        m = _orchestrator()
        poll = inspect.getsource(m._poll_retell_provider)
        restart = inspect.getsource(m._restart_window)
        assert "retell_page_checkpointed" in poll
        assert "retell_page_changed" in poll
        # v1.15.2 F9: the escalation a run of `retell_page_changed` warnings
        # ends in, so an unstable page is an alertable state and not a silence.
        assert "retell_page_unstable" in poll
        assert "retell_bootstrap_stuck" in restart
        assert "retell_window_stuck" in restart
        assert "total_pages" not in poll  # the capped event's field is gone with it
        assert "total_pages" not in restart

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
        assert m.RETELL_BOOTSTRAP_MAX_PAGES == 10  # amendment v1.14 B5
        assert m.RETELL_RUN_BUDGET == timedelta(minutes=20)  # v1.14 review fix F2
        assert m.RETELL_ACTIVITY_BUDGET == timedelta(
            hours=2
        )  # v1.14 review fix F1 (N1)
        assert m.RETELL_BEHIND_WARN == timedelta(minutes=20)
        assert m.RETELL_BEHIND_ERROR == timedelta(hours=6)
        assert m.RETELL_MAX_BACKOFF_EXPONENT == 6
        assert m.RETELL_LIST_PAGE_LIMIT == 100  # amendment v1.14 A1: was 1000

    def test_classify_boundaries(self):
        """v1.15.1 F1: the verdict is a function of the window's accumulated
        `page_counts` alone — no page argument, so a resumed page cannot be
        judged on the last response's slice."""
        m = _orchestrator()
        assert list(inspect.signature(m._classify).parameters) == ["counts"]

        def counts(stored, malformed, export_failed, failed):
            return [stored, malformed, export_failed, 0, 0, failed]

        assert m._classify(counts(0, 0, 0, 0)) == "ok"
        assert m._classify(counts(0, 1000, 0, 0)) == "ok"
        assert m._classify(counts(0, 1, 999, 0)) == "total"
        assert m._classify(counts(0, 999, 1, 0)) == "partial"
        assert m._classify(counts(999, 0, 0, 1)) == "partial"
        assert m._classify(counts(0, 1, 0, 999)) == "total"  # drops alone decide too

    def test_backoff_never_overflows(self):
        m = _orchestrator()
        assert m._backoff_delay(1) == timedelta(minutes=10)
        assert m._backoff_delay(40) == timedelta(hours=6)
        assert m._backoff_delay(10_000) == timedelta(hours=6)

    def test_poll_state_field_exists_and_is_not_exposed(self):
        from tracer.models.observability_provider import ObservabilityProvider
        from tracer.serializers.observability_provider import (
            ObservabilityProviderSerializer,
        )

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
        for key in (
            "transcript_with_tool_calls",
            "recording_url",
            "call_id",
            "end_timestamp",
        ):
            assert full[key] is not None

    def test_null_timestamp_levers(self):
        from tracer.tests.fixtures.retell_calls import list_item

        assert list_item("c1", None, 2_000)["start_timestamp"] is None
        assert list_item("c1", 1_000, None)["end_timestamp"] is None
