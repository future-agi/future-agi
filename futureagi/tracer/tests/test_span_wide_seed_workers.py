"""A seed over a slice wider than one day gets the wide seed worker budget.

Offline contracts for the seed-kind worker rule in
``trace_filter_reads.read_bounded_filter_page``: a seed over more than one day
runs with ``FILTER_SELECTOR_WIDE_SEED_MAX_THREADS`` workers, a narrow seed
keeps the single default worker, an explicit caller worker budget still caps
the wide seed, and the density probe keeps its one worker whatever the caller
or the wide budget say. Workers change latency only: every statement's rows,
bytes, memory caps and result are the same at any worker count, and this
module pins that every other setting a wide seed carries equals a narrow
seed's.
"""

from dataclasses import dataclass
from datetime import timedelta

import pytest
from django.conf import settings

from tracer.selectors import trace_filter_reads as selector
from tracer.tests.test_bounded_trace_filter_reads import (
    END,
    _ClassifierSettingsFakeExecutor,
    _time_filter,
    _WideInitialSliceFakeBuilder,
)
from tracer.tests.test_trace_text_candidate_seed import (
    _RowBudgetFakeBuilder,
    _RowBudgetFakeExecutor,
)

pytestmark = pytest.mark.unit
ONE_DAY = timedelta(days=1)


def _read(builder, executor, **overrides):
    options = {
        "builder": builder,
        "analytics": executor,
        "filters": [_time_filter(builder.start, builder.end)],
        "key_field": "id",
        "page_number": 0,
        "page_size": 1,
        "deadline_ms": 5_000,
        "max_seed_attempts": 24,
        "max_query_count": 64,
        "max_candidates": 4,
        "classify_batch_size": 4,
    }
    options.update(overrides)
    return selector.read_bounded_filter_page(**options)


def _sparse_builder(*, days_back: int = 5):
    """One row five days back: the 1 h opening slice doubles past one day."""

    row = {"id": "span", "start_time": END - timedelta(days=days_back)}
    return _WideInitialSliceFakeBuilder(
        [row], start=END - timedelta(days=14), end=END, recommended_seed_batch_size=4
    )


def _seed_envelopes(executor):
    """``(slice width, settings)`` for every seed statement, in issue order."""

    widths = [
        params["slice_end"] - params["slice_start"]
        for query, params in executor.calls
        if query == "seed"
    ]
    envelopes = [
        dict(seed_settings)
        for query, seed_settings in executor.settings_by_query
        if query == "seed"
    ]
    assert len(widths) == len(envelopes)
    return list(zip(widths, envelopes, strict=True))


def test_wide_seed_worker_budget_is_settings_backed():
    assert (
        selector._WIDE_SEED_MAX_THREADS
        == settings.FILTER_SELECTOR_WIDE_SEED_MAX_THREADS
    )
    assert settings.FILTER_SELECTOR_WIDE_SEED_MAX_THREADS == 4


def test_seed_over_one_day_gets_the_wide_budget_and_a_narrow_seed_keeps_one_worker(
    monkeypatch,
):
    monkeypatch.setattr(selector, "_WIDE_SEED_MAX_THREADS", 4)
    builder = _sparse_builder()
    executor = _ClassifierSettingsFakeExecutor(builder)
    page = _read(builder, executor)
    assert page.complete and [row["id"] for row in page.rows] == ["span"]

    envelopes = _seed_envelopes(executor)
    wide = [env for width, env in envelopes if width > ONE_DAY]
    narrow = [env for width, env in envelopes if width <= ONE_DAY]
    assert wide and narrow, [width for width, _ in envelopes]
    assert all(env["max_threads"] == 4 for env in wide)
    assert all(env["max_threads"] == 1 for env in narrow)
    # Workers are the ONLY thing that differs: a wide seed reads under the same
    # byte, memory, block and overflow caps as a narrow one, so the escalation
    # can change how fast the same rows are read and nothing about which rows.
    for env in wide:
        assert {k: v for k, v in env.items() if k != "max_threads"} == {
            k: v for k, v in narrow[0].items() if k != "max_threads"
        }


@pytest.mark.parametrize(
    "caller_workers,expected_wide", [(1, 1), (2, 2), (4, 4), (8, 4)]
)
def test_an_explicit_caller_worker_budget_still_caps_a_wide_seed(
    monkeypatch, caller_workers, expected_wide
):
    monkeypatch.setattr(selector, "_WIDE_SEED_MAX_THREADS", 4)
    builder = _sparse_builder()
    executor = _ClassifierSettingsFakeExecutor(builder)
    page = _read(
        builder,
        executor,
        read_settings={"max_threads": caller_workers, "max_memory_usage": 64 * 1024**2},
    )
    assert page.complete

    envelopes = _seed_envelopes(executor)
    wide = [env for width, env in envelopes if width > ONE_DAY]
    narrow = [env for width, env in envelopes if width <= ONE_DAY]
    assert wide and narrow
    assert all(env["max_threads"] == expected_wide for env in wide)
    # A caller that pins its workers keeps that pin on every narrow statement,
    # exactly as before this rule existed.
    assert all(env["max_threads"] == caller_workers for env in narrow)
    assert all(env["max_memory_usage"] == 64 * 1024**2 for _, env in envelopes)


class _RecordingRowBudgetFakeExecutor(_RowBudgetFakeExecutor):
    def __init__(self, builder, **kwargs):
        super().__init__(builder, **kwargs)
        self.settings_by_query: list[tuple[str, dict]] = []

    def execute_ch_query(self, query, params, *, timeout_ms, settings):
        self.settings_by_query.append((query, dict(settings)))
        return super().execute_ch_query(
            query, params, timeout_ms=timeout_ms, settings=settings
        )


@dataclass
class _ProbedRowBudgetFakeBuilder(_RowBudgetFakeBuilder):
    pass


def test_the_density_probe_keeps_one_worker_whatever_the_caller_or_wide_budget_say(
    monkeypatch,
):
    """The probe is a cost question: #2834's costed width relies on its clamp."""

    monkeypatch.setattr(selector, "_WIDE_SEED_MAX_THREADS", 4)
    builder = _ProbedRowBudgetFakeBuilder([], start=END - timedelta(days=30), end=END)
    executor = _RecordingRowBudgetFakeExecutor(
        builder, seed_read_rows=5_000, density_rows=0
    )
    page = _read(
        builder,
        executor,
        page_size=25,
        deadline_ms=8_000,
        read_settings={"max_threads": 4},
    )
    assert page.complete

    probes = [env for query, env in executor.settings_by_query if query == "density"]
    seeds = list(
        zip(
            executor.seed_widths,
            [env for query, env in executor.settings_by_query if query == "seed"],
            strict=True,
        )
    )
    wide = [env for width, env in seeds if width > ONE_DAY]
    assert probes and wide, (len(probes), [width for width, _ in seeds])
    # The caller asked for four workers and the wide budget is four, so the
    # probe's own clamp is the only thing that can produce one.
    assert all(env["max_threads"] == 1 for env in probes)
    assert all(env["max_threads"] == 4 for env in wide)


def test_span_list_hands_the_selector_no_worker_pin_and_its_own_statements_keep_one():
    from tracer.views.observation_span import (
        SPAN_LIST_READ_SETTINGS,
        SPAN_LIST_SINGLE_WORKER_READ_SETTINGS,
    )

    # The selector merges the caller's dict OVER its own per-kind rule, so a
    # pin here would cap the wide seed to that number on the span list. The
    # statements the view issues itself keep the single worker they had.
    assert "max_threads" not in SPAN_LIST_READ_SETTINGS
    assert SPAN_LIST_SINGLE_WORKER_READ_SETTINGS == {
        **SPAN_LIST_READ_SETTINGS,
        "max_threads": 1,
    }
